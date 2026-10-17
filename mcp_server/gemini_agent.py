"""Gemini on Vertex AI as the DarkGlass agent's reasoning model, choosing tools over MCP.

The agent does not import the tools. It starts the DarkGlass FastMCP server
(`python -m mcp_server.server`) over stdio, discovers the tools with MCP `list_tools`,
hands their schemas to Gemini, and executes whatever Gemini selects with MCP `call_tool`.
Every call streams to the UI as it happens.

Credentials: Google Application Default Credentials (`gcloud auth application-default login`),
with the configured project attached as the quota project.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.config import get_settings

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
MAX_TURNS = 12
REPO_ROOT = Path(__file__).resolve().parents[1]


def _client():
    import google.auth
    from google import genai

    s = get_settings()
    if not s.vertex_project_id:
        raise RuntimeError("set DARKGLASS_VERTEX_PROJECT_ID to your Google Cloud project id")
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"],
                                   quota_project_id=s.vertex_project_id)
    return genai.Client(vertexai=True, project=s.vertex_project_id, location=s.vertex_region, credentials=creds)


def _slim(result: dict[str, Any]) -> dict[str, Any]:
    """What the model needs to reason; the full traces still go to the UI."""
    drop = {"cypher_trace", "roadm_configuration_payload"}
    return json.loads(json.dumps({k: v for k, v in result.items() if k not in drop}, default=str))


def _mcp_result(res: Any) -> dict[str, Any]:
    """Decode an MCP CallToolResult into the tool's dict."""
    text = "".join(getattr(c, "text", "") for c in (res.content or []))
    if getattr(res, "isError", False):
        return {"error": text or "tool error"}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"error": f"non-JSON tool result: {text[:200]}"}
    return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data


def _count_in_engine_bar(result: dict[str, Any]) -> None:
    """The tools ran inside the MCP server process; credit their FalkorDB queries to the live engine bar."""
    from app.core.telemetry import telemetry

    for t in result.get("cypher_trace", []) or []:
        telemetry.record(t.get("cypher", ""), float(t.get("engine_ms") or 0.0), int(t.get("rows") or 0))


async def run_gemini(cable_id: str, emit: Emit, system_prompt: str) -> dict[str, Any]:
    from google.genai import types
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    s = get_settings()
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    # Announce first: credential discovery and server start-up must not leave the UI waiting.
    await emit("agent.start", {"mode": "llm", "cable_id": cable_id, "model": s.gemini_model, "provider": "gemini",
                               "transport": "mcp"})
    client = await asyncio.to_thread(_client)

    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(REPO_ROOT))
    summary = ""
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = (await session.list_tools()).tools
            await emit("agent.mcp", {"server": "darkglass", "tools": [t.name for t in listed]})

            tool = types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description or "",
                                          parameters_json_schema=t.inputSchema)
                for t in listed
            ])
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[tool],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            contents: list[Any] = [types.Content(role="user", parts=[types.Part.from_text(
                text=f"ALERT: anchor drag reported on cable '{cable_id}'. Gradient sync between US-East-Ashburn "
                     "and EU-West-Dublin has stalled. Restore it.")])]

            for _ in range(MAX_TURNS):
                response = await client.aio.models.generate_content(model=s.gemini_model, contents=contents,
                                                                    config=config)
                candidate = response.candidates[0] if response.candidates else None
                if candidate is None or candidate.content is None:
                    summary = "Model returned no content."
                    break
                contents.append(candidate.content)  # keep thought signatures intact for the next turn
                text = "".join(p.text for p in candidate.content.parts or []
                               if getattr(p, "text", None) and not p.thought)
                if text.strip():
                    await emit("agent.thought", {"text": text.strip()})
                calls = response.function_calls or []
                if not calls:
                    summary = text.strip()
                    break
                replies = []
                for call in calls:
                    args = dict(call.args or {})
                    n = len(steps) + 1
                    await emit("agent.tool_call", {"step": n, "tool": call.name, "arguments": args, "via": "mcp"})
                    t0 = time.perf_counter()
                    try:
                        result = _mcp_result(await session.call_tool(call.name, args))
                    except Exception as exc:  # report bad arguments back to the model instead of failing the run
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                    _count_in_engine_bar(result)
                    await emit("agent.tool_result", {"step": n, "tool": call.name, "result": result, "via": "mcp",
                                                     "tool_wall_ms": round((time.perf_counter() - t0) * 1000, 3)})
                    steps.append({"tool": call.name, "result": result})
                    replies.append(types.Part.from_function_response(name=call.name, response=_slim(result)))
                contents.append(types.Content(role="tool", parts=replies))

    detour = next((x["result"] for x in reversed(steps) if x["tool"] == "compute_optimal_optical_detour"), None)
    realloc = next((x["result"] for x in reversed(steps) if x["tool"] == "execute_spectrum_reallocation"), None)
    blast = next((x["result"] for x in steps if x["tool"] == "trace_severed_blast_radius"), {}) or {}
    restored = bool(realloc and str(realloc.get("status", "")).startswith("APPLIED"))
    chosen = None
    if restored and detour:
        applied = [u["span_id"] for u in realloc["updated_spans"]]
        chosen = next((c for c in detour["candidates"] if [h["span_id"] for h in c["hops"]] == applied), None)
    tunnels = blast.get("affected_tunnels", [])
    base = tunnels[0]["baseline_latency_ms"] if tunnels else None
    rebound = {r["tunnel_id"] for r in (realloc or {}).get("rebound_tunnels", [])}
    moved = [t for t in tunnels if t["tunnel_id"] in rebound]
    riders = {j for t in moved for j in t.get("jobs", [])}
    route_ms = (realloc or {}).get("route_latency_ms")
    from mcp_server.agent_runner import _still_down

    result = {
        "restored": restored,
        "mode": "llm",
        "transport": "mcp",
        "model": s.gemini_model,
        "reason": None if restored else "the model did not complete a reallocation",
        "summary": summary,
        "traffic_class": moved[0]["traffic_class"] if moved else None,
        "detour": chosen,
        "rerouted_tunnels": [t["tunnel_id"] for t in moved],
        "still_down": _still_down(blast, {t["tunnel_id"] for t in moved}),
        "restored_rings": sorted({r for t in moved for r in t.get("rings", [])}),
        "restored_gpus": sum(j["gpu_count"] for j in blast.get("affected_jobs", []) if j["job_id"] in riders),
        "spectrum_tbps": (realloc or {}).get("roadm_configuration_payload", {}).get("slice_tbps"),
        "baseline_latency_ms": base,
        "detour_latency_ms": route_ms,
        "latency_delta_ms": round(route_ms - base, 4) if route_ms is not None and base else None,
        "ceiling_ms": (detour or {}).get("constraints", {}).get("max_latency_ceiling_ms"),
        "jitter_budget_ms": min((j["max_jitter_ms"] for j in blast.get("affected_jobs", [])), default=None),
        "advisories": [],
        "graph_engine_ms": round(sum(x["result"].get("engine_ms", 0.0) for x in steps), 4),
        "graph_wall_ms": round(sum(x["result"].get("wall_ms", 0.0) for x in steps), 4),
        "agent_wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "steps": steps,
    }
    await emit("agent.complete", result)
    return result

"""Gemini on Vertex AI as the DarkGlass agent's reasoning model.

Gemini decides which FalkorDB tools to call and with what arguments; this module runs
an explicit function-calling loop so every call streams to the UI as it happens.
Credentials: Google Application Default Credentials (`gcloud auth application-default login`),
with the configured project attached as the quota project.
"""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from mcp_server.tools import (
    audit_secondary_choke_points,
    compute_optimal_optical_detour,
    execute_spectrum_reallocation,
    trace_severed_blast_radius,
)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
MAX_TURNS = 12

TOOLS: dict[str, tuple[Callable[..., dict], str, dict[str, Any]]] = {
    "trace_severed_blast_radius": (
        trace_severed_blast_radius,
        "Trace a severed subsea cable up through LogicalTunnels and NCCL rings to the affected training "
        "jobs. Returns affected jobs, GRADIENT_SYNC/other tunnels with src_gateway, dst_gateway and "
        "baseline_latency_ms, and the compromised SRLG (srlg_violation_id).",
        {
            "type": "object",
            "properties": {"cable_id": {"type": "string", "description": "Cable system id, e.g. 'dunant'."}},
            "required": ["cable_id"],
        },
    ),
    "compute_optimal_optical_detour": (
        compute_optimal_optical_detour,
        "SRLG-isolated, capacity- and latency-bounded shortest optical paths via FalkorDB algo.SPpaths. "
        "Returns path_found, hop_sequence, total_latency_ms and ranked candidates with their hops.",
        {
            "type": "object",
            "properties": {
                "src_station_code": {"type": "string", "description": "Origin gateway code, e.g. 'IAD'."},
                "dst_station_code": {"type": "string", "description": "Destination gateway code, e.g. 'DUB'."},
                "required_bandwidth_tbps": {"type": "number", "description": "Free spectrum needed per hop, Tbps."},
                "max_latency_ceiling_ms": {"type": "number", "description": "Max one-way latency, ms."},
                "excluded_srlg_id": {"type": "string", "description": "SRLG whose spans must not be used."},
            },
            "required": ["src_station_code", "dst_station_code", "required_bandwidth_tbps",
                         "max_latency_ceiling_ms", "excluded_srlg_id"],
        },
    ),
    "audit_secondary_choke_points": (
        audit_secondary_choke_points,
        "Betweenness centrality (FalkorDB algo.betweenness) of candidate detour stations on the surviving "
        "graph. Flags ELEVATED (>= p95) and CRITICAL (>= p99) stations.",
        {
            "type": "object",
            "properties": {
                "station_codes": {"type": "array", "items": {"type": "string"},
                                  "description": "Intermediate station codes of the candidate paths."},
            },
            "required": ["station_codes"],
        },
    ),
    "execute_spectrum_reallocation": (
        execute_spectrum_reallocation,
        "Atomically reserve spectrum on every hop of the chosen path, demote bulk traffic, rebind the "
        "tunnels onto the new route and emit a simulated ROADM configuration.",
        {
            "type": "object",
            "properties": {
                "hop_sequence": {
                    "type": "array",
                    "description": "Ordered hops of the chosen candidate.",
                    "items": {
                        "type": "object",
                        "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                                       "cable": {"type": "string"}},
                        "required": ["from", "to", "cable"],
                    },
                },
                "spectrum_slice_tbps": {"type": "number"},
                "traffic_class": {"type": "string", "description": "e.g. GRADIENT_SYNC"},
                "tunnel_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["hop_sequence", "spectrum_slice_tbps", "traffic_class", "tunnel_ids"],
        },
    ),
}


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


async def run_gemini(cable_id: str, emit: Emit, system_prompt: str) -> dict[str, Any]:
    import asyncio

    from google.genai import types

    s = get_settings()
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    # Announce first: credential discovery is blocking I/O and must not stall the event loop.
    await emit("agent.start", {"mode": "llm", "cable_id": cable_id, "model": s.gemini_model, "provider": "gemini"})
    client = await asyncio.to_thread(_client)

    tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(name=name, description=desc, parameters_json_schema=schema)
        for name, (_, desc, schema) in TOOLS.items()
    ])
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[tool],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents: list[Any] = [types.Content(role="user", parts=[types.Part.from_text(
        text=f"ALERT: anchor drag reported on cable '{cable_id}'. Gradient sync between US-East-Ashburn "
             "and EU-West-Dublin has stalled. Restore it.")])]

    summary = ""
    for _ in range(MAX_TURNS):
        response = await client.aio.models.generate_content(model=s.gemini_model, contents=contents, config=config)
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None:
            summary = "Model returned no content."
            break
        contents.append(candidate.content)  # keep thought signatures intact for the next turn
        text = "".join(p.text for p in candidate.content.parts or [] if getattr(p, "text", None) and not p.thought)
        if text.strip():
            await emit("agent.thought", {"text": text.strip()})
        calls = response.function_calls or []
        if not calls:
            summary = text.strip()
            break
        replies = []
        for call in calls:
            fn = TOOLS.get(call.name, (None,))[0]
            args = dict(call.args or {})
            if fn is None:
                replies.append(types.Part.from_function_response(name=call.name, response={"error": "unknown tool"}))
                continue
            n = len(steps) + 1
            await emit("agent.tool_call", {"step": n, "tool": call.name, "arguments": args})
            t0 = time.perf_counter()
            try:
                result = await asyncio.to_thread(fn, **args)
            except Exception as exc:  # report bad arguments back to the model instead of failing the run
                result = {"error": f"{type(exc).__name__}: {exc}"}
            await emit("agent.tool_result", {"step": n, "tool": call.name, "result": result,
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
    moved = [t for t in tunnels if t["tunnel_id"] in {r["tunnel_id"] for r in (realloc or {}).get("rebound_tunnels", [])}]
    riders = {j for t in moved for j in t.get("jobs", [])}
    result = {
        "restored": restored,
        "mode": "llm",
        "model": s.gemini_model,
        "reason": None if restored else "the model did not complete a reallocation",
        "summary": summary,
        "traffic_class": moved[0]["traffic_class"] if moved else None,
        "detour": chosen,
        "rerouted_tunnels": [t["tunnel_id"] for t in moved],
        "restored_rings": sorted({r for t in moved for r in t.get("rings", [])}),
        "restored_gpus": sum(j["gpu_count"] for j in blast.get("affected_jobs", []) if j["job_id"] in riders),
        "spectrum_tbps": (realloc or {}).get("roadm_configuration_payload", {}).get("slice_tbps"),
        "baseline_latency_ms": base,
        "detour_latency_ms": realloc["route_latency_ms"] if realloc and "route_latency_ms" in realloc else None,
        "latency_delta_ms": round(realloc["route_latency_ms"] - base, 4) if realloc and base and "route_latency_ms" in realloc else None,
        "jitter_budget_ms": min((j["max_jitter_ms"] for j in blast.get("affected_jobs", [])), default=None),
        "advisories": [],
        "graph_engine_ms": round(sum(x["result"].get("engine_ms", 0.0) for x in steps), 4),
        "graph_wall_ms": round(sum(x["result"].get("wall_ms", 0.0) for x in steps), 4),
        "agent_wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "steps": steps,
    }
    await emit("agent.complete", result)
    return result

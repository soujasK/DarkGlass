"""DarkGlass autonomous agent.

Two modes drive the same four FalkorDB tools:

playbook  Deterministic control loop (default). Reproducible, no API key needed,
          and what the "RUN FALKOR AGENT" button uses for the demo.
llm       Claude decides which tools to call and with which arguments, via the
          Anthropic SDK tool runner. Needs ANTHROPIC_API_KEY (or `ant auth login`).

CLI (from the repository root):
    python -m mcp_server.agent_runner --sever dunant              playbook, in-process
    python -m mcp_server.agent_runner --sever dunant --llm        Claude, in-process tools
    python -m mcp_server.agent_runner --llm --via-mcp --cable dunant
                                                                  Claude over the stdio MCP server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import mcp_server  # noqa: F401 - puts backend/ on sys.path
from app.core.config import get_settings
from mcp_server.tools import (
    audit_secondary_choke_points,
    compute_optimal_optical_detour,
    execute_spectrum_reallocation,
    trace_severed_blast_radius,
)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _noop_emit(_type: str, _payload: dict[str, Any]) -> None:
    return None


async def _call(emit: Emit, step: int, tool: str, fn: Callable[..., dict], args: dict[str, Any]) -> dict[str, Any]:
    await emit("agent.tool_call", {"step": step, "tool": tool, "arguments": args})
    t0 = time.perf_counter()
    result = await asyncio.to_thread(fn, **args)
    await emit("agent.tool_result", {"step": step, "tool": tool, "result": result,
                                     "tool_wall_ms": round((time.perf_counter() - t0) * 1000, 3)})
    return result


CLASS_PRIORITY = ["GRADIENT_SYNC", "CHECKPOINT_BURST", "BULK_BACKUP"]


def _plan(blast: dict[str, Any]) -> tuple[list[dict], dict, str] | None:
    """The most critical traffic class the fault cut, its tunnels, and the strictest job riding them."""
    affected = blast.get("affected_tunnels", [])
    for cls in CLASS_PRIORITY:
        group = [t for t in affected if t["traffic_class"] == cls]
        if group:
            riders = {j for t in group for j in t["jobs"]}
            jobs = [j for j in blast["affected_jobs"] if j["job_id"] in riders]
            job = min(jobs, key=lambda j: (j["priority"], j["max_jitter_ms"]))
            return group, job, cls
    return None


def _still_down(blast: dict[str, Any], moved: set[str]) -> list[dict[str, str]]:
    """Tunnels the cut hurt that this run did not move: reported, never hidden behind 'restored'."""
    return [{"tunnel_id": t["tunnel_id"], "traffic_class": t["traffic_class"]}
            for t in blast.get("affected_tunnels", []) if t["tunnel_id"] not in moved]


def _finish(result: dict[str, Any], steps: list[dict], started: float) -> dict[str, Any]:
    result["graph_engine_ms"] = round(sum(s["result"].get("engine_ms", 0.0) for s in steps), 4)
    result["graph_wall_ms"] = round(sum(s["result"].get("wall_ms", 0.0) for s in steps), 4)
    result["agent_wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
    result["steps"] = steps
    return result


async def run_playbook(cable_id: str, emit: Emit = _noop_emit) -> dict[str, Any]:
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    await emit("agent.start", {"mode": "playbook", "cable_id": cable_id})

    blast = await _call(emit, 1, "trace_severed_blast_radius", trace_severed_blast_radius, {"cable_id": cable_id})
    steps.append({"tool": "trace_severed_blast_radius", "result": blast})
    plan = _plan(blast)
    if plan is None:
        result = _finish({"restored": False, "mode": "playbook", "detour": None,
                          "reason": "no logical tunnel rides this cable; nothing to restore"}, steps, started)
        await emit("agent.complete", result)
        return result
    group, job, traffic_class = plan
    tunnel = group[0]

    required_tbps = sum(t["required_bandwidth_gbps"] for t in group) / 1000.0
    ceiling = round(tunnel["baseline_latency_ms"] + job["max_jitter_ms"], 4)
    detour = await _call(emit, 2, "compute_optimal_optical_detour", compute_optimal_optical_detour, {
        "src_station_code": tunnel["src_gateway"],
        "dst_station_code": tunnel["dst_gateway"],
        "required_bandwidth_tbps": required_tbps,
        "max_latency_ceiling_ms": ceiling,
        "excluded_srlg_id": blast["srlg_violation_id"],
    })
    steps.append({"tool": "compute_optimal_optical_detour", "result": detour})

    valid = [c for c in detour["candidates"]
             if c["within_ceiling"] and c["srlg_isolation_verified"] and c["capacity_verified"]]
    if not valid:
        best = detour["candidates"][0]["total_latency_ms"] if detour["candidates"] else None
        reason = (f"no SRLG-isolated path within {ceiling} ms "
                  f"(best clean path {best} ms)" if best else "no SRLG-isolated path with enough capacity exists")
        result = _finish({"restored": False, "mode": "playbook", "detour": None, "reason": reason,
                          "traffic_class": traffic_class, "ceiling_ms": ceiling}, steps, started)
        await emit("agent.complete", result)
        return result

    intermediate = sorted({code for c in valid for code in c["hop_sequence"][1:-1]})
    audit = await _call(emit, 3, "audit_secondary_choke_points", audit_secondary_choke_points,
                        {"station_codes": intermediate})
    steps.append({"tool": "audit_secondary_choke_points", "result": audit})

    risk = {s["station"]: s["risk"] for s in audit["stations"]}
    chosen = next((c for c in valid if all(risk.get(code) != "CRITICAL" for code in c["hop_sequence"][1:-1])),
                  valid[0])

    realloc = await _call(emit, 4, "execute_spectrum_reallocation", execute_spectrum_reallocation, {
        "hop_sequence": [{"from": h["from"], "to": h["to"], "cable": h["cable"]} for h in chosen["hops"]],
        "spectrum_slice_tbps": required_tbps,
        "traffic_class": traffic_class,
        "tunnel_ids": [t["tunnel_id"] for t in group],
    })
    steps.append({"tool": "execute_spectrum_reallocation", "result": realloc})

    restored = realloc["status"].startswith("APPLIED")
    moved = {t["tunnel_id"] for t in group}
    exposed = [t for t in blast["srlg_exposure"]["tunnels"] if t not in moved]
    riders = {j for t in group for j in t["jobs"]}
    result = _finish({
        "restored": restored,
        "mode": "playbook",
        "reason": None if restored else realloc["status"],
        "traffic_class": traffic_class,
        "detour": chosen if restored else None,
        "rerouted_tunnels": sorted(moved) if restored else [],
        "still_down": _still_down(blast, moved if restored else set()),
        "restored_rings": sorted({r for t in group for r in t["rings"]}) if restored else [],
        "restored_gpus": sum(j["gpu_count"] for j in blast["affected_jobs"] if j["job_id"] in riders)
        if restored else 0,
        "spectrum_tbps": required_tbps,
        "baseline_latency_ms": tunnel["baseline_latency_ms"],
        "detour_latency_ms": chosen["total_latency_ms"],
        "latency_delta_ms": round(chosen["total_latency_ms"] - tunnel["baseline_latency_ms"], 4),
        "ceiling_ms": ceiling,
        "jitter_budget_ms": job["max_jitter_ms"],
        "advisories": [
            f"{t} shares {blast['srlg_violation_id']} with the severed cable; pre-provision a disjoint standby path"
            for t in exposed
        ],
    }, steps, started)
    await emit("agent.complete", result)
    return result


# ---------------------------------------------------------------------------
# LLM mode
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the DarkGlass optical restoration agent for a geo-distributed AI supercluster \
(Ashburn IAD <-> Dublin DUB). A subsea cable fault has stalled NCCL Ring-AllReduce gradient sync; every \
second of stall costs about $5.12. Restore gradient traffic using only the four tools, which run Cypher \
on the FalkorDB graph that holds both the physical subsea plant and the training workloads.

Procedure:
1. trace_severed_blast_radius on the faulted cable to find affected jobs, GRADIENT_SYNC tunnels, gateways \
and the compromised SRLG.
2. compute_optimal_optical_detour from the tunnel's src_gateway to dst_gateway. Required bandwidth is the \
sum of the affected GRADIENT_SYNC tunnels' required_bandwidth_gbps divided by 1000. The latency ceiling is \
the tunnel's baseline_latency_ms plus the strictest affected job's max_jitter_ms. Exclude the compromised SRLG.
3. audit_secondary_choke_points on the intermediate stations of the valid candidate paths. Prefer the \
lowest-latency valid candidate with no CRITICAL station.
4. execute_spectrum_reallocation on the chosen candidate's hops with traffic_class GRADIENT_SYNC and the \
affected GRADIENT_SYNC tunnel ids.

Then reply with a short operator summary: what failed, what was rerouted, latency delta against the \
jitter budget, and any SRLG-exposed tunnels that still need a standby path. Do not invent data that the \
tools did not return."""


def _claude_client_and_extras() -> tuple[Any, dict[str, Any]]:
    """Claude client for the configured provider, plus provider-specific request fields.

    anthropic: first-party API with server-side refusal fallback enabled.
    vertex:    Claude on Google Cloud Vertex AI via Application Default Credentials. The
               server-side fallback beta is not offered there, so a refusal simply ends the
               run and is reported (stop_reason == "refusal").
    """
    s = get_settings()
    if s.llm_provider == "vertex":
        from anthropic import AsyncAnthropicVertex

        if not s.vertex_project_id:
            raise RuntimeError("set DARKGLASS_VERTEX_PROJECT_ID to your Google Cloud project id")
        return AsyncAnthropicVertex(project_id=s.vertex_project_id, region=s.vertex_region), {}
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(), {
        "betas": ["server-side-fallback-2026-07-01"],
        "extra_body": {"fallbacks": "default"},
    }


async def run_llm(cable_id: str, emit: Emit = _noop_emit) -> dict[str, Any]:
    """An LLM chooses the tool calls. Tool results also stream to `emit` for the UI."""
    if get_settings().llm_provider == "gemini":
        from mcp_server.gemini_agent import run_gemini

        return await run_gemini(cable_id, emit, SYSTEM_PROMPT)

    from anthropic import beta_async_tool

    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    counter = {"n": 0}

    async def record(tool: str, fn: Callable[..., dict], args: dict[str, Any]) -> str:
        counter["n"] += 1
        result = await _call(emit, counter["n"], tool, fn, args)
        steps.append({"tool": tool, "result": result})
        slim = {k: v for k, v in result.items() if k not in ("cypher_trace", "roadm_configuration_payload")}
        return json.dumps(slim, default=str)

    @beta_async_tool
    async def trace_severed_blast_radius_tool(cable_id: str) -> str:
        """Trace a severed subsea cable up through LogicalTunnels and NCCL rings to the affected training jobs.

        Args:
            cable_id: Cable system id, e.g. 'dunant'.
        """
        return await record("trace_severed_blast_radius", trace_severed_blast_radius, {"cable_id": cable_id})

    @beta_async_tool
    async def compute_optimal_optical_detour_tool(
        src_station_code: str, dst_station_code: str, required_bandwidth_tbps: float,
        max_latency_ceiling_ms: float, excluded_srlg_id: str,
    ) -> str:
        """SRLG-isolated, capacity- and latency-bounded shortest optical paths via FalkorDB algo.SPpaths.

        Args:
            src_station_code: Origin station or gateway code, e.g. 'IAD'.
            dst_station_code: Destination station or gateway code, e.g. 'DUB'.
            required_bandwidth_tbps: Spectrum every hop must have free, in Tbps.
            max_latency_ceiling_ms: Maximum one-way propagation latency in milliseconds.
            excluded_srlg_id: Shared Risk Link Group whose spans must not be used.
        """
        return await record("compute_optimal_optical_detour", compute_optimal_optical_detour, {
            "src_station_code": src_station_code, "dst_station_code": dst_station_code,
            "required_bandwidth_tbps": required_bandwidth_tbps,
            "max_latency_ceiling_ms": max_latency_ceiling_ms, "excluded_srlg_id": excluded_srlg_id,
        })

    @beta_async_tool
    async def audit_secondary_choke_points_tool(station_codes: list[str]) -> str:
        """Betweenness centrality of candidate detour stations on the surviving optical graph.

        Args:
            station_codes: Intermediate station codes of the candidate detour paths.
        """
        return await record("audit_secondary_choke_points", audit_secondary_choke_points,
                            {"station_codes": station_codes})

    @beta_async_tool
    async def execute_spectrum_reallocation_tool(
        hop_sequence: list[dict], spectrum_slice_tbps: float, traffic_class: str, tunnel_ids: list[str],
    ) -> str:
        """Atomically reserve spectrum on every hop, demote bulk traffic, rebind tunnels, emit ROADM config.

        Args:
            hop_sequence: Ordered hops, each an object {"from": code, "to": code, "cable": cable_id}.
            spectrum_slice_tbps: Spectrum to reserve on each hop, in Tbps.
            traffic_class: Traffic class, normally 'GRADIENT_SYNC'.
            tunnel_ids: LogicalTunnel ids to move onto the new route.
        """
        return await record("execute_spectrum_reallocation", execute_spectrum_reallocation, {
            "hop_sequence": hop_sequence, "spectrum_slice_tbps": spectrum_slice_tbps,
            "traffic_class": traffic_class, "tunnel_ids": tunnel_ids,
        })

    await emit("agent.start", {"mode": "llm", "cable_id": cable_id, "model": get_settings().anthropic_model,
                               "provider": get_settings().llm_provider})
    client, extras = _claude_client_and_extras()
    runner = client.beta.messages.tool_runner(
        model=get_settings().anthropic_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        **extras,
        tools=[trace_severed_blast_radius_tool, compute_optimal_optical_detour_tool,
               audit_secondary_choke_points_tool, execute_spectrum_reallocation_tool],
        messages=[{"role": "user", "content": (
            f"ALERT: anchor drag reported on cable '{cable_id}'. Gradient sync between US-East-Ashburn and "
            "EU-West-Dublin has stalled. Restore it.")}],
    )
    final = None
    async for message in runner:
        final = message
        for block in message.content:
            if block.type == "text" and block.text.strip():
                await emit("agent.thought", {"text": block.text})

    summary = ""
    if final is not None:
        if final.stop_reason == "refusal":
            summary = "Model declined the request."
        else:
            summary = "\n".join(b.text for b in final.content if b.type == "text")

    detour = next((s["result"] for s in reversed(steps) if s["tool"] == "compute_optimal_optical_detour"), None)
    realloc = next((s["result"] for s in reversed(steps) if s["tool"] == "execute_spectrum_reallocation"), None)
    restored = bool(realloc and realloc["status"].startswith("APPLIED"))
    chosen = None
    if restored and detour:
        applied = [u["span_id"] for u in realloc["updated_spans"]]
        chosen = next((c for c in detour["candidates"] if [h["span_id"] for h in c["hops"]] == applied), None)
    result = {
        "restored": restored,
        "mode": "llm",
        "detour": chosen,
        "detour_latency_ms": realloc["route_latency_ms"] if realloc else None,
        "summary": summary,
        "graph_engine_ms": round(sum(s["result"].get("engine_ms", 0.0) for s in steps), 4),
        "graph_wall_ms": round(sum(s["result"].get("wall_ms", 0.0) for s in steps), 4),
        "agent_wall_ms": round((time.perf_counter() - started) * 1000, 3),
        "steps": steps,
    }
    await emit("agent.complete", result)
    return result


async def run_llm_via_mcp(cable_id: str) -> str:
    """Claude driving the tools through the real stdio MCP server (mcp_server/server.py)."""
    from anthropic.lib.tools.mcp import async_mcp_tool
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    repo_root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(repo_root))
    client, extras = _claude_client_and_extras()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            runner = client.beta.messages.tool_runner(
                model=get_settings().anthropic_model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "medium"},
                **extras,
                tools=[async_mcp_tool(t, session) for t in tools],
                messages=[{"role": "user", "content": (
                    f"ALERT: anchor drag reported on cable '{cable_id}'. Gradient sync between "
                    "US-East-Ashburn and EU-West-Dublin has stalled. Restore it.")}],
            )
            final = None
            async for message in runner:
                final = message
                for block in message.content:
                    if block.type == "tool_use":
                        print(f"[agent] tool_use {block.name} {json.dumps(block.input)[:200]}")
            if final is None:
                return ""
            if final.stop_reason == "refusal":
                return "Model declined the request."
            return "\n".join(b.text for b in final.content if b.type == "text")


async def _print_emit(event: str, payload: dict[str, Any]) -> None:
    if event == "agent.tool_call":
        print(f"[agent] step {payload['step']} -> {payload['tool']}({json.dumps(payload['arguments'])[:160]})")
    elif event == "agent.tool_result":
        r = payload["result"]
        print(f"[agent] step {payload['step']} <- engine {r.get('engine_ms')} ms, wall {r.get('wall_ms')} ms")
    elif event == "agent.thought":
        print(f"[agent] {payload['text']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="DarkGlass autonomous restoration agent")
    ap.add_argument("--cable", default="dunant", help="cable id the fault is on")
    ap.add_argument("--sever", metavar="CABLE", help="inject an anchor drag on CABLE first (resets prior state)")
    ap.add_argument("--llm", action="store_true", help="let Claude choose the tool calls")
    ap.add_argument("--via-mcp", action="store_true", help="with --llm: call tools through the stdio MCP server")
    args = ap.parse_args()

    from app.services.chaos_engine import chaos

    cable = args.sever or args.cable
    if args.sever:
        chaos.reset()
        chaos.anchor_drag(args.sever)
        print(f"[chaos] anchor drag injected on {args.sever}")

    if args.llm and args.via_mcp:
        print(asyncio.run(run_llm_via_mcp(cable)))
        return 0
    result = asyncio.run((run_llm if args.llm else run_playbook)(cable, _print_emit))
    slim = {k: v for k, v in result.items() if k != "steps"}
    print(json.dumps(slim, indent=2, default=str))
    return 0 if result.get("restored") else 1


if __name__ == "__main__":
    sys.exit(main())

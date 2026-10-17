"""REST endpoints for simulation, agent control, telemetry and benchmarks."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.api.ws import bus
from app.core.config import get_settings
from app.core.database import ping, run
from app.services.chaos_engine import chaos

router = APIRouter(prefix="/api")


class AnchorDragRequest(BaseModel):
    cable_id: str = "dunant"


class AgentRunRequest(BaseModel):
    mode: Literal["playbook", "llm"] = "playbook"


class AskRequest(BaseModel):
    question: str


class BenchmarkRunRequest(BaseModel):
    quick: bool = True


_agent_lock = asyncio.Lock()
_bench_state: dict[str, Any] = {"running": False, "error": None}


@router.get("/health")
def health() -> dict[str, Any]:
    ok = ping()
    counts: dict[str, Any] = {}
    if ok:
        # Separate counts: a combined MATCH chain returns no row at all while a reset re-seeds the jobs.
        for key, q in (("stations", "MATCH (n:LandingStation) RETURN count(n) AS n"),
                       ("active_spans", "MATCH ()-[r:OPTICAL_SPAN]->() RETURN count(r) AS n"),
                       ("jobs", "MATCH (j:TrainingJob) RETURN count(j) AS n")):
            try:
                counts[key] = run(q, read_only=True).rows[0]["n"]
            except Exception:
                counts[key] = None
    s = get_settings()
    model = s.gemini_model if s.llm_provider == "gemini" else s.anthropic_model
    return {"falkordb": "up" if ok else "down", "graph": s.graph_name, "llm_provider": s.llm_provider,
            "llm_model": model, **counts}


@router.get("/engine")
def engine() -> dict[str, Any]:
    from app.core.telemetry import telemetry

    return telemetry.snapshot()


@router.get("/state")
def state() -> dict[str, Any]:
    return chaos.snapshot()


@router.get("/tunnels")
def tunnels() -> list[dict[str, Any]]:
    return run("""
        MATCH (t:LogicalTunnel)
        OPTIONAL MATCH (t)-[:CARRIED_BY]->(cs:CableSystem)
        RETURN t.id AS id, t.traffic_class AS traffic_class, t.state AS state,
               t.required_bandwidth_gbps AS required_bandwidth_gbps,
               t.baseline_latency_ms AS baseline_latency_ms, t.current_latency_ms AS current_latency_ms,
               t.route_codes AS route_codes, t.route_span_ids AS route_span_ids, collect(cs.id) AS carried_by
        ORDER BY id
    """, read_only=True).rows


@router.get("/cables")
def cables() -> list[dict[str, Any]]:
    """Subsea cables that carry at least one logical tunnel - the meaningful fault-injection targets."""
    return run("""
        MATCH (cs:CableSystem)<-[:CARRIED_BY]-(t:LogicalTunnel)
        RETURN cs.id AS id, cs.name AS name, collect(t.id) AS tunnels,
               collect(DISTINCT t.traffic_class) AS traffic_classes
        ORDER BY size(tunnels) DESC, name
    """, read_only=True).rows


@router.get("/cables/catalog")
def cable_catalog() -> list[dict[str, Any]]:
    """Every subsea cable in the graph with its metadata, landing stations, traffic and scan findings."""
    props = run("""
        MATCH (c:CableSystem)
        RETURN c.id AS id, c.name AS name, c.length_km AS length_km, c.design_capacity_tbps AS capacity_tbps,
               c.capacity_model AS capacity_model, c.ready_for_service AS rfs, c.owners AS owners,
               c.scan_risk AS risk, c.scan_rank AS risk_rank, c.scan_isolated_countries AS isolates,
               c.scan_capacity_loss_tbps AS atlantic_loss_tbps, c.scan_gpus_at_risk AS gpus_at_risk
    """, read_only=True).rows
    landings = {r["cid"]: r for r in run("""
        MATCH (a:LandingStation)-[r:OPTICAL_SPAN|SEVERED_SPAN]->()
        WHERE r.medium = 'SUBSEA'
        WITH r.cable_id AS cid, collect(DISTINCT a.name) AS stations, collect(DISTINCT a.country_name) AS countries
        RETURN cid, stations, countries
    """, read_only=True).rows}
    traffic = {r["cid"]: r["classes"] for r in run("""
        MATCH (t:LogicalTunnel)-[:CARRIED_BY]->(c:CableSystem)
        RETURN c.id AS cid, collect(DISTINCT t.traffic_class) AS classes
    """, read_only=True).rows}
    out = []
    for p in props:
        land = landings.get(p["id"], {})
        out.append({
            **p,
            "length_km": p["length_km"] if (p["length_km"] or -1) > 0 else None,
            "rfs": p["rfs"] if (p["rfs"] or -1) > 0 else None,
            "stations": sorted(land.get("stations", [])),
            "countries": sorted(c for c in land.get("countries", []) if c),
            "traffic": traffic.get(p["id"], []),
            "isolates": p["isolates"] or [],
        })
    # Cables our workloads depend on first, then the most dangerous by scan risk, then by name.
    out.sort(key=lambda c: (not c["traffic"], -(c["risk"] or 0), c["name"]))
    return out


@router.post("/chaos/anchor-drag")
async def anchor_drag(req: AnchorDragRequest) -> dict[str, Any]:
    try:
        snap = await asyncio.to_thread(chaos.anchor_drag, req.cable_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await bus.publish("chaos.severed", snap)
    return snap


@router.post("/agent/run")
async def agent_run(req: AgentRunRequest, background: BackgroundTasks) -> dict[str, Any]:
    from mcp_server.agent_runner import run_llm, run_playbook

    snap = chaos.snapshot()
    if not snap["incident"]:
        raise HTTPException(status_code=409, detail="no active incident; simulate a fault first")
    if snap["incident"]["resolved_at"]:
        raise HTTPException(status_code=409, detail="incident already resolved; reset to run again")
    if _agent_lock.locked():
        raise HTTPException(status_code=409, detail="agent already running")
    cable_id = snap["incident"]["cable_id"]

    async def execute() -> dict[str, Any]:
        async with _agent_lock:
            await bus.publish("state", chaos.mark_agent_started())
            try:
                result = await (run_llm if req.mode == "llm" else run_playbook)(cable_id, bus.publish)
            except Exception as exc:  # surface the failure to the UI instead of hanging it
                await bus.publish("agent.error", {"error": str(exc)})
                raise
            final = chaos.resolve(result)
            await bus.publish("state", final)
            return final

    if req.mode == "llm":
        background.add_task(execute)
        return {"accepted": True, "mode": "llm"}
    return await execute()


@router.post("/reset")
async def reset() -> dict[str, Any]:
    snap = await asyncio.to_thread(chaos.reset)
    await bus.publish("reset", snap)
    return snap


@router.post("/scan/run")
async def scan_run() -> dict[str, Any]:
    """Cut every subsea cable on Earth, one at a time, in parallel FalkorDB sandboxes."""
    from app.services.resilience_scan import ScanBusy, run_scan

    loop = asyncio.get_running_loop()

    def progress(event: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(bus.publish(f"scan.{event['phase']}", event), loop)

    async def execute() -> None:
        try:
            await asyncio.to_thread(run_scan, progress)
        except ScanBusy:
            pass
        except Exception as exc:
            await bus.publish("scan.error", {"error": str(exc)})

    asyncio.create_task(execute())
    return {"accepted": True}


@router.post("/ask")
async def ask_graph(req: AskRequest) -> dict[str, Any]:
    """Plain-English question -> Cypher (Gemini) -> read-only FalkorDB query."""
    from app.services.ask_graph import ask

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="empty question")
    try:
        return await asyncio.to_thread(ask, req.question.strip()[:400])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"model call failed: {exc}") from exc


@router.get("/scan/latest")
def scan_latest() -> dict[str, Any]:
    from app.services.resilience_scan import summary_from_graph

    return {"summary": summary_from_graph()}


@router.get("/benchmark")
def benchmark() -> dict[str, Any]:
    path = get_settings().benchmark_dir / "latest.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return {"running": _bench_state["running"], "error": _bench_state["error"], "report": report}


@router.post("/benchmark/run")
async def benchmark_run(req: BenchmarkRunRequest) -> dict[str, Any]:
    from app.services.benchmark import run_benchmark

    if _bench_state["running"]:
        raise HTTPException(status_code=409, detail="benchmark already running")

    async def execute() -> None:
        _bench_state.update(running=True, error=None)
        await bus.publish("benchmark.started", {"quick": req.quick})
        try:
            report = await asyncio.to_thread(run_benchmark, req.quick)
            await bus.publish("benchmark.complete", {"headline": report["headline"]})
        except Exception as exc:
            _bench_state["error"] = str(exc)
            await bus.publish("benchmark.error", {"error": str(exc)})
        finally:
            _bench_state["running"] = False

    asyncio.create_task(execute())
    return {"accepted": True}

"""REST endpoints for simulation, agent control, telemetry and benchmarks."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from app.api.ws import bus
from app.core.config import get_settings
from app.core.database import ping, run
from app.core.limits import HourlyCap, visitor_of
from app.services.chaos_engine import chaos

router = APIRouter(prefix="/api")


class AnchorDragRequest(BaseModel):
    cable_id: str = "dunant"


class AgentRunRequest(BaseModel):
    mode: Literal["playbook", "llm"] = "llm"


class AskRequest(BaseModel):
    question: str


class BenchmarkRunRequest(BaseModel):
    quick: bool = True


log = logging.getLogger("darkglass")
_agent_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


def _root_cause(exc: BaseException) -> str:
    """The MCP client wraps failures in task groups; show the operator the error underneath."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc)
    if "NOT_FOUND" in text and "models/" in text:
        model = text.split("models/", 1)[1].split("`")[0].split("'")[0]
        return f"Gemini model '{model}' is not available on this Google Cloud project"
    if "RESOURCE_EXHAUSTED" in text:
        return "Vertex AI quota is used up for now; wait a minute, or pick Playbook"
    if "PERMISSION_DENIED" in text or "DefaultCredentialsError" in type(exc).__name__ + text:
        return "no access to Vertex AI; run `gcloud auth application-default login` and set the project"
    first = text.split("\n")[0]
    return (first if len(first) <= 200 else first[:200].rsplit(" ", 1)[0] + "...") or type(exc).__name__


def _background(coro) -> None:
    """Run a coroutine in the background. The loop only holds tasks weakly, so keep a reference until it ends."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
_bench_state: dict[str, Any] = {"running": False, "error": None}


_agent_cap = HourlyCap(get_settings().agent_hourly_limit, get_settings().agent_visitor_hourly_limit)
_ask_cap = HourlyCap(get_settings().ask_hourly_limit, get_settings().ask_visitor_hourly_limit)


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
                counts[key] = run(q, read_only=True, track=False).rows[0]["n"]
            except Exception:
                counts[key] = None
        try:  # the most critical job, for the start screen's headline
            rows = run("""MATCH (j:TrainingJob) RETURN j.name AS name, j.gpu_count AS gpus,
                          j.sync_interval_ms AS sync_ms, j.max_jitter_ms AS jitter_ms ORDER BY j.priority LIMIT 1""",
                       read_only=True, track=False).rows
            counts["flagship"] = rows[0] if rows else None
        except Exception:
            counts["flagship"] = None
    s = get_settings()
    model = s.gemini_model if s.llm_provider == "gemini" else s.anthropic_model
    return {"falkordb": "up" if ok else "down", "graph": s.graph_name, "llm_provider": s.llm_provider,
            "llm_model": model, "llm_available": s.llm_available, **counts}


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
    """, read_only=True, track=False).rows


@router.get("/cables")
def cables() -> list[dict[str, Any]]:
    """Subsea cables that carry at least one logical tunnel - the meaningful fault-injection targets."""
    return run("""
        MATCH (cs:CableSystem)<-[:CARRIED_BY]-(t:LogicalTunnel)
        RETURN cs.id AS id, cs.name AS name, collect(t.id) AS tunnels,
               collect(DISTINCT t.traffic_class) AS traffic_classes
        ORDER BY size(tunnels) DESC, name
    """, read_only=True, track=False).rows


@router.get("/cables/catalog")
def cable_catalog() -> list[dict[str, Any]]:
    """Every subsea cable in the graph with its metadata, landing stations, traffic and scan findings."""
    props = run("""
        MATCH (c:CableSystem)
        RETURN c.id AS id, c.name AS name, c.length_km AS length_km, c.design_capacity_tbps AS capacity_tbps,
               c.capacity_model AS capacity_model, c.ready_for_service AS rfs, c.owners AS owners,
               c.scan_risk AS risk, c.scan_rank AS risk_rank, c.scan_isolated_countries AS isolates,
               c.scan_capacity_loss_tbps AS atlantic_loss_tbps, c.scan_gpus_at_risk AS gpus_at_risk
    """, read_only=True, track=False).rows
    landings = {r["cid"]: r for r in run("""
        MATCH (a:LandingStation)-[r:OPTICAL_SPAN|SEVERED_SPAN]->()
        WHERE r.medium = 'SUBSEA'
        WITH r.cable_id AS cid, collect(DISTINCT a.name) AS stations, collect(DISTINCT a.country_name) AS countries
        RETURN cid, stations, countries
    """, read_only=True, track=False).rows}
    traffic = {r["cid"]: r["classes"] for r in run("""
        MATCH (t:LogicalTunnel)-[:CARRIED_BY]->(c:CableSystem)
        RETURN c.id AS cid, collect(DISTINCT t.traffic_class) AS classes
    """, read_only=True, track=False).rows}
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
async def agent_run(req: AgentRunRequest, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    from mcp_server.agent_runner import run_llm, run_playbook

    snap = chaos.snapshot()
    if not snap["incident"]:
        raise HTTPException(status_code=409, detail="no active incident; simulate a fault first")
    if snap["incident"]["resolved_at"]:
        raise HTTPException(status_code=409, detail="incident already resolved; reset to run again")
    if _agent_lock.locked():
        raise HTTPException(status_code=409, detail="agent already running")
    if req.mode == "llm" and not get_settings().llm_available:
        raise HTTPException(status_code=503, detail="Gemini is turned off on this server; choose Playbook under Agent")
    mode, notice = req.mode, None
    if mode == "llm" and (hit := _agent_cap.take(visitor_of(request))):
        # Over the demo's Gemini budget: run the same FalkorDB tools with the Playbook instead of failing.
        mode = "playbook"
        notice = ("Gemini has reached this demo's hourly limit, so the Playbook ran the same FalkorDB tools."
                  if hit == "site" else
                  "You have used this demo's Gemini runs for the hour, so the Playbook ran the same FalkorDB tools.")
    cable_id = snap["incident"]["cable_id"]

    async def execute() -> dict[str, Any]:
        async with _agent_lock:
            await bus.publish("state", chaos.mark_agent_started())
            held: list[dict[str, Any]] = []

            async def emit(kind: str, payload: dict[str, Any]) -> None:
                # Hold back Gemini's "not restored" ending while the Playbook may still finish the fix, so the
                # screen does not flash a failure first.
                if kind == "agent.complete" and mode == "llm" and not payload.get("restored")                         and get_settings().llm_fail_fast:
                    held.append(payload)
                    return
                await bus.publish(kind, payload)

            try:
                try:
                    result = await (run_llm if mode == "llm" else run_playbook)(cable_id, emit)
                except Exception as exc:
                    if not _playbook_can_finish(mode, cable_id):
                        raise
                    log.warning("Gemini failed for %s (%s); the Playbook finishes the fix", cable_id, _root_cause(exc))
                    result = await _finish_with_playbook(cable_id, f"Gemini was unavailable ({_root_cause(exc)})")
                else:
                    if not result.get("restored") and _playbook_can_finish(mode, cable_id):
                        result = await _finish_with_playbook(cable_id, "Gemini stopped before moving the traffic")
                    elif held:
                        await bus.publish("agent.complete", held[-1])
            except Exception as exc:  # surface the failure to the UI instead of hanging it
                log.exception("agent run failed for %s", cable_id)
                await bus.publish("state", chaos.mark_agent_failed())
                await bus.publish("agent.error", {"error": _root_cause(exc)})
                raise
            final = chaos.resolve(result)
            await bus.publish("state", final)
            if get_settings().auto_proof:
                _launch_proof(cable_id)  # measure this very cut in all three engines, in the background
            return final

    if mode == "llm":
        background.add_task(execute)
        return {"accepted": True, "mode": "llm"}
    final = await execute()
    return {**final, "notice": notice} if notice else final


def _playbook_can_finish(mode: str, cable_id: str) -> bool:
    """On a public demo, a Gemini run that failed or stopped early is finished by the Playbook, but only while
    traffic is still on the cut cable, so a reroute Gemini already applied is never applied twice."""
    if mode != "llm" or not get_settings().llm_fail_fast:
        return False
    left = run("MATCH (t:LogicalTunnel)-[:CARRIED_BY]->(:CableSystem {id: $c}) RETURN count(t) AS n",
               {"c": cable_id}, read_only=True, track=False).rows[0]["n"]
    return left > 0


async def _finish_with_playbook(cable_id: str, why: str) -> dict[str, Any]:
    from mcp_server.agent_runner import run_playbook

    await bus.publish("agent.notice", {"text": f"{why}, so the Playbook finished the fix with the same FalkorDB tools."})
    return await run_playbook(cable_id, bus.publish)


class ProofRequest(BaseModel):
    cable_id: str | None = None


_proof_stage: dict[str, Any] = {"stage": None}


def _launch_proof(cable_id: str) -> bool:
    from app.services import live_proof

    if live_proof.state()["running"]:
        return False
    loop = asyncio.get_running_loop()

    def progress(stage: str) -> None:
        _proof_stage["stage"] = stage
        asyncio.run_coroutine_threadsafe(bus.publish("proof.progress", {"cable_id": cable_id, "stage": stage}), loop)

    async def execute() -> None:
        await bus.publish("proof.started", {"cable_id": cable_id})
        try:
            report = await asyncio.to_thread(live_proof.start, cable_id, progress)
            await bus.publish("proof.complete", {"report": report})
        except Exception as exc:
            log.exception("live proof failed for %s", cable_id)
            await bus.publish("proof.error", {"cable_id": cable_id, "error": str(exc)})
        finally:
            _proof_stage["stage"] = None

    _background(execute())
    return True


@router.get("/proof/live")
def proof_live() -> dict[str, Any]:
    from app.services import live_proof

    st = live_proof.state()
    return {"running": st["running"], "cable_id": st["cable_id"], "stage": _proof_stage["stage"],
            "error": st["error"], "report": live_proof.latest()}


@router.post("/proof/live")
async def proof_live_run(req: ProofRequest) -> dict[str, Any]:
    """Cut any cable in a GRAPH.COPY sandbox and answer the agent's questions in FalkorDB, SQL and NetworkX."""
    snap = chaos.snapshot()
    cable_id = req.cable_id or (snap["incident"] or {}).get("cable_id")
    if not cable_id:
        raise HTTPException(status_code=400, detail="pick a cable")
    if not run("MATCH (c:CableSystem {id: $id}) RETURN count(c) AS n", {"id": cable_id}, read_only=True, track=False).rows[0]["n"]:
        raise HTTPException(status_code=404, detail=f"no cable with id '{cable_id}'")
    if not _launch_proof(cable_id):
        raise HTTPException(status_code=409, detail="a proof is already running")
    return {"accepted": True, "cable_id": cable_id}


@router.post("/reset")
async def reset() -> dict[str, Any]:
    if _agent_lock.locked():
        # Resetting under a running agent lets it write its reroute into a graph that was just restored.
        raise HTTPException(status_code=409, detail="the agent is still working; reset when it finishes")
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
            log.exception("planet scan failed")
            await bus.publish("scan.error", {"error": str(exc)})

    _background(execute())
    return {"accepted": True}


@router.post("/ask")
async def ask_graph(req: AskRequest, request: Request) -> dict[str, Any]:
    """Plain-English question -> Cypher (Gemini) -> read-only FalkorDB query."""
    from app.services.ask_graph import ask

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="empty question")
    if not get_settings().llm_available:
        raise HTTPException(status_code=503, detail="Ask the graph needs Gemini, which is turned off on this server")
    if hit := _ask_cap.take(visitor_of(request)):
        raise HTTPException(status_code=429, detail=(
            "Ask the graph has reached this demo's hourly limit; try again later" if hit == "site" else
            "You have used this demo's questions for the hour; try again later"))
    try:
        return await asyncio.to_thread(ask, req.question.strip()[:400])
    except Exception as exc:
        if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
            raise HTTPException(status_code=503, detail="Gemini is busy right now; try again in a minute") from exc
        raise HTTPException(status_code=502, detail=f"model call failed: {exc}") from exc


@router.get("/queries/{tab}")
def tab_queries(tab: str) -> dict[str, Any]:
    """The exact queries behind the planet scan or the proof, read from the code that runs them."""
    import textwrap

    def q(name: str, does: str, text: str, language: str = "cypher") -> dict[str, str]:
        return {"name": name, "does": does, "language": language, "text": textwrap.dedent(text).strip()}

    if tab == "scan":
        from app.services import resilience_scan as rs
        return {"tab": "scan", "queries": [
            q("GRAPH.COPY", "Clone the live graph into 4 sandbox graphs; the scan never touches the original",
              "GRAPH.COPY darkglass_core darkglass_scan_0   (and _1, _2, _3)", "command"),
            q("Cut", "Fail one cable inside a sandbox: its spans become :SEVERED_SPAN", rs.SEVER),
            q("algo.WCC", "Connected components: which landing stations fall off the main network", rs.ISLANDS),
            q("algo.SPpaths", "How much slower Ashburn to Dublin gets on the surviving glass", rs.SHORTEST),
            q("GPU impact", "Which training jobs ride this cable", rs.GPU_IMPACT),
            q("algo.maxFlow", "North America to Europe capacity left after the cut", rs.TRANSATLANTIC),
            q("Restore", "Put the cable back before the next one is cut", rs.RESTORE),
        ]}
    if tab == "proof":
        from app.services import live_proof as lp
        from mcp_server.tools.blast_radius import IMPACT, PATH
        from mcp_server.tools.detour_finder import SEARCH
        return {"tab": "proof", "queries": [
            q("GRAPH.COPY", "Snapshot the live graph so every engine answers the same cut", "GRAPH.COPY darkglass_core <sandbox>", "command"),
            q("Who is hurt: FalkorDB", "The 11-hop path from the seabed to the training job", PATH),
            q("Who is hurt: FalkorDB sweep", "Every GPU behind the cut, located to server, rack and hall", IMPACT),
            q("Safest detour: FalkorDB", "algo.SPpaths over the masked optical layer", SEARCH),
            q("Who is hurt: SQL", "The same question as joins in SQLite", lp.SQL_IMPACT, "sql"),
            q("Safest detour: SQL", "A recursive CTE: it has to be told how deep to search", lp.SQL_DETOUR, "sql"),
        ]}
    raise HTTPException(status_code=404, detail="queries are listed for 'scan' and 'proof'")


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

    _background(execute())
    return {"accepted": True}

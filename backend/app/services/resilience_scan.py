"""Planet-wide N-1 resilience scan: every subsea cable on Earth, cut one at a time, inside FalkorDB.

How FalkorDB is used
--------------------
* GRAPH.COPY       clones the live graph into WORKERS isolated sandbox graphs, so production is
                   never touched and the sandboxes run in parallel on FalkorDB's thread pool.
* Relationship retyping (:OPTICAL_SPAN -> :SEVERED_SPAN) cuts a cable inside a sandbox.
* algo.WCC         finds which landing stations (and countries) fall off the main network.
* algo.SPpaths     re-measures the Ashburn -> Dublin shortest path on the surviving glass.
* Cypher traversal CableSystem <- LogicalTunnel <- NCCL_Ring <- TrainingJob prices the GPU impact.
* algo.maxFlow     measures total North America -> Europe capacity (multi-source, multi-sink) after
                   every cut that touches either continent, on a derived :FLOW_LINK layer: one edge
                   per station pair in whole Gbps (maxFlow rejects multi-edges and capacity ranges
                   wider than 2^32).
* Results are written back to the live graph: risk properties on every :CableSystem and a
  (:ResilienceScan)-[:RANKED]->(:CableSystem) subgraph, so the scan itself is queryable.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.core.database import get_db, get_graph, run

WORKERS = 4
SANDBOX = "darkglass_whatif_{}"
SRC, DST = "IAD", "DUB"
BLOCKED = 1_000_000.0

SEVER = """
MATCH (a:LandingStation)-[r:OPTICAL_SPAN {cable_id: $cable}]->(b:LandingStation)
CREATE (a)-[s:SEVERED_SPAN]->(b) SET s = properties(r)
DELETE r
RETURN a.id AS a, b.id AS b
"""
RESTORE = """
MATCH (a:LandingStation)-[r:SEVERED_SPAN {cable_id: $cable}]->(b:LandingStation)
CREATE (a)-[s:OPTICAL_SPAN]->(b) SET s = properties(r)
DELETE r
RETURN count(s) AS n
"""
ISLANDS = """
CALL algo.WCC({nodeLabels: ['LandingStation'], relationshipTypes: ['OPTICAL_SPAN']})
YIELD node, componentId
WITH componentId, collect(node.id) AS ids
ORDER BY size(ids) DESC
WITH collect(ids) AS comps
RETURN size(comps[0]) AS main_size, reduce(acc = [], c IN comps[1..] | acc + c) AS outside
"""
SHORTEST = """
MATCH (s:LandingStation {code: $src}), (t:LandingStation {code: $dst})
CALL algo.SPpaths({sourceNode: s, targetNode: t, relTypes: ['OPTICAL_SPAN'],
                   weightProp: 'route_weight', pathCount: 1})
YIELD pathWeight
RETURN pathWeight
"""
GPU_IMPACT = """
MATCH (cs:CableSystem {id: $cable})<-[:CARRIED_BY]-(t:LogicalTunnel)<-[:USES_TUNNEL]-(:NCCL_Ring)<-[:COORDINATES]-(j:TrainingJob)
WITH collect(DISTINCT t.traffic_class) AS classes, collect(DISTINCT j) AS jobs
RETURN classes, reduce(g = 0, j IN jobs | g + j.gpu_count) AS gpus,
       any(c IN classes WHERE c = 'GRADIENT_SYNC') AS stalls
"""
NORTH_AMERICA = ["United States", "Canada"]
EUROPE = ["United Kingdom", "Ireland", "France", "Spain", "Portugal", "Norway", "Denmark", "Netherlands",
          "Belgium", "Germany", "Iceland", "Sweden", "Finland", "Italy"]
BUILD_FLOW = """
MATCH (a:LandingStation)-[s:OPTICAL_SPAN]->(b:LandingStation)
WHERE s.status = 'ACTIVE'
WITH a, b, sum(s.available_capacity_tbps) AS cap
CREATE (a)-[:FLOW_LINK {capacity_gbps: toInteger(round(cap * 1000)) + 1}]->(b)
RETURN count(*) AS links
"""
REFLOW_PAIRS = """
UNWIND $pairs AS p
MATCH (a:LandingStation {id: p.a})-[f:FLOW_LINK]->(b:LandingStation {id: p.b})
OPTIONAL MATCH (a)-[s:OPTICAL_SPAN]->(b)
WITH f, sum(coalesce(s.available_capacity_tbps, 0.0)) AS cap
SET f.capacity_gbps = toInteger(round(cap * 1000)) + 1
RETURN count(f) AS n
"""
TRANSATLANTIC = """
MATCH (s:LandingStation) WHERE s.country_name IN $na AND s.lon > -100 AND s.kind = 'LANDING_STATION'
WITH collect(s) AS src
MATCH (t:LandingStation) WHERE t.country_name IN $eu AND t.kind = 'LANDING_STATION'
WITH src, collect(t) AS dst
CALL algo.maxFlow({sourceNodes: src, targetNodes: dst, capacityProperty: 'capacity_gbps',
                   relationshipTypes: ['FLOW_LINK']})
YIELD maxFlow
RETURN maxFlow / 1000.0 AS tbps
"""
ATLANTIC_CABLES = """
MATCH (a:LandingStation)-[r:OPTICAL_SPAN]->(:LandingStation)
WHERE r.medium = 'SUBSEA' AND (a.country_name IN $na OR a.country_name IN $eu)
RETURN collect(DISTINCT r.cable_id) AS ids
"""
WRITE_BACK = """
UNWIND $rows AS r
MATCH (c:CableSystem {id: r.id})
SET c.scan_risk = r.risk, c.scan_rank = r.rank, c.scan_isolated_stations = r.isolated,
    c.scan_isolated_countries = r.countries, c.scan_gpus_at_risk = r.gpus,
    c.scan_latency_delta_ms = r.latency_delta, c.scan_capacity_loss_tbps = r.capacity_loss
RETURN count(c) AS n
"""
CLEAR_SCAN = "MATCH (old:ResilienceScan) DETACH DELETE old"
WRITE_SCAN = """
CREATE (s:ResilienceScan {id: $id, finished_at: $finished_at, duration_ms: $duration_ms, scenarios: $scenarios,
                          workers: $workers, graph_engine_ms: $engine_ms, baseline_capacity_tbps: $base_cap,
                          baseline_latency_ms: $base_lat})
WITH s
UNWIND $top AS t
MATCH (c:CableSystem {id: t.id})
CREATE (s)-[:RANKED {rank: t.rank, risk: t.risk}]->(c)
RETURN count(*) AS ranked
"""


class ScanBusy(RuntimeError):
    pass


_lock = threading.Lock()


def _risk(isolated: int, countries: int, gpus: int, latency_delta: float | None, unreachable: bool,
          capacity_loss_tbps: float) -> float:
    """Explainable score: islanded stations and countries, GPUs at risk, gateway latency, lost capacity."""
    score = min(isolated, 60) * 1.0 + countries * 8.0 + (gpus / 65_536) * 60.0
    score += 40.0 if unreachable else max(0.0, latency_delta or 0.0) * 3.0
    score += max(0.0, capacity_loss_tbps) / 4.0
    return round(score, 2)


def _scenario(graph_name: str, cable: dict, baseline: dict, country_of: dict[str, str],
              atlantic: set[str]) -> dict[str, Any]:
    g = get_graph(graph_name)
    engine = 0.0
    capacity = None
    cut = run(SEVER, {"cable": cable["id"]}, graph=g)
    engine += cut.engine_ms
    pairs = [{"a": r["a"], "b": r["b"]} for r in cut.rows]
    flows = cable["id"] in atlantic and bool(pairs)
    try:
        isl = run(ISLANDS, read_only=True, graph=g)
        sp = run(SHORTEST, {"src": SRC, "dst": DST}, read_only=True, graph=g)
        gp = run(GPU_IMPACT, {"cable": cable["id"]}, read_only=True, graph=g)
        engine += isl.engine_ms + sp.engine_ms + gp.engine_ms
        if flows:
            engine += run(REFLOW_PAIRS, {"pairs": pairs}, graph=g).engine_ms
            mf = run(TRANSATLANTIC, {"na": NORTH_AMERICA, "eu": EUROPE}, read_only=True, graph=g)
            engine += mf.engine_ms
            capacity = mf.rows[0]["tbps"] if mf.rows else None
    finally:
        engine += run(RESTORE, {"cable": cable["id"]}, graph=g).engine_ms
        if flows:
            engine += run(REFLOW_PAIRS, {"pairs": pairs}, graph=g).engine_ms

    newly = sorted(set(isl.rows[0]["outside"]) - baseline["outside"])
    countries = sorted({country_of.get(s, "") for s in newly} - {""})
    lat = sp.rows[0]["pathWeight"] if sp.rows else None
    unreachable = lat is None or lat >= BLOCKED
    delta = None if unreachable else round(lat - baseline["latency"], 4)
    impact = gp.rows[0] if gp.rows else {"gpus": 0, "classes": [], "stalls": False}
    return {
        "id": cable["id"],
        "name": cable["name"],
        "isolated": len(newly),
        "isolated_ids": newly,
        "countries": countries,
        "gpus": int(impact["gpus"] or 0),
        "classes": impact["classes"] or [],
        "stalls": bool(impact["stalls"]),
        "latency_delta": delta,
        "unreachable": unreachable,
        "capacity_loss": round(baseline["capacity"] - capacity, 2) if capacity is not None else 0.0,
        "engine_ms": round(engine, 3),
        "risk": _risk(len(newly), len(countries), int(impact["gpus"] or 0), delta, unreachable,
                      baseline["capacity"] - capacity if capacity is not None else 0.0),
    }


def _card(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("id", "name", "rank", "risk", "isolated", "countries", "gpus", "stalls",
                              "latency_delta", "unreachable", "capacity_loss", "classes")}


def run_scan(progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    if not _lock.acquire(blocking=False):
        raise ScanBusy("a scan is already running")
    db = get_db()
    sandboxes = [SANDBOX.format(i) for i in range(WORKERS)]
    started = time.perf_counter()
    try:
        # 1. Parallel sandboxes: instant clones of the live graph.
        t0 = time.perf_counter()
        for name in sandboxes:
            try:
                get_graph(name).delete()
            except Exception:
                pass  # did not exist
            tc = time.perf_counter()
            db.connection.execute_command("GRAPH.COPY", get_graph().name, name)
            from app.core.telemetry import telemetry

            telemetry.record(f"GRAPH.COPY {get_graph().name} {name}", (time.perf_counter() - tc) * 1000, 0, kind="copy")
            run(BUILD_FLOW, graph=get_graph(name))
        copy_ms = (time.perf_counter() - t0) * 1000

        cables = run("MATCH (c:CableSystem) RETURN c.id AS id, c.name AS name ORDER BY c.id", read_only=True).rows
        country_of = {r["id"]: r["c"] for r in run(
            "MATCH (s:LandingStation) RETURN s.id AS id, s.country_name AS c", read_only=True).rows}
        g0 = get_graph(sandboxes[0])
        base_isl = run(ISLANDS, read_only=True, graph=g0).rows[0]
        base_sp = run(SHORTEST, {"src": SRC, "dst": DST}, read_only=True, graph=g0).rows
        base_mf = run(TRANSATLANTIC, {"na": NORTH_AMERICA, "eu": EUROPE}, read_only=True, graph=g0).rows
        atlantic = set(run(ATLANTIC_CABLES, {"na": NORTH_AMERICA, "eu": EUROPE}, read_only=True,
                           graph=g0).rows[0]["ids"])
        baseline = {"outside": set(base_isl["outside"]), "latency": base_sp[0]["pathWeight"] if base_sp else 0.0,
                    "capacity": base_mf[0]["tbps"] if base_mf else 0.0}
        if progress:
            progress({"phase": "started", "total": len(cables), "workers": WORKERS, "copy_ms": round(copy_ms, 1)})

        # 2. Every cable cut, WORKERS sandboxes in parallel.
        results: list[dict[str, Any]] = []
        done_lock = threading.Lock()
        buckets = [cables[i::WORKERS] for i in range(WORKERS)]

        def worker(i: int) -> None:
            for cable in buckets[i]:
                res = _scenario(sandboxes[i], cable, baseline, country_of, atlantic)
                with done_lock:
                    results.append(res)
                    n = len(results)
                if progress and (n % 12 == 0 or n == len(cables)):
                    with done_lock:
                        batch = [{"id": r["id"], "risk": r["risk"]} for r in results[-12:]]
                    progress({"phase": "progress", "done": n, "total": len(cables), "batch": batch,
                              "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)})

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(worker, range(WORKERS)))
        scan_ms = (time.perf_counter() - started) * 1000

        results.sort(key=lambda r: r["risk"], reverse=True)

        # 4. Write the scan back into the live graph.
        for rank, r in enumerate(results, 1):
            r["rank"] = rank
        run(WRITE_BACK, {"rows": [{k: r[k] for k in ("id", "risk", "rank", "isolated", "countries", "gpus",
                                                      "latency_delta", "capacity_loss")} for r in results]})
        engine_total = round(sum(r["engine_ms"] for r in results), 1)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        scan_id = f"scan-{uuid.uuid4().hex[:8]}"
        run(CLEAR_SCAN)
        run(WRITE_SCAN, {
            "id": scan_id, "finished_at": time.time(), "duration_ms": duration_ms, "scenarios": len(results),
            "workers": WORKERS, "engine_ms": engine_total, "base_cap": baseline["capacity"], "base_lat": baseline["latency"],
            "top": [{"id": r["id"], "rank": r["rank"], "risk": r["risk"]} for r in results[:20]],
        })

        max_risk = max((r["risk"] for r in results), default=1.0) or 1.0
        summary = {
            "id": scan_id,
            "scenarios": len(results),
            "workers": WORKERS,
            "copy_ms": round(copy_ms, 1),
            "scan_ms": round(scan_ms, 1),
            "duration_ms": duration_ms,
            "graph_engine_ms": engine_total,
            "baseline_transatlantic_tbps": round(baseline["capacity"], 1),
            "baseline_latency_ms": round(baseline["latency"], 4),
            "cables_that_island_stations": sum(1 for r in results if r["isolated"]),
            "cables_that_stall_training": sum(1 for r in results if r["stalls"]),
            "top": [_card(r) for r in results[:12]],
            "top_cluster": [_card(r) for r in sorted(results, key=lambda r: (r["gpus"], r["capacity_loss"]),
                                                    reverse=True)[:5] if r["gpus"]],
            "top_planet": [_card(r) for r in sorted(results, key=lambda r: (len(r["countries"]), r["isolated"]),
                                                   reverse=True)[:5] if r["isolated"]],
            "top_capacity": [_card(r) for r in sorted(results, key=lambda r: r["capacity_loss"],
                                                     reverse=True)[:5] if r["capacity_loss"] > 0],
            "heat": {r["id"]: round(math.sqrt(r["risk"] / max_risk), 3) for r in results},
        }
        if progress:
            progress({"phase": "complete", "summary": summary})
        return summary
    finally:
        for name in sandboxes:
            try:
                get_graph(name).delete()
            except Exception:
                pass
        _lock.release()


def summary_from_graph() -> dict[str, Any] | None:
    """Rebuild the scan summary purely from what the scan wrote into the graph."""
    scan = run("MATCH (s:ResilienceScan) RETURN s {.*} AS s", read_only=True).rows
    if not scan:
        return None
    s = scan[0]["s"]
    rows = run("""
        MATCH (c:CableSystem) WHERE c.scan_risk IS NOT NULL
        RETURN c.id AS id, c.name AS name, c.scan_rank AS rank, c.scan_risk AS risk,
               c.scan_isolated_stations AS isolated, c.scan_isolated_countries AS countries,
               c.scan_gpus_at_risk AS gpus, c.scan_latency_delta_ms AS latency_delta,
               c.scan_capacity_loss_tbps AS capacity_loss
        ORDER BY rank
    """, read_only=True).rows
    for r in rows:
        r.update(stalls=r["gpus"] >= 65_536, unreachable=r["latency_delta"] is None, classes=[])
    max_risk = max((r["risk"] for r in rows), default=1.0) or 1.0
    return {
        "id": s["id"],
        "scenarios": s["scenarios"],
        "workers": s["workers"],
        "duration_ms": s["duration_ms"],
        "scan_ms": s["duration_ms"],
        "graph_engine_ms": s["graph_engine_ms"],
        "baseline_transatlantic_tbps": round(s.get("baseline_capacity_tbps") or 0.0, 1),
        "baseline_latency_ms": s.get("baseline_latency_ms"),
        "cables_that_island_stations": sum(1 for r in rows if r["isolated"]),
        "cables_that_stall_training": sum(1 for r in rows if r["stalls"]),
        "top": [_card(r) for r in rows[:12]],
        "top_cluster": [_card(r) for r in sorted(rows, key=lambda r: (r["gpus"], r["capacity_loss"]),
                                                reverse=True)[:5] if r["gpus"]],
        "top_planet": [_card(r) for r in sorted(rows, key=lambda r: (len(r["countries"]), r["isolated"]),
                                               reverse=True)[:5] if r["isolated"]],
        "top_capacity": [_card(r) for r in sorted(rows, key=lambda r: r["capacity_loss"],
                                                 reverse=True)[:5] if r["capacity_loss"] > 0],
        "heat": {r["id"]: round(math.sqrt(r["risk"] / max_risk), 3) for r in rows},
    }


def latest_scan() -> dict[str, Any] | None:
    rows = run("""
        MATCH (s:ResilienceScan)
        OPTIONAL MATCH (s)-[k:RANKED]->(c:CableSystem)
        WITH s, k, c ORDER BY k.rank
        RETURN s {.*} AS scan, collect(c {.id, .name, .scan_risk, .scan_isolated_stations, .scan_isolated_countries,
                                          .scan_gpus_at_risk, .scan_latency_delta_ms, .scan_capacity_loss_tbps,
                                          rank: k.rank}) AS top
    """, read_only=True).rows
    return rows[0] if rows else None

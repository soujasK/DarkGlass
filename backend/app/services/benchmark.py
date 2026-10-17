"""Reproducible benchmark: FalkorDB vs SQLite recursive CTE vs NetworkX on one topology.

Integrity rules
---------------
* All three engines load data/graph/topology.json - the file FalkorDB's live graph
  was built from. FalkorDB is benchmarked on a separate graph (`darkglass_bench`)
  so live incident state cannot leak in.
* An edge-set fingerprint (sha256 over sorted src|dst|cable|latency rows) is taken
  from the file and read back from every engine; the report records whether they match.
* Every engine answers the same question: the 3 lowest-latency simple paths
  IAD -> DUB that avoid SRLG-USE-VAB-SHELF and have >= 3.2 Tbps headroom on every hop.
  Best-path latencies are compared across engines.
* Nothing is hardcoded: every number in the report is measured on this machine.
  SQL runs are cut off by a wall-clock limit and reported as TIMEOUT when they hit it.

Usage (from backend/):
    python -m app.services.benchmark                 standard run
    python -m app.services.benchmark --quick         fewer iterations (UI-triggered runs)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sqlite3
import statistics
import sys
import time
from typing import Any, Callable

import networkx as nx

from app.core.config import get_settings
from app.core.database import get_graph, run
from app.ingestion.import_telegeography import load_into_falkordb, load_topology_file

BENCH_GRAPH = "darkglass_bench"
SRC, DST = "IAD", "DUB"
EXCLUDED_SRLG = "SRLG-USE-VAB-SHELF"
REQUIRED_TBPS = 3.2
CEILING_MS = 50.0  # tunnel baseline (~38 ms) + 12 ms jitter budget
K = 3
BLOCKED = 1_000_000.0
SAMPLED_SOURCES, SEED = 32, 42


def pct(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)

    def q(p: float) -> float:
        if len(s) == 1:
            return s[0]
        idx = p * (len(s) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    return {"n": len(s), "p50": round(q(0.50), 4), "p95": round(q(0.95), 4), "p99": round(q(0.99), 4),
            "mean": round(statistics.fmean(s), 4), "min": round(s[0], 4), "max": round(s[-1], 4)}


def fingerprint(rows: list[tuple[str, str, str, float]]) -> str:
    h = hashlib.sha256()
    for r in sorted(f"{a}|{b}|{c}|{lat:.4f}" for a, b, c, lat in rows):
        h.update(r.encode())
        h.update(b"\n")
    return h.hexdigest()


def timed(fn: Callable[[], Any], iterations: int, warmup: int = 3) -> tuple[list[float], Any]:
    out = None
    for _ in range(warmup):
        out = fn()
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return samples, out


# ---------------------------------------------------------------------------
# FalkorDB
# ---------------------------------------------------------------------------

FK_SEARCH = """
MATCH (src:LandingStation {code: $src}), (dst:LandingStation {code: $dst})
CALL algo.SPpaths({sourceNode: src, targetNode: dst, relTypes: ['OPTICAL_SPAN'],
                   weightProp: 'route_weight', pathCount: $k})
YIELD path, pathWeight
RETURN [r IN relationships(path) | r.span_id] AS spans, pathWeight AS latency
ORDER BY latency
"""
FK_MASK = """
MATCH ()-[r:OPTICAL_SPAN]->()
WHERE r.srlg_id = $srlg OR r.available_capacity_tbps < $req
SET r.route_weight = $blocked
"""
FK_UNMASK = "MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.route_weight = $blocked SET r.route_weight = r.latency_ms"
FK_BETWEENNESS = """
CALL algo.betweenness({nodeLabels: ['LandingStation'], relationshipTypes: ['OPTICAL_SPAN']%s})
YIELD node, score RETURN count(node) AS n
"""


def bench_falkordb(topo: dict, it: dict[str, int]) -> dict[str, Any]:
    load_into_falkordb(topo, BENCH_GRAPH)
    g = get_graph(BENCH_GRAPH)
    rows = run("MATCH (a)-[r:OPTICAL_SPAN]->(b) RETURN a.id AS a, b.id AS b, r.cable_id AS c, r.latency_ms AS l",
               read_only=True, graph=g).rows
    fp = fingerprint([(r["a"], r["b"], r["c"], r["l"]) for r in rows])

    params = {"src": SRC, "dst": DST, "k": K}
    mask_params = {"srlg": EXCLUDED_SRLG, "req": REQUIRED_TBPS, "blocked": BLOCKED}

    # 1. Search only (mask applied once, outside the timed region).
    run(FK_MASK, mask_params, graph=g)
    engine: list[float] = []

    def search():
        res = run(FK_SEARCH, params, read_only=True, graph=g)
        engine.append(res.engine_ms)
        return res.rows

    wall, result = timed(search, it["falkordb_search"])
    engine = engine[-len(wall):]
    run(FK_UNMASK, {"blocked": BLOCKED}, graph=g)
    best = result[0] if result and result[0]["latency"] < BLOCKED else None

    # 2. Full tool cycle: mask -> search -> restore, as the agent runs it.
    def cycle():
        run(FK_MASK, mask_params, graph=g)
        r = run(FK_SEARCH, params, read_only=True, graph=g)
        run(FK_UNMASK, {"blocked": BLOCKED}, graph=g)
        return r.rows

    cycle_wall, _ = timed(cycle, it["falkordb_cycle"], warmup=1)

    # 3. Betweenness centrality, sampled and exact.
    bc_engine: list[float] = []

    def bc(extra: str):
        def _f():
            res = run(FK_BETWEENNESS % extra, read_only=True, graph=g)
            bc_engine.append(res.engine_ms)
        return _f

    sampled_wall, _ = timed(bc(f", samplingSize: {SAMPLED_SOURCES}, samplingSeed: {SEED}"), it["centrality_sampled"], 1)
    sampled_engine = bc_engine[-len(sampled_wall):]
    bc_engine.clear()
    exact_wall, _ = timed(bc(f", samplingSize: {len(topo['stations'])}, samplingSeed: {SEED}"),
                          it["centrality_exact"], 0)
    exact_engine = list(bc_engine)

    g.delete()
    return {
        "fingerprint": fp,
        "best_path": {"latency_ms": round(best["latency"], 4), "hops": len(best["spans"]), "spans": best["spans"]}
        if best else None,
        "detour_search": {"wall_ms": pct(wall), "engine_ms": pct(engine)},
        "detour_tool_cycle": {"wall_ms": pct(cycle_wall)},
        "centrality_sampled": {"wall_ms": pct(sampled_wall), "engine_ms": pct(sampled_engine)},
        "centrality_exact": {"wall_ms": pct(exact_wall), "engine_ms": pct(exact_engine)},
    }


# ---------------------------------------------------------------------------
# SQLite recursive CTE
# ---------------------------------------------------------------------------

SQL_CTE = """
WITH RECURSIVE walk(node, visited, hops, latency, spans) AS (
    SELECT :src, ',' || :src || ',', 0, 0.0, ''
    UNION ALL
    SELECT s.dst, w.visited || s.dst || ',', w.hops + 1, w.latency + s.latency_ms, w.spans || s.span_id || ';'
    FROM walk AS w
    JOIN spans AS s ON s.src = w.node
    WHERE w.hops < :max_hops
      AND w.node <> :dst
      AND s.status = 'ACTIVE'
      AND s.srlg_id <> :srlg
      AND s.available_capacity_tbps >= :req
      AND w.latency + s.latency_ms <= :ceiling
      AND instr(w.visited, ',' || s.dst || ',') = 0
)
SELECT spans, latency, hops FROM walk WHERE node = :dst ORDER BY latency LIMIT :k
"""


def build_sqlite(topo: dict) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    code = {s["id"]: s["code"] for s in topo["stations"]}
    con.execute("CREATE TABLE stations (id TEXT PRIMARY KEY, code TEXT UNIQUE)")
    con.execute("""CREATE TABLE spans (span_id TEXT, src TEXT, dst TEXT, cable_id TEXT, latency_ms REAL,
                   available_capacity_tbps REAL, srlg_id TEXT, status TEXT)""")
    con.executemany("INSERT INTO stations VALUES (?, ?)", list(code.items()))
    con.executemany("INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (s["span_id"], code[s["src"]], code[s["dst"]], s["cable_id"], s["latency_ms"],
         s["available_capacity_tbps"], s["srlg_id"], s["status"]) for s in topo["spans"]])
    con.execute("CREATE INDEX spans_src ON spans (src)")
    return con


def bench_sqlite(topo: dict, depths: list[int], iterations: int, timeout_s: float) -> dict[str, Any]:
    con = build_sqlite(topo)
    ids = {r[1]: r[0] for r in con.execute("SELECT id, code FROM stations")}
    fp = fingerprint([(ids[a], ids[b], c, l) for a, b, c, l in
                      con.execute("SELECT src, dst, cable_id, latency_ms FROM spans")])
    runs = []
    for depth in depths:
        params = {"src": SRC, "dst": DST, "max_hops": depth, "srlg": EXCLUDED_SRLG, "req": REQUIRED_TBPS,
                  "ceiling": CEILING_MS, "k": K}
        samples, result, timed_out = [], None, False
        for _ in range(iterations):
            deadline = time.perf_counter() + timeout_s
            con.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 20_000)
            t0 = time.perf_counter()
            try:
                result = con.execute(SQL_CTE, params).fetchall()
            except sqlite3.OperationalError:
                timed_out = True
                samples.append((time.perf_counter() - t0) * 1000)
                break
            finally:
                con.set_progress_handler(None, 0)
            samples.append((time.perf_counter() - t0) * 1000)
        best = result[0] if result and not timed_out else None
        runs.append({
            "max_hops": depth,
            "status": "TIMEOUT" if timed_out else "OK",
            "timeout_s": timeout_s,
            "wall_ms": pct(samples),
            "best_path": {"latency_ms": round(best[1], 4), "hops": best[2]} if best else None,
        })
        if timed_out:
            break  # deeper searches only grow
    return {"fingerprint": fp, "runs": runs}


# ---------------------------------------------------------------------------
# NetworkX
# ---------------------------------------------------------------------------


def build_networkx(topo: dict) -> nx.MultiDiGraph:
    code = {s["id"]: s["code"] for s in topo["stations"]}
    g = nx.MultiDiGraph()
    g.add_nodes_from(code.values())
    for s in topo["spans"]:
        g.add_edge(code[s["src"]], code[s["dst"]], key=s["span_id"], cable_id=s["cable_id"],
                   latency_ms=s["latency_ms"], srlg_id=s["srlg_id"], avail=s["available_capacity_tbps"],
                   status=s["status"], src_id=s["src"], dst_id=s["dst"])
    return g


def bench_networkx(topo: dict, it: dict[str, int]) -> dict[str, Any]:
    mg = build_networkx(topo)
    fp = fingerprint([(d["src_id"], d["dst_id"], d["cable_id"], d["latency_ms"]) for _, _, d in mg.edges(data=True)])

    def query():
        # Same constraints, applied the idiomatic NetworkX way: filter, collapse parallel spans, Yen's k-shortest.
        g = nx.DiGraph()
        for u, v, key, d in mg.edges(keys=True, data=True):
            if d["status"] != "ACTIVE" or d["srlg_id"] == EXCLUDED_SRLG or d["avail"] < REQUIRED_TBPS:
                continue
            if not g.has_edge(u, v) or d["latency_ms"] < g[u][v]["latency_ms"]:
                g.add_edge(u, v, latency_ms=d["latency_ms"], span_id=key)
        paths = []
        for p in nx.shortest_simple_paths(g, SRC, DST, weight="latency_ms"):
            lat = sum(g[a][b]["latency_ms"] for a, b in zip(p, p[1:]))
            if lat > CEILING_MS:
                break
            paths.append((lat, [g[a][b]["span_id"] for a, b in zip(p, p[1:])]))
            if len(paths) == K:
                break
        return paths

    wall, result = timed(query, it["networkx_search"])
    best = result[0] if result else None

    simple = nx.DiGraph(mg)  # unweighted structure, as FalkorDB's betweenness sees it
    sampled_wall, _ = timed(lambda: nx.betweenness_centrality(simple, k=SAMPLED_SOURCES, seed=SEED, normalized=False),
                            it["centrality_sampled"], 1)
    exact_wall, _ = timed(lambda: nx.betweenness_centrality(simple, normalized=False), it["centrality_exact"], 0)
    return {
        "fingerprint": fp,
        "best_path": {"latency_ms": round(best[0], 4), "hops": len(best[1]), "spans": best[1]} if best else None,
        "detour_search": {"wall_ms": pct(wall)},
        "centrality_sampled": {"wall_ms": pct(sampled_wall)},
        "centrality_exact": {"wall_ms": pct(exact_wall)},
    }


# ---------------------------------------------------------------------------


def run_benchmark(quick: bool = False, sql_timeout_s: float = 30.0) -> dict[str, Any]:
    s = get_settings()
    topo = load_topology_file()
    file_fp = fingerprint([(x["src"], x["dst"], x["cable_id"], x["latency_ms"]) for x in topo["spans"]])
    it = {
        "falkordb_search": 200 if quick else 1000,
        "falkordb_cycle": 20 if quick else 100,
        "networkx_search": 20 if quick else 100,
        "centrality_sampled": 5 if quick else 20,
        "centrality_exact": 1 if quick else 3,
        "sql": 1 if quick else 3,
    }
    random.seed(SEED)
    started = time.time()
    print("[bench] FalkorDB ...", flush=True)
    fk = bench_falkordb(topo, it)
    print("[bench] NetworkX ...", flush=True)
    nxr = bench_networkx(topo, it)
    print("[bench] SQLite recursive CTE ...", flush=True)
    sq = bench_sqlite(topo, [4, 6, 8, 10, 12], it["sql"], sql_timeout_s)

    sql_ok = [r for r in sq["runs"] if r["status"] == "OK" and r["best_path"]]
    latencies = {
        "falkordb": fk["best_path"]["latency_ms"] if fk["best_path"] else None,
        "networkx": nxr["best_path"]["latency_ms"] if nxr["best_path"] else None,
        "sqlite_deepest_completed": sql_ok[-1]["best_path"]["latency_ms"] if sql_ok else None,
    }
    deepest_sql = sq["runs"][-1]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_s": round(time.time() - started, 1),
        "mode": "quick" if quick else "standard",
        "iterations": it,
        "machine": {"python": platform.python_version(), "platform": platform.platform(),
                    "networkx": nx.__version__, "sqlite": sqlite3.sqlite_version},
        "workload": {
            "question": f"{K} lowest-latency simple paths {SRC}->{DST} avoiding {EXCLUDED_SRLG}, "
                        f">= {REQUIRED_TBPS} Tbps headroom per hop, <= {CEILING_MS} ms",
            "stations": topo["meta"]["counts"]["stations"],
            "spans_directed": topo["meta"]["counts"]["spans_directed"],
        },
        "parity": {
            "topology_file": file_fp,
            "falkordb": fk["fingerprint"],
            "networkx": nxr["fingerprint"],
            "sqlite": sq["fingerprint"],
            "verified": len({file_fp, fk["fingerprint"], nxr["fingerprint"], sq["fingerprint"]}) == 1,
        },
        "results_agree": {
            **latencies,
            "falkordb_equals_networkx": latencies["falkordb"] is not None
            and latencies["falkordb"] == latencies["networkx"],
        },
        "falkordb": fk,
        "networkx": nxr,
        "sqlite": sq,
        "headline": {
            "falkordb_sppaths_engine_p50_ms": fk["detour_search"]["engine_ms"]["p50"],
            "falkordb_sppaths_wall_p50_ms": fk["detour_search"]["wall_ms"]["p50"],
            "networkx_yen_wall_p50_ms": nxr["detour_search"]["wall_ms"]["p50"],
            "sqlite_cte_status": deepest_sql["status"],
            "sqlite_cte_max_hops": deepest_sql["max_hops"],
            "sqlite_cte_wall_ms": deepest_sql["wall_ms"]["p50"],
            "falkordb_betweenness_sampled_engine_p50_ms": fk["centrality_sampled"]["engine_ms"]["p50"],
            "networkx_betweenness_sampled_p50_ms": nxr["centrality_sampled"]["wall_ms"]["p50"],
            "falkordb_betweenness_exact_engine_p50_ms": fk["centrality_exact"]["engine_ms"]["p50"],
            "networkx_betweenness_exact_p50_ms": nxr["centrality_exact"]["wall_ms"]["p50"],
        },
    }
    s.benchmark_dir.mkdir(parents=True, exist_ok=True)
    (s.benchmark_dir / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--sql-timeout", type=float, default=30.0, help="seconds before a SQL run is cut off")
    args = ap.parse_args()
    r = run_benchmark(args.quick, args.sql_timeout)
    print(json.dumps({"parity": r["parity"], "results_agree": r["results_agree"], "headline": r["headline"]},
                     indent=2))
    print("[bench] SQL depth sweep:")
    for x in r["sqlite"]["runs"]:
        print(f"[bench]   max_hops={x['max_hops']:>2}  {x['status']:<7}  p50 {x['wall_ms']['p50']:>10.1f} ms  "
              f"best {x['best_path']}")
    print(f"[bench] wrote {get_settings().benchmark_dir / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

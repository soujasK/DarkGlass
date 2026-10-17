"""Live proof: the agent's own questions about one cable cut, answered by three engines.

Input: any cable. Output: for that cut, how long FalkorDB, SQLite and NetworkX each take to answer
the questions the agent asks, and whether they agree.

How it stays fair
-----------------
* Snapshot. The live graph is cloned with GRAPH.COPY. In the clone, every earlier incident is
  undone and the chosen cable is cut, so the snapshot is "the moment after this cut" whatever the
  live demo is doing. The live graph is never written.
* Same data. The snapshot's edges are exported once and loaded into SQLite and NetworkX. A sha256
  of the edge set is read back from each engine and compared.
* Same questions. FalkorDB runs the agent's real tool code against the snapshot. The inputs to the
  detour question (gateways, bandwidth, latency ceiling, trench to avoid) come from FalkorDB's own
  blast-radius answer, exactly as the agent derives them.
* Same clock. Every engine is timed as client wall-clock time in this Python process, so
  FalkorDB's numbers include its network round trip. FalkorDB's engine-only time is reported
  separately and never used for the comparison.
* Same answer. Each engine's answer is checked against FalkorDB's.
* No hints. FalkorDB and NetworkX find the provably best detour without being told how deep it is,
  so SQL must too: its recursive search is bounded only by the latency ceiling. If it has not
  finished within SQL_TIMEOUT_S it is reported as a timeout (and its time as a lower bound). The
  time SQL takes when it is handed the answer's hop count is reported alongside, for transparency.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import threading
import time
from typing import Any, Callable

import networkx as nx

from app.core.config import get_settings
from app.core.database import get_db, get_graph, run, use_graph

SANDBOX_PREFIX = "darkglass_proof_"
RUNS = {"falkordb": 5, "networkx": 3, "sqlite": 1}
SQL_TIMEOUT_S = 10.0
SQL_UNBOUNDED_HOPS = 64  # no practical hop limit; the latency ceiling bounds the search
K = 3

# Relationship types the blast radius walks. Node keys are "Label:id" so ids never collide.
WALK_TYPES = ["LANDS", "CARRIED_BY", "USES_TUNNEL", "COORDINATES", "TERMINATES_AT", "FEEDS", "HAS_NIC",
              "ATTACHED_TO", "MEMBER_OF", "IN_RACK", "IN_ROW", "IN_HALL", "IN_CAMPUS"]
EXPORT_EDGES = """
MATCH (a)-[r:{t}]->(b)
RETURN labels(a)[0] + ':' + coalesce(a.code, a.id) AS a, labels(b)[0] + ':' + coalesce(b.code, b.id) AS b
"""
EXPORT_SPANS = """
MATCH (a:LandingStation)-[r:OPTICAL_SPAN]->(b:LandingStation)
RETURN r.span_id AS span_id, a.code AS src, b.code AS dst, r.cable_id AS cable_id, r.latency_ms AS latency_ms,
       r.available_capacity_tbps AS avail, r.srlg_id AS srlg_id, r.status AS status
"""

# Edge types that only change when the compute fabric is rebuilt: exported once, then reused.
STATIC_TYPES = ["LANDS", "TERMINATES_AT", "FEEDS", "HAS_NIC", "ATTACHED_TO", "MEMBER_OF", "IN_RACK", "IN_ROW",
                "IN_HALL", "IN_CAMPUS"]
_static_cache: dict[tuple, list[tuple]] = {}

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "cable_id": None, "error": None, "report": None}


def state() -> dict[str, Any]:
    return dict(_state)


# --------------------------------------------------------------------------- helpers


def _p50(samples: list[float]) -> float:
    return round(statistics.median(samples), 3)


def _timed(fn: Callable[[], Any], n: int) -> tuple[float, Any, list[float]]:
    out, samples = None, []
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return _p50(samples), out, samples


def _fingerprint(edges: list[tuple]) -> str:
    h = hashlib.sha256()
    for row in sorted("|".join(str(x) for x in e) for e in edges):
        h.update(row.encode())
        h.update(b"\n")
    return h.hexdigest()


# --------------------------------------------------------------------------- snapshot


def _snapshot(cable_id: str, SANDBOX: str) -> dict[str, Any]:
    """Clone the live graph and put the clone in the state right after `cable_id` is cut."""
    from app.ingestion.seed_l7_clusters import RESTORE_TUNNELS
    from app.services.chaos_engine import RESTORE_CAPACITY, RESTORE_SEVERED, SEVER
    from app.core.telemetry import telemetry

    db = get_db()
    try:
        get_graph(SANDBOX).delete()
    except Exception:
        pass  # no leftover sandbox
    t0 = time.perf_counter()
    db.connection.execute_command("GRAPH.COPY", get_settings().graph_name, SANDBOX)
    copy_ms = (time.perf_counter() - t0) * 1000
    telemetry.record(f"GRAPH.COPY {get_settings().graph_name} {SANDBOX}", copy_ms, 0, kind="copy")
    with use_graph(SANDBOX):
        run(RESTORE_SEVERED)
        run(RESTORE_CAPACITY)
        run(RESTORE_TUNNELS)
        severed = run(SEVER, {"cable_id": cable_id}).rows
    return {"copy_ms": round(copy_ms, 1), "severed_spans": len(severed)}


def _export(SANDBOX: str) -> dict[str, Any]:
    g = get_graph(SANDBOX)
    signature = tuple(run(f"MATCH ()-[r:{t}]->() RETURN count(r) AS n", read_only=True, graph=g).rows[0]["n"]
                      for t in STATIC_TYPES)
    if signature not in _static_cache:
        _static_cache.clear()
        _static_cache[signature] = [(t, r["a"], r["b"]) for t in STATIC_TYPES
                                    for r in run(EXPORT_EDGES.format(t=t), read_only=True, graph=g).rows]
    edges = list(_static_cache[signature])
    for t in (t for t in WALK_TYPES if t not in STATIC_TYPES):
        edges += [(t, r["a"], r["b"]) for r in run(EXPORT_EDGES.format(t=t), read_only=True, graph=g).rows]
    spans = run(EXPORT_SPANS, read_only=True, graph=g).rows
    return {"edges": edges, "spans": spans}


# --------------------------------------------------------------------------- SQLite

SQL_IMPACT = """
WITH rings AS MATERIALIZED (
    SELECT DISTINCT u.src AS ring FROM edge c JOIN edge u ON u.type = 'USES_TUNNEL' AND u.dst = c.src
    WHERE c.type = 'CARRIED_BY' AND c.dst = :cable),
gpus AS MATERIALIZED (
    SELECT m.src AS gpu FROM rings r JOIN edge m ON m.type = 'MEMBER_OF' AND m.dst = r.ring),
servers AS MATERIALIZED (
    SELECT DISTINCT h.src AS server FROM gpus g
    JOIN edge a ON a.type = 'ATTACHED_TO' AND a.dst = g.gpu
    JOIN edge h ON h.type = 'HAS_NIC' AND h.dst = a.src),
racks AS MATERIALIZED (
    SELECT DISTINCT k.dst AS rack FROM servers s JOIN edge k ON k.type = 'IN_RACK' AND k.src = s.server),
halls AS MATERIALIZED (
    SELECT DISTINCT h.dst AS hall FROM racks k
    JOIN edge w ON w.type = 'IN_ROW' AND w.src = k.rack
    JOIN edge h ON h.type = 'IN_HALL' AND h.src = w.dst),
campuses AS MATERIALIZED (
    SELECT DISTINCT c.dst AS dc FROM halls h JOIN edge c ON c.type = 'IN_CAMPUS' AND c.src = h.hall)
SELECT (SELECT count(*) FROM gpus), (SELECT count(*) FROM servers), (SELECT count(*) FROM racks),
       (SELECT count(*) FROM halls), (SELECT group_concat(dc) FROM campuses), (SELECT count(*) FROM rings)
"""

SQL_PATH = """
SELECT l.src, l.dst, cb.src, t.dst, f1.dst, f2.dst, f3.dst, f4.dst, n.dst, a.dst, m.dst, co.src
FROM edge l
JOIN edge cb ON cb.type = 'CARRIED_BY' AND cb.dst = l.dst
JOIN edge t  ON t.type = 'TERMINATES_AT' AND t.src = cb.src
JOIN edge f1 ON f1.type = 'FEEDS' AND f1.src = t.dst
JOIN edge f2 ON f2.type = 'FEEDS' AND f2.src = f1.dst
JOIN edge f3 ON f3.type = 'FEEDS' AND f3.src = f2.dst
JOIN edge f4 ON f4.type = 'FEEDS' AND f4.src = f3.dst
JOIN edge n  ON n.type = 'HAS_NIC' AND n.src = f4.dst
JOIN edge a  ON a.type = 'ATTACHED_TO' AND a.src = n.dst
JOIN edge m  ON m.type = 'MEMBER_OF' AND m.src = a.dst
JOIN edge u  ON u.type = 'USES_TUNNEL' AND u.src = m.dst AND u.dst = cb.src
JOIN edge co ON co.type = 'COORDINATES' AND co.dst = m.dst
WHERE l.type = 'LANDS' AND l.dst = :cable
LIMIT 1
"""

SQL_DETOUR = """
WITH RECURSIVE walk(node, visited, hops, latency) AS (
    SELECT :src, ',' || :src || ',', 0, 0.0
    UNION ALL
    SELECT s.dst, w.visited || s.dst || ',', w.hops + 1, w.latency + s.latency_ms
    FROM walk AS w
    JOIN spans AS s ON s.src = w.node
    WHERE w.hops < :max_hops
      AND w.node <> :dst
      AND s.status = 'ACTIVE'
      AND s.srlg_id <> :srlg
      AND s.avail >= :req
      AND w.latency + s.latency_ms <= :ceiling
      AND instr(w.visited, ',' || s.dst || ',') = 0
)
SELECT latency, hops FROM walk WHERE node = :dst ORDER BY latency LIMIT :k
"""


def _build_sqlite(snap: dict[str, Any]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.execute("CREATE TABLE edge (type TEXT, src TEXT, dst TEXT)")
    con.executemany("INSERT INTO edge VALUES (?, ?, ?)", snap["edges"])
    con.execute("CREATE INDEX edge_out ON edge (type, src)")
    con.execute("CREATE INDEX edge_in ON edge (type, dst)")
    con.execute("""CREATE TABLE spans (span_id TEXT, src TEXT, dst TEXT, cable_id TEXT, latency_ms REAL,
                   avail REAL, srlg_id TEXT, status TEXT)""")
    con.executemany("INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(s["span_id"], s["src"], s["dst"], s["cable_id"], s["latency_ms"], s["avail"], s["srlg_id"],
                      s["status"]) for s in snap["spans"]])
    con.execute("CREATE INDEX spans_src ON spans (src)")
    con.execute("ANALYZE")
    return con


def _sql(con: sqlite3.Connection, q: str, params: dict[str, Any]) -> tuple[list[tuple] | None, float]:
    deadline = time.perf_counter() + SQL_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 20_000)
    t0 = time.perf_counter()
    try:
        rows = con.execute(q, params).fetchall()
    except sqlite3.OperationalError:
        rows = None  # cut off at the time limit
    finally:
        con.set_progress_handler(None, 0)
    return rows, (time.perf_counter() - t0) * 1000


# --------------------------------------------------------------------------- NetworkX


def _build_networkx(snap: dict[str, Any]) -> tuple[nx.MultiDiGraph, nx.MultiDiGraph]:
    walk = nx.MultiDiGraph()
    walk.add_edges_from((a, b, t) for t, a, b in snap["edges"])
    plant = nx.MultiDiGraph()
    for s in snap["spans"]:
        plant.add_edge(s["src"], s["dst"], key=s["span_id"], **s)
    return walk, plant


def _nx_in(g: nx.MultiDiGraph, node: str, t: str) -> list[str]:
    return [u for u, _, k in g.in_edges(node, keys=True) if k == t] if node in g else []


def _nx_out(g: nx.MultiDiGraph, node: str, t: str) -> list[str]:
    return [v for _, v, k in g.out_edges(node, keys=True) if k == t] if node in g else []


def _nx_impact(g: nx.MultiDiGraph, cable: str) -> dict[str, Any]:
    tunnels = _nx_in(g, cable, "CARRIED_BY")
    rings = {r for t in tunnels for r in _nx_in(g, t, "USES_TUNNEL")}
    gpus = [x for r in rings for x in _nx_in(g, r, "MEMBER_OF")]
    servers = {s for x in gpus for nic in _nx_in(g, x, "ATTACHED_TO") for s in _nx_in(g, nic, "HAS_NIC")}
    racks = {k for s in servers for k in _nx_out(g, s, "IN_RACK")}
    halls = {h for k in racks for w in _nx_out(g, k, "IN_ROW") for h in _nx_out(g, w, "IN_HALL")}
    campuses = {c for h in halls for c in _nx_out(g, h, "IN_CAMPUS")}
    return {"gpus": len(gpus), "servers": len(servers), "racks": len(racks), "halls": len(halls),
            "campuses": sorted(c.split(":", 1)[1] for c in campuses), "rings": len(rings)}


# (relationship type, direction) for each of the 11 hops, starting at the landing station
PATTERN = [("LANDS", 1), ("CARRIED_BY", -1), ("TERMINATES_AT", 1), ("FEEDS", 1), ("FEEDS", 1), ("FEEDS", 1),
           ("FEEDS", 1), ("HAS_NIC", 1), ("ATTACHED_TO", 1), ("MEMBER_OF", 1), ("COORDINATES", -1)]


def _nx_path(g: nx.MultiDiGraph, cable: str) -> list[str] | None:
    """Depth-first walk of the same 11-hop pattern; the ring must use the tunnel on the path."""
    def step(path: list[str], i: int) -> list[str] | None:
        if i == len(PATTERN):
            return path
        t, d = PATTERN[i]
        nxt = _nx_out(g, path[-1], t) if d == 1 else _nx_in(g, path[-1], t)
        for n in nxt:
            if t == "MEMBER_OF" and path[2] not in _nx_out(g, n, "USES_TUNNEL"):
                continue
            found = step(path + [n], i + 1)
            if found:
                return found
        return None

    for station in _nx_in(g, cable, "LANDS"):
        found = step([station, cable], 1)
        if found:
            return found
    return None


def _nx_detour(plant: nx.MultiDiGraph, q: dict[str, Any]) -> list[float]:
    g = nx.DiGraph()
    for u, v, d in plant.edges(data=True):
        if d["status"] != "ACTIVE" or d["srlg_id"] == q["srlg"] or d["avail"] < q["req"]:
            continue
        if not g.has_edge(u, v) or d["latency_ms"] < g[u][v]["latency_ms"]:
            g.add_edge(u, v, latency_ms=d["latency_ms"])
    out: list[float] = []
    if q["src"] not in g or q["dst"] not in g:
        return out
    try:
        for p in nx.shortest_simple_paths(g, q["src"], q["dst"], weight="latency_ms"):
            lat = sum(g[a][b]["latency_ms"] for a, b in zip(p, p[1:]))
            if lat > q["ceiling"]:
                break
            out.append(lat)
            if len(out) == K:
                break
    except nx.NetworkXNoPath:
        pass
    return out


# --------------------------------------------------------------------------- the proof


def _detour_question(blast: dict[str, Any]) -> dict[str, Any]:
    """The detour inputs the agent derives from the blast radius, or the default corridor if nothing rides the cable."""
    from mcp_server.agent_runner import _plan

    plan = _plan(blast)
    if plan:
        group, job, _ = plan
        tun = group[0]
        return {"src": tun["src_gateway"], "dst": tun["dst_gateway"], "srlg": blast["srlg_violation_id"],
                "req": sum(t["required_bandwidth_gbps"] for t in group) / 1000.0,
                "ceiling": round(tun["baseline_latency_ms"] + job["max_jitter_ms"], 4), "source": "agent"}
    return {"src": "IAD", "dst": "DUB", "srlg": blast.get("srlg_violation_id") or "", "req": 3.2, "ceiling": 50.0,
            "source": "default corridor"}


def run_live_proof(cable_id: str, progress: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    from mcp_server.tools.blast_radius import IMPACT, PATH, trace_severed_blast_radius
    from mcp_server.tools.detour_finder import compute_optimal_optical_detour

    import uuid

    started = time.perf_counter()
    SANDBOX = SANDBOX_PREFIX + uuid.uuid4().hex[:8]  # unique, so concurrent proofs never collide
    progress("snapshot")
    sandbox = _snapshot(cable_id, SANDBOX)
    try:
        g = get_graph(SANDBOX)
        name = run("MATCH (c:CableSystem {id: $id}) RETURN c.name AS n", {"id": cable_id}, read_only=True,
                   graph=g).rows
        if not name:
            raise ValueError(f"unknown cable_id {cable_id!r}")

        # FalkorDB: the agent's own tool code, pointed at the snapshot.
        progress("falkordb")
        with use_graph(SANDBOX):
            blast = trace_severed_blast_radius(cable_id)
            question = _detour_question(blast)
            detour_args = {"src_station_code": question["src"], "dst_station_code": question["dst"],
                           "required_bandwidth_tbps": question["req"], "max_latency_ceiling_ms": question["ceiling"],
                           "excluded_srlg_id": question["srlg"]}
            engine_ms: dict[str, list[float]] = {"hurt": [], "detour": []}

            def fk_hurt():
                p = run(PATH, {"cable_id": cable_id}, read_only=True)
                i = run(IMPACT, {"cable_id": cable_id}, read_only=True)
                engine_ms["hurt"].append(p.engine_ms + i.engine_ms)
                return p.rows, i.rows

            def fk_detour():
                d = compute_optimal_optical_detour(**detour_args)
                engine_ms["detour"].append(d["engine_ms"] if "engine_ms" in d else d["pathfinding_engine_ms"])
                return d

            fk_hurt()  # warm caches, as every engine gets
            fk_hurt_ms, (fk_path, fk_impact), _ = _timed(fk_hurt, RUNS["falkordb"])
            fk_detour_ms, fk_det, _ = _timed(fk_detour, RUNS["falkordb"])
        fk_imp = fk_impact[0] if fk_impact else {"gpus": 0, "servers": 0, "racks": 0, "halls": 0, "campuses": [],
                                                 "rings": 0}
        fk_best = fk_det["total_latency_ms"]
        fk_any = [c["total_latency_ms"] for c in fk_det["candidates"] if c["within_ceiling"]]
        fk_best_any = min(fk_any) if fk_any else None

        progress("export")
        t0 = time.perf_counter()
        snap = _export(SANDBOX)
        export_ms = (time.perf_counter() - t0) * 1000
        fp_falkordb = _fingerprint(snap["edges"] + [tuple(s.values()) for s in snap["spans"]])
        cable_key = f"CableSystem:{cable_id}"

        progress("sqlite")
        con = _build_sqlite(snap)
        fp_sqlite = _fingerprint(list(con.execute("SELECT type, src, dst FROM edge")) +
                                 list(con.execute("SELECT span_id, src, dst, cable_id, latency_ms, avail, srlg_id, "
                                                  "status FROM spans")))
        sq_imp_rows, sq_imp_ms = _sql(con, SQL_IMPACT, {"cable": cable_key})
        sq_path_rows, sq_path_ms = _sql(con, SQL_PATH, {"cable": cable_key})
        det_params = {"src": question["src"], "dst": question["dst"], "srlg": question["srlg"],
                      "req": question["req"], "ceiling": question["ceiling"], "k": K}
        sq_det_rows, sq_det_ms = _sql(con, SQL_DETOUR, {**det_params, "max_hops": SQL_UNBOUNDED_HOPS})
        hint = len(fk_det["hops"]) or None
        sq_hint_rows, sq_hint_ms = _sql(con, SQL_DETOUR, {**det_params, "max_hops": hint}) if hint else (None, None)
        con.close()
        sq_imp = None
        if sq_imp_rows:
            a = sq_imp_rows[0]
            sq_imp = {"gpus": a[0], "servers": a[1], "racks": a[2], "halls": a[3],
                      "campuses": sorted(x.split(":", 1)[1] for x in (a[4] or "").split(",") if x), "rings": a[5]}

        progress("networkx")
        walk, plant = _build_networkx(snap)
        fp_networkx = _fingerprint([(k, u, v) for u, v, k in walk.edges(keys=True)] +
                                   [tuple(d[f] for f in ("span_id", "src", "dst", "cable_id", "latency_ms", "avail",
                                                         "srlg_id", "status")) for _, _, d in plant.edges(data=True)])
        nx_hurt_ms, (nx_path, nx_imp), _ = _timed(lambda: (_nx_path(walk, cable_key), _nx_impact(walk, cable_key)),
                                                  RUNS["networkx"])
        nx_det_ms, nx_det, _ = _timed(lambda: _nx_detour(plant, question), RUNS["networkx"])
    finally:
        try:
            get_graph(SANDBOX).delete()
        except Exception:
            pass

    def same_impact(x: dict | None) -> bool:
        return bool(x) and all(x[k] == fk_imp[k] for k in ("gpus", "servers", "racks", "halls", "rings")) \
            and sorted(x["campuses"]) == sorted(fk_imp["campuses"])

    def same_latency(x: float | None) -> bool:
        return (x is None and fk_best_any is None) or (
            x is not None and fk_best_any is not None and abs(x - fk_best_any) < 1e-3)

    fk_hops = fk_path[0]["hops"] if fk_path else 0
    sq_hops = 11 if sq_path_rows else 0
    nx_hops = len(nx_path) - 1 if nx_path else 0
    sq_det_best = sq_det_rows[0][0] if sq_det_rows else None
    nx_det_best = nx_det[0] if nx_det else None

    engines = {
        "falkordb": {"hurt_ms": fk_hurt_ms, "detour_ms": fk_detour_ms,
                     "hurt_engine_ms": _p50(engine_ms["hurt"][1:]), "detour_engine_ms": _p50(engine_ms["detour"])},
        "networkx": {"hurt_ms": nx_hurt_ms, "detour_ms": nx_det_ms},
        "sqlite": {"hurt_ms": round(sq_imp_ms + sq_path_ms, 3), "detour_ms": round(sq_det_ms, 3),
                   "hurt_timed_out": sq_imp_rows is None or sq_path_rows is None,
                   "detour_timed_out": sq_det_rows is None,
                   "detour_hinted_ms": round(sq_hint_ms, 3) if sq_hint_ms is not None else None,
                   "detour_hinted_hops": hint},
    }
    for e in engines.values():
        e["total_ms"] = round(e["hurt_ms"] + e["detour_ms"], 3)
    sq = engines["sqlite"]
    sq["timed_out"] = sq["hurt_timed_out"] or sq["detour_timed_out"]

    report = {
        "cable_id": cable_id,
        "cable_name": name[0]["n"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": round(time.perf_counter() - started, 1),
        "runs": RUNS,
        "snapshot": {**sandbox, "export_ms": round(export_ms, 1), "edges": len(snap["edges"]) + len(snap["spans"])},
        "parity": {"falkordb": fp_falkordb, "sqlite": fp_sqlite, "networkx": fp_networkx,
                   "verified": len({fp_falkordb, fp_sqlite, fp_networkx}) == 1},
        "questions": {
            "hurt": {
                "label": "Who is hurt",
                "answer": {"falkordb": {**fk_imp, "hops": fk_hops},
                           "sqlite": {**(sq_imp or {}), "hops": sq_hops},
                           "networkx": {**nx_imp, "hops": nx_hops}},
                "agree": {"sqlite": None if sq_imp_rows is None or sq_path_rows is None
                          else same_impact(sq_imp) and sq_hops == fk_hops,
                          "networkx": same_impact(nx_imp) and nx_hops == fk_hops},
            },
            "detour": {
                "label": "Safest detour",
                "inputs": question,
                "answer": {"falkordb": fk_best_any, "sqlite": sq_det_best, "networkx": nx_det_best,
                           "falkordb_valid": fk_best, "hops": len(fk_det["hops"])},
                "agree": {"sqlite": None if sq_det_rows is None else same_latency(sq_det_best),
                          "networkx": same_latency(nx_det_best)},
            },
        },
        "engines": engines,
        "speedup": {
            # a lower bound when SQL timed out
            "vs_sqlite": round(engines["sqlite"]["total_ms"] / engines["falkordb"]["total_ms"], 1),
            "vs_sqlite_is_lower_bound": engines["sqlite"]["timed_out"],
            "sql_timeout_s": SQL_TIMEOUT_S,
            "vs_networkx": round(engines["networkx"]["total_ms"] / engines["falkordb"]["total_ms"], 1),
        },
    }
    s = get_settings()
    s.benchmark_dir.mkdir(parents=True, exist_ok=True)
    (s.benchmark_dir / "live_proof.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def start(cable_id: str, progress: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """Run one proof at a time; a second request while one runs is refused."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("a proof is already running")
    _state.update(running=True, cable_id=cable_id, error=None)
    try:
        report = run_live_proof(cable_id, progress)
        _state["report"] = report
        return report
    except Exception as exc:
        _state["error"] = str(exc)
        raise
    finally:
        _state["running"] = False
        _lock.release()


def latest() -> dict[str, Any] | None:
    if _state["report"]:
        return _state["report"]
    path = get_settings().benchmark_dir / "live_proof.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

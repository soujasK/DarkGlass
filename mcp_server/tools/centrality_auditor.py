"""Tool 3: audit_secondary_choke_points - betweenness centrality on the surviving optical graph.

Severed spans are retyped to :SEVERED_SPAN, so algo.betweenness over OPTICAL_SPAN
already sees only surviving glass. Risk is graded against the graph-wide score
distribution (percentiles computed in the same query), not a fixed constant.

Scores are Brandes betweenness estimated from SAMPLING_SIZE source nodes with a
fixed seed (FalkorDB's exact mode was measured at ~0.75-1.6 s on this graph, far
outside the restoration budget; the sampled estimate runs in ~35 ms).
"""

from __future__ import annotations

from typing import Any

from app.core.database import run

CENTRALITY = """
CALL algo.betweenness({nodeLabels: ['LandingStation'], relationshipTypes: ['OPTICAL_SPAN'],
                       samplingSize: $sampling_size, samplingSeed: $sampling_seed})
YIELD node, score
WITH collect({code: node.code, name: node.name, score: score}) AS rows,
     percentileCont(score, 0.95) AS p95,
     percentileCont(score, 0.99) AS p99,
     max(score) AS max_score
UNWIND rows AS r
WITH r, p95, p99, max_score
WHERE r.code IN $candidate_path_stations
RETURN r.code AS station, r.name AS name, r.score AS centrality_index, p95, p99, max_score
ORDER BY centrality_index DESC
"""


def _grade(score: float, p95: float, p99: float) -> str:
    if score >= p99:
        return "CRITICAL"
    if score >= p95:
        return "ELEVATED"
    return "NOMINAL"


SAMPLING_SIZE = 32
SAMPLING_SEED = 42


def audit_secondary_choke_points(station_codes: list[str]) -> dict[str, Any]:
    res = run(CENTRALITY, {"candidate_path_stations": list(station_codes), "sampling_size": SAMPLING_SIZE,
                           "sampling_seed": SAMPLING_SEED}, read_only=True)
    traces = [res.trace("betweenness_centrality")]
    from mcp_server.tools import summarize_timing

    if not res.rows:
        return {"choke_point_detected": False, "highest_centrality_station": None, "centrality_score": 0.0,
                "risk_assessment": "NOMINAL", "stations": [], "cypher_trace": traces, **summarize_timing(traces)}

    p95, p99 = res.rows[0]["p95"], res.rows[0]["p99"]
    stations = [{
        "station": r["station"],
        "name": r["name"],
        "centrality_index": round(r["centrality_index"], 2),
        "risk": _grade(r["centrality_index"], p95, p99),
    } for r in res.rows]
    top = stations[0]
    return {
        "choke_point_detected": top["risk"] != "NOMINAL",
        "highest_centrality_station": top["station"],
        "centrality_score": top["centrality_index"],
        "risk_assessment": top["risk"],
        "thresholds": {"p95": round(p95, 2), "p99": round(p99, 2), "graph_max": round(res.rows[0]["max_score"], 2)},
        "method": f"Brandes betweenness, {SAMPLING_SIZE} sampled sources, seed {SAMPLING_SEED}",
        "stations": stations,
        "cypher_trace": traces,
        **summarize_timing(traces),
    }

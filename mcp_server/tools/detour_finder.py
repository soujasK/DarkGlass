"""Tool 2: compute_optimal_optical_detour - SRLG-isolated, capacity- and latency-bounded routing.

Why a mask instead of a WHERE filter
------------------------------------
Filtering algo.SPpaths output with `WHERE ALL(r IN relationships(path) ...)` runs
after the search: when the k shortest paths all cross the compromised SRLG (common
at shared shelf approaches) the filter empties the result even though a clean
detour exists. Bounding the search with maxLen / maxCost instead was measured on
this topology to miss valid paths or run into the query timeout.

So the tool runs three phases inside one lock:
  1. MASK     spans in the excluded SRLG -> status 'SRLG_BLOCKED'; spans without
              headroom -> 'CAPACITY_MASKED'; both get route_weight = 1e6.
  2. SEARCH   algo.SPpaths over OPTICAL_SPAN weighted by route_weight. Masked
              spans are never relaxed while any clean path exists; a returned
              path with weight >= 1e6 means no clean path exists.
  3. RESTORE  masked spans return to ACTIVE with route_weight = latency_ms.
Severed spans are retyped to :SEVERED_SPAN and are invisible to phase 2 entirely.
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.config import get_settings
from app.core.database import run

ROUTE_BLOCKED_WEIGHT = 1_000_000.0
_route_lock = threading.Lock()

# Two statements rather than one OR predicate, so each is an edge index scan.
MASK_SRLG = """
MATCH ()-[r:OPTICAL_SPAN {srlg_id: $excluded_srlg}]->()
WHERE r.status = 'ACTIVE'
SET r.status = 'SRLG_BLOCKED', r.route_weight = $blocked_weight
RETURN count(r) AS srlg_masked
"""

MASK_CAPACITY = """
MATCH ()-[r:OPTICAL_SPAN]->()
WHERE r.available_capacity_tbps < $required_tbps AND r.status = 'ACTIVE'
SET r.status = 'CAPACITY_MASKED', r.route_weight = $blocked_weight
RETURN count(r) AS capacity_masked
"""

SEARCH = """
MATCH (src:LandingStation {code: $src_code}), (dst:LandingStation {code: $dst_code})
CALL algo.SPpaths({
  sourceNode: src,
  targetNode: dst,
  relTypes: ['OPTICAL_SPAN'],
  weightProp: 'route_weight',
  pathCount: $k
}) YIELD path, pathWeight
RETURN [n IN nodes(path) | n.code] AS station_hops,
       [r IN relationships(path) | r.cable_id] AS cable_traversal,
       [r IN relationships(path) | {span_id: r.span_id, cable: r.cable_id, medium: r.medium,
                                    latency_ms: r.latency_ms, srlg_id: r.srlg_id, status: r.status,
                                    available_capacity_tbps: r.available_capacity_tbps}] AS spans,
       pathWeight AS cumulative_latency_ms
ORDER BY cumulative_latency_ms ASC
"""

RESTORE = """
MATCH ()-[r:OPTICAL_SPAN]->()
WHERE r.status IN $masked_states
SET r.status = 'ACTIVE', r.route_weight = r.latency_ms
RETURN count(r) AS restored
"""
MASKED_STATES = ["SRLG_BLOCKED", "CAPACITY_MASKED"]


def compute_optimal_optical_detour(
    src_station_code: str,
    dst_station_code: str,
    required_bandwidth_tbps: float,
    max_latency_ceiling_ms: float,
    excluded_srlg_id: str,
    candidate_paths: int | None = None,
) -> dict[str, Any]:
    k = candidate_paths or get_settings().candidate_paths
    with _route_lock:
        try:
            mask_srlg = run(MASK_SRLG, {"excluded_srlg": excluded_srlg_id, "blocked_weight": ROUTE_BLOCKED_WEIGHT})
            mask_cap = run(MASK_CAPACITY, {"required_tbps": required_bandwidth_tbps,
                                           "blocked_weight": ROUTE_BLOCKED_WEIGHT})
            search = run(SEARCH, {"src_code": src_station_code, "dst_code": dst_station_code, "k": k},
                         read_only=True)
        finally:
            restore = run(RESTORE, {"masked_states": MASKED_STATES})

    traces = [mask_srlg.trace("phase_1a_mask_srlg"), mask_cap.trace("phase_1b_mask_capacity"),
              search.trace("phase_2_sppaths"), restore.trace("phase_3_restore")]

    seen: set[tuple] = set()
    candidates = []
    for row in search.rows:
        key = tuple(s["span_id"] for s in row["spans"])
        if key in seen:
            continue
        seen.add(key)
        clean = row["cumulative_latency_ms"] < ROUTE_BLOCKED_WEIGHT
        # Defensive verification, independent of the engine-side mask.
        isolated = all(s["srlg_id"] != excluded_srlg_id for s in row["spans"])
        capacity_ok = all(s["available_capacity_tbps"] >= required_bandwidth_tbps for s in row["spans"])
        hops = [
            {"from": a, "to": b, "cable": s["cable"], "span_id": s["span_id"], "medium": s["medium"],
             "latency_ms": s["latency_ms"], "srlg_id": s["srlg_id"],
             "available_capacity_tbps": s["available_capacity_tbps"]}
            for a, b, s in zip(row["station_hops"], row["station_hops"][1:], row["spans"])
        ]
        candidates.append({
            "hop_sequence": row["station_hops"],
            "cables_used": list(dict.fromkeys(c for c in row["cable_traversal"] if c != "backhaul")),
            "total_latency_ms": round(row["cumulative_latency_ms"], 4) if clean else None,
            "within_ceiling": clean and row["cumulative_latency_ms"] <= max_latency_ceiling_ms,
            "srlg_isolation_verified": clean and isolated,
            "capacity_verified": clean and capacity_ok,
            "hops": hops,
        })

    valid = [c for c in candidates if c["within_ceiling"] and c["srlg_isolation_verified"] and c["capacity_verified"]]
    best = valid[0] if valid else None
    from mcp_server.tools import summarize_timing

    return {
        "path_found": best is not None,
        "hop_sequence": best["hop_sequence"] if best else [],
        "cables_used": best["cables_used"] if best else [],
        "total_latency_ms": best["total_latency_ms"] if best else None,
        "hops": best["hops"] if best else [],
        "candidates": candidates,
        "constraints": {
            "src_station_code": src_station_code,
            "dst_station_code": dst_station_code,
            "required_bandwidth_tbps": required_bandwidth_tbps,
            "max_latency_ceiling_ms": max_latency_ceiling_ms,
            "excluded_srlg_id": excluded_srlg_id,
        },
        "masked_spans": {"srlg": mask_srlg.rows[0]["srlg_masked"], "capacity": mask_cap.rows[0]["capacity_masked"]},
        "pathfinding_engine_ms": round(search.engine_ms, 4),
        "pathfinding_wall_ms": round(search.wall_ms, 4),
        "cypher_trace": traces,
        **summarize_timing(traces),
    }

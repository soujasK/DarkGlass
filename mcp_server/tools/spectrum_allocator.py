"""Tool 4: execute_spectrum_reallocation - reserve spectrum on a detour and emit ROADM config.

The capacity reservation is one atomic Cypher statement: it only mutates when every
requested hop exists and has headroom, so a partial reservation cannot be left behind.
The ROADM payload is a simulation (no hardware is driven); its shape follows the
ITU-T G.694.1 flexible grid (6.25 GHz centre granularity, 12.5 GHz slot width).
"""

from __future__ import annotations

import time
from typing import Any

from app.core.database import run

RESERVE = """
UNWIND $allocated_hops AS hop
MATCH (a:LandingStation {code: hop.from})-[r:OPTICAL_SPAN {cable_id: hop.cable}]->(b:LandingStation {code: hop.to})
WITH collect({r: r, a: a.code, b: b.code}) AS matched
WHERE size(matched) = size($allocated_hops)
  AND all(m IN matched WHERE m.r.available_capacity_tbps >= $allocated_tbps)
UNWIND matched AS m
WITH m.r AS r, m.a AS a, m.b AS b
SET r.available_capacity_tbps = r.available_capacity_tbps - $allocated_tbps
RETURN r.span_id AS updated_span, a AS from_code, b AS to_code, r.cable_id AS cable, r.medium AS medium,
       r.length_km AS length_km, r.latency_ms AS latency_ms, r.available_capacity_tbps AS remaining_headroom
"""

DEMOTE_BACKGROUND = """
MATCH (t:LogicalTunnel {traffic_class: 'BULK_BACKUP'})-[:CARRIED_BY]->(cs:CableSystem)
WHERE cs.id IN $cables AND t.state <> 'DEMOTED_SCAVENGER' AND NOT t.id IN $exclude
SET t.state = 'DEMOTED_SCAVENGER'
RETURN DISTINCT t.id AS demoted_tunnel
"""

REBIND_TUNNELS = """
MATCH (t:LogicalTunnel) WHERE t.id IN $tunnel_ids
OPTIONAL MATCH (t)-[c:CARRIED_BY]->(:CableSystem)
DELETE c
WITH DISTINCT t
SET t.state = 'REROUTED', t.route_codes = $route_codes, t.route_span_ids = $route_span_ids,
    t.current_latency_ms = $latency_ms
WITH t
UNWIND $cables AS cid
MATCH (cs:CableSystem {id: cid})
CREATE (t)-[:CARRIED_BY]->(cs)
RETURN t.id AS tunnel_id, collect(cs.id) AS carried_by
"""

GRID_ANCHOR_THZ = 193.1


def _roadm_payload(rows: list[dict], slice_tbps: float, traffic_class: str) -> dict[str, Any]:
    """Simulated flex-grid configuration: one media channel per hop, carriers sized by reach."""
    nodes = []
    for i, r in enumerate(rows):
        long_haul = r["length_km"] > 3000
        carrier_gbps = 400 if long_haul else 800
        modulation = "PCS-16QAM" if long_haul else "PCS-64QAM"
        slot_ghz = 87.5 if long_haul else 112.5
        carriers = max(1, int(-(-slice_tbps * 1000 // carrier_gbps)))
        width_ghz = carriers * slot_ghz
        centre_thz = round(GRID_ANCHOR_THZ + (i % 8) * 0.0125 * 16, 5)
        nodes.append({
            "roadm": f"ROADM-{r['from_code']}",
            "degree_out": f"{r['cable']}/{r['to_code']}",
            "action": "ADD" if i == 0 else "EXPRESS",
            "span_id": r["updated_span"],
            "media_channel": {
                "centre_frequency_thz": centre_thz,
                "slot_width_ghz": width_ghz,
                "n": round((centre_thz - GRID_ANCHOR_THZ) / 0.00625),
                "m": int(width_ghz / 12.5),
            },
            "carriers": carriers,
            "carrier_rate_gbps": carrier_gbps,
            "modulation": modulation,
            "launch_power_dbm_per_carrier": 1.0 if long_haul else 0.0,
        })
    if rows:
        nodes.append({"roadm": f"ROADM-{rows[-1]['to_code']}", "action": "DROP", "span_id": rows[-1]["updated_span"]})
    return {
        "simulated": True,
        "grid": "ITU-T G.694.1 flexgrid",
        "traffic_class": traffic_class,
        "priority_queue": "EF" if traffic_class == "GRADIENT_SYNC" else "AF31",
        "slice_tbps": slice_tbps,
        "nodes": nodes,
    }


def execute_spectrum_reallocation(
    hop_sequence: list[dict],
    spectrum_slice_tbps: float,
    traffic_class: str,
    tunnel_ids: list[str] | None = None,
) -> dict[str, Any]:
    hops = [{"from": h["from"], "to": h["to"], "cable": h["cable"]} for h in hop_sequence]
    t0 = time.perf_counter()
    reserve = run(RESERVE, {"allocated_hops": hops, "allocated_tbps": spectrum_slice_tbps})
    traces = [reserve.trace("reserve_spectrum")]

    if len(reserve.rows) != len(hops):
        from mcp_server.tools import summarize_timing

        return {
            "status": "REJECTED_INSUFFICIENT_HEADROOM",
            "execution_time_ms": round((time.perf_counter() - t0) * 1000, 4),
            "updated_spans": [],
            "cypher_trace": traces,
            **summarize_timing(traces),
        }

    by_span = {r["updated_span"]: r for r in reserve.rows}
    ordered = []
    for h in hops:
        match = next(r for r in by_span.values()
                     if r["from_code"] == h["from"] and r["to_code"] == h["to"] and r["cable"] == h["cable"])
        ordered.append(match)

    cables = list(dict.fromkeys(r["cable"] for r in ordered if r["medium"] == "SUBSEA"))
    # Never demote the tunnels this reallocation exists to move.
    demote = run(DEMOTE_BACKGROUND, {"cables": cables, "exclude": tunnel_ids or []})
    traces.append(demote.trace("demote_background_traffic"))

    latency = round(sum(r["latency_ms"] for r in ordered), 4)
    rebound = []
    if tunnel_ids:
        rebind = run(REBIND_TUNNELS, {
            "tunnel_ids": tunnel_ids,
            "route_codes": [hops[0]["from"]] + [h["to"] for h in hops],
            "route_span_ids": [r["updated_span"] for r in ordered],
            "latency_ms": latency,
            "cables": cables,
        })
        traces.append(rebind.trace("rebind_logical_tunnels"))
        rebound = rebind.rows

    execution_ms = (time.perf_counter() - t0) * 1000
    from mcp_server.tools import summarize_timing

    timing = summarize_timing(traces)
    return {
        "status": "APPLIED_SUB_2MS" if timing["engine_ms"] < 2.0 else "APPLIED",
        "execution_time_ms": round(execution_ms, 4),
        "route_latency_ms": latency,
        "updated_spans": [{"span_id": r["updated_span"], "remaining_headroom_tbps": round(r["remaining_headroom"], 3)}
                          for r in ordered],
        "demoted_tunnels": [r["demoted_tunnel"] for r in demote.rows],
        "rebound_tunnels": rebound,
        "roadm_configuration_payload": _roadm_payload(ordered, spectrum_slice_tbps, traffic_class),
        "cypher_trace": traces,
        **timing,
    }

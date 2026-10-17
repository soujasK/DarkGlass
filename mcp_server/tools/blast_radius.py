"""Tool 1: trace_severed_blast_radius - Layer 1 cable fault -> Layer 7 jobs and rings."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.database import run

# Spans of a cut cable are retyped to :SEVERED_SPAN, so both types are matched here.
# Each type is matched separately so both use their relationship index on cable_id.
DIRECT_IMPACT = """
MATCH (cs:CableSystem {id: $cable_id})
OPTIONAL MATCH ()-[live:OPTICAL_SPAN {cable_id: $cable_id}]->()
WITH cs, collect(live.srlg_id) AS live_srlgs, collect(DISTINCT live.status) AS live_states
OPTIONAL MATCH ()-[cut:SEVERED_SPAN {cable_id: $cable_id}]->()
WITH cs, live_srlgs, live_states, collect(cut.srlg_id) AS cut_srlgs, collect(DISTINCT cut.status) AS cut_states
WITH cs, live_srlgs + cut_srlgs AS srlgs, live_states + cut_states AS span_states
OPTIONAL MATCH (job:TrainingJob)-[:COORDINATES]->(ring:NCCL_Ring)-[:USES_TUNNEL]->(tun:LogicalTunnel)-[:CARRIED_BY]->(cs)
RETURN cs.name AS cable_name,
       srlgs,
       span_states,
       job.id AS affected_job_id,
       job.name AS model_name,
       job.gpu_count AS total_gpus,
       job.max_jitter_ms AS jitter_budget,
       job.priority AS priority,
       ring.id AS broken_ring_id,
       tun.id AS tunnel_id,
       tun.traffic_class AS traffic_class,
       tun.src_dc AS dc_origin,
       tun.dst_dc AS dc_target,
       tun.src_gateway AS src_gateway,
       tun.dst_gateway AS dst_gateway,
       tun.required_bandwidth_gbps AS bandwidth_needed,
       tun.baseline_latency_ms AS baseline_latency_ms
"""

SRLG_EXPOSURE = """
OPTIONAL MATCH ()-[live:OPTICAL_SPAN]->()
WHERE live.srlg_id IN $srlgs AND live.medium = 'SUBSEA' AND live.cable_id <> $cable_id
WITH collect(DISTINCT live.cable_id) AS live_cables
OPTIONAL MATCH ()-[cut:SEVERED_SPAN]->()
WHERE cut.srlg_id IN $srlgs AND cut.medium = 'SUBSEA' AND cut.cable_id <> $cable_id
WITH live_cables, collect(DISTINCT cut.cable_id) AS cut_cables
WITH live_cables + cut_cables AS exposed
MATCH (cs:CableSystem) WHERE cs.id IN exposed
MATCH (job:TrainingJob)-[:COORDINATES]->(ring:NCCL_Ring)-[:USES_TUNNEL]->(tun:LogicalTunnel)-[:CARRIED_BY]->(cs)
RETURN DISTINCT cs.id AS exposed_cable, cs.name AS exposed_cable_name, job.id AS job_id,
       ring.id AS ring_id, tun.id AS tunnel_id
"""


# The full cross-layer chain as one pure traversal: landing station -> cable -> tunnel -> ring -> job.
PATH = """
MATCH p = (s:LandingStation)-[:LANDS]->(c:CableSystem {id: $cable_id})<-[:CARRIED_BY]-(t:LogicalTunnel)
          <-[:USES_TUNNEL]-(r:NCCL_Ring)<-[:COORDINATES]-(j:TrainingJob)
WITH p, j ORDER BY j.priority LIMIT 1
RETURN length(p) AS hops,
       [n IN nodes(p) | coalesce(n.code, n.name, n.id)] AS nodes,
       [x IN relationships(p) | type(x)] AS rels
"""

HISTORY = """
MATCH (i:Incident)-[:SEVERED]->(:CableSystem {id: $cable_id})
OPTIONAL MATCH (i)-[:REROUTED_VIA]->(v:CableSystem)
WITH i, collect(v.name) AS via
ORDER BY i.resolved_at DESC
RETURN count(i) AS failures,
       collect({resolved_at: i.resolved_at, via: via, agent_ms: i.agent_ms, agent_mode: i.agent_mode})[0] AS last
"""


def trace_severed_blast_radius(cable_id: str) -> dict[str, Any]:
    direct = run(DIRECT_IMPACT, {"cable_id": cable_id}, read_only=True)
    if not direct.rows:
        return {"error": f"unknown cable_id {cable_id!r}", "cypher_trace": [direct.trace("direct_impact")]}

    first = direct.rows[0]
    srlg_counts = Counter(first["srlgs"])
    srlgs = sorted(srlg_counts)
    primary_srlg = srlg_counts.most_common(1)[0][0] if srlg_counts else None

    jobs: dict[str, dict] = {}
    tunnels: dict[str, dict] = {}
    rings: list[str] = []
    for r in direct.rows:
        if r["affected_job_id"] is None:
            continue
        jobs.setdefault(r["affected_job_id"], {
            "job_id": r["affected_job_id"],
            "model_name": r["model_name"],
            "gpu_count": r["total_gpus"],
            "max_jitter_ms": r["jitter_budget"],
            "priority": r["priority"],
        })
        tunnels.setdefault(r["tunnel_id"], {
            "tunnel_id": r["tunnel_id"],
            "traffic_class": r["traffic_class"],
            "required_bandwidth_gbps": r["bandwidth_needed"],
            "src_gateway": r["src_gateway"],
            "dst_gateway": r["dst_gateway"],
            "baseline_latency_ms": r["baseline_latency_ms"],
            "origin_dc": r["dc_origin"],
            "target_dc": r["dc_target"],
            "jobs": [],
            "rings": [],
        })
        if r["affected_job_id"] not in tunnels[r["tunnel_id"]]["jobs"]:
            tunnels[r["tunnel_id"]]["jobs"].append(r["affected_job_id"])
        tunnels[r["tunnel_id"]]["rings"].append(r["broken_ring_id"])
        if r["broken_ring_id"] not in rings:
            rings.append(r["broken_ring_id"])

    traces = [direct.trace("direct_impact")]
    exposure_rows: list[dict] = []
    if srlgs:
        exposure = run(SRLG_EXPOSURE, {"srlgs": srlgs, "cable_id": cable_id}, read_only=True)
        traces.append(exposure.trace("srlg_exposure"))
        exposure_rows = exposure.rows

    walk = run(PATH, {"cable_id": cable_id}, read_only=True)
    traces.append(walk.trace("multi_hop_path"))
    traversal = None
    if walk.rows:
        w = walk.rows[0]
        traversal = {"hops": w["hops"], "nodes": w["nodes"], "rels": w["rels"],
                     "engine_ms": round(walk.engine_ms, 3)}

    hist = run(HISTORY, {"cable_id": cable_id}, read_only=True)
    traces.append(hist.trace("incident_memory"))
    history = hist.rows[0] if hist.rows else {"failures": 0, "last": None}

    any_tunnel = next(iter(tunnels.values()), None)
    from mcp_server.tools import summarize_timing

    return {
        "cable_id": cable_id,
        "cable_name": first["cable_name"],
        "span_states": first["span_states"],
        "affected_jobs": sorted(jobs.values(), key=lambda j: j["priority"]),
        "affected_rings": rings,
        "affected_tunnels": sorted(tunnels.values(), key=lambda t: t["tunnel_id"]),
        "origin_dc": any_tunnel["origin_dc"] if any_tunnel else None,
        "target_dc": any_tunnel["target_dc"] if any_tunnel else None,
        "srlg_violation_id": primary_srlg,
        "srlg_ids": srlgs,
        "srlg_exposure": {
            "cables": sorted({r["exposed_cable"] for r in exposure_rows}),
            "cable_names": sorted({r["exposed_cable_name"] for r in exposure_rows}),
            "rings": sorted({r["ring_id"] for r in exposure_rows}),
            "tunnels": sorted({r["tunnel_id"] for r in exposure_rows}),
        },
        "traversal": traversal,
        "history": {"failures": history["failures"], "last": history["last"] if history["failures"] else None},
        "cypher_trace": traces,
        **summarize_timing(traces),
    }

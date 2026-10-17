"""Seed the Layer 7 workload graph: training jobs, NCCL rings and logical tunnels.

Workload parameters are MLSys-modelled (hyperscaler cluster telemetry is not public).
Tunnel routes are NOT hand-drawn: each tunnel is pinned to the transatlantic cable
its operator would lease or own, and FalkorDB computes the terrestrial/subsea legs
on either side with algo.SPpaths over the physical graph:

    route = SP(src_gateway -> x) + span(x -> y on pinned cable) + SP(y -> dst_gateway)

choosing the pinned span (x -> y) that minimises total propagation latency.

Usage (from backend/, after import_telegeography):
    python -m app.ingestion.seed_l7_clusters
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.core.database import run

JOBS = [
    {"id": "job_llama4_400b", "name": "Llama-4 400B MoE pretrain", "model_params": "400B_MoE",
     "gpu_count": 65536, "sync_interval_ms": 45.0, "max_jitter_ms": 12.0, "priority": 1},
    {"id": "job_rlhf_70b", "name": "RLHF policy 70B", "model_params": "70B_DENSE",
     "gpu_count": 8192, "sync_interval_ms": 120.0, "max_jitter_ms": 30.0, "priority": 3},
    {"id": "job_embed_refresh", "name": "Embedding index refresh", "model_params": "8B_ENCODER",
     "gpu_count": 2048, "sync_interval_ms": 900.0, "max_jitter_ms": 250.0, "priority": 5},
]

TUNNELS = [
    {"id": "tun_iad_dub_pri", "traffic_class": "GRADIENT_SYNC", "required_bandwidth_gbps": 3200.0,
     "pinned_cable": "dunant"},
    {"id": "tun_iad_dub_sec", "traffic_class": "GRADIENT_SYNC", "required_bandwidth_gbps": 3200.0,
     "pinned_cable": "marea"},
    {"id": "tun_iad_dub_ckpt", "traffic_class": "CHECKPOINT_BURST", "required_bandwidth_gbps": 1600.0,
     "pinned_cable": "amitie"},
    {"id": "tun_iad_dub_bulk", "traffic_class": "BULK_BACKUP", "required_bandwidth_gbps": 800.0,
     "pinned_cable": "havfrueaec-2"},
]

# ring id, owning job, tunnel, ring index
RINGS = (
    [(f"ring_iad_dub_{i:02d}", "job_llama4_400b", "tun_iad_dub_pri", i) for i in range(1, 5)]
    + [(f"ring_iad_dub_{i:02d}", "job_llama4_400b", "tun_iad_dub_sec", i) for i in range(5, 9)]
    + [("ring_rlhf_ckpt_01", "job_rlhf_70b", "tun_iad_dub_ckpt", 1)]
    + [("ring_embed_bulk_01", "job_embed_refresh", "tun_iad_dub_bulk", 1)]
)

SRC_GATEWAY, DST_GATEWAY = "IAD", "DUB"
SRC_DC, DST_DC = "US-East-Ashburn", "EU-West-Dublin"

SHORTEST = """
MATCH (src:LandingStation {code: $src}), (dst:LandingStation {code: $dst})
CALL algo.SPpaths({sourceNode: src, targetNode: dst, relTypes: ['OPTICAL_SPAN'],
                   weightProp: 'route_weight', pathCount: 1})
YIELD path, pathWeight
RETURN [n IN nodes(path) | n.code] AS hops,
       [r IN relationships(path) | r.span_id] AS spans,
       [r IN relationships(path) | r.cable_id] AS cables,
       pathWeight AS latency_ms
"""


def _sp(src: str, dst: str) -> dict[str, Any] | None:
    if src == dst:
        return {"hops": [src], "spans": [], "cables": [], "latency_ms": 0.0}
    rows = run(SHORTEST, {"src": src, "dst": dst}, read_only=True).rows
    return rows[0] if rows else None


def pinned_route(cable_id: str) -> dict[str, Any]:
    spans = run("""
        MATCH (a:LandingStation)-[r:OPTICAL_SPAN {cable_id: $cable}]->(b:LandingStation)
        RETURN a.code AS a, b.code AS b, r.span_id AS span_id, r.latency_ms AS latency_ms
    """, {"cable": cable_id}, read_only=True).rows
    best = None
    for sp in spans:
        head, tail = _sp(SRC_GATEWAY, sp["a"]), _sp(sp["b"], DST_GATEWAY)
        if head is None or tail is None:
            continue
        # Reject routes that cross the pinned cable twice.
        if cable_id in head["cables"] or cable_id in tail["cables"]:
            continue
        total = head["latency_ms"] + sp["latency_ms"] + tail["latency_ms"]
        if best is None or total < best["latency_ms"]:
            best = {
                "hops": head["hops"] + tail["hops"],
                "spans": head["spans"] + [sp["span_id"]] + tail["spans"],
                "cables": head["cables"] + [cable_id] + tail["cables"],
                "latency_ms": round(total, 4),
            }
    if best is None:
        raise RuntimeError(f"no IAD->DUB route can be pinned to cable {cable_id!r}")
    return best


RESTORE_TUNNELS = """
MATCH (t:LogicalTunnel)
OPTIONAL MATCH (t)-[c:CARRIED_BY]->(:CableSystem)
DELETE c
WITH DISTINCT t
SET t.state = 'ACTIVE', t.route_codes = t.baseline_route_codes, t.route_span_ids = t.baseline_route_span_ids,
    t.current_latency_ms = t.baseline_latency_ms
WITH t
UNWIND t.baseline_carried_by AS cid
MATCH (cs:CableSystem {id: cid})
CREATE (t)-[:CARRIED_BY]->(cs)
RETURN count(*) AS links
"""


def restore_tunnels() -> int:
    """Put every tunnel back on its baseline route in place (jobs, rings and the GPU fabric are untouched)."""
    return run(RESTORE_TUNNELS).rows[0]["links"]


def seed() -> dict[str, Any]:
    run("MATCH (n) WHERE n:TrainingJob OR n:NCCL_Ring OR n:LogicalTunnel DETACH DELETE n")

    run("UNWIND $rows AS r CREATE (j:TrainingJob) SET j = r", {"rows": JOBS})

    summary = {}
    for tun in TUNNELS:
        route = pinned_route(tun["pinned_cable"])
        subsea = sorted({c for c in route["cables"] if c != "backhaul"})
        props = {
            **tun,
            "src_dc": SRC_DC,
            "dst_dc": DST_DC,
            "src_gateway": SRC_GATEWAY,
            "dst_gateway": DST_GATEWAY,
            "route_codes": route["hops"],
            "route_span_ids": route["spans"],
            "baseline_route_codes": route["hops"],
            "baseline_route_span_ids": route["spans"],
            "baseline_carried_by": subsea,
            "baseline_latency_ms": route["latency_ms"],
            "current_latency_ms": route["latency_ms"],
            "state": "ACTIVE",
        }
        run("""
            CREATE (t:LogicalTunnel) SET t = $props
            WITH t
            UNWIND $cables AS cid
            MATCH (cs:CableSystem {id: cid})
            CREATE (t)-[:CARRIED_BY]->(cs)
        """, {"props": props, "cables": subsea})
        summary[tun["id"]] = {"latency_ms": route["latency_ms"], "hops": len(route["hops"]) - 1,
                              "carried_by": subsea}

    run("""
        UNWIND $rows AS r
        MATCH (j:TrainingJob {id: r.job}), (t:LogicalTunnel {id: r.tunnel})
        CREATE (ring:NCCL_Ring {id: r.id, ring_index: r.idx, bucket_size_mb: 128, timeout_threshold_ms: 60.0})
        CREATE (j)-[:COORDINATES]->(ring)
        CREATE (ring)-[:USES_TUNNEL]->(t)
    """, {"rows": [{"id": rid, "job": job, "tunnel": tun, "idx": idx} for rid, job, tun, idx in RINGS]})
    return summary


def main() -> int:
    summary = seed()
    print("[seed] Layer 7 workloads seeded")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

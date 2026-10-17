"""The AI supercluster's compute fabric, modelled to the individual GPU.

Two campuses, Ashburn (IAD) and Dublin (DUB), each a three-tier Clos network:

    (:BorderRouter)-[:FEEDS]->(:SuperSpine)-[:FEEDS]->(:Spine)-[:FEEDS]->(:Leaf)-[:FEEDS]->(:Server)
    (:Server)-[:HAS_NIC]->(:NIC)-[:ATTACHED_TO]->(:GPU)-[:MEMBER_OF]->(:NCCL_Ring)
    (:Server)-[:IN_RACK]->(:Rack)-[:IN_ROW]->(:Row)-[:IN_HALL]->(:DataHall)-[:IN_CAMPUS]->(:LandingStation:DataCenter)
    (:LogicalTunnel)-[:TERMINATES_AT]->(:BorderRouter)

The FEEDS edges model each device's primary uplink as a tree, so every GPU is reachable from
its campus border routers by exactly one path. That keeps deep traversals exact and fast.
Real Clos fabrics are multi-pathed; the tree is the forwarding view a blast radius needs.

With the jobs seeded in seed_l7_clusters this produces about 75,776 GPUs, 75,776 NICs,
9,472 servers, 2,368 racks and a few hundred switches, or roughly 165k nodes and 250k+ edges.
The cut-cable-to-GPU blast radius then becomes an 11-hop traversal.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.database import run

GPUS_PER_SERVER = 8
SERVERS_PER_LEAF = 32
SERVERS_PER_RACK = 4
RACKS_PER_ROW = 32
HALLS_PER_CAMPUS = 4
SPINES_PER_CAMPUS = 32
SUPERSPINES_PER_CAMPUS = 8
ROUTERS_PER_CAMPUS = 2
CAMPUSES = ["IAD", "DUB"]
BATCH = 5000

INDEXES = [f"CREATE INDEX FOR (n:{label}) ON (n.id)" for label in
           ("BorderRouter", "SuperSpine", "Spine", "Leaf", "Server", "NIC", "GPU", "Rack", "Row", "DataHall")]


def _batches(rows: list[dict], n: int = BATCH):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _create_nodes(label: str, rows: list[dict]) -> None:
    for b in _batches(rows):
        run(f"UNWIND $rows AS r CREATE (n:{label}) SET n = r", {"rows": b})


def _link(src_label: str, rel: str, dst_label: str, pairs: list[dict]) -> None:
    for b in _batches(pairs):
        run(f"""
            UNWIND $rows AS r
            MATCH (a:{src_label} {{id: r.a}}), (b:{dst_label} {{id: r.b}})
            CREATE (a)-[:{rel}]->(b)
        """, {"rows": b})


def _gpu_plan() -> list[tuple[str, str, int]]:
    """(campus, ring_id, gpu_count) per ring: every job's GPUs split evenly across both campuses."""
    rings = run("""
        MATCH (j:TrainingJob)-[:COORDINATES]->(r:NCCL_Ring)
        WITH j, collect(r.id) AS rings
        RETURN j.gpu_count AS gpus, rings ORDER BY j.priority
    """, read_only=True).rows
    plan = []
    for job in rings:
        per_ring = job["gpus"] // len(job["rings"])
        for rid in sorted(job["rings"]):
            for campus in CAMPUSES:
                plan.append((campus, rid, per_ring // len(CAMPUSES)))
    return plan


def build_fabric() -> dict[str, Any]:
    t0 = time.perf_counter()
    run("""MATCH (n) WHERE n:BorderRouter OR n:SuperSpine OR n:Spine OR n:Leaf OR n:Server OR n:NIC OR n:GPU
           OR n:Rack OR n:Row OR n:DataHall DETACH DELETE n""")
    for q in INDEXES:
        try:
            run(q)
        except Exception:
            pass  # index already exists

    plan = _gpu_plan()
    counts: dict[str, int] = {}
    for campus in CAMPUSES:
        routers = [{"id": f"{campus}-DCI-{i + 1}", "campus": campus} for i in range(ROUTERS_PER_CAMPUS)]
        supers = [{"id": f"{campus}-SSP-{i + 1:02d}", "campus": campus} for i in range(SUPERSPINES_PER_CAMPUS)]
        spines = [{"id": f"{campus}-SPN-{i + 1:02d}", "campus": campus} for i in range(SPINES_PER_CAMPUS)]
        servers, gpus, nics, gpu_ring = [], [], [], []
        for _, rid, n in (p for p in plan if p[0] == campus):
            for _ in range(n // GPUS_PER_SERVER):
                sid = f"{campus}-SRV-{len(servers) + 1:05d}"
                servers.append({"id": sid, "campus": campus, "model": "H100 SXM5 x8"})
                for g in range(GPUS_PER_SERVER):
                    gid = f"{sid}-G{g}"
                    gpus.append({"id": gid, "campus": campus, "model": "H100", "slot": g})
                    nics.append({"id": f"{sid}-N{g}", "campus": campus, "model": "ConnectX-7 400G"})
                    gpu_ring.append({"a": gid, "b": rid})
        leaves = [{"id": f"{campus}-LEF-{i + 1:03d}", "campus": campus}
                  for i in range(-(-len(servers) // SERVERS_PER_LEAF))]
        racks = [{"id": f"{campus}-RCK-{i + 1:04d}", "campus": campus}
                 for i in range(-(-len(servers) // SERVERS_PER_RACK))]
        rows = [{"id": f"{campus}-ROW-{i + 1:02d}", "campus": campus} for i in range(-(-len(racks) // RACKS_PER_ROW))]
        halls = [{"id": f"{campus}-HALL-{i + 1}", "campus": campus} for i in range(HALLS_PER_CAMPUS)]

        for label, items in (("BorderRouter", routers), ("SuperSpine", supers), ("Spine", spines), ("Leaf", leaves),
                             ("Server", servers), ("NIC", nics), ("GPU", gpus), ("Rack", racks), ("Row", rows),
                             ("DataHall", halls)):
            _create_nodes(label, items)
            counts[label] = counts.get(label, 0) + len(items)

        # Primary-uplink tree: router -> super-spine -> spine -> leaf -> server
        _link("BorderRouter", "FEEDS", "SuperSpine",
              [{"a": routers[i % len(routers)]["id"], "b": x["id"]} for i, x in enumerate(supers)])
        _link("SuperSpine", "FEEDS", "Spine",
              [{"a": supers[i % len(supers)]["id"], "b": x["id"]} for i, x in enumerate(spines)])
        _link("Spine", "FEEDS", "Leaf",
              [{"a": spines[i % len(spines)]["id"], "b": x["id"]} for i, x in enumerate(leaves)])
        _link("Leaf", "FEEDS", "Server",
              [{"a": leaves[i // SERVERS_PER_LEAF]["id"], "b": x["id"]} for i, x in enumerate(servers)])
        # Server -> NIC -> GPU (rail-optimised: one 400G NIC per GPU), GPU -> sync ring
        _link("Server", "HAS_NIC", "NIC", [{"a": n["id"].rsplit("-N", 1)[0], "b": n["id"]} for n in nics])
        _link("NIC", "ATTACHED_TO", "GPU", [{"a": n["id"], "b": n["id"].rsplit("-N", 1)[0] + "-G" + n["id"].rsplit("-N", 1)[1]}
                                            for n in nics])
        _link("GPU", "MEMBER_OF", "NCCL_Ring", gpu_ring)
        # Location: server -> rack -> row -> hall -> campus gateway
        _link("Server", "IN_RACK", "Rack",
              [{"a": x["id"], "b": racks[i // SERVERS_PER_RACK]["id"]} for i, x in enumerate(servers)])
        _link("Rack", "IN_ROW", "Row", [{"a": x["id"], "b": rows[i // RACKS_PER_ROW]["id"]} for i, x in enumerate(racks)])
        _link("Row", "IN_HALL", "DataHall",
              [{"a": x["id"], "b": halls[i % len(halls)]["id"]} for i, x in enumerate(rows)])
        run("""
            MATCH (h:DataHall {campus: $campus}), (dc:LandingStation {code: $campus})
            CREATE (h)-[:IN_CAMPUS]->(dc)
        """, {"campus": campus})

    link_tunnels()
    nodes = sum(counts.values())
    edges = run("""MATCH ()-[r]->() WHERE type(r) IN ['FEEDS','HAS_NIC','ATTACHED_TO','MEMBER_OF','IN_RACK','IN_ROW',
                   'IN_HALL','IN_CAMPUS','TERMINATES_AT'] RETURN count(r) AS n""", read_only=True).rows[0]["n"]
    return {"nodes": nodes, "edges": edges, "by_label": counts, "seconds": round(time.perf_counter() - t0, 1)}


def link_tunnels() -> None:
    """Every tunnel terminates on the border routers of both campuses."""
    run("MATCH (:LogicalTunnel)-[r:TERMINATES_AT]->() DELETE r")
    run("""
        MATCH (t:LogicalTunnel), (r:BorderRouter)
        CREATE (t)-[:TERMINATES_AT]->(r)
    """)


def relink_rings() -> None:
    """Re-attach GPUs to their rings after a re-seed recreated the ring nodes (ids are stable)."""
    plan = _gpu_plan()
    pairs: list[dict] = []
    for campus in CAMPUSES:
        servers = run("MATCH (s:Server {campus: $c}) RETURN s.id AS id ORDER BY id", {"c": campus},
                      read_only=True).rows
        i = 0
        for _, rid, n in (p for p in plan if p[0] == campus):
            for _ in range(n // GPUS_PER_SERVER):
                sid = servers[i]["id"]
                i += 1
                pairs += [{"a": f"{sid}-G{g}", "b": rid} for g in range(GPUS_PER_SERVER)]
    _link("GPU", "MEMBER_OF", "NCCL_Ring", pairs)
    link_tunnels()


def ensure_fabric() -> dict[str, Any] | None:
    """Build the fabric if missing; repair ring and tunnel links if the workloads were re-seeded."""
    gpus = run("MATCH (g:GPU) RETURN count(g) AS n", read_only=True).rows[0]["n"]
    if gpus == 0:
        return build_fabric()
    members = run("MATCH ()-[m:MEMBER_OF]->() RETURN count(m) AS n", read_only=True).rows[0]["n"]
    if members < gpus:
        relink_rings()
        return {"relinked": True}
    terminates = run("MATCH ()-[t:TERMINATES_AT]->() RETURN count(t) AS n", read_only=True).rows[0]["n"]
    if terminates == 0:
        link_tunnels()
        return {"tunnels_linked": True}
    return None


if __name__ == "__main__":
    import json

    print(json.dumps(build_fabric(), indent=2))

"""Startup self-check: make the graph demo-ready no matter how the last session ended.

* waits for FalkorDB to accept connections
* loads the physical plant from data/graph/topology.json if the graph is empty
  (for example after the FalkorDB container was recreated)
* seeds the Layer 7 workloads if they are missing
* restores any incident left half-finished by a previous backend process
  (severed spans, rerouted tunnels, reserved capacity), because incident state
  lives in the backend's memory and does not survive a restart
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import get_settings
from app.core.database import ping, run

log = logging.getLogger("darkglass.bootstrap")


def _wait_for_falkordb(timeout_s: float = 60.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ping():
            return True
        time.sleep(1.0)
    return False


def ensure_ready() -> dict[str, Any]:
    """Never raises: a failed self-check is logged, and the API still starts so /api/health can report it."""
    try:
        return _ensure_ready()
    except Exception:
        log.exception("startup self-check failed")
        return {"falkordb": ping(), "error": True}


def _ensure_ready() -> dict[str, Any]:
    from app.core.database import get_db

    report: dict[str, Any] = {"falkordb": False, "loaded": False, "seeded": False, "recovered": False}
    if not _wait_for_falkordb():
        log.error("FalkorDB is not reachable on %s:%s", get_settings().falkordb_host, get_settings().falkordb_port)
        return report
    report["falkordb"] = True

    # A graph key that does not exist rejects even read-only queries, so check the graph list first.
    exists = get_settings().graph_name in (get_db().list_graphs() or [])
    stations = (run("MATCH (s:LandingStation) RETURN count(s) AS n", read_only=True).rows[0]["n"]
                if exists else 0)
    if stations == 0:
        from app.ingestion.import_telegeography import load_into_falkordb, load_topology_file

        path = get_settings().graph_dir / "topology.json"
        if not path.exists():
            log.error("graph is empty and %s is missing; run app.ingestion.import_telegeography", path)
            return report
        load_into_falkordb(load_topology_file())
        report["loaded"] = True

    try:  # incident memory index (graphs built before incident memory existed lack it)
        run("CREATE INDEX FOR (n:Incident) ON (n.id)")
    except Exception:
        pass  # already exists

    jobs = run("MATCH (j:TrainingJob) RETURN count(j) AS n", read_only=True).rows[0]["n"]
    if jobs == 0:
        from app.ingestion.seed_l7_clusters import seed

        seed()
        report["seeded"] = True

    dirty = run("""
        OPTIONAL MATCH ()-[cut:SEVERED_SPAN]->() WITH count(cut) AS cut
        OPTIONAL MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.status <> 'ACTIVE' OR r.available_capacity_tbps <> r.baseline_available_tbps
        WITH cut, count(r) AS spans
        OPTIONAL MATCH (t:LogicalTunnel) WHERE t.state <> 'ACTIVE'
        RETURN cut, spans, count(t) AS tunnels
    """, read_only=True).rows[0]
    if dirty["cut"] or dirty["spans"] or dirty["tunnels"]:
        from app.services.chaos_engine import chaos

        chaos.reset()
        report["recovered"] = True
        report["recovered_from"] = dirty
    log.info("startup self-check: %s", report)
    return report

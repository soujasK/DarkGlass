"""End-to-end tests through the real API against a live FalkorDB (docker compose up -d).

They mutate the demo graph and always reset it afterwards. Run from backend/:
    pytest -q                      everything except the slow planet scan
    pytest -q -m slow              the 603-cable planet scan as well
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import ping, run
from app.main import app

pytestmark = pytest.mark.skipif(not ping(), reason="FalkorDB is not running")

FEATURED = [("dunant", True), ("marea", True), ("amitie", False), ("havfrueaec-2", False)]


@pytest.fixture(scope="module")
def client():
    import time

    started = time.time()
    with TestClient(app) as c:  # runs the startup self-check
        c.post("/api/reset")
        yield c
        c.post("/api/reset")
    # Tests must not pollute the demo's incident memory.
    run("MATCH (i:Incident) WHERE i.started_at >= $t DETACH DELETE i", {"t": started})


@pytest.fixture(autouse=True)
def clean(client):
    client.post("/api/reset")
    yield
    client.post("/api/reset")


def test_health_reports_full_graph(client):
    h = client.get("/api/health").json()
    assert h["falkordb"] == "up"
    assert h["stations"] == 1790 and h["active_spans"] == 8900 and h["jobs"] == 3


def test_fix_without_incident_is_rejected(client):
    assert client.post("/api/agent/run", json={"mode": "playbook"}).status_code == 409


@pytest.mark.parametrize("cable,stalls", FEATURED)
def test_incident_lifecycle(client, cable, stalls):
    snap = client.post("/api/chaos/anchor-drag", json={"cable_id": cable}).json()
    inc = snap["incident"]
    assert inc["cable_id"] == cable and snap["stall_active"] is stalls and inc["fault_coordinate"]
    assert client.post("/api/chaos/anchor-drag", json={"cable_id": cable}).status_code == 409

    res = client.post("/api/agent/run", json={"mode": "playbook"}).json()
    agent = res["incident"]["agent"]
    assert agent["restored"] is True and res["incident"]["resolved_at"] and not res["stall_active"]
    compromised = inc["severed_spans"][0]["srlg_id"]
    for hop in agent["detour"]["hops"]:
        assert hop["srlg_id"] != compromised, "detour must avoid the compromised risk group"
        assert hop["cable"] != cable, "detour must avoid the cut cable"
    assert agent["detour_latency_ms"] <= agent["ceiling_ms"]
    assert [s["tool"] for s in agent["steps"]] == [
        "trace_severed_blast_radius",
        "compute_optimal_optical_detour",
        "audit_secondary_choke_points",
        "execute_spectrum_reallocation",
    ]
    assert client.post("/api/agent/run", json={"mode": "playbook"}).status_code == 409
    assert any(t["state"] == "REROUTED" for t in client.get("/api/tunnels").json())


def test_cable_with_no_traffic_does_not_stall(client):
    snap = client.post("/api/chaos/anchor-drag", json={"cable_id": "2africa"}).json()
    assert snap["stall_active"] is False and snap["incident"]["degraded"] is False


def test_unknown_cable_is_rejected(client):
    assert client.post("/api/chaos/anchor-drag", json={"cable_id": "no-such-cable"}).status_code == 409


def test_reset_restores_everything(client):
    client.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"})
    client.post("/api/agent/run", json={"mode": "playbook"})
    client.post("/api/reset")
    h = client.get("/api/health").json()
    assert h["active_spans"] == 8900
    assert all(t["state"] == "ACTIVE" for t in client.get("/api/tunnels").json())
    dirty = run("MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.available_capacity_tbps <> r.baseline_available_tbps "
                "RETURN count(r) AS n", read_only=True).rows[0]["n"]
    assert dirty == 0


def test_startup_self_check_recovers_an_orphaned_cut(client):
    """Regression: a backend restart mid-incident used to leave the cable cut but the UI 'intact'."""
    from app.services.bootstrap import ensure_ready
    from app.services.chaos_engine import chaos

    client.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"})
    chaos.incident = None  # what a process restart does to in-memory state
    report = ensure_ready()
    assert report["recovered"] is True
    assert client.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"}).status_code == 200


def test_read_only_queries_cannot_write():
    """Ask-the-graph runs generated Cypher with GRAPH.RO_QUERY; FalkorDB itself must refuse writes."""
    with pytest.raises(Exception):
        run("CREATE (:ShouldNotExist {x: 1})", read_only=True)


def test_empty_question_is_rejected(client):
    assert client.post("/api/ask", json={"question": "   "}).status_code == 400


def test_engine_telemetry_counts_queries(client):
    before = client.get("/api/engine").json()["total_calls"]
    client.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"})
    after = client.get("/api/engine").json()
    assert after["total_calls"] > before and len(after["capabilities"]) == 9


@pytest.mark.slow
def test_planet_scan_covers_every_cable_and_persists():
    from app.services.resilience_scan import run_scan, summary_from_graph

    s = run_scan()
    assert s["scenarios"] == 603 and s["baseline_transatlantic_tbps"] > 0
    assert s["cables_that_island_stations"] > 0 and s["cables_that_stall_training"] > 0
    persisted = summary_from_graph()
    assert persisted and persisted["scenarios"] == 603
    leftovers = [g for g in run_scan.__globals__["get_db"]().list_graphs() if g.startswith("darkglass_whatif")]
    assert not leftovers, "sandbox graphs must be cleaned up"


def test_cable_catalog_lists_every_cable_with_metadata(client):
    cat = client.get("/api/cables/catalog").json()
    assert len(cat) == 603
    by_id = {c["id"]: c for c in cat}
    dunant = by_id["dunant"]
    assert dunant["rfs"] == 2021 and dunant["capacity_tbps"] == 250.0 and "GRADIENT_SYNC" in dunant["traffic"]
    assert set(dunant["countries"]) == {"France", "United States"}
    assert cat[0]["traffic"], "cables our workloads depend on are listed first"


def test_any_cable_can_be_cut_including_ones_without_workloads(client):
    snap = client.post("/api/chaos/anchor-drag", json={"cable_id": "kafos"}).json()
    assert snap["incident"]["cable_id"] == "kafos" and snap["stall_active"] is False


def test_health_never_errors_during_reset(client):
    import threading

    errors = []

    def hammer():
        for _ in range(15):
            if client.get("/api/health").status_code != 200:
                errors.append(1)

    t = threading.Thread(target=hammer)
    t.start()
    for _ in range(3):
        client.post("/api/reset")
    t.join()
    assert not errors


def test_incident_memory_is_written_and_recalled(client):
    before = client.post("/api/chaos/anchor-drag", json={"cable_id": "marea"}).json()
    n0 = before["incident"]["blast_radius"]["history"]["failures"]
    client.post("/api/agent/run", json={"mode": "playbook"})
    client.post("/api/reset")  # memory must survive a reset
    after = client.post("/api/chaos/anchor-drag", json={"cable_id": "marea"}).json()
    hist = after["incident"]["blast_radius"]["history"]
    assert hist["failures"] == n0 + 1
    assert hist["last"]["via"], "the cable that saved the day is remembered"

"""Hand-picked failure scenarios, end to end against a live FalkorDB.

Each scenario is chosen for a specific reason (see SCENARIOS). Every test leaves the graph as it
found it, and every mutation is followed by a graph-consistency check:
    pytest -q tests/test_scenarios.py
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.core.database import ping, run
from app.main import app

pytestmark = pytest.mark.skipif(not ping(), reason="FalkorDB is not running")

PRI, SEC, CKPT, BULK = "tun_iad_dub_pri", "tun_iad_dub_sec", "tun_iad_dub_ckpt", "tun_iad_dub_bulk"
TOOLS = ["trace_severed_blast_radius", "compute_optimal_optical_detour",
         "audit_secondary_choke_points", "execute_spectrum_reallocation"]

# cable, why it was picked, does training stall, class the agent must fix first, tunnels it must move
SCENARIOS = [
    ("dunant", "primary gradient path; shares the Virginia Beach trench with MAREA and BRUSA",
     True, "GRADIENT_SYNC", {PRI}),
    ("marea", "secondary gradient path; same trench as Dunant",
     True, "GRADIENT_SYNC", {SEC}),
    ("pan-european-crossing-uk-ireland", "carries every tunnel of every class at once",
     True, "GRADIENT_SYNC", {PRI, SEC}),
    ("channel-islands-9-liberty-submarine-cable", "both gradient tunnels on one small cable",
     True, "GRADIENT_SYNC", {PRI, SEC}),
    ("high-capacity-undersea-guernsey-optical-fibre-hugo", "both gradient tunnels, different cable",
     True, "GRADIENT_SYNC", {PRI, SEC}),
    ("sea2shore", "checkpoint traffic only: degraded, not stalled",
     False, "CHECKPOINT_BURST", {CKPT}),
    ("amitie", "checkpoint traffic on the Bude shelf risk group",
     False, "CHECKPOINT_BURST", {CKPT}),
    ("havfrueaec-2", "backup traffic only, and the usual detour for everything else",
     False, "BULK_BACKUP", {BULK}),
]
NO_TRAFFIC = ["kafos", "brusa", "grace-hopper", "2africa"]  # brusa/grace-hopper share a trench with our cables


# --------------------------------------------------------------------------- fixtures and helpers


@pytest.fixture(scope="module")
def client():
    started = time.time()
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c
        c.post("/api/reset")
    run("MATCH (i:Incident) WHERE i.started_at >= $t DETACH DELETE i", {"t": started})


@pytest.fixture(autouse=True)
def clean(client):
    client.post("/api/reset")
    yield
    client.post("/api/reset")
    assert_consistent()
    assert_baseline()


def q(cypher: str, params: dict | None = None) -> list[dict]:
    return run(cypher, params or {}, read_only=True).rows


def assert_consistent() -> None:
    """Invariants that must hold after any operation, finished or not."""
    leftovers = q("MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.status IN ['SRLG_BLOCKED', 'CAPACITY_MASKED'] "
                  "RETURN count(r) AS n")[0]["n"]
    assert leftovers == 0, "detour masks were not restored"
    weights = q("MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.status = 'ACTIVE' AND r.route_weight <> r.latency_ms "
                "RETURN count(r) AS n")[0]["n"]
    assert weights == 0, "route weights were left blocked"
    negative = q("MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.available_capacity_tbps < 0 RETURN count(r) AS n")[0]["n"]
    assert negative == 0, "capacity was over-reserved"
    orphans = q("MATCH (t:LogicalTunnel) WHERE NOT (t)-[:CARRIED_BY]->() RETURN collect(t.id) AS ids")[0]["ids"]
    assert not orphans, f"tunnels with no cable: {orphans}"


def assert_baseline() -> None:
    assert q("MATCH ()-[s:SEVERED_SPAN]->() RETURN count(s) AS n")[0]["n"] == 0
    assert q("MATCH ()-[r:OPTICAL_SPAN]->() WHERE r.available_capacity_tbps <> r.baseline_available_tbps "
             "RETURN count(r) AS n")[0]["n"] == 0
    states = q("MATCH (t:LogicalTunnel) RETURN collect(DISTINCT t.state) AS s")[0]["s"]
    assert states == ["ACTIVE"]


def trench_siblings(cable_id: str) -> set[str]:
    """Every cable that shares a non-cable-specific risk group with this one, itself included."""
    rows = q("""
        MATCH ()-[a:OPTICAL_SPAN|SEVERED_SPAN {cable_id: $c}]->()
        WITH collect(DISTINCT a.srlg_id) AS g
        MATCH ()-[b:OPTICAL_SPAN|SEVERED_SPAN]->() WHERE b.srlg_id IN g
        RETURN collect(DISTINCT b.cable_id) AS cs""", {"c": cable_id})
    return set(rows[0]["cs"])


def severed_cables() -> set[str]:
    return set(q("MATCH ()-[s:SEVERED_SPAN]->() RETURN collect(DISTINCT s.cable_id) AS c")[0]["c"])


def tunnels(client) -> dict[str, dict]:
    return {t["id"]: t for t in client.get("/api/tunnels").json()}


def cut(client, cable: str) -> dict:
    r = client.post("/api/chaos/anchor-drag", json={"cable_id": cable})
    assert r.status_code == 200, r.text
    return r.json()


def fix(client) -> dict:
    r = client.post("/api/agent/run", json={"mode": "playbook"})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- the agent, cable by cable


@pytest.mark.parametrize("cable,why,stalls,cls,moved", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_agent_restores_the_right_traffic(client, cable, why, stalls, cls, moved):
    before = tunnels(client)
    snap = cut(client, cable)
    assert snap["stall_active"] is stalls, why
    assert snap["incident"]["degraded"] is True
    assert_consistent()

    res = fix(client)
    agent = res["incident"]["agent"]
    assert agent["restored"] is True, agent.get("reason")
    assert agent["traffic_class"] == cls, "the most critical class the cut hurt is fixed first"
    assert set(agent["rerouted_tunnels"]) == moved
    assert [s["tool"] for s in agent["steps"]] == TOOLS
    assert res["stall_active"] is False and res["incident"]["resolved_at"]

    # The detour avoids the cut cable and every cable in the same trench.
    banned = trench_siblings(cable)
    used = {h["cable"] for h in agent["detour"]["hops"]}
    assert not used & banned, f"detour rides the damaged trench: {used & banned}"
    assert agent["detour_latency_ms"] <= agent["ceiling_ms"]

    # The ceiling is the moved tunnel's baseline plus the strictest affected job's jitter budget.
    blast = agent["steps"][0]["result"]
    group = [t for t in blast["affected_tunnels"] if t["tunnel_id"] in moved]
    riders = {j for t in group for j in t["jobs"]}
    strictest = min((j for j in blast["affected_jobs"] if j["job_id"] in riders),
                    key=lambda j: (j["priority"], j["max_jitter_ms"]))
    assert agent["ceiling_ms"] == pytest.approx(group[0]["baseline_latency_ms"] + strictest["max_jitter_ms"])

    # Moved tunnels now ride the detour, end to end; untouched tunnels keep their cables.
    after = tunnels(client)
    hop_codes = [agent["detour"]["hops"][0]["from"]] + [h["to"] for h in agent["detour"]["hops"]]
    for tid, t in after.items():
        if tid in moved:
            assert t["state"] == "REROUTED" and cable not in t["carried_by"]
            assert t["route_codes"] == hop_codes
            assert t["current_latency_ms"] == pytest.approx(agent["detour_latency_ms"], abs=1e-3)
        else:
            assert sorted(t["carried_by"]) == sorted(before[tid]["carried_by"])

    # Exactly the reserved bandwidth was taken from each detour span.
    tbps = agent["spectrum_tbps"]
    for h in agent["detour"]["hops"]:
        r = q("MATCH ()-[r:OPTICAL_SPAN {span_id: $s}]->() RETURN r.available_capacity_tbps AS a, "
              "r.baseline_available_tbps AS b", {"s": h["span_id"]})[0]
        assert r["b"] - r["a"] == pytest.approx(tbps, abs=1e-6)
    assert_consistent()


@pytest.mark.parametrize("cable", NO_TRAFFIC)
def test_cut_with_no_traffic_is_explained_not_fixed(client, cable):
    snap = cut(client, cable)
    assert snap["stall_active"] is False and snap["incident"]["degraded"] is False
    before = tunnels(client)
    agent = fix(client)["incident"]["agent"]
    assert agent["restored"] is False
    assert "nothing to restore" in agent["reason"]
    assert [s["tool"] for s in agent["steps"]] == ["trace_severed_blast_radius"], "no action without cause"
    assert tunnels(client) == before


def test_all_class_cut_prioritises_gradient_sync_and_demotes_backup(client):
    cut(client, "pan-european-crossing-uk-ireland")
    agent = fix(client)["incident"]["agent"]
    blast = agent["steps"][0]["result"]
    assert {t["traffic_class"] for t in blast["affected_tunnels"]} == {"GRADIENT_SYNC", "CHECKPOINT_BURST",
                                                                       "BULK_BACKUP"}
    realloc = agent["steps"][3]["result"]
    assert set(realloc["demoted_tunnels"]) <= {BULK}
    assert PRI not in realloc["demoted_tunnels"] and SEC not in realloc["demoted_tunnels"]


# --------------------------------------------------------------------------- shared trench and compound failures


def test_dunant_cut_flags_marea_as_exposed(client):
    """Multi-hop reasoning: the agent learns MAREA lies in the same trench, so its detour cannot use it."""
    br = cut(client, "dunant")["incident"]["blast_radius"]
    assert br["srlg_violation_id"] == "SRLG-USE-VAB-SHELF"
    # Exposure lists the trench cables our traffic rides; BRUSA shares the trench but carries none.
    assert br["srlg_exposure"]["cables"] == ["marea"]
    assert SEC in br["srlg_exposure"]["tunnels"]
    agent = fix(client)["incident"]["agent"]
    assert not {"marea", "brusa"} & set(agent["detour"]["cables_used"])


def test_second_cut_on_the_detour_is_healed_again(client):
    """Compound failure: after the fix, the cable the agent moved traffic onto fails too."""
    cut(client, "dunant")
    first = fix(client)["incident"]["agent"]
    detour_cable = first["detour"]["cables_used"][0]

    snap = cut(client, detour_cable)
    assert snap["stall_active"] is True, "the rerouted gradient traffic is hit again"
    br = snap["incident"]["blast_radius"]
    assert PRI in {t["tunnel_id"] for t in br["affected_tunnels"]}

    second = fix(client)["incident"]["agent"]
    assert second["restored"] is True, second.get("reason")
    used = set(second["detour"]["cables_used"])
    assert not used & severed_cables(), "the new detour must avoid both cuts"
    assert not used & trench_siblings("dunant")
    assert_consistent()


@pytest.mark.parametrize("pair", [("dunant", "marea"), ("marea", "dunant"), ("dunant", "sea2shore"),
                                  ("sea2shore", "dunant")])
def test_sequential_cuts_in_either_order(client, pair):
    for cable in pair:
        cut(client, cable)
        assert fix(client)["incident"]["agent"]["restored"] is True
        assert_consistent()
    assert severed_cables() == set(pair)


def test_cutting_a_cable_the_traffic_already_left_hurts_nothing(client):
    """After sea2shore's fix moves checkpoint traffic away, amitie (its other baseline cable) carries none."""
    cut(client, "sea2shore")
    assert fix(client)["incident"]["agent"]["restored"] is True
    snap = cut(client, "amitie")
    assert snap["incident"]["degraded"] is False and snap["incident"]["blast_radius"]["affected_tunnels"] == []
    assert "nothing to restore" in fix(client)["incident"]["agent"]["reason"]


def test_cutting_while_training_is_stalled_is_refused(client):
    cut(client, "dunant")
    r = client.post("/api/chaos/anchor-drag", json={"cable_id": "marea"})
    assert r.status_code == 409
    assert severed_cables() == {"dunant"}


def test_cutting_during_an_unresolved_degraded_incident_is_refused(client):
    """Regression: a degraded but unresolved incident used to be silently replaced by the next cut."""
    cut(client, "sea2shore")  # checkpoint traffic is down, training is not stalled
    r = client.post("/api/chaos/anchor-drag", json={"cable_id": "dunant"})
    assert r.status_code == 409
    assert client.get("/api/state").json()["incident"]["cable_id"] == "sea2shore"
    assert severed_cables() == {"sea2shore"}


def test_cut_with_no_traffic_does_not_block_the_next_cut(client):
    cut(client, "kafos")
    assert cut(client, "dunant")["incident"]["cable_id"] == "dunant"


@pytest.mark.parametrize("cable,down", [
    ("pan-european-crossing-uk-ireland", {CKPT, BULK}),
    ("dunant", set()),
    ("sea2shore", set()),
])
def test_traffic_left_on_the_cut_cable_is_reported(client, cable, down):
    """Honesty: 'restored' never hides lower-priority traffic still riding the cut cable."""
    cut(client, cable)
    res = fix(client)
    agent = res["incident"]["agent"]
    assert agent["restored"] is True
    assert {t["tunnel_id"] for t in agent["still_down"]} == down
    assert res["incident"]["degraded"] is bool(down)
    live = tunnels(client)
    for tid in down:
        assert cable in live[tid]["carried_by"], "reported as down because it really is"


def test_repeated_cycles_return_to_an_identical_graph(client):
    def fingerprint():
        return (q("MATCH ()-[r:OPTICAL_SPAN]->() RETURN count(r) AS n, sum(r.available_capacity_tbps) AS cap")[0],
                {t: sorted(v["carried_by"]) for t, v in tunnels(client).items()})

    base = fingerprint()
    for cable in ["dunant", "pan-european-crossing-uk-ireland", "havfrueaec-2", "marea", "dunant"]:
        cut(client, cable)
        fix(client)
        client.post("/api/reset")
        assert fingerprint() == base, f"state drifted after {cable}"


# --------------------------------------------------------------------------- tools called directly, edge cases


def _real_detour_inputs() -> dict:
    from mcp_server.tools.blast_radius import trace_severed_blast_radius

    t = next(t for t in trace_severed_blast_radius("dunant")["affected_tunnels"] if t["tunnel_id"] == PRI)
    return {"src_station_code": t["src_gateway"], "dst_station_code": t["dst_gateway"],
            "required_bandwidth_tbps": t["required_bandwidth_gbps"] / 1000, "max_latency_ceiling_ms": 60.0,
            "excluded_srlg_id": "SRLG-USE-VAB-SHELF"}


@pytest.mark.parametrize("override,why", [
    ({"max_latency_ceiling_ms": 0.5}, "latency ceiling no route can meet"),
    ({"required_bandwidth_tbps": 1e6}, "more bandwidth than any cable has"),
    ({"src_station_code": "NOPE"}, "unknown source station"),
    ({"dst_station_code": "NOPE"}, "unknown destination station"),
], ids=["latency", "bandwidth", "bad-src", "bad-dst"])
def test_detour_reports_no_path_and_restores_masks(override, why):
    from mcp_server.tools.detour_finder import compute_optimal_optical_detour

    res = compute_optimal_optical_detour(**{**_real_detour_inputs(), **override})
    assert res["path_found"] is False, why
    assert res["hops"] == [] and res["total_latency_ms"] is None
    assert all(not (c["within_ceiling"] and c["srlg_isolation_verified"] and c["capacity_verified"])
               for c in res["candidates"])
    assert_consistent()


def test_detour_is_deterministic_and_ordered():
    from mcp_server.tools.detour_finder import compute_optimal_optical_detour

    a = compute_optimal_optical_detour(**_real_detour_inputs())
    b = compute_optimal_optical_detour(**_real_detour_inputs())
    assert a["path_found"] and a["hop_sequence"] == b["hop_sequence"]
    lat = [c["total_latency_ms"] for c in a["candidates"] if c["total_latency_ms"] is not None]
    assert lat == sorted(lat), "candidates come back fastest first"
    assert all(h["srlg_id"] != "SRLG-USE-VAB-SHELF" for h in a["hops"])
    assert_consistent()


def test_concurrent_detour_searches_do_not_corrupt_masks():
    from mcp_server.tools.detour_finder import compute_optimal_optical_detour

    results, errors = [], []

    def go(srlg):
        try:
            results.append(compute_optimal_optical_detour(**{**_real_detour_inputs(), "excluded_srlg_id": srlg}))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(g,)) for g in
               ["SRLG-USE-VAB-SHELF", "SRLG-UK-BUDE-SHELF", "SRLG-USE-VAB-SHELF", "SRLG-CBL-havfrueaec-2"]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 4
    for r in results:
        assert all(h["srlg_id"] != r["constraints"]["excluded_srlg_id"] for h in r["hops"])
    assert_consistent()


@pytest.mark.parametrize("mode", ["too-much-bandwidth", "one-bogus-hop"])
def test_reallocation_is_all_or_nothing(mode):
    from mcp_server.tools.detour_finder import compute_optimal_optical_detour
    from mcp_server.tools.spectrum_allocator import execute_spectrum_reallocation

    hops = [{"from": h["from"], "to": h["to"], "cable": h["cable"]}
            for h in compute_optimal_optical_detour(**_real_detour_inputs())["hops"]]
    slice_tbps = 0.1
    if mode == "too-much-bandwidth":
        slice_tbps = 1e6
    else:
        hops.append({"from": hops[-1]["to"], "to": "NOPE", "cable": "no-such-cable"})
    res = execute_spectrum_reallocation(hops, slice_tbps, "GRADIENT_SYNC", [PRI])
    assert res["status"] == "REJECTED_INSUFFICIENT_HEADROOM" and res["updated_spans"] == []
    assert_baseline()  # no span reserved, no tunnel moved


def test_unknown_cable_blast_radius_is_an_error_not_a_crash():
    from mcp_server.tools.blast_radius import trace_severed_blast_radius

    res = trace_severed_blast_radius("no-such-cable")
    assert "error" in res and res["cypher_trace"]


@pytest.mark.parametrize("codes", [[], ["NOPE", "ALSO-NOPE"]], ids=["empty", "unknown"])
def test_choke_point_audit_handles_empty_and_unknown_stations(codes):
    from mcp_server.tools.centrality_auditor import audit_secondary_choke_points

    res = audit_secondary_choke_points(codes)
    assert res["choke_point_detected"] is False and res["risk_assessment"] == "NOMINAL"


# --------------------------------------------------------------------------- explainability


def test_every_step_explains_itself_with_the_cypher_it_ran(client):
    cut(client, "dunant")
    agent = fix(client)["incident"]["agent"]
    for step in agent["steps"]:
        traces = step["result"]["cypher_trace"]
        assert traces, f"{step['tool']} returned no trace"
        for t in traces:
            assert t["cypher"].strip() and t["label"] and t["engine_ms"] >= 0 and "params" in t
    detour = agent["steps"][1]["result"]
    assert detour["constraints"]["excluded_srlg_id"] == "SRLG-USE-VAB-SHELF"
    audit = agent["steps"][2]["result"]
    assert audit["method"] and {"p95", "p99"} <= set(audit["thresholds"])
    assert agent["steps"][3]["result"]["roadm_configuration_payload"]["nodes"]


def test_failed_fix_says_why():
    """Even when the agent cannot act, it explains which constraint ruled each route out."""
    from mcp_server.tools.detour_finder import compute_optimal_optical_detour

    res = compute_optimal_optical_detour(**{**_real_detour_inputs(), "max_latency_ceiling_ms": 0.5})
    assert res["constraints"]["max_latency_ceiling_ms"] == 0.5
    assert res["candidates"] and all(c["within_ceiling"] is False for c in res["candidates"])


# --------------------------------------------------------------------------- API robustness


@pytest.mark.parametrize("method,path,body,code", [
    ("post", "/api/chaos/anchor-drag", {"cable_id": ""}, 409),
    ("post", "/api/chaos/anchor-drag", {"cable_id": "dunant' OR 1=1 //"}, 409),
    ("post", "/api/chaos/anchor-drag", {"cable_id": 42}, 422),
    ("post", "/api/agent/run", {"mode": "yolo"}, 422),
    ("post", "/api/ask", {}, 422),
    ("post", "/api/proof/live", {}, 400),
], ids=["empty-id", "injection", "wrong-type", "bad-mode", "no-question", "proof-no-cable"])
def test_bad_requests_are_rejected_cleanly(client, method, path, body, code):
    assert getattr(client, method)(path, json=body).status_code == code
    assert severed_cables() == set()


@pytest.mark.parametrize("write", [
    "CREATE (:X)", "MERGE (:X {a: 1})", "MATCH (n:LandingStation) SET n.hacked = true",
    "MATCH (n:TrainingJob) DETACH DELETE n", "MATCH ()-[r:OPTICAL_SPAN]->() DELETE r",
])
def test_read_only_mode_blocks_every_kind_of_write(write):
    with pytest.raises(Exception):
        run(write, read_only=True)
    assert q("MATCH (n:TrainingJob) RETURN count(n) AS n")[0]["n"] == 3


def test_simultaneous_cuts_only_one_wins(client):
    codes = []

    def go(cable):
        codes.append(client.post("/api/chaos/anchor-drag", json={"cable_id": cable}).status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in ["dunant", "marea", "dunant"]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409]
    assert len(severed_cables()) == 1


def test_simultaneous_fixes_only_one_acts(client):
    cut(client, "dunant")
    codes = []

    def go():
        codes.append(client.post("/api/agent/run", json={"mode": "playbook"}).status_code)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409]
    assert tunnels(client)[PRI]["state"] == "REROUTED"
    assert_consistent()

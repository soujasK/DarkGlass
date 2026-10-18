"""Fast unit tests: pure logic, no FalkorDB required."""

from __future__ import annotations

import math

from app.core.telemetry import CAPABILITIES, classify
from app.ingestion.geo import haversine_km, project_on_segment
from app.ingestion.import_telegeography import era_capacity_tbps, generate_codes
from app.services.ask_graph import _clean
from app.services.resilience_scan import _risk


def test_light_in_silica_constant():
    from app.core.config import get_settings

    tau = get_settings().tau_ms_per_km
    assert math.isclose(tau, 0.004897, rel_tol=1e-3)  # 4.897 us/km


def test_haversine_known_distance():
    # Virginia Beach -> Bude: about 5,700 km great-circle (cables run longer, ~6,000-6,600 km)
    d = haversine_km((-75.98, 36.85), (-4.54, 50.83))
    assert 5_600 < d < 5_800


def test_projection_on_segment_is_bounded():
    t, q, d = project_on_segment((0.0, 1.0), (-1.0, 0.0), (1.0, 0.0))
    assert 0.0 <= t <= 1.0 and math.isclose(q[0], 0.0, abs_tol=1e-6) and 100 < d < 120


def test_station_codes_are_unique_and_honour_overrides():
    stations = [{"id": "virginia-beach-va-united-states", "name": "Virginia Beach, VA, United States"}] + [
        {"id": f"s{i}", "name": f"Saint Place {i}, Somewhere"} for i in range(60)
    ]
    codes = generate_codes(stations)
    assert codes["virginia-beach-va-united-states"] == "VAB"
    assert len(set(codes.values())) == len(codes)


def test_capacity_model_is_monotonic_by_era():
    years = [1990, 1998, 2003, 2008, 2013, 2016, 2019, 2023]
    caps = [era_capacity_tbps(y) for y in years]
    assert caps == sorted(caps)


def test_telemetry_classifies_every_capability():
    assert classify("CALL algo.SPpaths({}) YIELD path") == "sppaths"
    assert classify("CALL algo.betweenness({}) YIELD node") == "betweenness"
    assert classify("CALL algo.WCC({}) YIELD node") == "wcc"
    assert classify("CALL algo.maxFlow({}) YIELD maxFlow") == "maxflow"
    assert classify("CREATE INDEX FOR (n:X) ON (n.id)") == "index"
    assert classify("MATCH (a)-[r:X]->(b) SET r.x = 1") == "mutation"
    assert classify("MATCH (a)-[r:X]->(b) RETURN a") == "traversal"
    # a count with no relationship hop is not a traversal (it used to inflate the engine bar)
    assert classify("MATCH (n:LandingStation) RETURN count(n) AS n") == "lookup"
    assert len(CAPABILITIES) == 9


def test_risk_score_orders_impacts_sensibly():
    quiet = _risk(0, 0, 0, 0.0, False, 0.0)
    islands = _risk(9, 5, 0, 0.0, False, 0.0)
    gpus = _risk(0, 0, 65_536, 0.0, False, 0.0)
    cut_off = _risk(0, 0, 0, None, True, 0.0)
    assert quiet == 0.0 and islands > quiet and gpus > quiet and cut_off > quiet


def test_generated_cypher_is_cleaned_and_bounded():
    q = _clean("```cypher\nMATCH (n) RETURN n;\n```")
    assert q.startswith("MATCH") and q.rstrip().endswith("LIMIT 25") and "```" not in q
    assert _clean("MATCH (n) RETURN n LIMIT 5").endswith("LIMIT 5")


def test_llm_switch_for_a_public_demo():
    from app.core.config import Settings

    assert not Settings(llm_enabled=False, vertex_project_id="p").llm_available  # demo: Gemini off
    assert not Settings(llm_provider="gemini", vertex_project_id=None).llm_available  # no project, no Gemini
    assert Settings(llm_provider="gemini", vertex_project_id="p").llm_available

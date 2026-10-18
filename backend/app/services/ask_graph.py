"""Ask the graph: plain-English questions -> Cypher (Gemini on Vertex AI) -> FalkorDB.

Safety: generated Cypher is executed with GRAPH.RO_QUERY, so FalkorDB itself refuses any
write. Results are capped, and station codes / cable ids found in the result are returned
so the UI can light them up on the globe.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.database import run
from app.core.telemetry import telemetry

SCHEMA = """
Graph `darkglass_core` (FalkorDB, openCypher).

Nodes
(:LandingStation {id, code, name, lat, lon, country (ISO-2), country_name, kind})
   kind is 'LANDING_STATION' or 'DC_GATEWAY'. Gateways: code 'IAD' (Ashburn, US) and 'DUB' (Dublin).
   Gateways also carry the label :DataCenter.
(:CableSystem {id, name, design_capacity_tbps, capacity_model, length_km, ready_for_service, owners,
               scan_risk, scan_rank, scan_isolated_stations, scan_isolated_countries (list),
               scan_gpus_at_risk, scan_latency_delta_ms, scan_capacity_loss_tbps})
   ready_for_service is an integer year (e.g. 2020); owners is one comma-separated string.
   The scan_* properties come from the planet-wide failure scan (higher scan_risk = more dangerous):
   scan_isolated_stations = landing stations that fall off the subsea network if this cable fails (in
   scan_isolated_countries); scan_gpus_at_risk = GPUs whose gradient sync would freeze.
(:TrainingJob {id, name, model_params, gpu_count, sync_interval_ms, max_jitter_ms, priority})
(:NCCL_Ring {id, ring_index, bucket_size_mb, timeout_threshold_ms})
(:LogicalTunnel {id, src_dc, dst_dc, src_gateway, dst_gateway, required_bandwidth_gbps, traffic_class,
                 state, baseline_latency_ms, current_latency_ms, route_codes (list)})
(:ResilienceScan {id, scenarios, duration_ms, baseline_capacity_tbps})
Compute fabric (both campuses, campus property 'IAD' or 'DUB'):
(:BorderRouter {id, campus}) (:SuperSpine {id, campus}) (:Spine {id, campus}) (:Leaf {id, campus})
(:Server {id, campus, model}) (:NIC {id, campus, model}) (:GPU {id, campus, model, slot})
(:Rack {id, campus}) (:Row {id, campus}) (:DataHall {id, campus})
(:Incident {id, kind, started_at, resolved_at (epoch seconds), restored, stall_seconds, loss_usd, agent_mode,
            agent_ms, graph_engine_ms, traffic_class, gpus_restored, latency_delta_ms, job_ids (list)})
   Incident memory: every past outage DarkGlass resolved.

Relationships
(:LandingStation)-[:OPTICAL_SPAN {span_id, cable_id, medium ('SUBSEA'|'BACKHAUL'), length_km, latency_ms,
                                   max_capacity_tbps, available_capacity_tbps, srlg_id, status}]->(:LandingStation)
   Each physical span exists once per direction. cable_id = CableSystem.id ('backhaul' for overland links).
(:LandingStation)-[:SEVERED_SPAN {...same properties}]->(:LandingStation)   spans currently cut
(:LandingStation)-[:LANDS]->(:CableSystem)     a cable lands at this station (use this to go from stations to cables)
(:TrainingJob)-[:COORDINATES]->(:NCCL_Ring)
(:NCCL_Ring)-[:USES_TUNNEL]->(:LogicalTunnel)
(:LogicalTunnel)-[:CARRIED_BY]->(:CableSystem)
(:ResilienceScan)-[:RANKED {rank, risk}]->(:CableSystem)
(:LogicalTunnel)-[:TERMINATES_AT]->(:BorderRouter)-[:FEEDS]->(:SuperSpine)-[:FEEDS]->(:Spine)-[:FEEDS]->(:Leaf)
   -[:FEEDS]->(:Server)-[:HAS_NIC]->(:NIC)-[:ATTACHED_TO]->(:GPU)-[:MEMBER_OF]->(:NCCL_Ring)
(:Server)-[:IN_RACK]->(:Rack)-[:IN_ROW]->(:Row)-[:IN_HALL]->(:DataHall)-[:IN_CAMPUS]->(:LandingStation)  campus gateway IAD / DUB
(:Incident)-[:SEVERED]->(:CableSystem)        the cable that failed
(:Incident)-[:REROUTED_VIA]->(:CableSystem)   the subsea cable(s) the traffic moved onto

Procedures available: algo.SPpaths({sourceNode, targetNode, relTypes, weightProp, pathCount}) YIELD path, pathWeight;
algo.betweenness({nodeLabels, relationshipTypes, samplingSize, samplingSeed}) YIELD node, score;
algo.WCC({nodeLabels, relationshipTypes}) YIELD node, componentId.
"""

INSTRUCTIONS = """You translate questions about a subsea-cable and AI-training graph into ONE read-only
openCypher query for FalkorDB. Rules:
- Output only the Cypher query. No prose, no markdown fences.
- Read-only: MATCH / OPTIONAL MATCH / WITH / RETURN / ORDER BY / LIMIT / CALL algo.* only.
- Always end with LIMIT 25 or less.
- Return human-readable columns (names, codes, numbers) with clear aliases.
- To find cables at a station use (s:LandingStation)-[:LANDS]->(c:CableSystem). Prefer pure traversals such as
  (s)-[:LANDS]->(c)<-[:CARRIED_BY]-(t)<-[:USES_TUNNEL]-(r)<-[:COORDINATES]-(j) over property joins.
- Country names are in country_name (e.g. 'United States', 'Ireland').
- FalkorDB does not support the =~ regex operator. For case-insensitive name matching use
  toLower(c.name) CONTAINS 'marea'.
"""


@lru_cache
def _client():
    """One long-lived client per process (a temporary client closes its HTTP pool mid-request)."""
    import google.auth
    from google import genai

    s = get_settings()
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"],
                                   quota_project_id=s.vertex_project_id)
    return genai.Client(vertexai=True, project=s.vertex_project_id, location=s.vertex_region, credentials=creds,
                        http_options={"timeout": int(ATTEMPT_TIMEOUT_S * 1000)})


# Vertex latency is long-tailed: most answers take seconds, a few never return. A stuck request is
# abandoned and asked again, so the question fails with a clear error instead of hanging the UI.
ATTEMPT_TIMEOUT_S = 45.0
ATTEMPTS = 3


def _generate(client, model: str, contents: str, config: dict):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            if "timeout" not in f"{type(exc).__name__} {exc}".lower():
                raise
            if attempt == ATTEMPTS:
                raise RuntimeError(f"{model} did not answer in {ATTEMPTS} tries of {ATTEMPT_TIMEOUT_S:.0f} s") from exc


def _clean(cypher: str) -> str:
    cypher = cypher.strip()
    cypher = re.sub(r"^```(?:cypher)?\s*|\s*```$", "", cypher, flags=re.I).strip()
    cypher = cypher.rstrip(";").strip()
    if not re.search(r"\bLIMIT\s+\d+\s*$", cypher, re.I):
        cypher += "\nLIMIT 25"
    return cypher


def _jsonable(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    props = getattr(v, "properties", None)
    if isinstance(props, dict):  # Node / Edge
        return {k: _jsonable(x) for k, x in props.items()}
    return str(v)


def ask(question: str) -> dict[str, Any]:
    s = get_settings()
    t0 = time.perf_counter()
    client = _client()
    response = _generate(client, s.gemini_model, f"{SCHEMA}\n\nQuestion: {question}",
                         {"system_instruction": INSTRUCTIONS, "thinking_config": {"thinking_level": "low"}})
    llm_ms = (time.perf_counter() - t0) * 1000
    cypher = _clean(response.text or "")
    try:
        res = run(cypher, read_only=True)
    except Exception as exc:
        telemetry.record(cypher, 0.0, 0, kind="text2cypher")
        return {"question": question, "cypher": cypher, "error": str(exc), "llm_ms": round(llm_ms, 1)}
    telemetry.record(cypher, res.engine_ms, len(res.rows), kind="text2cypher")

    rows = [{k: _jsonable(v) for k, v in r.items()} for r in res.rows[:25]]
    st_rows = run("MATCH (s:LandingStation) RETURN s.code AS code, s.name AS name", read_only=True).rows
    stations = {r["code"] for r in st_rows}
    station_names = {r["name"]: r["code"] for r in st_rows}
    cables = {r["id"] for r in run("MATCH (c:CableSystem) RETURN c.id AS id", read_only=True).rows}
    names = {r["name"]: r["id"] for r in run("MATCH (c:CableSystem) RETURN c.name AS name, c.id AS id",
                                              read_only=True).rows}
    hit_stations: set[str] = set()
    hit_cables: set[str] = set()

    def scan(v: Any) -> None:
        if isinstance(v, str):
            if v in stations:
                hit_stations.add(v)
            if v in station_names:
                hit_stations.add(station_names[v])
            if v in cables:
                hit_cables.add(v)
            if v in names:
                hit_cables.add(names[v])
        elif isinstance(v, list):
            for x in v:
                scan(x)
        elif isinstance(v, dict):
            for x in v.values():
                scan(x)

    for r in rows:
        scan(r)
    return {
        "question": question,
        "cypher": cypher,
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
        "row_count": len(res.rows),
        "engine_ms": round(res.engine_ms, 3),
        "llm_ms": round(llm_ms, 1),
        "model": s.gemini_model,
        "highlight": {"stations": sorted(hit_stations), "cables": sorted(hit_cables)},
    }

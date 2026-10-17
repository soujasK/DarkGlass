"""Physical fault injection and incident state.

An anchor drag severs every span of a cable system. Severed spans are retyped from
:OPTICAL_SPAN to :SEVERED_SPAN (properties preserved, status 'SEVERED'), which removes
them from every subsequent algo.SPpaths and algo.betweenness evaluation - the graph
the agent reasons over is the surviving physical plant.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.database import run
from app.ingestion.geo import haversine_km

SHELF_FAULT_OFFSET_KM = 120.0  # anchor drags happen on the continental shelf, near shore

SEVER = """
MATCH (a:LandingStation)-[r:OPTICAL_SPAN {cable_id: $cable_id}]->(b:LandingStation)
CREATE (a)-[s:SEVERED_SPAN]->(b)
SET s = properties(r), s.status = 'SEVERED'
DELETE r
RETURN s.span_id AS span_id, s.geo_id AS geo_id, s.geo_reversed AS geo_reversed, s.srlg_id AS srlg_id,
       a.code AS from_code, b.code AS to_code, a.id AS from_id, b.id AS to_id
"""

RESTORE_SEVERED = """
MATCH (a:LandingStation)-[r:SEVERED_SPAN]->(b:LandingStation)
CREATE (a)-[s:OPTICAL_SPAN]->(b)
SET s = properties(r), s.status = 'ACTIVE', s.route_weight = s.latency_ms
DELETE r
RETURN count(s) AS restored
"""

# Incident memory: every resolution is written into the graph and survives resets, so the
# agent (and Ask the graph) can recall what happened the last time a cable failed.
REMEMBER = """
MATCH (cut:CableSystem {id: $cable_id})
CREATE (i:Incident {id: $id, kind: $kind, started_at: $started_at, resolved_at: $resolved_at,
                    restored: $restored, stall_seconds: $stall_seconds, loss_usd: $loss_usd,
                    agent_mode: $agent_mode, agent_ms: $agent_ms, graph_engine_ms: $graph_engine_ms,
                    traffic_class: $traffic_class, gpus_restored: $gpus, latency_delta_ms: $latency_delta,
                    job_ids: $job_ids})
CREATE (i)-[:SEVERED]->(cut)
WITH i
UNWIND $via AS vid
MATCH (v:CableSystem {id: vid})
CREATE (i)-[:REROUTED_VIA]->(v)
RETURN count(*) AS n
"""

RESTORE_CAPACITY = """
MATCH ()-[r:OPTICAL_SPAN]->()
WHERE r.available_capacity_tbps <> r.baseline_available_tbps OR r.status <> 'ACTIVE'
SET r.available_capacity_tbps = r.baseline_available_tbps, r.status = 'ACTIVE', r.route_weight = r.latency_ms
RETURN count(r) AS restored
"""


@lru_cache
def _span_geometry() -> dict[str, list[list[float]]]:
    path = get_settings().graph_dir / "span_geometry.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fault_point(geo_id: str, shelf_at_start: bool | None) -> dict[str, float] | None:
    coords = _span_geometry().get(geo_id)
    if not coords:
        return None
    pts = coords if shelf_at_start in (True, None) else list(reversed(coords))
    if shelf_at_start is None:
        # No shelf end: fault at the along-route midpoint.
        total = sum(haversine_km(tuple(p), tuple(q)) for p, q in zip(pts, pts[1:]))
        target = total / 2
    else:
        target = SHELF_FAULT_OFFSET_KM
    walked = 0.0
    for p, q in zip(pts, pts[1:]):
        seg = haversine_km(tuple(p), tuple(q))
        if walked + seg >= target and seg > 0:
            f = (target - walked) / seg
            return {"lon": round(p[0] + (q[0] - p[0]) * f, 4), "lat": round(p[1] + (q[1] - p[1]) * f, 4)}
        walked += seg
    return {"lon": pts[-1][0], "lat": pts[-1][1]}


class ChaosEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.incident: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        s = get_settings()
        inc = self.incident
        now = time.time()
        # Loss accrues only while gradient sync is stalled (non-gradient cuts degrade, they do not stall).
        stall_seconds = 0.0
        stalls = bool(inc and inc["blast_radius"].get("affected_tunnels")
                      and any(t["traffic_class"] == "GRADIENT_SYNC" for t in inc["blast_radius"]["affected_tunnels"]))
        if inc and inc["stall_active"]:
            stall_seconds = now - inc["started_at"]
        elif inc and stalls and inc.get("resolved_at"):
            stall_seconds = inc["resolved_at"] - inc["started_at"]
        return {
            "server_time": now,
            "incident": inc,
            "stall_active": bool(inc and inc["stall_active"]),
            "stall_seconds": round(stall_seconds, 3),
            "loss_rate_usd_s": round(s.stall_loss_usd_per_second, 4) if inc and inc["stall_active"] else 0.0,
            "loss_usd": round(stall_seconds * s.stall_loss_usd_per_second, 2),
            "loss_rate_usd_hr": s.stall_loss_usd_per_hour,
            "legacy_reconvergence_s": s.legacy_reconvergence_s,
        }

    def anchor_drag(self, cable_id: str) -> dict[str, Any]:
        from mcp_server.tools.blast_radius import trace_severed_blast_radius

        with self._lock:
            if self.incident and self.incident["stall_active"]:
                raise RuntimeError("an incident is already active; reset first")
            res = run(SEVER, {"cable_id": cable_id})
            if not res.rows:
                raise ValueError(f"cable {cable_id!r} has no active spans to sever")

            from app.ingestion.import_telegeography import SHELF_SRLGS

            first = res.rows[0]
            reversed_geo = bool(first["geo_reversed"])
            # Geometry is stored from the span's original 'a' end; work out which end is on a shelf.
            geo_start_id = first["to_id"] if reversed_geo else first["from_id"]
            geo_end_id = first["from_id"] if reversed_geo else first["to_id"]
            shelf_at_start = True if geo_start_id in SHELF_SRLGS else (False if geo_end_id in SHELF_SRLGS else None)
            fault = _fault_point(first["geo_id"], shelf_at_start)

            blast = trace_severed_blast_radius(cable_id)
            affected = blast.get("affected_tunnels", [])
            stall = any(t["traffic_class"] == "GRADIENT_SYNC" for t in affected)
            self.incident = {
                "incident_id": f"inc-{uuid.uuid4().hex[:8]}",
                "kind": "ANCHOR_DRAG",
                "cable_id": cable_id,
                "cable_name": blast.get("cable_name"),
                "severed_spans": [{"span_id": r["span_id"], "geo_id": r["geo_id"], "from": r["from_code"],
                                   "to": r["to_code"], "srlg_id": r["srlg_id"]} for r in res.rows],
                "fault_coordinate": fault,
                "started_at": time.time(),
                "agent_started_at": None,
                "resolved_at": None,
                "stall_active": stall,
                "degraded": bool(affected),
                "blast_radius": blast,
                "sever_trace": res.trace("sever_cable"),
                "agent": None,
            }
            return self.snapshot()

    def mark_agent_started(self) -> dict[str, Any]:
        with self._lock:
            if self.incident is None:
                raise RuntimeError("no active incident")
            self.incident["agent_started_at"] = time.time()
            self.incident["agent"] = None
            return self.snapshot()

    def resolve(self, agent_result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.incident is None:
                raise RuntimeError("no active incident")
            self.incident["agent"] = agent_result
            if agent_result.get("restored"):
                self.incident["stall_active"] = False
                self.incident["degraded"] = False
                self.incident["resolved_at"] = time.time()
                self._remember(agent_result)
            return self.snapshot()

    def _remember(self, agent_result: dict[str, Any]) -> None:
        inc = self.incident
        assert inc is not None
        s = get_settings()
        stalls = any(t["traffic_class"] == "GRADIENT_SYNC" for t in inc["blast_radius"].get("affected_tunnels", []))
        stall_seconds = (inc["resolved_at"] - inc["started_at"]) if stalls else 0.0
        detour = agent_result.get("detour") or {}
        try:
            run(REMEMBER, {
                "cable_id": inc["cable_id"],
                "id": inc["incident_id"],
                "kind": inc["kind"],
                "started_at": inc["started_at"],
                "resolved_at": inc["resolved_at"],
                "restored": True,
                "stall_seconds": round(stall_seconds, 3),
                "loss_usd": round(stall_seconds * s.stall_loss_usd_per_second, 2),
                "agent_mode": agent_result.get("mode", "playbook"),
                "agent_ms": agent_result.get("agent_wall_ms"),
                "graph_engine_ms": agent_result.get("graph_engine_ms"),
                "traffic_class": agent_result.get("traffic_class"),
                "gpus": agent_result.get("restored_gpus") or 0,
                "latency_delta": agent_result.get("latency_delta_ms"),
                "job_ids": sorted({j["job_id"] for j in inc["blast_radius"].get("affected_jobs", [])}),
                "via": detour.get("cables_used", []),
            })
        except Exception:  # memory is a bonus; never let it break a restoration
            pass

    def reset(self) -> dict[str, Any]:
        from app.ingestion.seed_l7_clusters import restore_tunnels

        with self._lock:
            restored = run(RESTORE_SEVERED).rows[0]["restored"]
            capacity = run(RESTORE_CAPACITY).rows[0]["restored"]
            restore_tunnels()
            self.incident = None
            return {"restored_severed_spans": restored, "restored_capacity_spans": capacity, **self.snapshot()}


chaos = ChaosEngine()

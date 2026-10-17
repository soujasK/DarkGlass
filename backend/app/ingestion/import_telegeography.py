"""Build the DarkGlass physical topology from TeleGeography data and load it into FalkorDB.

Pipeline
--------
1. Read data/cables.geojson, data/landing_stations.geojson, data/cable_details.json
   (fetched by fetch_telegeography.py). Planned (not yet lit) cables are skipped.
2. For every cable, build a geometric route graph from its polylines, join branch
   lines at their branching units, and snap each landing station onto the route.
   Station-to-station along-route distances give a minimum spanning tree; each
   tree edge becomes one physical span in each direction. Polyline vertices are
   NOT graph nodes - curvature is written to static frontend JSON only.
3. Add modelled domestic backhaul (terrestrial / short-haul) between nearby
   stations, plus two data-centre gateways (Ashburn IAD, Dublin DUB).
4. Assign Shared Risk Link Groups: shared continental-shelf approaches and
   named ocean choke points; otherwise one SRLG per cable system.
5. Write data/graph/topology.json - the single canonical dataset that FalkorDB,
   the SQLite benchmark and the NetworkX benchmark all load - and the static
   frontend bundles under frontend/public/data/.
6. Load the topology into the FalkorDB graph `darkglass_core`.

Usage (from backend/):
    python -m app.ingestion.import_telegeography            build + load
    python -m app.ingestion.import_telegeography --no-load  build files only
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx

from app.core.config import get_settings
from app.ingestion.geo import great_circle_points, haversine_km, project_on_segment

# ---------------------------------------------------------------------------
# Modelling constants (documented in README "Data provenance")
# ---------------------------------------------------------------------------

JUNCTION_SNAP_KM = 40.0  # a branch line endpoint this close to another line joins it
BACKHAUL_ROUTE_FACTOR = 1.25  # terrestrial fibre is longer than the great circle
GAP_ROUTE_FACTOR = 1.15  # used when a cable's drawn polylines leave stations disconnected
BACKHAUL_K = 2
BACKHAUL_MAX_KM = 500.0
BACKHAUL_MST_MAX_KM = 1000.0  # never model overland fibre across open ocean (Hawaii, Guam, ...)
BACKHAUL_CAPACITY_TBPS = 96.0
# algo.SPpaths weightProp. Equal to latency_ms while a span is routable; raised to
# ROUTE_BLOCKED_WEIGHT while it is masked so the engine never relaxes it.
ROUTE_BLOCKED_WEIGHT = 1_000_000.0

# Countries joined by land, so that domestic-style backhaul may cross the border.
LAND_NEIGHBOURS = {
    frozenset(p)
    for p in [
        ("France", "Spain"), ("France", "Belgium"), ("France", "Italy"), ("France", "Germany"),
        ("France", "Monaco"), ("Belgium", "Netherlands"), ("Netherlands", "Germany"),
        ("Germany", "Denmark"), ("Germany", "Poland"), ("Spain", "Portugal"),
        ("Denmark", "Sweden"), ("Sweden", "Norway"), ("Sweden", "Finland"),
        ("United States", "Canada"), ("United States", "Mexico"),
    ]
}

# Shared continental-shelf approaches: long-haul spans that touch these stations
# share the same shallow-water trench and therefore one SRLG.
SHELF_SRLGS = {
    "virginia-beach-va-united-states": "SRLG-USE-VAB-SHELF",
    "bude-united-kingdom": "SRLG-UK-BUDE-SHELF",
}
SHELF_MIN_SPAN_KM = 1000.0

# Named ocean choke points (lat, lon, radius_km); spans whose route passes inside share an SRLG.
CHOKEPOINT_SRLGS = [
    ("SRLG-BAB-EL-MANDEB", 12.60, 43.35, 80.0),
    ("SRLG-LUZON-STRAIT", 20.80, 121.00, 180.0),
    ("SRLG-STRAIT-OF-GIBRALTAR", 35.95, -5.60, 40.0),
    ("SRLG-SINGAPORE-STRAIT", 1.20, 103.95, 45.0),
]

# Design capacity: only figures publicly stated by the cable owner are used verbatim.
PUBLISHED_CAPACITY_TBPS = {"dunant": 250.0, "marea": 200.0}

# Three-letter codes for the stations the demo narrative names; all others are generated.
CODE_OVERRIDES = {
    "virginia-beach-va-united-states": "VAB",
    "bude-united-kingdom": "BDE",
    "bilbao-spain": "BIO",
    "saint-hilaire-de-riez-france": "SHR",
    "lynn-ma-united-states": "LYN",
    "bellport-ny-united-states": "BLP",
    "shirley-ny-united-states": "SHY",
    "wall-township-nj-united-states": "WAL",
    "killala-ireland": "KLA",
    "dublin-ireland": "DBN",
    "lecanvey-ireland": "LCV",
    "cork-ireland": "ORK",
    "galway-ireland": "GWY",
    "le-porge-france": "LPG",
    "fortaleza-brazil": "FOR",
    "sines-portugal": "SIE",
    "lisbon-portugal": "LIS",
    "carcavelos-portugal": "CVL",
    "marseille-france": "MRS",
    "halifax-ns-canada": "HFX",
    "holyhead-united-kingdom": "HLY",
    "southport-united-kingdom": "SPT",
    "blackpool-united-kingdom": "BPL",
    "singapore-singapore": "SIN",
}

DATA_CENTRES = [
    {
        "id": "dc-us-east-ashburn",
        "code": "IAD",
        "name": "US-East-Ashburn (AI campus gateway)",
        "lat": 39.0438,
        "lon": -77.4874,
        "country": "United States",
        "backhaul_k": 8,
        "backhaul_max_km": 700.0,
    },
    {
        "id": "dc-eu-west-dublin",
        "code": "DUB",
        "name": "EU-West-Dublin (AI campus gateway)",
        "lat": 53.3260,
        "lon": -6.4400,
        "country": "Ireland",
        "backhaul_k": 6,
        "backhaul_max_km": 300.0,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _stable_unit(key: str) -> float:
    """Deterministic pseudo-random number in [0, 1) derived from a string."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) / float(16**12)


def era_capacity_tbps(rfs_year: int | None) -> float:
    """Design capacity modelled from commissioning era (coherent-optics generations)."""
    y = rfs_year or 2000
    for limit, cap in ((1995, 0.04), (2000, 0.64), (2005, 3.2), (2010, 8.0), (2015, 24.0),
                       (2018, 60.0), (2021, 160.0)):
        if y < limit:
            return cap
    return 250.0


_ISO_CACHE: dict[str, str] = {}
_ISO_FIXUPS = {
    "Korea, Rep.": "KR", "Congo, Dem. Rep.": "CD", "Congo, Rep.": "CG", "Russia": "RU",
    "Taiwan": "TW", "Vietnam": "VN", "Turkey": "TR", "Iran": "IR", "Syria": "SY",
    "Brunei": "BN", "Micronesia": "FM", "Virgin Islands (U.K.)": "VG",
    "Virgin Islands (U.S.)": "VI", "Saint Martin": "MF", "Sint Maarten": "SX",
    "Cote d'Ivoire": "CI", "Côte d'Ivoire": "CI", "Curacao": "CW", "Curaçao": "CW",
    "Canary Islands": "ES", "Bonaire, Sint Eustatius and Saba": "BQ", "Kosovo": "XK",
    "Gambia": "GM", "Bahamas": "BS", "Laos": "LA", "Tanzania": "TZ", "Venezuela": "VE",
}


def iso_code(country: str) -> str:
    if country in _ISO_CACHE:
        return _ISO_CACHE[country]
    code = _ISO_FIXUPS.get(country)
    if code is None:
        try:
            import pycountry

            try:
                code = pycountry.countries.lookup(country).alpha_2
            except LookupError:
                code = pycountry.countries.search_fuzzy(country)[0].alpha_2
        except Exception:
            code = _ascii(country)[:2].upper()
    _ISO_CACHE[country] = code
    return code


def generate_codes(stations: list[dict[str, Any]]) -> dict[str, str]:
    """Unique 3-letter codes (4 when exhausted). Stations must be ordered by importance."""
    used = set(CODE_OVERRIDES.values()) | {d["code"] for d in DATA_CENTRES}
    out: dict[str, str] = {}
    for st in stations:
        sid = st["id"]
        if sid in CODE_OVERRIDES:
            out[sid] = CODE_OVERRIDES[sid]
            continue
        place = re.sub(r"[^A-Z ]", "", _ascii(st["name"].split(",")[0]).upper()).strip()
        letters = place.replace(" ", "") or "XXX"
        words = place.split()
        cands = []
        if len(words) >= 3:
            cands.append("".join(w[0] for w in words[:3]))
        if len(words) == 2:
            cands.append(words[0][0] + words[1][:2])
            cands.append(words[0][:2] + words[1][0])
        cands.append(letters[:3])
        consonants = [c for c in letters[1:] if c not in "AEIOU"]
        if len(consonants) >= 2:
            cands.append(letters[0] + consonants[0] + consonants[1])
        cands.extend(letters[0] + a + b for a, b in itertools.combinations(letters[1:], 2))
        code = next((c for c in cands if len(c) == 3 and c not in used), None)
        if code is None:
            code = next(f"{letters[:3]}{i}" for i in itertools.count(1) if f"{letters[:3]}{i}" not in used)
        used.add(code)
        out[sid] = code
    return out


# ---------------------------------------------------------------------------
# Per-cable route reconstruction
# ---------------------------------------------------------------------------


class CableRoute:
    """Geometric route graph of one cable system, used to measure along-route span length."""

    def __init__(self, lines: list[list[tuple[float, float]]]):
        self.g = nx.Graph()
        self.pos: dict[Any, tuple[float, float]] = {}
        self._seq = 0
        for li, line in enumerate(lines):
            prev = None
            for vi, pt in enumerate(line):
                n = ("v", li, vi)
                self.g.add_node(n, line=li)
                self.pos[n] = pt
                if prev is not None:
                    self.g.add_edge(prev, n, length=haversine_km(self.pos[prev], pt), est=False)
                prev = n
        self.lines = lines

    def _route_edges(self, exclude_line: int | None):
        for u, v in self.g.edges():
            if u[0] == "s" or v[0] == "s":
                continue
            if exclude_line is not None and self.g.nodes[u].get("line") == exclude_line:
                continue
            yield u, v

    def nearest(self, p: tuple[float, float], exclude_line: int | None = None):
        best = None
        for u, v in self._route_edges(exclude_line):
            t, q, d = project_on_segment(p, self.pos[u], self.pos[v])
            if best is None or d < best[3]:
                best = (u, v, t, d, q)
        return best

    def attach(self, node, p: tuple[float, float], exclude_line: int | None = None,
               max_km: float | None = None) -> bool:
        best = self.nearest(p, exclude_line)
        if best is None:
            return False
        u, v, t, d, q = best
        if max_km is not None and d > max_km:
            return False
        if t <= 1e-6:
            anchor = u
        elif t >= 1 - 1e-6:
            anchor = v
        else:
            self._seq += 1
            anchor = ("p", self._seq)
            self.g.add_node(anchor, line=self.g.nodes[u].get("line"))
            self.pos[anchor] = q
            self.g.remove_edge(u, v)
            self.g.add_edge(u, anchor, length=haversine_km(self.pos[u], q), est=False)
            self.g.add_edge(anchor, v, length=haversine_km(q, self.pos[v]), est=False)
        if node not in self.pos:
            self.pos[node] = p
            self.g.add_node(node)
        if anchor != node:
            self.g.add_edge(node, anchor, length=d, est=False)
        return True

    def join_branches(self) -> None:
        for li, line in enumerate(self.lines):
            for vi in {0, len(line) - 1}:
                self.attach(("v", li, vi), line[vi], exclude_line=li, max_km=JUNCTION_SNAP_KM)


def cable_spans(
    cable_id: str, lines: list[list[tuple[float, float]]], stations: dict[str, tuple[float, float]]
) -> list[dict[str, Any]]:
    """Minimum spanning tree over a cable's landing stations, measured along the drawn route."""
    sids = sorted(stations)
    if len(sids) < 2:
        return []
    route = CableRoute([ln for ln in lines if len(ln) >= 2])
    has_route = route.g.number_of_edges() > 0
    if has_route:
        route.join_branches()
    for sid in sids:
        node = ("s", sid)
        if not (has_route and route.attach(node, stations[sid])):
            route.pos[node] = stations[sid]
            route.g.add_node(node)

    # Connect fragments the drawn geometry leaves apart (estimated great-circle bridge).
    station_nodes = [("s", s) for s in sids]
    while True:
        comps = [c for c in nx.connected_components(route.g) if any(n in c for n in station_nodes)]
        if len(comps) <= 1:
            break
        base = comps[0]
        best = None
        for other in comps[1:]:
            for a in (n for n in station_nodes if n in base):
                for b in (n for n in station_nodes if n in other):
                    d = haversine_km(route.pos[a], route.pos[b])
                    if best is None or d < best[2]:
                        best = (a, b, d)
        a, b, d = best
        route.g.add_edge(a, b, length=d * GAP_ROUTE_FACTOR, est=True)

    # Along-route distances between every pair of stations.
    k = nx.Graph()
    paths: dict[tuple, list] = {}
    for a in station_nodes:
        dist, path = nx.single_source_dijkstra(route.g, a, weight="length")
        for b in station_nodes:
            if b != a and b in dist:
                k.add_edge(a, b, length=dist[b])
                paths[(a, b)] = path[b]

    spans = []
    for a, b, data in sorted(nx.minimum_spanning_edges(k, weight="length", data=True)):
        path = paths[(a, b)]
        est = any(route.g.edges[u, v].get("est") for u, v in zip(path, path[1:]))
        geom = [route.pos[n] for n in path]
        if est:
            # Replace estimated bridges with great-circle arcs for display.
            pts: list[tuple[float, float]] = []
            for u, v in zip(path, path[1:]):
                seg = (great_circle_points(route.pos[u], route.pos[v])
                       if route.g.edges[u, v].get("est") else [route.pos[u], route.pos[v]])
                pts.extend(seg if not pts else seg[1:])
            geom = pts
        spans.append({
            "cable_id": cable_id,
            "a": a[1],
            "b": b[1],
            "length_km": round(data["length"], 1),
            "route_model": "POLYLINE_ESTIMATED_GAP" if est else "POLYLINE",
            "geometry": [[round(x, 4), round(y, 4)] for x, y in geom],
        })
    return spans


# ---------------------------------------------------------------------------
# Topology build
# ---------------------------------------------------------------------------


def _assign_srlg(span: dict[str, Any]) -> str:
    if span["medium"] == "BACKHAUL":
        a, b = sorted((span["a"], span["b"]))
        return f"SRLG-BKH-{a}-{b}"
    if span["length_km"] >= SHELF_MIN_SPAN_KM:
        for end in (span["a"], span["b"]):
            if end in SHELF_SRLGS:
                return SHELF_SRLGS[end]
    for srlg, lat, lon, radius in CHOKEPOINT_SRLGS:
        if any(haversine_km((x, y), (lon, lat)) <= radius for x, y in span["geometry"]):
            return srlg
    return f"SRLG-CBL-{span['cable_id']}"


def build_topology() -> dict[str, Any]:
    s = get_settings()
    d = s.data_dir
    cables_geo = json.loads((d / "cables.geojson").read_text(encoding="utf-8"))
    stations_geo = json.loads((d / "landing_stations.geojson").read_text(encoding="utf-8"))
    details = json.loads((d / "cable_details.json").read_text(encoding="utf-8"))

    st_pos = {f["properties"]["id"]: tuple(f["geometry"]["coordinates"]) for f in stations_geo["features"]}
    st_name = {f["properties"]["id"]: f["properties"]["name"] for f in stations_geo["features"]}

    lines_by_cable: dict[str, list[list[tuple[float, float]]]] = defaultdict(list)
    colour: dict[str, str] = {}
    for f in cables_geo["features"]:
        cid = f["properties"]["id"]
        colour[cid] = f["properties"].get("color", "#38BDF8")
        geom = f["geometry"]
        parts = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        lines_by_cable[cid].extend([[tuple(p[:2]) for p in part] for part in parts])

    cables: list[dict[str, Any]] = []
    raw_spans: list[dict[str, Any]] = []
    station_country: dict[str, str] = {}
    skipped_planned = 0
    for cid in sorted(details):
        det = details[cid]
        if det.get("is_planned"):
            skipped_planned += 1
            continue
        lps = {}
        for lp in det.get("landing_points", []):
            if lp["id"] in st_pos:
                lps[lp["id"]] = st_pos[lp["id"]]
                station_country[lp["id"]] = lp.get("country") or ""
        if len(lps) < 2:
            continue
        rfs = det.get("rfs_year")
        published = PUBLISHED_CAPACITY_TBPS.get(cid)
        cap = published or era_capacity_tbps(rfs)
        length_txt = (det.get("length") or "").replace(",", "").replace("km", "").strip()
        try:
            length_km = float(length_txt)
        except ValueError:
            length_km = None
        cables.append({
            "id": cid,
            "name": det["name"],
            "design_capacity_tbps": cap,
            "capacity_model": "PUBLISHED" if published else "RFS_ERA_MODEL",
            "length_km": length_km,
            "ready_for_service": rfs,
            "owners": det.get("owners") or "",
            "color": colour.get(cid, "#38BDF8"),
        })
        for sp in cable_spans(cid, lines_by_cable.get(cid, []), lps):
            sp["medium"] = "SUBSEA"
            sp["max_capacity_tbps"] = cap
            raw_spans.append(sp)

    cable_by_id = {c["id"]: c for c in cables}

    # Stations: every landing point used by an in-service cable, ordered by degree for code assignment.
    degree: dict[str, int] = defaultdict(int)
    for sp in raw_spans:
        degree[sp["a"]] += 1
        degree[sp["b"]] += 1
    station_ids = sorted(degree, key=lambda sid: (-degree[sid], sid))
    stations = [{
        "id": sid,
        "name": st_name[sid],
        "lat": round(st_pos[sid][1], 5),
        "lon": round(st_pos[sid][0], 5),
        "country": station_country.get(sid, ""),
        "kind": "LANDING_STATION",
    } for sid in station_ids]
    codes = generate_codes(stations)
    for st in stations:
        st["code"] = codes[st["id"]]
        st["country_iso"] = iso_code(st["country"]) if st["country"] else ""

    for dc in DATA_CENTRES:
        stations.append({
            "id": dc["id"], "code": dc["code"], "name": dc["name"], "lat": dc["lat"], "lon": dc["lon"],
            "country": dc["country"], "country_iso": iso_code(dc["country"]), "kind": "DC_GATEWAY",
        })
    by_id = {st["id"]: st for st in stations}

    # Modelled backhaul between nearby landing stations (same country or land neighbours).
    def can_backhaul(x: dict, y: dict) -> bool:
        return x["country"] == y["country"] or frozenset((x["country"], y["country"])) in LAND_NEIGHBOURS

    backhaul_pairs: set[tuple[str, str]] = set()
    landing = [st for st in stations if st["kind"] == "LANDING_STATION"]
    for st in landing:
        near = sorted(
            ((haversine_km((st["lon"], st["lat"]), (o["lon"], o["lat"])), o["id"])
             for o in landing if o["id"] != st["id"] and can_backhaul(st, o)),
        )
        for dist, oid in near[:BACKHAUL_K]:
            if dist <= BACKHAUL_MAX_KM:
                backhaul_pairs.add(tuple(sorted((st["id"], oid))))

    # k-nearest alone fragments a coastline into islands of mutual neighbours; a minimum
    # spanning tree per land region keeps every country's stations reachable overland.
    region = nx.Graph()
    region.add_nodes_from({st["country"] for st in landing})
    region.add_edges_from(tuple(p) for p in LAND_NEIGHBOURS if all(c in region for c in p))
    for countries in nx.connected_components(region):
        members = [st for st in landing if st["country"] in countries]
        k = nx.Graph()
        for x, y in itertools.combinations(members, 2):
            if can_backhaul(x, y):
                dist = haversine_km((x["lon"], x["lat"]), (y["lon"], y["lat"]))
                if dist <= BACKHAUL_MST_MAX_KM:
                    k.add_edge(x["id"], y["id"], length=dist)
        for a, b in nx.minimum_spanning_edges(k, weight="length", data=False):
            backhaul_pairs.add(tuple(sorted((a, b))))

    for dc in DATA_CENTRES:
        near = sorted(
            ((haversine_km((dc["lon"], dc["lat"]), (o["lon"], o["lat"])), o["id"])
             for o in landing if o["country"] == dc["country"]),
        )
        for dist, oid in near[: dc["backhaul_k"]]:
            if dist <= dc["backhaul_max_km"]:
                backhaul_pairs.add(tuple(sorted((dc["id"], oid))))

    for a, b in sorted(backhaul_pairs):
        pa, pb = (by_id[a]["lon"], by_id[a]["lat"]), (by_id[b]["lon"], by_id[b]["lat"])
        raw_spans.append({
            "cable_id": "backhaul",
            "a": a,
            "b": b,
            "length_km": round(haversine_km(pa, pb) * BACKHAUL_ROUTE_FACTOR, 1),
            "route_model": "GREAT_CIRCLE_X1.25",
            "geometry": [[round(x, 4), round(y, 4)] for x, y in great_circle_points(pa, pb, 50.0)],
            "medium": "BACKHAUL",
            "max_capacity_tbps": BACKHAUL_CAPACITY_TBPS,
        })

    # Directed spans + geometry table.
    tau = s.tau_ms_per_km
    spans: list[dict[str, Any]] = []
    geometry: dict[str, list] = {}
    for sp in raw_spans:
        sp["srlg_id"] = _assign_srlg(sp)
        ca, cb = by_id[sp["a"]]["code"], by_id[sp["b"]]["code"]
        geo_id = f"{sp['cable_id']}:{ca}-{cb}"
        geometry[geo_id] = sp["geometry"]
        util_key = sp["cable_id"] if sp["medium"] == "SUBSEA" else geo_id
        for src, dst, reverse in ((sp["a"], sp["b"], False), (sp["b"], sp["a"], True)):
            utilisation = 0.35 + 0.45 * _stable_unit(f"{util_key}|{src}>{dst}")
            avail = round(sp["max_capacity_tbps"] * (1.0 - utilisation), 3)
            spans.append({
                "span_id": f"{sp['cable_id']}:{by_id[src]['code']}>{by_id[dst]['code']}",
                "src": src,
                "dst": dst,
                "cable_id": sp["cable_id"],
                "medium": sp["medium"],
                "length_km": sp["length_km"],
                "latency_ms": round(sp["length_km"] * tau, 4),
                "max_capacity_tbps": sp["max_capacity_tbps"],
                "available_capacity_tbps": avail,
                "baseline_available_tbps": avail,
                "srlg_id": sp["srlg_id"],
                "status": "ACTIVE",
                "route_weight": round(sp["length_km"] * tau, 4),
                "route_model": sp["route_model"],
                "geo_id": geo_id,
                "geo_reversed": reverse,
            })

    return {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "TeleGeography Submarine Cable Map (submarinecablemap.com), CC BY-NC-SA 3.0",
            "tau_ms_per_km": tau,
            "counts": {
                "stations": len(stations),
                "cables": len(cables),
                "spans_directed": len(spans),
                "subsea_spans_directed": sum(1 for x in spans if x["medium"] == "SUBSEA"),
                "backhaul_spans_directed": sum(1 for x in spans if x["medium"] == "BACKHAUL"),
                "planned_cables_skipped": skipped_planned,
            },
        },
        "stations": stations,
        "cables": [cable_by_id[c] for c in sorted(cable_by_id)],
        "spans": spans,
        "_geometry": geometry,
        "_cable_lines": {c["id"]: lines_by_cable.get(c["id"], []) for c in cables},
    }


def write_outputs(topo: dict[str, Any]) -> None:
    s = get_settings()
    s.graph_dir.mkdir(parents=True, exist_ok=True)
    s.frontend_data_dir.mkdir(parents=True, exist_ok=True)

    geometry = topo.pop("_geometry")
    cable_lines = topo.pop("_cable_lines")

    (s.graph_dir / "topology.json").write_text(json.dumps(topo, separators=(",", ":")), encoding="utf-8")
    (s.graph_dir / "span_geometry.json").write_text(json.dumps(geometry, separators=(",", ":")), encoding="utf-8")

    fe = s.frontend_data_dir
    (fe / "stations.json").write_text(json.dumps([
        {k: st[k] for k in ("id", "code", "name", "lat", "lon", "country_iso", "kind")}
        for st in topo["stations"]
    ], separators=(",", ":")), encoding="utf-8")
    (fe / "cables.json").write_text(json.dumps([
        {"id": c["id"], "name": c["name"], "rfs": c["ready_for_service"],
         "lines": [[[round(x, 3), round(y, 3)] for x, y in ln] for ln in cable_lines[c["id"]]]}
        for c in topo["cables"]
    ], separators=(",", ":")), encoding="utf-8")
    (fe / "span_geometry.json").write_text(json.dumps(geometry, separators=(",", ":")), encoding="utf-8")
    (fe / "topology_meta.json").write_text(json.dumps(topo["meta"], indent=2), encoding="utf-8")


def load_topology_file() -> dict[str, Any]:
    return json.loads((get_settings().graph_dir / "topology.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# FalkorDB load
# ---------------------------------------------------------------------------

INDEXES = [
    "CREATE INDEX FOR (n:LandingStation) ON (n.code)",
    "CREATE INDEX FOR (n:LandingStation) ON (n.id)",
    "CREATE INDEX FOR (n:CableSystem) ON (n.id)",
    "CREATE INDEX FOR (n:TrainingJob) ON (n.id)",
    "CREATE INDEX FOR (n:NCCL_Ring) ON (n.id)",
    "CREATE INDEX FOR (n:LogicalTunnel) ON (n.id)",
    "CREATE INDEX FOR (n:Incident) ON (n.id)",
    "CREATE INDEX FOR ()-[r:OPTICAL_SPAN]-() ON (r.cable_id)",
    "CREATE INDEX FOR ()-[r:OPTICAL_SPAN]-() ON (r.srlg_id)",
    "CREATE INDEX FOR ()-[r:OPTICAL_SPAN]-() ON (r.span_id)",
    "CREATE INDEX FOR ()-[r:OPTICAL_SPAN]-() ON (r.status)",
    "CREATE INDEX FOR ()-[r:OPTICAL_SPAN]-() ON (r.available_capacity_tbps)",
    "CREATE INDEX FOR ()-[r:SEVERED_SPAN]-() ON (r.cable_id)",
    "CREATE INDEX FOR ()-[r:SEVERED_SPAN]-() ON (r.srlg_id)",
]


# Physical-to-logical bridge as a real relationship, so station -> cable -> tunnel -> ring -> job
# is one pure graph traversal (no property joins).
LANDS_EDGES = """
MATCH (a:LandingStation)-[r:OPTICAL_SPAN|SEVERED_SPAN]->()
WHERE r.medium = 'SUBSEA'
WITH DISTINCT a, r.cable_id AS cid
MATCH (c:CableSystem {id: cid})
MERGE (a)-[:LANDS]->(c)
RETURN count(*) AS n
"""


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def load_into_falkordb(topo: dict[str, Any], graph_name: str | None = None) -> dict[str, Any]:
    from app.core.database import get_graph, run

    g = get_graph(graph_name)
    try:
        g.delete()
    except Exception:
        pass  # graph did not exist yet
    g = get_graph(graph_name)
    t0 = time.perf_counter()

    for q in INDEXES:
        run(q, graph=g)

    for batch in _chunks(topo["stations"], 500):
        run("""
            UNWIND $rows AS r
            CREATE (s:LandingStation {id: r.id, code: r.code, name: r.name, lat: r.lat, lon: r.lon,
                                      country: r.country_iso, country_name: r.country, kind: r.kind})
        """, {"rows": batch}, graph=g)
    run("MATCH (s:LandingStation {kind: 'DC_GATEWAY'}) SET s:DataCenter", graph=g)

    for batch in _chunks(topo["cables"], 500):
        run("""
            UNWIND $rows AS r
            CREATE (:CableSystem {id: r.id, name: r.name, design_capacity_tbps: r.design_capacity_tbps,
                                  capacity_model: r.capacity_model, length_km: r.length_km,
                                  ready_for_service: r.ready_for_service, owners: r.owners})
        """, {"rows": [{**c, "length_km": c["length_km"] or -1.0, "ready_for_service": c["ready_for_service"] or -1}
                       for c in batch]}, graph=g)

    span_props = ("span_id", "cable_id", "medium", "length_km", "latency_ms", "max_capacity_tbps",
                  "available_capacity_tbps", "baseline_available_tbps", "srlg_id", "status", "route_weight",
                  "route_model", "geo_id", "geo_reversed")
    for batch in _chunks(topo["spans"], 1000):
        run("""
            UNWIND $rows AS r
            MATCH (a:LandingStation {id: r.src}), (b:LandingStation {id: r.dst})
            CREATE (a)-[s:OPTICAL_SPAN]->(b)
            SET s = r.props
        """, {"rows": [{"src": sp["src"], "dst": sp["dst"], "props": {k: sp[k] for k in span_props}}
                       for sp in batch]}, graph=g)

    run(LANDS_EDGES, graph=g)

    counts = run("""
        MATCH (n:LandingStation) WITH count(n) AS stations
        MATCH ()-[r:OPTICAL_SPAN]->() WITH stations, count(r) AS spans
        MATCH (c:CableSystem) RETURN stations, spans, count(c) AS cables
    """, read_only=True, graph=g).rows[0]
    counts["load_seconds"] = round(time.perf_counter() - t0, 2)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-load", action="store_true", help="build files only; do not touch FalkorDB")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    topo = build_topology()
    print(f"[ingest] topology built in {time.perf_counter() - t0:.1f}s: {json.dumps(topo['meta']['counts'])}")
    write_outputs(topo)
    print(f"[ingest] wrote {get_settings().graph_dir / 'topology.json'} and frontend bundles")
    if args.no_load:
        return 0
    counts = load_into_falkordb(topo)
    print(f"[ingest] FalkorDB '{get_settings().graph_name}' loaded: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Live FalkorDB engine telemetry.

Every query executed through app.core.database.run() is classified by the FalkorDB capability
it exercises and aggregated here. The API streams a snapshot to the UI a few times a second,
so the engine bar shows real calls and real engine milliseconds, never an animation.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any

# Display order matters: this is the order of the engine bar.
CAPABILITIES: list[tuple[str, str, str]] = [
    ("traversal", "Cypher traversal", "multi-hop pattern matching across layers"),
    ("sppaths", "algo.SPpaths", "weighted shortest paths"),
    ("betweenness", "algo.betweenness", "choke-point centrality"),
    ("wcc", "algo.WCC", "connected components, islands"),
    ("maxflow", "algo.maxFlow", "capacity between continents"),
    ("copy", "GRAPH.COPY", "instant sandbox universes"),
    ("mutation", "Live mutation", "cuts, reservations, rebinds"),
    ("index", "Edge indexes", "index-backed lookups"),
    ("text2cypher", "Text to Cypher", "questions answered by the graph"),
]

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("sppaths", re.compile(r"algo\.SPpaths", re.I)),
    ("betweenness", re.compile(r"algo\.betweenness", re.I)),
    ("wcc", re.compile(r"algo\.WCC", re.I)),
    ("maxflow", re.compile(r"algo\.maxFlow", re.I)),
    ("index", re.compile(r"CREATE\s+INDEX", re.I)),
    ("mutation", re.compile(r"\b(SET|CREATE|DELETE|MERGE)\b", re.I)),
    ("traversal", re.compile(r"-\[", re.I)),
]


def classify(cypher: str) -> str:
    for kind, rx in _RULES:
        if rx.search(cypher):
            return kind
    return "traversal"


def _label(cypher: str) -> str:
    line = " ".join(cypher.split())
    return line[:140]


class Telemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats = {k: {"calls": 0, "engine_ms": 0.0, "last_ms": 0.0, "last_at": 0.0} for k, _, _ in CAPABILITIES}
        self._recent: deque[dict[str, Any]] = deque(maxlen=8)
        self._window: deque[float] = deque()
        self._version = 0
        self._total_calls = 0
        self._total_engine = 0.0

    def record(self, cypher: str, engine_ms: float, rows: int, kind: str | None = None) -> None:
        kind = kind or classify(cypher)
        now = time.time()
        with self._lock:
            st = self._stats.setdefault(kind, {"calls": 0, "engine_ms": 0.0, "last_ms": 0.0, "last_at": 0.0})
            st["calls"] += 1
            st["engine_ms"] += engine_ms
            st["last_ms"] = engine_ms
            st["last_at"] = now
            # every index-backed lookup also counts toward the index capability
            if kind != "index" and re.search(r"\{(code|id|cable_id|srlg_id|span_id|status):", cypher):
                ix = self._stats["index"]
                ix["calls"] += 1
                ix["last_at"] = now
            self._recent.appendleft({"kind": kind, "query": _label(cypher), "engine_ms": round(engine_ms, 3),
                                     "rows": rows, "at": now})
            self._window.append(now)
            self._total_calls += 1
            self._total_engine += engine_ms
            self._version += 1

    @property
    def version(self) -> int:
        return self._version

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            while self._window and now - self._window[0] > 1.0:
                self._window.popleft()
            return {
                "capabilities": [
                    {"key": k, "name": name, "does": does, "calls": self._stats[k]["calls"],
                     "engine_ms": round(self._stats[k]["engine_ms"], 2),
                     "last_ms": round(self._stats[k]["last_ms"], 3),
                     "age_s": round(now - self._stats[k]["last_at"], 2) if self._stats[k]["last_at"] else None}
                    for k, name, does in CAPABILITIES
                ],
                "recent": list(self._recent),
                "queries_per_s": len(self._window),
                "total_calls": self._total_calls,
                "total_engine_ms": round(self._total_engine, 1),
                "server_time": now,
            }


telemetry = Telemetry()

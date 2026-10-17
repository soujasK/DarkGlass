"""FalkorDB connection pool and timed query execution.

Every query that the agent tools run goes through `run()`, which records both
the engine-internal execution time reported by FalkorDB (the GraphBLAS work)
and the client wall-clock time (engine + Redis round trip + result parsing).
The UI displays both; neither number is synthesized.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import redis
from falkordb import FalkorDB, Graph

from app.core.config import get_settings

_lock = threading.Lock()
_db: FalkorDB | None = None


def get_db() -> FalkorDB:
    """Process-wide FalkorDB client backed by a bounded, blocking Redis connection pool."""
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                s = get_settings()
                pool = redis.BlockingConnectionPool(
                    host=s.falkordb_host,
                    port=s.falkordb_port,
                    password=s.falkordb_password,
                    max_connections=s.falkordb_pool_size,
                    timeout=10,
                    decode_responses=True,
                )
                _db = FalkorDB(connection_pool=pool)
    return _db


_graph_override: ContextVar[str | None] = ContextVar("graph_override", default=None)


def get_graph(name: str | None = None) -> Graph:
    return get_db().select_graph(name or _graph_override.get() or get_settings().graph_name)


def copy_graph(src: str, dst: str, wait_s: float = 30.0) -> None:
    """GRAPH.COPY, retried while the server is busy forking.

    The copy runs in a forked child, and Redis allows one child at a time: when an automatic
    background save holds the slot, the copy fails with "could not fork" until the save ends.
    """
    deadline = time.monotonic() + wait_s
    while True:
        try:
            get_db().connection.execute_command("GRAPH.COPY", src, dst)
            return
        except Exception as exc:
            if "could not fork" not in str(exc) or time.monotonic() > deadline:
                raise
            time.sleep(0.25)


@contextmanager
def use_graph(name: str):
    """Point every run() in this context at another graph, e.g. a GRAPH.COPY sandbox."""
    token = _graph_override.set(name)
    try:
        yield
    finally:
        _graph_override.reset(token)


@dataclass
class TimedResult:
    rows: list[dict[str, Any]]
    engine_ms: float  # FalkorDB "Query internal execution time"
    wall_ms: float  # client-observed round trip
    cypher: str
    params: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def trace(self, label: str) -> dict[str, Any]:
        """Serializable record of this query for the Curator's Drawer."""
        return {
            "label": label,
            "cypher": self.cypher.strip(),
            "params": self.params,
            "engine_ms": round(self.engine_ms, 4),
            "wall_ms": round(self.wall_ms, 4),
            "rows": len(self.rows),
            "stats": self.stats,
        }


_STAT_KEYS = (
    "nodes_created",
    "nodes_deleted",
    "relationships_created",
    "relationships_deleted",
    "properties_set",
)


def run(
    cypher: str,
    params: dict[str, Any] | None = None,
    *,
    read_only: bool = False,
    graph: Graph | None = None,
) -> TimedResult:
    g = graph or get_graph()
    params = params or {}
    t0 = time.perf_counter()
    res = g.ro_query(cypher, params) if read_only else g.query(cypher, params)
    wall = (time.perf_counter() - t0) * 1000.0
    header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    rows = [dict(zip(header, r)) for r in res.result_set]
    stats = {}
    for key in _STAT_KEYS:
        val = getattr(res, key, 0) or 0
        if val:
            stats[key] = val
    from app.core.telemetry import telemetry

    telemetry.record(cypher, float(res.run_time_ms or 0.0), len(rows))
    return TimedResult(
        rows=rows,
        engine_ms=float(res.run_time_ms or 0.0),
        wall_ms=wall,
        cypher=cypher,
        params=params,
        stats=stats,
    )


def ping() -> bool:
    try:
        return bool(get_db().connection.ping())
    except Exception:
        return False

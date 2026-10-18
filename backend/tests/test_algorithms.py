"""FalkorDB's graph algorithms checked against NetworkX on the same edges (read-only, live FalkorDB).

The README and the blog claim that the agent's centrality and shortest-path answers match an
independent implementation. These tests make that claim checkable:
    pytest -q tests/test_algorithms.py
"""

from __future__ import annotations

import networkx as nx
import pytest

from app.core.database import ping, run

pytestmark = pytest.mark.skipif(not ping(), reason="FalkorDB is not running")

BETWEENNESS = """
CALL algo.betweenness({nodeLabels: ['LandingStation'], relationshipTypes: ['OPTICAL_SPAN'],
                       samplingSize: $n, samplingSeed: 42})
YIELD node, score
RETURN node.code AS code, score
"""


def _rank_correlation(a: dict[str, float], b: dict[str, float]) -> float:
    """Spearman's rho without scipy: Pearson correlation of the two rank vectors."""
    keys = sorted(set(a) & set(b))

    def ranks(d):
        order = sorted(keys, key=lambda k: -d[k])
        pos = {k: i for i, k in enumerate(order)}
        return [pos[k] for k in keys]

    ra, rb = ranks(a), ranks(b)
    n = len(keys)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    var_a = sum((x - ma) ** 2 for x in ra)
    var_b = sum((y - mb) ** 2 for y in rb)
    return cov / (var_a * var_b) ** 0.5


def _top(d: dict[str, float], k: int = 10) -> list[str]:
    return sorted(d, key=d.get, reverse=True)[:k]


@pytest.fixture(scope="module")
def optical():
    """The surviving optical layer exactly as the algorithms see it."""
    stations = [r["code"] for r in run("MATCH (s:LandingStation) RETURN s.code AS code", read_only=True, track=False).rows]
    edges = run(
        "MATCH (a:LandingStation)-[r:OPTICAL_SPAN]->(b:LandingStation) RETURN a.code AS a, b.code AS b, r.latency_ms AS w",
        read_only=True, track=False,
    ).rows
    g = nx.DiGraph()
    g.add_nodes_from(stations)
    for e in edges:  # parallel spans between two stations: keep the fastest, as a shortest path would
        if not g.has_edge(e["a"], e["b"]) or e["w"] < g[e["a"]][e["b"]]["w"]:
            g.add_edge(e["a"], e["b"], w=e["w"])
    return g


@pytest.fixture(scope="module")
def reference(optical):
    return nx.betweenness_centrality(optical, normalized=False)


def _falkordb_betweenness(n: int) -> dict[str, float]:
    return {r["code"]: r["score"] for r in run(BETWEENNESS, {"n": n}, read_only=True, track=False).rows}


def test_exact_betweenness_ranks_like_networkx(optical, reference):
    ours = _falkordb_betweenness(optical.number_of_nodes())
    assert set(_top(ours)) == set(_top(reference))
    assert _rank_correlation(ours, reference) > 0.98


def test_agent_sample_finds_the_same_choke_points(reference):
    """The agent samples 32 sources for speed; the stations it flags must still be the real hot spots."""
    ours = _falkordb_betweenness(32)
    assert _top(ours, 1) == _top(reference, 1)
    assert len(set(_top(ours)) & set(_top(reference))) >= 8
    assert _rank_correlation(ours, reference) > 0.95


@pytest.mark.parametrize("src,dst", [("IAD", "DUB"), ("DUB", "IAD")])
def test_shortest_path_latency_matches_dijkstra(optical, src, dst):
    row = run(
        """
        MATCH (s:LandingStation {code: $src}), (t:LandingStation {code: $dst})
        CALL algo.SPpaths({sourceNode: s, targetNode: t, relTypes: ['OPTICAL_SPAN'], weightProp: 'latency_ms', pathCount: 1})
        YIELD pathWeight
        RETURN pathWeight
        """,
        {"src": src, "dst": dst}, read_only=True, track=False,
    ).rows[0]
    assert row["pathWeight"] == pytest.approx(nx.dijkstra_path_length(optical, src, dst, weight="w"), rel=1e-9)

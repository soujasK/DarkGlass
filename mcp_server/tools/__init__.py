"""The four DarkGlass agent tools. Each executes compiled Cypher against FalkorDB
and returns its result plus a `cypher_trace` (exact queries, parameters and
measured engine / wall-clock timings) for the Curator's Drawer."""

from mcp_server.tools.blast_radius import trace_severed_blast_radius
from mcp_server.tools.centrality_auditor import audit_secondary_choke_points
from mcp_server.tools.detour_finder import compute_optimal_optical_detour
from mcp_server.tools.spectrum_allocator import execute_spectrum_reallocation

__all__ = [
    "trace_severed_blast_radius",
    "compute_optimal_optical_detour",
    "audit_secondary_choke_points",
    "execute_spectrum_reallocation",
]


def summarize_timing(traces: list[dict]) -> dict[str, float]:
    return {
        "engine_ms": round(sum(t["engine_ms"] for t in traces), 4),
        "wall_ms": round(sum(t["wall_ms"] for t in traces), 4),
    }

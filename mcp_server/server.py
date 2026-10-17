"""DarkGlass FastMCP server: four mission-critical tools over FalkorDB.

Run over stdio (default, for MCP clients such as Claude Desktop / Claude Code):
    python -m mcp_server.server
Run over streamable HTTP:
    python -m mcp_server.server --http --port 8765
"""

from __future__ import annotations

import argparse
from typing import Any

import mcp_server  # noqa: F401 - puts backend/ on sys.path
from mcp.server.fastmcp import FastMCP

from mcp_server import tools

mcp = FastMCP(
    "darkglass",
    instructions=(
        "Cross-layer optical restoration tools for a geo-distributed AI supercluster. The FalkorDB graph "
        "`darkglass_core` holds TeleGeography subsea cables and landing stations (Layer 1) joined to "
        "training jobs, NCCL rings and logical tunnels (Layer 7). Typical order: "
        "trace_severed_blast_radius -> compute_optimal_optical_detour -> audit_secondary_choke_points -> "
        "execute_spectrum_reallocation."
    ),
)


@mcp.tool()
def trace_severed_blast_radius(cable_id: str) -> dict[str, Any]:
    """Trace the upward blast radius of a severed or degraded subsea cable into Layer 7: affected training
    jobs, NCCL rings and logical tunnels, the compromised Shared Risk Link Group, and other tunnels that
    share that SRLG. cable_id is a cable system id such as 'dunant'."""
    return tools.trace_severed_blast_radius(cable_id)


@mcp.tool()
def compute_optimal_optical_detour(
    src_station_code: str,
    dst_station_code: str,
    required_bandwidth_tbps: float,
    max_latency_ceiling_ms: float,
    excluded_srlg_id: str,
) -> dict[str, Any]:
    """Latency-bounded optical pathfinding with FalkorDB algo.SPpaths. Spans in excluded_srlg_id and spans
    without required_bandwidth_tbps of headroom are masked in-engine before the search. Returns the best
    path (station hops, cables, total one-way latency) plus up to three ranked candidates."""
    return tools.compute_optimal_optical_detour(
        src_station_code, dst_station_code, required_bandwidth_tbps, max_latency_ceiling_ms, excluded_srlg_id
    )


@mcp.tool()
def audit_secondary_choke_points(station_codes: list[str]) -> dict[str, Any]:
    """Betweenness centrality (FalkorDB algo.betweenness) across the surviving optical graph for the given
    candidate detour stations. Flags stations at or above the graph's 95th (ELEVATED) and 99th (CRITICAL)
    percentile as amplifier-saturation risks."""
    return tools.audit_secondary_choke_points(station_codes)


@mcp.tool()
def execute_spectrum_reallocation(
    hop_sequence: list[dict],
    spectrum_slice_tbps: float,
    traffic_class: str,
    tunnel_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Atomically reserve spectrum_slice_tbps on every hop ({from, to, cable} objects), demote BULK_BACKUP
    tunnels sharing those cables, optionally rebind tunnel_ids onto the new route, and return a simulated
    flex-grid ROADM configuration payload."""
    return tools.execute_spectrum_reallocation(hop_sequence, spectrum_slice_tbps, traffic_class, tunnel_ids)


def main() -> None:
    ap = argparse.ArgumentParser(description="DarkGlass FastMCP server")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.http:
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()

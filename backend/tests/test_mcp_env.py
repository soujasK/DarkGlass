"""The agents start the MCP tool server as a child process; it must see the same settings as the API.

Regression: the MCP library starts stdio servers with a minimal environment, so on a deployment the
tools looked for FalkorDB on localhost instead of DARKGLASS_FALKORDB_HOST and every Gemini fix failed.
"""

from __future__ import annotations

import asyncio
import os

import app  # noqa: F401 - puts the repository root on sys.path for mcp_server
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from mcp_server import server_params


def test_server_params_pass_the_environment(monkeypatch):
    monkeypatch.setenv("DARKGLASS_FALKORDB_HOST", "falkordb-test-host")
    assert server_params().env["DARKGLASS_FALKORDB_HOST"] == "falkordb-test-host"


def test_the_tool_server_uses_the_configured_falkordb(monkeypatch):
    """Point the child at a port nothing listens on: its error must name that port, not the default."""
    monkeypatch.setenv("DARKGLASS_FALKORDB_PORT", "6399")

    async def call() -> str:
        async with stdio_client(server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("trace_severed_blast_radius", {"cable_id": "dunant"})
                return " ".join(getattr(c, "text", "") for c in result.content)

    text = asyncio.run(asyncio.wait_for(call(), timeout=60))
    assert "6399" in text, text[:200]
    assert os.environ.get("DARKGLASS_FALKORDB_PORT") == "6399"

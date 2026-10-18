"""DarkGlass MCP server package.

Tool implementations share the backend's FalkorDB connection pool and settings,
so the backend directory is placed on sys.path here.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def server_params():
    """How the agents start this MCP server over stdio.

    The full environment is passed on purpose: the MCP library otherwise starts the server with a minimal
    one, and the server would lose settings such as DARKGLASS_FALKORDB_HOST and look for FalkorDB on
    localhost (which only happens to work on a developer machine).
    """
    import os

    from mcp.client.stdio import StdioServerParameters

    return StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"],
                                 cwd=str(Path(__file__).resolve().parents[1]), env=dict(os.environ))

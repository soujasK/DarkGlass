"""DarkGlass backend package.

The repository root is added to sys.path so the backend can import the
sibling `mcp_server` package (the FastMCP tool implementations are shared
between the MCP server and the FastAPI control plane).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

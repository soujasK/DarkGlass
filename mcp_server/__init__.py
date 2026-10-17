"""DarkGlass MCP server package.

Tool implementations share the backend's FalkorDB connection pool and settings,
so the backend directory is placed on sys.path here.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

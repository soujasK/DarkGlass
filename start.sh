#!/usr/bin/env bash
# DarkGlass: one-command start (macOS / Linux).   ./start.sh
# First run installs dependencies and builds the graph (a few minutes); later runs start in seconds.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/backend/.venv/bin/python"

echo "1/5  FalkorDB (docker compose)"
docker compose -f "$ROOT/docker-compose.yml" up -d >/dev/null

echo "2/5  Backend dependencies"
[ -x "$PY" ] || python3 -m venv "$ROOT/backend/.venv"
"$PY" -m pip install -q -r "$ROOT/backend/requirements.txt"

echo "3/5  Data"
cd "$ROOT/backend"
[ -f "$ROOT/data/cables.geojson" ] || "$PY" -m app.ingestion.fetch_telegeography
[ -f "$ROOT/data/graph/topology.json" ] || "$PY" -m app.ingestion.import_telegeography --no-load
# The backend's startup self-check loads the graph into FalkorDB and seeds the workloads when needed.

echo "4/5  Frontend"
cd "$ROOT/frontend"
[ -d node_modules ] || npm install --no-audit --no-fund
[ -f .next/BUILD_ID ] || npm run build

echo "5/5  Starting servers (Ctrl+C stops both)"
(cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app --port 8010) &
(cd "$ROOT/frontend" && npm start) &
trap 'kill 0' INT TERM
echo "DarkGlass: http://localhost:3000   API: http://localhost:8010   FalkorDB browser: http://localhost:3001"
wait

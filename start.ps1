# DarkGlass: one-command start (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
# First run installs dependencies and builds the graph (a few minutes); later runs start in seconds.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

Write-Host "1/5  FalkorDB (docker compose)"
docker compose -f (Join-Path $root "docker-compose.yml") up -d | Out-Null

Write-Host "2/5  Backend dependencies"
if (-not (Test-Path $py)) {
    python -m venv (Join-Path $backend ".venv")
    & $py -m pip install -q --upgrade pip
}
& $py -m pip install -q -r (Join-Path $backend "requirements.txt")

Write-Host "3/5  Data"
Push-Location $backend
if (-not (Test-Path (Join-Path $root "data\cables.geojson"))) { & $py -m app.ingestion.fetch_telegeography }
if (-not (Test-Path (Join-Path $root "data\graph\topology.json"))) { & $py -m app.ingestion.import_telegeography --no-load }
Pop-Location
# The backend's startup self-check loads the graph into FalkorDB and seeds the workloads when needed.

Write-Host "4/5  Frontend"
Push-Location $frontend
if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
if (-not (Test-Path ".next\BUILD_ID")) { npm run build }
Pop-Location

Write-Host "5/5  Starting servers"
Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8010" -WorkingDirectory $backend
Start-Process -FilePath "npm.cmd" -ArgumentList "start" -WorkingDirectory $frontend
Start-Sleep -Seconds 6
Start-Process "http://localhost:3000"
Write-Host "DarkGlass is running: http://localhost:3000  (API http://localhost:8010, FalkorDB browser http://localhost:3001)"

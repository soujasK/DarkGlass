# DarkGlass

**Self-healing networks for AI supercomputers, running on FalkorDB.**
WeMakeDevs x FalkorDB Graph Hacks, Track 01: Agents That Act on Connected Data.

![Training restored](docs/screenshots/3-restored.png)

A 65,536-GPU training run spans Ashburn and Dublin. It syncs every 45 ms over undersea fibre, so
when a ship's anchor cuts one cable, every GPU freezes and the job burns about $5 a second.
DarkGlass keeps the real subsea network (603 cables and 1,788 landing stations from TeleGeography)
and the AI supercluster down to every GPU (jobs, sync rings, tunnels, border routers, Clos switches,
servers, NICs, GPUs, racks and halls: about 170,000 nodes) in **one FalkorDB graph**. An agent works
out who is hurt with an 11-hop traversal from the seabed to the GPU, finds the fastest detour that
avoids the damaged trench, checks it for bottlenecks, and moves the traffic. The fix takes about 0.3 s.

## What you see

| | |
|---|---|
| ![Start](docs/screenshots/1-start.png) | **Incident.** Pick any of the 603 cables from the searchable list and cut it. |
| ![Cut](docs/screenshots/2-cable-cut.png) | **Input.** The cable goes dark, a counter shows the money burning, and FalkorDB recalls past failures of this cable from its incident memory. |
| ![Restored](docs/screenshots/3-restored.png) | **Output.** The agent (a deterministic playbook, or Gemini 4 Argon choosing the queries itself) restores training. The graph view shows the 11-hop path FalkorDB walked, the GPUs, servers, racks and halls behind the cut, and the tunnel's `CARRIED_BY` edge moving to the new cable. |
| ![Planet scan](docs/screenshots/4-planet-scan-running.png) | **Planet scan.** FalkorDB cuts every one of the 603 cables, one at a time, in four parallel sandbox copies of the graph. The globe becomes a risk heat map. |
| ![Scan result](docs/screenshots/5-planet-scan.png) | The worst cables for the planet (countries cut off), for transatlantic capacity, and for our GPUs. |
| ![Ask](docs/screenshots/6-ask-the-graph.png) | **Ask the graph.** Gemini writes the Cypher, FalkorDB answers in milliseconds, and the answer lights up on the globe. |
| ![Proof](docs/screenshots/7-proof.png) | **Proof.** The same question on the same data, run in FalkorDB, Python NetworkX and SQL. |

The bar along the bottom of every screen is the **live FalkorDB engine**. Each capability lights up
when FalkorDB actually runs it, with real call counts taken from query telemetry.

## Why FalkorDB is the core of this

| FalkorDB capability | What it does here |
|---|---|
| Multi-hop Cypher traversal | "Who is hurt?": one 11-hop path from landing station to cable, tunnel, border router, super-spine, spine, leaf, server, NIC, GPU, sync ring and job, plus a sweep that locates every affected GPU to its server, rack, hall and campus (32,768 GPUs for Dunant in about 0.15 s) |
| `algo.SPpaths` | The fastest detour that avoids the damaged trench and has enough free capacity |
| `algo.betweenness` | Checks the detour for overloaded relay stations |
| `algo.WCC` | Planet scan: which stations and countries are cut off after each failure |
| `algo.maxFlow` | Planet scan: total North America to Europe capacity left after each failure (multi-source, multi-sink) |
| `GRAPH.COPY` | Planet scan: four sandbox copies of the planet run in parallel; production is never touched |
| Live mutations | Cuts, atomic all-or-nothing bandwidth reservation, tunnel rebinding, scan results written back |
| Edge indexes | Index-backed lookups on cable, risk group, status and capacity |
| Graph as memory | Each resolved incident is stored as `(:Incident)-[:SEVERED]->(:CableSystem)` and `-[:REROUTED_VIA]->`. The agent recalls it the next time that cable fails |
| Text to Cypher | Ask the graph: Gemini 4 Argon writes Cypher, which runs under `GRAPH.RO_QUERY`, so a generated query can never write |

## Graph data model

One graph, `darkglass_core`, holds four layers. About 170,000 nodes and 270,000 relationships.

```
Physical plant (TeleGeography)
(:LandingStation {code, name, country, lat, lon})-[:OPTICAL_SPAN {cable_id, srlg_id, length_km, latency_ms,
    capacity_tbps, available_capacity_tbps, route_weight, status}]->(:LandingStation)
(:LandingStation)-[:LANDS]->(:CableSystem {id, name, rfs_year, length_km, owners})
A cut retypes a cable's spans to :SEVERED_SPAN.

Workloads
(:TrainingJob)-[:COORDINATES]->(:NCCL_Ring)-[:USES_TUNNEL]->(:LogicalTunnel)-[:CARRIED_BY]->(:CableSystem)

Compute fabric (Ashburn IAD and Dublin DUB)
(:LogicalTunnel)-[:TERMINATES_AT]->(:BorderRouter)-[:FEEDS]->(:SuperSpine)-[:FEEDS]->(:Spine)
    -[:FEEDS]->(:Leaf)-[:FEEDS]->(:Server)-[:HAS_NIC]->(:NIC)-[:ATTACHED_TO]->(:GPU)-[:MEMBER_OF]->(:NCCL_Ring)
(:Server)-[:IN_RACK]->(:Rack)-[:IN_ROW]->(:Row)-[:IN_HALL]->(:DataHall)-[:IN_CAMPUS]->(:LandingStation)

Memory and analysis
(:Incident)-[:SEVERED]->(:CableSystem), (:Incident)-[:REROUTED_VIA]->(:CableSystem)
(:ResilienceScan)-[:RANKED {rank, risk}]->(:CableSystem)
:FLOW_LINK  derived whole-Gbps layer for algo.maxFlow
```

The fabric's `FEEDS` edges are each device's primary uplink, so every GPU has exactly one path to
its border routers. Real Clos fabrics are multi-pathed; the tree is the forwarding view a blast
radius needs, and it keeps the 11-hop query exact.

## Cypher queries and algorithms

| Where | What |
|---|---|
| `mcp_server/tools/blast_radius.py` | 11-hop `PATH` traversal, the GPU `IMPACT` sweep, shared-trench exposure, incident memory recall |
| `mcp_server/tools/detour_finder.py` | Route-weight masking, then `algo.SPpaths` (k=3, weighted, capacity-checked), then restore |
| `mcp_server/tools/centrality_auditor.py` | `algo.betweenness` (32-source sample, fixed seed) |
| `mcp_server/tools/spectrum_allocator.py` | Atomic capacity reservation, backup demotion, `CARRIED_BY` rebinding |
| `backend/app/services/resilience_scan.py` | `GRAPH.COPY` sandboxes, `algo.WCC`, `algo.maxFlow` |
| `backend/app/services/chaos_engine.py` | Cuts (edge retyping), restores, incident memory writes |
| `backend/app/services/ask_graph.py` | Text to Cypher, run under `GRAPH.RO_QUERY` |
| `backend/app/ingestion/` | Bulk `UNWIND` loads, node and edge indexes |

## Measured results

From `python -m app.services.benchmark` on the development machine. Every number in the UI comes
from the latest run; none are hardcoded.

The workload: find the 3 fastest paths Ashburn to Dublin that avoid the damaged Virginia Beach
trench, with at least 3.2 Tbps free on every hop and a 50 ms ceiling. All three engines load the
same `topology.json` (1,790 nodes, 8,900 edges). A sha256 of the edge set, read back from each
engine, confirms they hold identical data, and all three return the same 30.4075 ms route.

| Engine | Task | p50 |
|---|---|---|
| **FalkorDB** `algo.SPpaths` (engine time) | detour search | **0.80 ms** |
| FalkorDB, client wall time incl. network | detour search | 2.04 ms |
| Python NetworkX 3.6 (Yen k-shortest) | detour search | 34.0 ms |
| SQLite recursive CTE (up to 12 hops) | detour search | 1,770 ms |
| **FalkorDB** `algo.betweenness`, 32 sources | bottleneck audit | **22 ms** |
| NetworkX betweenness, 32 sources | bottleneck audit | 131 ms |
| FalkorDB / NetworkX, exact betweenness | bottleneck audit | 830 ms / 7,510 ms |

The SQL time grows exponentially with path length. At 4, 6, 8, 10 and 12 hops it takes 0.8, 4.4,
34.6, 323 and 1,770 ms, and below 8 hops it misses the real best route.

One caveat: the 0.8 ms figure is the search alone. The full detour tool, including masking and
restoring about 1,200 low-capacity links, takes about 50 ms.

Planet scan: 603 failures in about 15 s on the development machine. Each scan runs roughly 600 WCC
calls, 300 max-flow runs, 1,800 mutations and 3,000 index lookups.

## Run it

Prerequisites: Docker, Python 3.11+, Node 20+.

```bash
powershell -ExecutionPolicy Bypass -File .\start.ps1     # Windows
./start.sh                                                # macOS / Linux
```

This one command starts FalkorDB, installs whatever is missing, downloads and builds the data on
the first run (a few minutes), and opens http://localhost:3000. On every start, the backend
self-checks: it loads the graph if FalkorDB is empty and restores any incident a previous process
left half-finished. FalkorDB data lives on a Docker volume.

Ports: frontend 3000, API 8010, FalkorDB 6390, FalkorDB browser 3001. They are deliberately not
8000 or 6379, to avoid clashing with local services.

**Gemini 4 Argon (optional).** It runs on Google Cloud Vertex AI with your gcloud login; no API key
is stored. Run `gcloud auth application-default login`, then set `DARKGLASS_VERTEX_PROJECT_ID` (see
`backend/.env.example`). Without it, everything works except Ask the graph and the Gemini agent
option.

**Tests** (FalkorDB must be running):

```bash
cd backend
pytest -q              # 28 unit and end-to-end tests, about 20 s
pytest -q -m slow      # also the full 603-cable planet scan
```

The tests cover:
- the lifecycle of every cable class
- detours that avoid the damaged trench
- invalid actions being rejected
- read-only enforcement
- recovery after a restart mid-incident
- incident memory
- the cable catalog
- the 11-hop traversal and GPU impact counts

**Manual setup**: `docker compose up -d`; then, in `backend/`, create a venv and
`pip install -r requirements.txt`, run `python -m app.ingestion.fetch_telegeography` and
`python -m app.ingestion.import_telegeography`, then `uvicorn app.main:app --port 8010`. In
`frontend/`, run `npm install && npm run build && npm start`.

## The agent and the MCP server

The four agent tools are plain functions that run Cypher. They are exposed both to the web app and
as a **FastMCP server** (`python -m mcp_server.server`), so any MCP client can call them.

| Tool | FalkorDB work |
|---|---|
| `trace_severed_blast_radius` | 11-hop traversal from station to GPU and job, GPU/server/rack impact sweep, shared-trench exposure, and incident memory recall |
| `compute_optimal_optical_detour` | Mask the trench and full links, run `algo.SPpaths`, then restore |
| `audit_secondary_choke_points` | `algo.betweenness`, graded against the graph's own 95th and 99th percentiles |
| `execute_spectrum_reallocation` | Atomic bandwidth reservation, demoting backup traffic, rebinding the tunnel, and a simulated ROADM configuration |

There are two ways to run the agent:
- **Playbook:** a deterministic loop.
- **Gemini 4 Argon:** the model chooses which tools to call and with what arguments, then writes an
  operator summary.

Claude (Anthropic API or Vertex) is also supported in `mcp_server/agent_runner.py`.

```json
{ "mcpServers": { "darkglass": { "command": "<repo>/backend/.venv/Scripts/python",
  "args": ["-m", "mcp_server.server"], "cwd": "<repo>" } } }
```

## Engineering notes

These behaviours were found by testing against FalkorDB 4.22.

- **The trench is masked by weight, not by a path bound.** `maxLen`/`maxCost` bounds on
  `algo.SPpaths` either returned false "no path" results or timed out on this graph. So blocked
  links get a 1e6 route weight before the search and are restored after it, and a Python check
  confirms the isolation independently.
- **Cuts retype edges.** A cut cable's spans become `:SEVERED_SPAN`, so routing and centrality run
  over surviving glass only.
- **The centrality procedure is `algo.betweenness`.** The audit uses a 32-source sample with a
  fixed seed.
- **Max-flow needs a derived layer.** `algo.maxFlow` rejects parallel edges and capacity ranges
  wider than 2^32, so the scan runs it on a derived `:FLOW_LINK` layer in whole Gbps.

## Data: what is real and what is modelled

| | |
|---|---|
| **Real** (TeleGeography, CC BY-NC-SA 3.0) | Cables, landing stations, coordinates, routes, length, in-service year, owners |
| **Measured** | Span lengths along the real route polylines; latency = length x 4.897 us/km |
| **Modelled** | The compute fabric (H100 servers, ConnectX-7 NICs, Clos switches, racks, halls), capacity (published figures for Dunant and MAREA, an era model for the rest), utilisation, overland backhaul, risk groups, the AI jobs, the $18,420/hr cost, the 3-minute manual-reroute comparison, and ROADM commands |

TeleGeography data is CC BY-NC-SA 3.0. Attribute it, and do not use it commercially.

## Layout

```
backend/app/core/        config, pooled FalkorDB client, query telemetry
backend/app/ingestion/   TeleGeography fetch, topology build, workload seeding
backend/app/services/    chaos engine (cuts, memory), planet scan, ask-the-graph, benchmark, startup self-check
backend/app/api/         REST and WebSocket
backend/tests/           unit and end-to-end tests
mcp_server/              FastMCP server, the four tools, playbook and LLM agents
frontend/src/            Next.js + react-three-fiber globe, mode screens, FalkorDB engine bar
```

## AI assistance

This project was built with help from AI coding assistants (Anthropic's Claude, via Claude Code),
which wrote much of the code and documentation under the author's direction. At runtime, Gemini 4
Argon on Vertex AI is optional: it powers the LLM agent mode and Ask the graph. Everything else is
deterministic code and FalkorDB queries.

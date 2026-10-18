# DarkGlass

**Self-healing networks for AI supercomputers, running on FalkorDB.**
WeMakeDevs x FalkorDB Graph Hacks, Track 01: Agents That Act on Connected Data.

![Training restored](docs/screenshots/3-restored.png)

A 65,536-GPU training run spans Ashburn and Dublin. It syncs every 45 ms over undersea fibre, so
when a ship's anchor cuts one cable, every GPU freezes and the job burns about $5 a second.
DarkGlass keeps the real subsea network (603 cables and 1,788 landing stations from TeleGeography)
and the AI supercluster down to every GPU (jobs, sync rings, tunnels, border routers, Clos switches,
servers, NICs, GPUs, racks and halls: about 166,000 nodes) in **one FalkorDB graph**. An agent works
out who is hurt with an 11-hop traversal from the seabed to the GPU, finds the fastest detour that
avoids the damaged trench, checks it for bottlenecks, and moves the traffic. FalkorDB's part of the
fix takes under half a second; with Gemini choosing the tools, the whole fix takes 35 to 50 s.

## What you see

| | |
|---|---|
| ![Start](docs/screenshots/1-start.png) | **Incident.** Pick any of the 603 cables from the searchable list and cut it. |
| ![Cut](docs/screenshots/2-cable-cut.png) | **Input.** The cable goes dark, a counter shows the money burning, and FalkorDB recalls past failures of this cable from its incident memory. |
| ![Restored](docs/screenshots/3-restored.png) | **Output.** The agent (Gemini 3.8 Flash choosing the queries itself over MCP, or a deterministic playbook) restores training. The graph view shows the 11-hop path FalkorDB walked, the GPUs, servers, racks and halls behind the cut, and the tunnel's `CARRIED_BY` edge moving to the new cable. |
| ![Planet scan](docs/screenshots/4-planet-scan-running.png) | **Planet scan.** FalkorDB cuts every one of the 603 cables, one at a time, in four parallel sandbox copies of the graph. The globe becomes a risk heat map. |
| ![Scan result](docs/screenshots/5-planet-scan.png) | The worst cables for the planet (landing stations stranded from the subsea network), for transatlantic capacity, and for our GPUs (GPUs whose gradient sync would freeze). |
| ![Ask](docs/screenshots/6-ask-the-graph.png) | **Ask the graph.** Gemini writes the Cypher, FalkorDB answers in milliseconds, and the answer lights up on the globe. |
| ![Proof](docs/screenshots/7-proof.png) | **Proof.** The cut you just made, answered live in FalkorDB, Python NetworkX and SQL on the same hash-checked snapshot. |

The bar along the bottom of every screen is the **live FalkorDB engine**. Each capability lights up
when FalkorDB actually runs it, with real call counts taken from query telemetry.

## Why FalkorDB is the core of this

> **Why it has to be a graph.** "Who is hurt" is one 11-hop path across four layers, from the seabed
> through the cable, tunnel and switch fabric down to the GPU and its job: a single Cypher pattern,
> or 11 joins in SQL. "Where do we move it" is worse. The safest detour has no fixed depth, so SQL
> needs a recursive search: it takes 1,770 ms when capped at 12 hops, misses the best route below 8,
> and does not finish in 10 s without a cap. FalkorDB's `algo.SPpaths` answers in 0.8 ms.

| FalkorDB capability | What it does here |
|---|---|
| Multi-hop Cypher traversal | "Who is hurt?": one 11-hop path from landing station to cable, tunnel, border router, super-spine, spine, leaf, server, NIC, GPU, sync ring and job, plus a sweep that locates every affected GPU to its server, rack, hall and campus (32,768 GPUs for Dunant in about 0.15 s) |
| `algo.SPpaths` | The fastest detour that avoids the damaged trench and has enough free capacity |
| `algo.betweenness` | Checks the detour for overloaded relay stations |
| `algo.WCC` | Planet scan: which landing stations fall off the main subsea network after each failure, and in which countries. A stranded station is not a country offline: inland fibre is not modelled |
| `algo.maxFlow` | Planet scan: total North America to Europe capacity left after each failure (multi-source, multi-sink) |
| `GRAPH.COPY` | Planet scan: four sandbox copies of the planet run in parallel; production is never touched |
| Live mutations | Cuts, atomic all-or-nothing bandwidth reservation, tunnel rebinding, scan results written back |
| Edge indexes | Index-backed lookups on cable, risk group, status and capacity |
| Graph as memory | Each resolved incident is stored as `(:Incident)-[:SEVERED]->(:CableSystem)` and `-[:REROUTED_VIA]->`. The agent recalls it the next time that cable fails |
| Text to Cypher | Ask the graph: Gemini 3.8 Flash writes Cypher, which runs under `GRAPH.RO_QUERY`, so a generated query can never write |

## Graph data model

One graph, `darkglass_core`, holds four layers: about 166,000 nodes and 261,000 relationships.

```
Physical plant (TeleGeography)
(:LandingStation {code, name, country_name, lat, lon})-[:OPTICAL_SPAN {cable_id, srlg_id, length_km,
    latency_ms, max_capacity_tbps, available_capacity_tbps, route_weight, status}]->(:LandingStation)
(:LandingStation)-[:LANDS]->(:CableSystem {id, name, design_capacity_tbps, ready_for_service, length_km, owners})
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

From `python -m app.services.benchmark` on the development machine. The UI shows the latest run;
nothing is hardcoded. The Proof screen also measures each cut live.

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

Planet scan: 603 failures in 15 to 50 s on the development machine, depending on load. Each scan
runs roughly 600 WCC calls, 300 max-flow runs, 1,800 mutations and 3,000 index lookups.

### Ask the graph: eval

**66 of 69 with Gemini 3.8 Flash (22 of 23 on each of 3 runs), median answer 2.3 s.** Run it with
`python -m app.services.ask_eval` (results in `data/benchmarks/ask_eval.json`).

The 23 questions in `backend/app/services/ask_eval.py` are hand-written and cover lookups (4),
aggregates (6), multi-hop traversals up to 7 hops (9), scan results and incident memory (3), and one
`algo.SPpaths` question. Each has a hand-written reference Cypher, so the expected answer is computed
from the live graph, not typed in. An answer passes only if it matches:

- **number**: the first row holds the expected value (1% tolerance for decimals);
- **set**: every expected item is present, and nothing from a "must not appear" query is. "Which jobs
  depend on Dunant" fails if it simply lists every job;
- **top**: the first row is the expected item (either one, when two are tied).

Every question runs 3 times, because the model is not deterministic. The one miss is the Ireland
capacity question: a cable can land at several Irish stations, and Flash sums it more than once
(1,026 Tbps instead of 1,022.88). We left it in rather than add a hint for it. The first Flash run
scored 59/69 because Flash used the `=~` operator, which FalkorDB does not support; the prompt now
says so.

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

**Gemini 3.8 Flash (optional).** It runs on Google Cloud Vertex AI with your gcloud login; no API key
is stored. Run `gcloud auth application-default login`, then set `DARKGLASS_VERTEX_PROJECT_ID` (see
`backend/.env.example`). `DARKGLASS_GEMINI_MODEL` sets the model for both the agent and Ask the
graph. Gemini is the default agent; without it, choose **Playbook** under Agent, and everything else
works except Ask the graph.

**Tests** (FalkorDB must be running):

```bash
cd backend
pytest -q              # 125 unit, scenario and end-to-end tests, about 2 minutes
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
- hand-picked failure scenarios (`tests/test_scenarios.py`): a cable carrying every traffic class,
  shared-trench siblings, checkpoint-only and backup-only cuts, a second cut on the detour itself,
  cuts in either order, impossible constraints, all-or-nothing reservation, and simultaneous cuts and
  fixes. After every scenario the graph is checked for leftover masks, negative capacity and orphaned
  tunnels
- the Ask-the-graph eval's reference answers and scorer

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
- **Gemini 3.8 Flash (the default):** the agent starts the FastMCP server, discovers the tools with MCP
  `list_tools`, and the model chooses which tools to call and with what arguments, then writes an
  operator summary.
- **Playbook:** a deterministic loop over the same four tools, with no model. Pick it under Agent if
  Vertex AI is not set up; a Gemini run without credentials stops with an error saying so.

The agent moves the most critical traffic the cut hurt first: gradient sync, then checkpoints, then
backup. If a cut hits several classes, the tunnels left on the cut cable are listed as `still_down`
and shown on screen.

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

## Build log

- **Oct 15–16:** chose the problem, designed the four-layer graph model, picked the TeleGeography
  data and prototyped the queries.
- **Oct 17:** first working version committed.
- **Oct 18:** testing, fixes, the Ask-the-graph eval and the switch to Gemini 3.8 Flash.

## AI assistance

**During development:** Claude Code was used as a coding assistant, under the author's direction. The
author reviewed and tested the work: every number on screen was checked against the graph, and the
test suite and the Ask-the-graph eval run against a live FalkorDB.

**At runtime, AI is used in exactly two places, and both are optional:**

- **LLM agent mode:** Gemini 3.8 Flash chooses which of the four tools to call, over MCP.
- **Ask the graph:** Gemini 3.8 Flash turns a plain-English question into a read-only Cypher query.

Everything else involves no AI model: the Playbook agent, cuts, the planet scan, the live proof, and
every number on screen. That is ordinary code that gives the same result every time, and FalkorDB
does the actual work: the 11-hop traversal, `algo.SPpaths`, `algo.betweenness`, `algo.WCC` and
`algo.maxFlow`. Even when Gemini picks the tools, the answers come from the graph: in our Gemini
runs the whole fix took 35 to 50 s, and FalkorDB's share was under half a second.

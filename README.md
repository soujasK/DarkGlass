<div align="center">

<img src="docs/media/banner.jpg" alt="DarkGlass: self-healing networks for AI supercomputers" width="100%">

<h3>An agent that heals the network under a 65,536-GPU training run, by reasoning over one FalkorDB graph.</h3>

[![Track 01](https://img.shields.io/badge/Graph_Hacks-Track_01:_Agents_That_Act_on_Connected_Data-E0B156?style=flat-square)](https://www.wemakedevs.org/hackathons/falkordb)
[![FalkorDB](https://img.shields.io/badge/FalkorDB-graph_engine-7AA7D9?style=flat-square)](https://www.falkordb.com)
[![MCP](https://img.shields.io/badge/MCP-tool_selection-93BBEA?style=flat-square)](https://modelcontextprotocol.io)
[![Gemini](https://img.shields.io/badge/Gemini_3.8_Flash-Vertex_AI-B8D0EE?style=flat-square)](#the-agent-and-the-mcp-server)
[![Tests](https://img.shields.io/badge/tests-137_passing-3FB950?style=flat-square)](#tests)

**[🌐 Live demo](https://8-234-112-123.sslip.io)** &nbsp;·&nbsp;
**[▶ 3-minute video](https://youtu.be/-fh9tKQJ4Z8)** &nbsp;·&nbsp;
**[📝 Blog: 419 milliseconds](https://darkglass-blog.onrender.com)** &nbsp;·&nbsp;
**[⚡ Run it in one command](#run-it)**

</div>

<br>

A 65,536-GPU training run spans Ashburn and Dublin. Every **45 ms** its GPUs sync over undersea fibre,
so when a ship's anchor cuts one cable, every GPU freezes and the job burns about **$5 a second**.

DarkGlass keeps the real subsea network (603 cables, 1,788 landing stations from TeleGeography) and the
AI supercluster down to every GPU in **one FalkorDB graph of about 166,000 nodes**. When a cable breaks,
an agent works out who is hurt with an **11-hop traversal from the seabed to the GPU**, finds the fastest
detour that avoids the damaged trench, checks it for bottlenecks, moves the traffic, and explains every
step with the exact Cypher behind it.

<div align="center">
<img src="docs/media/fix.gif" alt="The agent finishing its four steps and restoring training" width="88%">
<br><sub>Recorded live: the four agent steps complete, then "Fixed in 34.9 s · now on Havfrue/AEC-2, 7.7 ms faster".</sub>
</div>

<br>

## At a glance

| | |
|:--|:--|
| **One graph, four layers** | about 166,000 nodes and 261,000 relationships: subsea plant, logical tunnels, compute fabric, workloads |
| **Who is hurt** | one Cypher pattern, **11 hops**, seabed to GPU: 32,768 GPUs behind the Dunant cut in about 0.15 s |
| **Where to go** | `algo.SPpaths` finds the safe detour in **0.8 ms**; the same search in SQL takes **1,770 ms** and misses it below 8 hops |
| **Agent** | Gemini 3.8 Flash picks FalkorDB tools over **MCP**; a no-AI Playbook runs the same tools and reaches the same fix |
| **Planet scan** | every one of the 603 cables failed once, in 4 `GRAPH.COPY` sandboxes, in about a minute |
| **Verified** | 137 tests; algorithms cross-checked against NetworkX; text-to-Cypher eval 66 / 69 |

## Contents

[How it works](#how-it-works) · [Track 01 key focus](#track-01-key-focus-where-to-see-each-one) ·
[FalkorDB features](#falkordb-features-used-in-falkordbs-own-terms) · [What you see](#what-you-see) ·
[Why a graph](#why-it-has-to-be-a-graph) · [Data model](#graph-data-model) · [Queries](#cypher-queries-and-algorithms) ·
[Results](#measured-results) · [Run it](#run-it) · [Deploy](#deploy-it) · [Agent and MCP](#the-agent-and-the-mcp-server) ·
[Tests](#tests) · [Engineering notes](#engineering-notes) · [Data](#data-what-is-real-and-what-is-modelled) ·
[AI assistance](#ai-assistance)

## How it works

```mermaid
flowchart LR
    A["⚓ Cable cut<br/>spans retyped to<br/>:SEVERED_SPAN"] --> B{"Agent<br/>Gemini 3.8 Flash over MCP<br/>or the Playbook"}
    B -->|"1 · who is hurt"| T1["11-hop Cypher traversal<br/>+ GPU impact sweep"]
    B -->|"2 · where to go"| T2["algo.SPpaths<br/>trench and full links masked"]
    B -->|"3 · will it hold"| T3["algo.betweenness<br/>vs the graph's own p95 / p99"]
    B -->|"4 · move it"| T4["atomic write<br/>reserve bandwidth,<br/>move CARRIED_BY"]
    T1 & T2 & T3 & T4 --> G[("FalkorDB<br/>darkglass_core<br/>~166k nodes")]
    G --> R["✅ Training restored<br/>why this route · every query · memory"]
```

The agent reads the graph to decide, then acts on it: the detour's limits come from the traversal's
answer, a cable in the same trench is excluded because the graph says so, and the fix is written back
as graph memory, so the next failure of that cable starts from what worked last time.

## Track 01 key focus: where to see each one

Each item is labelled in the app with a small tag carrying the same name.

| Key focus | Where to see it | Code |
|---|---|---|
| **Multi-hop reasoning** | Cut a cable. The graph panel shows the 11-hop traversal from landing station to cable, tunnel, border router, three switch tiers, server, NIC, GPU, ring and job, with the GPUs, servers, racks and halls it reaches | `PATH`, `IMPACT` in `mcp_server/tools/blast_radius.py` |
| **Graph queries and algorithms as agent tools** | The four steps while the agent works, and "Tools Gemini chose" on the result: the traversal, `algo.SPpaths`, `algo.betweenness`, and an atomic write. The planet scan adds `algo.WCC`, `algo.maxFlow` and `GRAPH.COPY` | `mcp_server/tools/` |
| **Tool selection through MCP** | "Gemini 3.8 Flash is choosing tools over MCP": the agent discovers the tools with MCP `list_tools` and calls the ones it picks with `call_tool` | `mcp_server/gemini_agent.py`, `mcp_server/server.py` |
| **Actions based on what the graph shows** | "Now on Havfrue/AEC-2": MAREA is excluded because the graph puts it in the same trench, then bandwidth is reserved and the tunnel's `CARRIED_BY` edge moves. The fix is written to graph memory | `mcp_server/tools/spectrum_allocator.py`, `backend/app/services/chaos_engine.py` |
| **Explainable results** | "Why this route", each tool's exact Cypher, Gemini's summary, "Not fixed yet" for traffic left behind, "View queries" on every tab, and the Proof tab, which answers the same cut in FalkorDB, NetworkX and SQL | `frontend/src/components/hud/Chapter.tsx`, `backend/app/services/live_proof.py` |

## FalkorDB features used, in FalkorDB's own terms

The same wording is on screen in the app; times are in the [demo video](https://youtu.be/-fh9tKQJ4Z8).

| FalkorDB feature | How DarkGlass uses it | In the app | Video |
|---|---|---|---|
| **MCP** | Gemini discovers DarkGlass's four FalkorDB tools with MCP and picks each one itself | "choosing tools over MCP" | 0:37 |
| **Query with Cypher** | An 11-hop traversal finds who is hurt; writes move the traffic; Ask the graph turns English into read-only Cypher | Every tool and figure opens its exact Cypher | 0:45, 1:42, 2:25 |
| **Shortest path** | `algo.SPpaths` finds the fastest detour that avoids the damaged trench | Engine bar: "Shortest path (SPpaths)" | 0:54 |
| **Centrality** | `algo.betweenness` checks the detour for overloaded relay stations | Engine bar: "Centrality (betweenness)" | 1:02 |
| **Connected components** | `algo.WCC` finds the landing stations each failure cuts off | Engine bar: "Connected components (WCC)" | 2:11 |
| **Max flow** (beyond the list) | `algo.maxFlow` measures North America to Europe capacity after each failure | Board "Drains the Atlantic" | 2:11 |
| **Isolated graphs in one instance** | `GRAPH.COPY` makes 4 sandbox graphs for the planet scan and one for the proof; the live graph is never touched | "4 sandbox graphs in one FalkorDB instance (GRAPH.COPY)" | 1:57 |
| **Context that persists across sessions** | Each agent step writes to the graph; a reload or restart finds the incident and its result | "Saved in the graph: reload and the incident is still here" | 1:35 |
| **Explore the graph: FalkorDB Browser** | The same graph in FalkorDB Browser at http://localhost:3001 (local runs) | Drawer: "Open in FalkorDB Browser" | 1:48 |

Not used: PageRank, community detection, and the GraphRAG-SDK / LangChain / LlamaIndex style integrations.
DarkGlass talks to FalkorDB through MCP and Cypher directly.

## What you see

| | |
|---|---|
| ![Start](docs/screenshots/1-start.png) | **Incident.** Pick any of the 603 cables from the searchable list and cut it. |
| ![Cut](docs/screenshots/2-cable-cut.png) | **Input.** The cable goes dark, a counter shows the money burning, and FalkorDB recalls past failures of this cable from its incident memory. |
| ![Restored](docs/screenshots/3-restored.png) | **Output.** The agent restores training. The graph view shows the 11-hop path FalkorDB walked, the GPUs, servers, racks and halls behind the cut, and the tunnel's `CARRIED_BY` edge moving to the new cable. |
| ![Planet scan](docs/screenshots/4-planet-scan-running.png) | **Planet scan.** FalkorDB cuts every one of the 603 cables, one at a time, in four parallel sandbox copies of the graph. The globe becomes a risk heat map. |
| ![Scan result](docs/screenshots/5-planet-scan.png) | The worst cables for the planet (landing stations stranded), for transatlantic capacity, and for our GPUs. |
| ![Ask](docs/screenshots/6-ask-the-graph.png) | **Ask the graph.** Gemini writes the Cypher, FalkorDB answers in milliseconds, and the answer lights up on the globe. |
| ![Proof](docs/screenshots/7-proof.png) | **Proof.** The cut you just made, answered live in FalkorDB, Python NetworkX and SQL on the same hash-checked snapshot. |

The bar along the bottom of every screen is the **live FalkorDB engine**: each capability lights up when
FalkorDB actually runs it, with real call counts from query telemetry, and "View queries" opens the exact
queries behind the current tab.

## Why it has to be a graph

> "Who is hurt" is one 11-hop path across four layers, from the seabed through the cable, tunnel and
> switch fabric down to the GPU and its job: a single Cypher pattern, or 11 joins in SQL. "Where do we
> move it" is worse. The safest detour has no fixed depth, so SQL needs a recursive search: it takes
> 1,770 ms when capped at 12 hops, misses the best route below 8, and does not finish in 10 s without a
> cap. FalkorDB's `algo.SPpaths` answers in 0.8 ms.

```cypher
MATCH p = (s:LandingStation)-[:LANDS]->(c:CableSystem {id: $cable_id})<-[:CARRIED_BY]-(t:LogicalTunnel)
          -[:TERMINATES_AT]->(:BorderRouter)-[:FEEDS]->(:SuperSpine)-[:FEEDS]->(:Spine)-[:FEEDS]->(:Leaf)
          -[:FEEDS]->(:Server)-[:HAS_NIC]->(:NIC)-[:ATTACHED_TO]->(:GPU)-[:MEMBER_OF]->(ring:NCCL_Ring)
          <-[:COORDINATES]-(:TrainingJob)
WHERE (ring)-[:USES_TUNNEL]->(t)
RETURN length(p) AS hops, [n IN nodes(p) | coalesce(n.code, n.name, n.id)] AS nodes
```

| FalkorDB capability | What it does here |
|---|---|
| Multi-hop Cypher traversal | "Who is hurt?": the 11-hop path above, plus a sweep that locates every affected GPU to its server, rack, hall and campus (32,768 GPUs for Dunant in about 0.15 s) |
| `algo.SPpaths` | The fastest detour that avoids the damaged trench and has enough free capacity |
| `algo.betweenness` | Checks the detour for overloaded relay stations |
| `algo.WCC` | Planet scan: which landing stations fall off the main subsea network after each failure, and in which countries. A stranded station is not a country offline: inland fibre is not modelled |
| `algo.maxFlow` | Planet scan: total North America to Europe capacity left after each failure (multi-source, multi-sink) |
| `GRAPH.COPY` | Planet scan: four sandbox copies of the planet run in parallel; production is never touched |
| Live mutations | Cuts, atomic all-or-nothing bandwidth reservation, tunnel rebinding, scan results written back |
| Edge indexes | Index-backed lookups on cable, risk group, status and capacity |
| Graph as memory | Each resolved incident is stored as `(:Incident)-[:SEVERED]->(:CableSystem)` and `-[:REROUTED_VIA]->`; the agent recalls it the next time that cable fails |
| Text to Cypher | Ask the graph: Gemini writes Cypher, which runs under `GRAPH.RO_QUERY`, so a generated query can never write |

## Graph data model

One graph, `darkglass_core`: about 166,000 nodes and 261,000 relationships.

```mermaid
flowchart LR
    subgraph P["Physical plant · TeleGeography"]
        LS(["LandingStation"]) -- "OPTICAL_SPAN<br/>latency · capacity · srlg_id" --> LS2(["LandingStation"])
        LS -- LANDS --> CS(["CableSystem"])
    end
    subgraph W["Workloads"]
        TJ(["TrainingJob"]) -- COORDINATES --> NR(["NCCL_Ring"]) -- USES_TUNNEL --> LT(["LogicalTunnel"])
    end
    LT -- CARRIED_BY --> CS
    subgraph F["Compute fabric · Ashburn and Dublin"]
        BR(["BorderRouter"]) -- FEEDS --> SS(["SuperSpine → Spine → Leaf"]) -- FEEDS --> SV(["Server"])
        SV -- HAS_NIC --> NIC(["NIC"]) -- ATTACHED_TO --> GPU(["GPU"])
    end
    LT -- TERMINATES_AT --> BR
    GPU -- MEMBER_OF --> NR
    subgraph M["Memory and analysis"]
        IN(["Incident"]) -- "SEVERED · REROUTED_VIA" --> CS
    end
```

<details>
<summary><b>Full schema with properties</b></summary>

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

The fabric's `FEEDS` edges are each device's primary uplink, so every GPU has exactly one path to its
border routers. Real Clos fabrics are multi-pathed; the tree is the forwarding view a blast radius
needs, and it keeps the 11-hop query exact.

</details>

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

From `python -m app.services.benchmark` on the development machine (16 Oct 2026). The app's
"View queries" drawer shows this stored run with its date; the Proof tab measures every cut live.

The workload: the 3 fastest paths Ashburn to Dublin that avoid the damaged Virginia Beach trench,
with at least 3.2 Tbps free on every hop and a 50 ms ceiling. All engines load the same
`topology.json` (1,790 nodes, 8,900 edges); a sha256 of the edge set read back from each engine
confirms identical data, and all return the same 30.4075 ms route.

| Engine | Task | p50 |
|---|---|---|
| **FalkorDB** `algo.SPpaths` (engine time) | detour search | **0.80 ms** |
| FalkorDB, client wall time incl. network | detour search | 2.04 ms |
| Python NetworkX 3.6 (Yen k-shortest) | detour search | 34.0 ms |
| SQLite recursive CTE (up to 12 hops) | detour search | 1,770 ms |
| **FalkorDB** `algo.betweenness`, 32 sources | bottleneck audit | **22 ms** |
| NetworkX betweenness, 32 sources | bottleneck audit | 131 ms |
| FalkorDB / NetworkX, exact betweenness | bottleneck audit | 830 ms / 7,510 ms |

SQL time grows exponentially with path length: at 4, 6, 8, 10 and 12 hops it takes 0.8, 4.4, 34.6, 323
and 1,770 ms, and below 8 hops it misses the real best route. To be fair about it: 0.8 ms is the search
alone; the full detour tool, including masking and restoring about 1,200 low-capacity links, takes
about 50 ms. Timings vary by machine; the answers (who is hurt, the route, the scan rankings) do not.

Planet scan: 603 failures in 15 to 65 s on the development machine, about 3 to 5 minutes on the 2-vCPU
demo server. Each scan runs roughly 600 WCC calls, 300 max-flow runs, 1,800 mutations and 3,000 index
lookups.

<details>
<summary><b>Ask the graph eval: 66 of 69</b></summary>

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

</details>

## Run it

**Try it now:** [**the live demo**](https://8-234-112-123.sslip.io) runs the whole stack, Gemini included.

**Or locally.** You need Git, [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running,
about 2 GB free memory), Python 3.11+ and Node.js 20+. No accounts or keys: the cable data is in the
repo, and the Playbook agent needs no AI service.

```bash
git clone https://github.com/soujasK/DarkGlass.git
cd DarkGlass
powershell -ExecutionPolicy Bypass -File .\start.ps1     # Windows (PowerShell)
./start.sh                                                # macOS / Linux
```

The script starts FalkorDB and FalkorDB Browser with `docker compose`, installs the Python and Node
packages, builds the frontend, and starts the API (port 8010) and the app (port 3000). The **first run**
takes 5 to 10 minutes, mostly installs; the API then loads the graph (about 166,000 nodes, under a
minute), so if http://localhost:3000 opens first, wait a moment and refresh. Later runs start in seconds.

- **Without Gemini:** under **Agent**, choose **Playbook**. Everything works except Ask the graph.
- **With Gemini 3.8 Flash:** it runs on Vertex AI with your gcloud login, no API key stored. Run
  `gcloud auth application-default login`, then set `DARKGLASS_VERTEX_PROJECT_ID` (see `backend/.env.example`).
- **FalkorDB Browser:** open http://localhost:3001 (or "Open in FalkorDB Browser" in the app), keep the
  defaults (host `localhost`, port `6379`), click **Log in**, pick `darkglass_core`, and run any Cypher,
  e.g. `MATCH (j:TrainingJob)-[r]->(n) RETURN j, r, n LIMIT 25`.
- **Stop:** close the two server windows (Windows) or press Ctrl+C (macOS / Linux), then `docker compose down`.

Ports: app 3000, API 8010, FalkorDB 6390, Browser 3001 (deliberately not 8000 or 6379). Use the
included `docker-compose.yml`: it raises FalkorDB's per-query limit to 15 s; the 1 s default is too
short for the first load and the planet scan.

<details>
<summary><b>If something goes wrong</b></summary>

| What you see | Fix |
|---|---|
| **Setup** | |
| `docker: command not found`, or `Cannot connect to the Docker daemon` | Install / start Docker Desktop, wait until it says it is running, then run the script again |
| `docker compose` is not a command (Linux) | Install the Compose plugin (`sudo apt install docker-compose-plugin`), or add your user to the `docker` group if it needs `sudo` |
| `port is already allocated`, or `EADDRINUSE` | Another program uses 3000, 3001, 6390 or 8010. Stop it, then run the script again |
| `python` not found, or it opens the Microsoft Store (Windows) | Install Python 3.11+ from python.org and tick "Add python.exe to PATH" |
| `running scripts is disabled on this system` (Windows) | Run it exactly as shown: `powershell -ExecutionPolicy Bypass -File .\start.ps1` |
| `permission denied: ./start.sh` (macOS / Linux) | Run `bash start.sh`, or `chmod +x start.sh` once |
| pip or npm errors about versions | Check `python --version` (3.11 or newer) and `node --version` (20 or newer) |
| **Running** | |
| The app says **Offline** (top right) | The API is still loading the graph on first run. Wait about a minute; it reconnects by itself |
| The globe is blank or black | Turn on hardware acceleration in your browser (the globe uses WebGL) and use a recent Chrome, Edge or Firefox |
| Planet scan fails, or the log says `could not fork` | Give Docker more memory (Docker Desktop, Settings, Resources: 4 GB is plenty) |
| `Query timed out` in the API log | FalkorDB was started without the included `docker-compose.yml`. Run `docker compose up -d` from the repo folder |
| An incident looks stuck | Click **Reset** (top right). The API also repairs half-finished incidents when it restarts |
| Anything else odd with the data | `docker compose down -v` deletes the graph; the next start rebuilds it from the files in `data/` |
| The app looks old after `git pull` | Delete `frontend/.next` and run the script again, so the frontend rebuilds |
| **Gemini (optional)** | |
| The agent stops with a Gemini error (model not found, permission denied, or quota) | Choose **Playbook** under Agent: it runs the same FalkorDB tools without Gemini |
| Ask the graph shows an error | It is the one feature that needs Gemini; set it up as above |
| **FalkorDB Browser** | |
| It shows a login page | Keep the defaults and click **Log in**, then pick `darkglass_core` |

**Manual setup** (what the scripts do): `docker compose up -d`; then in `backend/`: `python -m venv .venv`,
activate it, `pip install -r requirements.txt`, `uvicorn app.main:app --port 8010`. In `frontend/`:
`npm install && npm run build && npm start`. The API builds the graph on its first start.

</details>

## Deploy it

`deploy/` runs the whole stack on one server: FalkorDB, the API, the web app, and Caddy, which gets the
HTTPS certificate by itself.

```bash
DOMAIN=your.host.name docker compose -f deploy/docker-compose.yml up -d --build
```

- **Locked down:** only ports 80 and 443 are open; FalkorDB and its Browser stay private.
- **Gemini on a budget:** 200 agent runs and 400 questions an hour for the whole site. It signs in as
  the machine's own service account, so no key is stored; `LLM_ENABLED=false` turns it off.
- **Nobody waits on a busy model:** if Google rate-limits Gemini, it answers too slowly, or it stops
  before moving the traffic, the Playbook finishes the fix at once with the same FalkorDB tools, and the
  screen says so. Past the hourly budget, a fix runs the Playbook too; Ask asks you to try again in a minute.
- **Clean for every visitor:** an incident left alone for 10 minutes resets.

The [live demo](https://8-234-112-123.sslip.io) is a Google Compute Engine e2-medium (2 vCPU, 4 GB) with
`deploy/gce-startup.sh` as its startup script: it installs Docker, fetches this repo, uses
`<external-ip>.sslip.io` as a free HTTPS hostname, and turns Gemini on when the instance metadata has
`llm=true`.

## The agent and the MCP server

The four agent tools are plain functions that run Cypher. They are exposed both to the web app and as a
**FastMCP server** (`python -m mcp_server.server`), so any MCP client can call them.

| Tool | FalkorDB work |
|---|---|
| `trace_severed_blast_radius` | 11-hop traversal from station to GPU and job, GPU/server/rack impact sweep, shared-trench exposure, incident memory recall |
| `compute_optimal_optical_detour` | Mask the trench and full links, run `algo.SPpaths`, then restore |
| `audit_secondary_choke_points` | `algo.betweenness`, graded against the graph's own 95th and 99th percentiles |
| `execute_spectrum_reallocation` | Atomic bandwidth reservation, demoting backup traffic, rebinding the tunnel, and a simulated ROADM configuration |

Two ways to run the agent:

- **Gemini 3.8 Flash (default):** the agent starts the FastMCP server, discovers the tools with MCP
  `list_tools`, and the model chooses which to call and with what arguments, then writes an operator
  summary. A fix typically takes 20 to 70 s, depending on Vertex AI; FalkorDB's share is under half a second.
- **Playbook:** a deterministic loop over the same four tools, with no model, in about half a second.

The agent moves the most critical traffic first: gradient sync, then checkpoints, then backup. Tunnels a
run could not move are listed as `still_down` and shown on screen, never hidden behind "restored".

```json
{ "mcpServers": { "darkglass": { "command": "<repo>/backend/.venv/Scripts/python",
  "args": ["-m", "mcp_server.server"], "cwd": "<repo>" } } }
```

## Tests

```bash
cd backend
pytest -q              # 137 unit, scenario, algorithm and end-to-end tests, about 3 minutes
pytest -q -m slow      # also the full 603-cable planet scan
```

FalkorDB must be running. Highlights:

- **Hand-picked failure scenarios** (`tests/test_scenarios.py`): a cable carrying every traffic class,
  shared-trench siblings, checkpoint-only and backup-only cuts, a second cut on the detour itself, cuts
  in either order, impossible constraints, all-or-nothing reservation, simultaneous cuts and fixes, and
  malformed or hostile requests. After every scenario the graph is checked for leftover masks, negative
  capacity and orphaned tunnels.
- **Algorithms against NetworkX** (`tests/test_algorithms.py`): exact betweenness has the same top 10
  stations, the agent's 32-source sample finds the same #1 and at least 8 of the top 10, and
  `algo.SPpaths` latency equals Dijkstra's.
- **The agent's plumbing:** the MCP tool server inherits the API's settings (`tests/test_mcp_env.py`),
  and on a public demo the Playbook finishes a fix Gemini cannot (`tests/test_llm_fallback.py`).
- The lifecycle of every cable class, read-only enforcement, recovery after a restart mid-incident,
  incident memory, the cable catalog, the 11-hop traversal and GPU counts, and the Ask eval's scorer.

## Engineering notes

Found by testing against FalkorDB 4.22:

- **The trench is masked by weight, not by a path bound.** `maxLen`/`maxCost` bounds on `algo.SPpaths`
  either returned false "no path" results or timed out on this graph, and filtering the k shortest
  paths afterwards can delete every valid detour near a shared shelf. So blocked links get a 1e6 route
  weight before the search and are restored after it.
- **Cuts retype edges.** A cut cable's spans become `:SEVERED_SPAN`, so routing and centrality run over
  surviving glass only.
- **Sampled betweenness is not rescaled.** The audit uses a 32-source sample with a fixed seed and
  grades by percentile, so only the ranking matters, and the ranking matches NetworkX.
- **Max-flow needs a derived layer.** `algo.maxFlow` rejects parallel edges and capacity ranges wider
  than 2^32, so the scan runs it on a derived `:FLOW_LINK` layer in whole Gbps.
- **Concurrent `GRAPH.COPY` can fail with "could not fork"**; the scan retries it.

## Data: what is real and what is modelled

| | |
|---|---|
| **Real** (TeleGeography, CC BY-NC-SA 3.0) | Cables, landing stations, coordinates, routes, length, in-service year, owners |
| **Measured** | Span lengths along the real route polylines; latency = length × 4.897 µs/km |
| **Modelled** | The compute fabric (H100 servers, ConnectX-7 NICs, Clos switches, racks, halls), capacity (published figures for Dunant and MAREA, an era model for the rest), utilisation, overland backhaul, risk groups, the AI jobs, the $18,420/hr cost, the 3-minute manual-reroute comparison, and ROADM commands |

The training jobs are illustrative. "Llama-4 400B MoE pretrain" borrows the publicly documented size of
Meta's Llama 4 Maverick (about 400B parameters, Mixture-of-Experts) to give the scenario a realistic
scale; it is not a real Meta workload and has no connection to Meta. The agent uses only each job's
numbers (GPU count, sync interval, jitter budget, priority), never its name.

TeleGeography data is CC BY-NC-SA 3.0. Attribute it, and do not use it commercially.

<details>
<summary><b>Repository layout</b></summary>

```
backend/app/core/        config, pooled FalkorDB client, query telemetry, demo rate limits
backend/app/ingestion/   TeleGeography fetch, topology build, workload seeding
backend/app/services/    chaos engine (cuts, memory), planet scan, ask-the-graph, benchmark, live proof, startup self-check
backend/app/api/         REST and WebSocket
backend/tests/           unit, scenario, algorithm and end-to-end tests
mcp_server/              FastMCP server, the four tools, Playbook and Gemini agents
frontend/src/            Next.js + react-three-fiber globe, mode screens, FalkorDB engine bar
deploy/                  production compose, Caddy HTTPS, Google Compute Engine startup script
```

</details>

## Build log

- **Oct 15–16:** chose the problem, designed the four-layer graph model, picked the TeleGeography data
  and prototyped the queries.
- **Oct 17:** first working version committed.
- **Oct 18:** testing, fixes, the Ask-the-graph eval and the switch to Gemini 3.8 Flash.
- **Oct 19:** edge-case sweep, NetworkX cross-checks as tests, and the public deployment on Google Cloud.

## AI assistance

**During development:** Claude Code was used as a coding assistant, under the author's direction. The
author reviewed and tested the work: every number on screen was checked against the graph, and the test
suite and the Ask-the-graph eval run against a live FalkorDB.

**At runtime, AI is used in exactly two places, and both are optional:**

- **LLM agent mode:** Gemini 3.8 Flash chooses which of the four tools to call, over MCP.
- **Ask the graph:** Gemini 3.8 Flash turns a plain-English question into a read-only Cypher query.

Everything else involves no AI model: the Playbook agent, cuts, the planet scan, the live proof, and every
number on screen. That is ordinary code that gives the same result every time, and FalkorDB does the
actual work. Even when Gemini picks the tools, the answers come from the graph: FalkorDB's share of a fix
is under half a second.

<div align="center">
<br>
<sub>Built for the <a href="https://www.wemakedevs.org/hackathons/falkordb">WeMakeDevs × FalkorDB Graph Hacks</a> · Track 01: Agents That Act on Connected Data</sub>
</div>

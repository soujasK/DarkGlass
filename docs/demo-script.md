# DarkGlass: 3-minute demo script

Record at 1600x900 or larger, one browser tab only (the demo state is shared by every open tab).

## Before you record

1. Run `start.ps1` and wait for http://localhost:3000.
2. Click **Reset** (top right).
3. Do one rehearsal: cut Dunant, press **Let FalkorDB fix it**, then click **Reset**. That first fix
   is stored in FalkorDB's incident memory, so the take you record shows the "Graph memory" line.
4. Open **Planet scan** once, so the leaderboard is warm.

## Script

| Time | Screen and action | Say |
|---|---|---|
| 0:00-0:20 | Incident start screen. Leave the globe rotating with the photons moving. | "65,536 GPUs training one model across Ashburn and Dublin, synced every 45 milliseconds over undersea cables. If one cable breaks, every GPU stops, and that costs about 18,000 dollars an hour." |
| 0:20-0:35 | Open the cable dropdown. Type "kafos" to show the search, then pick **Dunant**. Point at "Carries: live training". | "This is the real map: 603 cables and nearly 1,800 landing stations from TeleGeography. They live in one FalkorDB graph, together with the training jobs that depend on them." |
| 0:35-0:50 | Click **Cut Dunant**. The money counter rolls and the cable goes dark. Point at the **Graph memory** line. | "An anchor drag cuts Dunant. The money starts burning. FalkorDB remembers this cable has failed before, and what fixed it." |
| 0:50-1:25 | Gemini 3.8 Flash is already selected under Agent. Click **Let FalkorDB fix it**; speed the run up in the edit. Narrate the four questions as they fill in, then point at the graph view and the engine bar lighting up. | "Gemini finds the four tools over MCP and picks them itself. Four graph questions. Who is hurt? One 11-hop traversal: from the landing station, through the cable and tunnel, down the border router and three switch tiers, to the server, NIC and GPU, and up to the job. 32,768 GPUs on 4,096 servers in 1,024 racks, located in a fraction of a second. Find a safe detour: algo.SPpaths, with MAREA ruled out because it shares the same seabed trench. Check for bottlenecks: betweenness centrality. Move the traffic: an atomic reservation, and the tunnel's CARRIED_BY edge moves to Havfrue." |
| 1:25-1:40 | The result screen. Point at the "Decided by Gemini over MCP; FalkorDB did the work in N ms" line and the three numbers. | "Gemini decided; FalkorDB did the work in milliseconds. 65,536 GPUs back, and the live proof measures this very cut against SQL." |
| 1:40-1:50 | Click **Reset**, cut Dunant again, pick **Playbook** under Agent and fix. | "Without the model, the same four tools run as a fixed playbook: fixed in about a third of a second." |
| 1:50-2:20 | **Planet scan**, then **Run again**. Speed this part up 2x in the edit. Show the heat map and the engine bar at full load. | "Now the question no SQL database can answer live: what if every cable on Earth failed? FalkorDB clones the planet into four sandboxes and cuts all 603, measuring which landing stations fall off the subsea network and how much transatlantic capacity survives, with WCC and max-flow." |
| 2:20-2:35 | Point at the three leaderboards. | "KAFOS strands nine landing stations around the Black Sea. BRUSA drains 141 terabits from the Atlantic. And these three would freeze our GPUs." |
| 2:35-2:50 | **Ask the graph**, then click the Virginia Beach question. The answer lights up on the globe. | "Or just ask. Gemini writes the Cypher, read-only, and FalkorDB answers in milliseconds." |
| 2:50-3:00 | **Proof**. Read the numbers on screen. | "This exact cut, measured live on the same hash-checked snapshot in FalkorDB, NetworkX and SQL. On the detour, SQL gives up after 10 seconds. DarkGlass: self-healing networks on FalkorDB." |

## If something goes wrong on camera

- **The screen is stuck in a past state:** click Reset.
- **Gemini is slow:** a run takes about 30 to 60 s. Speed it up in the edit.
- **Gemini errors** (no Vertex credentials): run `gcloud auth application-default login`, or pick Playbook.
- **The numbers differ from this script:** they are measured live and vary by machine. Say what the screen shows.

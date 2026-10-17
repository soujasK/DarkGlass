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
| 0:20-0:35 | Open the cable dropdown. Type "kafos" to show the search, then pick **Dunant**. Point at "Carries: live training". | "This is the real map: 603 cables and 1,800 landing stations from TeleGeography. They live in one FalkorDB graph, together with the training jobs that depend on them." |
| 0:35-0:50 | Click **Cut Dunant**. The money counter rolls and the cable goes dark. Point at the **Graph memory** line. | "An anchor drag cuts Dunant. The money starts burning. FalkorDB remembers this cable has failed before, and what fixed it." |
| 0:50-1:25 | Click **Let FalkorDB fix it**. Narrate the four questions as they fill in, then point at the graph view and the engine bar lighting up. | "Four graph questions. Who is hurt? One 11-hop traversal: from the landing station, through the cable and tunnel, down the border router and three switch tiers, to the server, NIC and GPU, and up to the job. 32,768 GPUs on 4,096 servers in 1,024 racks, located in a fraction of a second. Find a safe detour: algo.SPpaths, with MAREA ruled out because it shares the same seabed trench. Check for bottlenecks: betweenness centrality. Move the traffic: an atomic reservation, and the tunnel's CARRIED_BY edge moves to Havfrue." |
| 1:25-1:40 | The result screen. Point at "Fixed in N ms" and the three numbers. | "Fixed in about a third of a second. 65,536 GPUs back, around 900 dollars saved against a manual reroute, and the route search runs 2,000 times faster than SQL." |
| 1:40-1:50 | Click **Gemini 4 Argon** under Agent and say it, without waiting for a run. (Or cut to a pre-recorded, sped-up Gemini run.) | "The same tools are a FastMCP server, so an LLM can drive them too. Here, Gemini 4 Argon chooses the queries itself." |
| 1:50-2:20 | **Planet scan**, then **Run again**. Speed this part up 2x in the edit. Show the heat map and the engine bar at full load. | "Now the question no SQL database can answer live: what if every cable on Earth failed? FalkorDB clones the planet into four sandboxes and cuts all 603, measuring which countries go dark and how much transatlantic capacity survives, with WCC and max-flow." |
| 2:20-2:35 | Point at the three leaderboards. | "KAFOS cuts off five countries. BRUSA drains 141 terabits from the Atlantic. And these three would freeze our GPUs." |
| 2:35-2:50 | **Ask the graph**, then click the Virginia Beach question. The answer lights up on the globe. | "Or just ask. Gemini writes the Cypher, read-only, and FalkorDB answers in milliseconds." |
| 2:50-3:00 | **Proof**. | "Same data, hash-verified in every engine, same answer: 0.8 milliseconds in FalkorDB versus 1.8 seconds in SQL. DarkGlass: self-healing networks on FalkorDB." |

## If something goes wrong on camera

- **The screen is stuck in a past state:** click Reset.
- **Gemini is slow:** it takes about 60 to 90 s per run. Keep it out of the live take or speed it up in the edit.
- **The numbers differ from this script:** they are measured live and vary by machine. Say what the screen shows.

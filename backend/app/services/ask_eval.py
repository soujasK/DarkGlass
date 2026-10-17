"""Ask-the-graph eval: hand-written questions, each with a hand-written reference Cypher.

The expected answer is computed from the live graph by the reference query (never hardcoded), and
Gemini's generated Cypher is scored against it:

* number: the first row of the answer holds the expected number (1% tolerance for decimals).
* set:    every expected item (matched by any of its id/name columns) appears in the answer, and none
          of the items the `wrong` reference query lists does, so returning everything fails.
* top:    the first row of the answer is the expected item (any of them, when tied).

    python -m app.services.ask_eval [runs]   runs every question 3 times (default), writes
                                             data/benchmarks/ask_eval.json
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from app import REPO_ROOT
from app.core.database import run

OUT = REPO_ROOT / "data" / "benchmarks" / "ask_eval.json"


@dataclass(frozen=True)
class Case:
    question: str
    kind: str  # lookup | aggregate | multi-hop | memory | algorithm
    mode: str  # number | set | top
    gold: str
    wrong: str | None = None  # items that must NOT appear: an over-broad answer fails


CASES = [
    # ---- lookups
    Case("How many subsea cable systems are in the graph?", "lookup", "number",
         "MATCH (c:CableSystem) RETURN count(c) AS n"),
    Case("Which countries does the Dunant cable land in?", "lookup", "set",
         "MATCH (s:LandingStation)-[:LANDS]->(:CableSystem {id: 'dunant'}) RETURN DISTINCT s.country_name AS country"),
    Case("In what year did MAREA enter service?", "lookup", "number",
         "MATCH (c:CableSystem {id: 'marea'}) RETURN c.ready_for_service AS year"),
    Case("Who owns the Amitie cable?", "lookup", "set",
         "MATCH (c:CableSystem {id: 'amitie'}) RETURN c.owners AS owners"),
    # ---- aggregates
    Case("How many GPUs are in the Dublin campus?", "aggregate", "number",
         "MATCH (g:GPU {campus: 'DUB'}) RETURN count(g) AS n"),
    Case("Which country has the most landing stations?", "aggregate", "top", """
         MATCH (s:LandingStation) WITH s.country_name AS country, count(s) AS n
         WITH max(n) AS top, collect({country: country, n: n}) AS all
         UNWIND all AS a WITH a WHERE a.n = top RETURN a.country AS country"""),
    Case("What is the longest subsea cable?", "aggregate", "top", """
         MATCH (c:CableSystem) WITH max(c.length_km) AS top
         MATCH (c:CableSystem) WHERE c.length_km = top RETURN c.id AS id, c.name AS name"""),
    Case("How many cables entered service in 2020?", "aggregate", "number",
         "MATCH (c:CableSystem) WHERE c.ready_for_service = 2020 RETURN count(c) AS n"),
    Case("Which landing station has the most cables landing at it?", "aggregate", "top", """
         MATCH (s:LandingStation)-[:LANDS]->(c:CableSystem) WITH s, count(c) AS n
         WITH max(n) AS top, collect({s: s, n: n}) AS all
         UNWIND all AS a WITH a WHERE a.n = top RETURN a.s.code AS code, a.s.name AS name"""),
    Case("What is the total design capacity in Tbps of all cables that land in Ireland?", "aggregate", "number", """
         MATCH (s:LandingStation {country_name: 'Ireland'})-[:LANDS]->(c:CableSystem)
         WITH DISTINCT c RETURN sum(c.design_capacity_tbps) AS tbps"""),
    # ---- multi-hop
    Case("Which cables carry the primary gradient sync tunnel tun_iad_dub_pri?", "multi-hop", "set",
         "MATCH (:LogicalTunnel {id: 'tun_iad_dub_pri'})-[:CARRIED_BY]->(c:CableSystem) RETURN c.id AS id, c.name AS name",
         wrong="""MATCH (:LogicalTunnel)-[:CARRIED_BY]->(c:CableSystem)
         WHERE NOT (:LogicalTunnel {id: 'tun_iad_dub_pri'})-[:CARRIED_BY]->(c) RETURN DISTINCT c.id AS id, c.name AS name"""),
    Case("Which training jobs depend on the Dunant cable?", "multi-hop", "set", """
         MATCH (j:TrainingJob)-[:COORDINATES]->(:NCCL_Ring)-[:USES_TUNNEL]->(:LogicalTunnel)
               -[:CARRIED_BY]->(:CableSystem {id: 'dunant'})
         RETURN DISTINCT j.id AS id, j.name AS name""",
         wrong="""MATCH (j:TrainingJob) WHERE NOT (j)-[:COORDINATES]->()-[:USES_TUNNEL]->()-[:CARRIED_BY]->(:CableSystem {id: 'dunant'})
         RETURN j.id AS id, j.name AS name"""),
    Case("Which traffic classes ride the Pan European Crossing (UK-Ireland) cable?", "multi-hop", "set", """
         MATCH (t:LogicalTunnel)-[:CARRIED_BY]->(:CableSystem {id: 'pan-european-crossing-uk-ireland'})
         RETURN DISTINCT t.traffic_class AS traffic_class"""),
    Case("Which other cables share a risk group (SRLG) with Dunant?", "multi-hop", "set", """
         MATCH ()-[a:OPTICAL_SPAN {cable_id: 'dunant'}]->() WITH collect(DISTINCT a.srlg_id) AS g
         MATCH ()-[b:OPTICAL_SPAN]->() WHERE b.srlg_id IN g AND b.cable_id <> 'dunant'
         WITH DISTINCT b.cable_id AS cid MATCH (c:CableSystem {id: cid}) RETURN c.id AS id, c.name AS name"""),
    Case("How many GPUs are members of the NCCL rings coordinated by the Llama-4 400B job?", "multi-hop", "number", """
         MATCH (:TrainingJob {id: 'job_llama4_400b'})-[:COORDINATES]->(r:NCCL_Ring)<-[:MEMBER_OF]-(g:GPU)
         RETURN count(DISTINCT g) AS n"""),
    Case("Which data halls contain servers whose GPUs belong to ring ring_iad_dub_01?", "multi-hop", "set", """
         MATCH (:NCCL_Ring {id: 'ring_iad_dub_01'})<-[:MEMBER_OF]-(:GPU)<-[:ATTACHED_TO]-(:NIC)<-[:HAS_NIC]-(:Server)
               -[:IN_RACK]->(:Rack)-[:IN_ROW]->(:Row)-[:IN_HALL]->(h:DataHall)
         RETURN DISTINCT h.id AS id"""),
    Case("Which NCCL rings send their traffic over gradient sync tunnels?", "multi-hop", "set", """
         MATCH (r:NCCL_Ring)-[:USES_TUNNEL]->(:LogicalTunnel {traffic_class: 'GRADIENT_SYNC'}) RETURN DISTINCT r.id AS id""",
         wrong="""MATCH (r:NCCL_Ring)-[:USES_TUNNEL]->(t:LogicalTunnel) WHERE t.traffic_class <> 'GRADIENT_SYNC'
         RETURN DISTINCT r.id AS id"""),
    Case("Which training jobs would be hit if every cable landing in France failed?", "multi-hop", "set", """
         MATCH (:LandingStation {country_name: 'France'})-[:LANDS]->(:CableSystem)<-[:CARRIED_BY]-(:LogicalTunnel)
               <-[:USES_TUNNEL]-(:NCCL_Ring)<-[:COORDINATES]-(j:TrainingJob)
         RETURN DISTINCT j.id AS id, j.name AS name""",
         wrong="""MATCH (j:TrainingJob) WHERE NOT (j)-[:COORDINATES]->()-[:USES_TUNNEL]->()-[:CARRIED_BY]->()
               <-[:LANDS]-(:LandingStation {country_name: 'France'})
         RETURN j.id AS id, j.name AS name"""),
    Case("How many racks are in the Ashburn campus data halls?", "multi-hop", "number", """
         MATCH (r:Rack)-[:IN_ROW]->(:Row)-[:IN_HALL]->(:DataHall)-[:IN_CAMPUS]->(:LandingStation {code: 'IAD'})
         RETURN count(DISTINCT r) AS n"""),
    # ---- scan results and incident memory
    Case("Which cable has the highest planet-scan risk?", "memory", "top", """
         MATCH (c:CableSystem) WITH max(c.scan_risk) AS top
         MATCH (c:CableSystem) WHERE c.scan_risk = top RETURN c.id AS id, c.name AS name"""),
    Case("How many times has the Dunant cable failed before, according to incident memory?", "memory", "number",
         "MATCH (i:Incident)-[:SEVERED]->(:CableSystem {id: 'dunant'}) RETURN count(i) AS n"),
    Case("Which cables have past incidents rerouted traffic onto?", "memory", "set",
         "MATCH (:Incident)-[:REROUTED_VIA]->(c:CableSystem) RETURN DISTINCT c.id AS id, c.name AS name"),
    # ---- graph algorithms
    Case("What is the lowest possible latency in ms between the IAD and DUB gateways over optical spans?",
         "algorithm", "number", """
         MATCH (a:LandingStation {code: 'IAD'}), (b:LandingStation {code: 'DUB'})
         CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['OPTICAL_SPAN'], weightProp: 'latency_ms',
                            pathCount: 1}) YIELD path, pathWeight
         RETURN pathWeight AS latency_ms"""),
]


# --------------------------------------------------------------------------- scoring


def _strings(v: Any) -> set[str]:
    if isinstance(v, str):
        return {v.strip().lower()}
    if isinstance(v, (list, tuple)):
        return set().union(*(_strings(x) for x in v)) if v else set()
    if isinstance(v, dict):
        return set().union(*(_strings(x) for x in v.values())) if v else set()
    return set()


def _numbers(row: dict) -> list[float]:
    return [float(v) for v in row.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _close(a: float, b: float) -> bool:
    return a == b if float(b).is_integer() else abs(a - b) <= max(abs(b) * 0.01, 1e-6)


def score(case: Case, gold_rows: list[dict], rows: list[dict], wrong_rows: list[dict] | None = None) -> bool:
    if not rows or not gold_rows:
        return False
    if case.mode == "number":
        want = _numbers(gold_rows[0])[0]
        return any(_close(x, want) for x in _numbers(rows[0]))
    items = [_strings(g) for g in gold_rows]  # each expected item: its acceptable id/name strings
    if case.mode == "top":
        first = _strings(rows[0])
        return any(item & first for item in items)
    seen = _strings(rows)
    if any(_strings(w) & seen for w in wrong_rows or []):
        return False
    return all(item & seen for item in items)


# --------------------------------------------------------------------------- run


def gold(case: Case) -> list[dict]:
    return run(case.gold, read_only=True).rows


def wrong(case: Case) -> list[dict]:
    return run(case.wrong, read_only=True).rows if case.wrong else []


TRANSIENT = ("RemoteProtocolError", "ConnectError", "ReadTimeout", "ServiceUnavailable", "503", "429")


def _ask_once(question: str) -> dict[str, Any]:
    """One answer; a dropped connection to the model is retried once, a wrong query never is."""
    from app.services.ask_graph import ask

    for attempt in range(2):
        try:
            return ask(question)
        except Exception as exc:  # an LLM or network failure counts as a miss, never a crash
            err = f"{type(exc).__name__}: {exc}"
            if attempt == 0 and any(t in err for t in TRANSIENT):
                time.sleep(2)
                continue
            return {"error": err, "transport_error": any(t in err for t in TRANSIENT)}
    return {"error": "unreachable"}


def _one_run() -> list[dict[str, Any]]:
    results = []
    for case in CASES:
        expected = gold(case)
        ans = _ask_once(case.question)
        ok = "error" not in ans and score(case, expected, ans.get("rows", []), wrong(case))
        results.append({
            "question": case.question, "kind": case.kind, "mode": case.mode, "passed": ok,
            "expected": expected[:10], "cypher": ans.get("cypher"), "answer": ans.get("rows", [])[:10],
            "error": ans.get("error"), "transport_error": bool(ans.get("transport_error")),
            "llm_ms": ans.get("llm_ms"), "engine_ms": ans.get("engine_ms"),
        })
        print(f"{'PASS' if ok else 'FAIL'}  [{case.kind}] {case.question}", flush=True)
    return results


def run_eval(runs: int = 3) -> dict[str, Any]:
    """Every question, `runs` times: the model is not deterministic, so one run is not a score."""
    from app.core.config import get_settings

    started = time.time()
    all_runs = []
    for i in range(runs):
        print(f"--- run {i + 1}/{runs}", flush=True)
        all_runs.append(_one_run())
    scores = [sum(r["passed"] for r in res) for res in all_runs]
    by_kind: dict[str, dict[str, int]] = {}
    per_question = []
    for idx, case in enumerate(CASES):
        k = by_kind.setdefault(case.kind, {"passed": 0, "total": 0})
        hits = sum(res[idx]["passed"] for res in all_runs)
        k["passed"] += hits
        k["total"] += runs
        per_question.append({"question": case.question, "kind": case.kind, "passed_runs": hits, "runs": runs})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": get_settings().gemini_model,
        "questions": len(CASES),
        "runs": runs,
        "scores": scores,
        "passed": sum(scores),
        "total": len(CASES) * runs,
        "accuracy": round(sum(scores) / (len(CASES) * runs), 4),
        "transport_errors": sum(r["transport_error"] for res in all_runs for r in res),
        "by_kind": by_kind,
        "per_question": per_question,
        "duration_s": round(time.time() - started, 1),
        "results": all_runs[-1],
    }
    OUT.write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    import sys

    r = run_eval(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
    print(f"\n{r['passed']}/{r['total']} over {r['runs']} runs ({' / '.join(map(str, r['scores']))} of "
          f"{r['questions']}), {r['model']}; written to {OUT}")

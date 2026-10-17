"""The Ask-the-graph eval itself: its reference answers and its scorer (no LLM needed)."""

from __future__ import annotations

import pytest

from app.core.database import ping
from app.services.ask_eval import CASES, Case, gold, score

live = pytest.mark.skipif(not ping(), reason="FalkorDB is not running")


def test_eval_covers_every_kind_of_question():
    kinds = {c.kind for c in CASES}
    assert len(CASES) >= 20 and kinds == {"lookup", "aggregate", "multi-hop", "memory", "algorithm"}
    assert len({c.question for c in CASES}) == len(CASES)


@live
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.question[:50])
def test_reference_query_is_read_only_and_answers(case):
    rows = gold(case)  # runs under GRAPH.RO_QUERY
    assert rows, "a question with no expected answer cannot score anything"
    if case.mode == "number":
        assert any(isinstance(v, (int, float)) for v in rows[0].values())


NUM = Case("q", "lookup", "number", "")
SET = Case("q", "multi-hop", "set", "")
TOP = Case("q", "aggregate", "top", "")


@pytest.mark.parametrize("case,gold_rows,rows,ok", [
    (NUM, [{"n": 603}], [{"count": 603}], True),
    (NUM, [{"n": 603}], [{"count": 602}], False),
    (NUM, [{"n": 603}], [{"label": "cables", "count": 603}], True),
    (NUM, [{"x": 30.3967}], [{"ms": 30.4}], True),            # 1% tolerance for decimals
    (NUM, [{"x": 30.3967}], [{"ms": 33.0}], False),
    (NUM, [{"n": 1}], [{"flag": True}], False),               # a boolean is not the number 1
    (SET, [{"id": "marea", "name": "MAREA"}, {"id": "brusa", "name": "BRUSA"}], [{"c": "MAREA"}, {"c": "BRUSA"}], True),
    (SET, [{"id": "marea", "name": "MAREA"}, {"id": "brusa", "name": "BRUSA"}], [{"c": "marea"}], False),
    (SET, [{"id": "marea"}], [{"cables": ["dunant", "marea"]}], True),  # lists in a cell count
    (TOP, [{"id": "TUA"}, {"id": "BAT"}], [{"code": "BAT"}, {"code": "TUA"}], True),  # ties: either one first
    (TOP, [{"id": "2africa", "name": "2Africa"}], [{"name": "EAC-C2C"}, {"name": "2Africa"}], False),
    (SET, [{"id": "x"}], [], False),
])
def test_scorer(case, gold_rows, rows, ok):
    assert score(case, gold_rows, rows) is ok


def test_over_broad_answer_fails():
    """Returning every ring instead of only the gradient sync ones must not score as correct."""
    gold_rows = [{"id": "ring_iad_dub_01"}, {"id": "ring_iad_dub_02"}]
    wrong_rows = [{"id": "ring_rlhf_ckpt_01"}]
    everything = [{"ring": "ring_iad_dub_01"}, {"ring": "ring_iad_dub_02"}, {"ring": "ring_rlhf_ckpt_01"}]
    assert score(SET, gold_rows, everything, wrong_rows) is False
    assert score(SET, gold_rows, everything[:2], wrong_rows) is True


@live
def test_must_not_appear_sets_never_overlap_the_answer():
    from app.services.ask_eval import _strings, wrong

    for case in CASES:
        bad = set().union(*(_strings(w) for w in wrong(case))) if case.wrong else set()
        good = set().union(*(_strings(g) for g in gold(case)))
        assert not bad & good, case.question

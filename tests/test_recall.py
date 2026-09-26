"""Recall check (spec §9): every forecast re-asked under role names. The re-asked states name no party (case-
insensitive), the stored deltas are the per-branch differences, and the analysis's judgments are unchanged. A stub
judge on the small $2.0M fixture; no network."""

import copy
import json
from types import SimpleNamespace

import pytest
from akoustis_fixture import REVIEW, SETUP, basis, judgment

from app.agent.jev import build_question, registry_question
from app.disputes.forecast import Forecaster, answer_distribution
from app.disputes.recall import MOVED, Roles, aliases, recall_check
from app.domain.investigation import SemanticObservation

BORROWER = "Akoustis Technologies, Inc."
PASSAGE = {"source": "Akoustis Technologies Form 10-Q (AKTS)", "date": "2024-05-20", "heading": "Qorvo, Inc. v. Akoustis",
           "quotes": ["Judge Jon P. McCalla entered judgment for QORVO in C.A. No. 21-1417-JPM"],
           "context": "The Bank of New York Mellon Trust Company, N.A., as trustee; akoustis XBAW filters; QRVO; "
                      "Tai-Saw; case 1:21-cv-01417-JPM"}


class Stub:
    """Answers depend on whether the state names a party, so the role pass moves every question."""

    def __init__(self):
        self.seen = []

    async def forecast(self, question_id, state, subject_ids, branches=None):
        text = json.dumps(state, ensure_ascii=False)
        self.seen.append((question_id, text, branches))
        named = "akoustis" in text.lower()
        if branches == ("yes", "no"):
            return SemanticObservation(observation_id="o", call_id="c", profile="p", question_id=question_id,
                                       question_version="1", answer=None, primitive="noul", noul_value=0.7 if named else 0.4)
        w = {b: (3.0 if named and i == 0 else 1.0) for i, b in enumerate(branches)}
        return SemanticObservation(observation_id="o", call_id="c", profile="p", question_id=question_id,
                                   question_version="1", answer=None, primitive="choice", probabilities=w)


@pytest.fixture(scope="module")
def fc():
    d = judgment(financing=(), components=(), amount=judgment().amount.model_copy(update={"value": 200_000_000}))
    f = Forecaster([d], {"f": SimpleNamespace(finding_id="f")}, borrower=BORROWER, review=REVIEW,
                   horizon=SETUP.horizon, hydrate=lambda _f: dict(PASSAGE), setup=SETUP, basis=basis()[1])
    f.all_paths()
    return f


def test_role_prompts_name_no_party_and_deltas_are_right(fc):
    import asyncio

    first = Stub()
    judgments = asyncio.run(fc.judge(first))
    assert judgments and all("akoustis" in t.lower() for _, t, _ in first.seen)  # the original states name the parties
    before = copy.deepcopy({k: j.distribution for k, j in judgments.items()})
    second = Stub()
    rc = recall_check(fc, judgments, second, BORROWER)
    table = aliases(fc.disputes, BORROWER)
    assert {"Qorvo", "AKTS", "QRVO", "McCalla", "1:21-cv-01417"} <= set(table)
    assert len(second.seen) == len(judgments)
    for qid, text, _ in second.seen:
        entry = registry_question(qid)
        prompt = (text + build_question(entry).model_dump_json()).lower()
        leaked = [a for a in table if a and a.lower() in prompt]
        assert not leaked, (qid, leaked)
    assert {k: j.distribution for k, j in judgments.items()} == before  # never changes the analysis's probabilities
    for k, j in judgments.items():
        n = fc.nodes[k]
        roles = answer_distribution(k, n.branches, SemanticObservation(
            observation_id="o", call_id="c", profile="p", question_id=n.question_id, question_version="1", answer=None,
            primitive="noul" if n.branches == ("yes", "no") else "choice", noul_value=0.4,
            probabilities={b: 1.0 for b in n.branches}))
        v = rc["nodes"][k]
        assert v["original"] == j.distribution and v["roles"] == pytest.approx(roles)
        assert v["max_change"] == pytest.approx(max(abs(j.distribution[b] - roles[b]) for b in n.branches), abs=1e-6)
    s = rc["summary"]
    ch = [v["max_change"] for v in rc["nodes"].values()]
    assert s["questions"] == len(judgments) and s["max_abs_change"] == max(ch)
    assert s["mean_abs_change"] == pytest.approx(sum(ch) / len(ch), abs=1e-6)
    assert {m["key"] for m in s["moved"]} == {k for k, v in rc["nodes"].items() if v["max_change"] > MOVED}


def test_roles_replace_case_insensitively_and_leave_other_text():
    r = Roles({"Akoustis": "the defendant", "QRVO": "the plaintiff"})
    assert r({"a": ["AKOUSTIS sued by qrvo", 3]}) == {"a": ["the defendant sued by the plaintiff", 3]}

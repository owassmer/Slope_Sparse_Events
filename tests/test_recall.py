"""Recall check (spec §9): every forecast re-asked under role names. The re-asked states name no party (case-
insensitive), the stored deltas are the per-branch differences, and the analysis's judgments are unchanged. A stub
judge on the small $2.0M fixture; no network."""

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from akoustis_fixture import REVIEW, SETUP, basis, judgment

from app.agent.jev import build_question, registry_question
from app.disputes.akoustis_pre_d import NOTES
from app.disputes.akoustis_pre_d import judgment as akoustis_judgment
from app.disputes.forecast import Forecaster, answer_distribution
from app.disputes.recall import MOVED, Roles, recall_check, roles_for
from app.domain.investigation import SemanticObservation

BORROWER = "Akoustis Technologies, Inc."
FIXTURE = Path(__file__).parent / "fixtures" / "recall_passages.json"  # real pre-D passages from the snapshot
# Written by hand (PR #16 review and the record), not taken from recall.py: nothing here may reach Jev in the roles
# pass. Lower case; quotes and whitespace are normalised before matching.
MUST_NOT_SURVIVE = [
    "akoustis", "qorvo", "qoorvo", "akts", "qrvo", "1584754", "1604778", "001-38029", "33-1229046",  # parties
    "grinding & dicing", "grinding and dicing", "gdsi", "rfm integrated", "rfmi", "xbaw",  # subsidiaries, product
    "aichele", "shealy", "boller", "bruggeworth", "geiss", "denbaars", "petock", "mcmahon", "mcguire",  # officers
    "7,522,018", "9,735,755", "10,256,786", "7522018", "9735755", "10256786", "'755", "'018", "'786",  # patents
    "21-cv-01417", "21-cv-1417", "21-1417", "1:21-cv", "76727",  # the case
    "mccalla", "mccal", "phipps", "judge jon", "jon p",  # the judge
    "huntersville", "canandaigua", "greensboro", "northcross", "thorndike", "28078",  # headquarters
    "tigan", "blumenfeld", "raucci", "mr. masters", "defosse", "morris, nichols", "sheppard", "warder",  # plaintiff
    "brauerman", "golden iii", "golden, ronald", "lemieux", "elkins", "jakopin", "bayard", "pillsbury",  # defendant
    "squire patton", "k&l gates", "selness",
    "bennis", "shanfield", "lebby", "bravman", "faison",  # experts and trial staff
]

REVIEW_FINDINGS = ["aichele", "shealy", "boller", "bruggeworth", "grinding & dicing", "gdsi", "rfm integrated",
                   "7,522,018", "9,735,755", "judge jon p mccalla", "huntersville", "canandaigua", "greensboro",
                   "tigan", "brauerman", "morris, nichols", "bayard", "pillsbury"]


def normal(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'")).lower()
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
    assert len(second.seen) == len(judgments)
    for qid, text, _ in second.seen:
        entry = registry_question(qid)
        prompt = normal(text + build_question(entry).model_dump_json())
        leaked = [a for a in MUST_NOT_SURVIVE if a in prompt]
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


def test_real_snapshot_passages_keep_no_identifier_after_the_roles_pass():
    """Real passages (verdict release, captions, signature and service blocks, the indenture's parties, 10-Q risk
    factors, officer signatures, the docket) against the hand-written list above, case-insensitively."""
    passages = json.loads(FIXTURE.read_text())["passages"]
    assert FIXTURE.stat().st_size < 50_000 and all(p["available_at"] <= "2024-06-20T23:59:59-04:00" for p in passages)
    raw = normal(" ".join(p["text"] for p in passages))
    assert not [a for a in REVIEW_FINDINGS if a not in raw]  # the passages do carry what the review found
    roles = roles_for([akoustis_judgment().model_copy(update={"financing": (NOTES,)})], BORROWER)
    after = {p["section_id"]: normal(roles(p["text"])) for p in passages}
    leaked = {k: [a for a in MUST_NOT_SURVIVE if a in t] for k, t in after.items()}
    assert not {k: v for k, v in leaked.items() if v}, leaked


def test_name_forms_in_the_record_become_roles():
    roles = roles_for([], BORROWER)
    for raw, want in [("Signed by Judge Jon P McCalla on 5/20/2024.", "Signed by the judge on 5/20/2024."),
                      ("(Golden, Ronald) (Entered: 01/06/2022)", "(counsel for the defendant) (Entered: 01/06/2022)"),
                      ("U.S. Patent Nos. 7,522,018 and 9,735,755", "the first patent in suit and the second patent in suit"),
                      ("the ’755 Patent", "the second patent in suit"),
                      ("Grinding and Dicing Services, Inc. (GDSI)", "the defendant's subsidiary (the defendant's subsidiary)"),
                      ("Huntersville , NC 28078", "the defendant's headquarters town"),
                      ("30 days of the Court", "30 days of the Court"), ("party litigant", "party litigant")]:
        assert roles(raw) == want, raw

"""Present-state reading of a live dispute: factor aggregation by each factor's rule, and a waiver of appeal kept as
forecast evidence rather than a pruned branch. Akoustis owes Qorvo the 20 May 2024 judgment (D. Del. 1:21-cv-01417,
D.I. 602); no network, the judge is a stub returning fixed readings (the waiver is a fixture, not a fact of the case)."""

import asyncio
from datetime import date

from app.disputes.forecast import Forecaster
from app.disputes.interpret import Interpreter
from app.domain.investigation import (
    AtomicFinding,
    DisputeInstance,
    FindingReading,
    SemanticObservation,
    SourceSpan,
)
from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit

BORROWER, PAYEE = "Akoustis Technologies, Inc.", "Qorvo, Inc."
REVIEW = date(2024, 6, 20)
SOURCES = {"q3_10q": ("sec_10q", "2024-05-13"), "verdict_8k": ("sec_8k", "2024-05-20"),
           "qorvo_release": ("press_release", "2024-05-20")}


def finding(fid: str, source: str) -> AtomicFinding:
    span = SourceSpan(source_id=source, section_id=f"{source}#legal", item_id=f"{source}#legal", start=0, end=1, quote="q")
    return AtomicFinding(finding_id=fid, dependency_id="dep", proposition=fid, target="t", spans=(span,),
                         status="accepted")


def draft() -> DisputeInstance:
    return DisputeInstance(
        instance_id="judgment", dependency_id="dep", model_id="m", model_version="3", title="Judgment (D.I. 602)",
        order_reference="D. Del. 1:21-cv-01417", nature="money_judgment", counterparty=PAYEE,
        finding_ids=("a", "b", "c"), judgment_date=date(2024, 5, 20),
        amount=EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=3_859_502_300,
                             provenance=Provenance(basis=Basis.DOCUMENTED)))


def interpreter(findings: list[AtomicFinding], judge=None) -> Interpreter:
    return Interpreter(draft(), findings, judge=judge, borrower=BORROWER, sources=SOURCES, hydrate=lambda f: {})


def test_latest_passage_wins_and_same_date_disagreement_stays_a_conflict():
    intends, considering, none = ("The payer states it intends to appeal", "The payer is considering or reserves the "
                                  "right to appeal", "The passage states no position on an appeal")
    fs = [finding("a", "q3_10q"), finding("b", "verdict_8k"), finding("c", "qorvo_release")]
    it = interpreter(fs)
    older = FindingReading(finding_id="a", source_date="2024-05-13", levels={"appeal_intent": {considering: 0.8, none: 0.2}})
    newer = FindingReading(finding_id="b", source_date="2024-05-20", levels={"appeal_intent": {intends: 0.9, none: 0.1}})
    by = {f.factor_id: f for f in it._aggregate([older, newer])}
    assert by["appeal_intent"].level_label == intends and not by["appeal_intent"].conflict
    assert by["appeal_intent"].distribution[considering] == 0  # the 13 May reading is superseded, not averaged in
    assert by["appeal_intent"].decisive.finding_id == "b"

    # Two passages of the same date disagree: kept as a conflict, never resolved to either level.
    same_day = FindingReading(finding_id="c", source_date="2024-05-20", levels={"appeal_intent": {none: 0.85, intends: 0.15}})
    f = {f.factor_id: f for f in it._aggregate([older, newer, same_day])}["appeal_intent"]
    assert f.conflict and f.level_label == "conflicting readings"
    assert abs(f.distribution[intends] - 0.525) < 1e-9 and abs(f.distribution[none] - 0.475) < 1e-9
    # A factor no passage reads stays unknown, never a default level.
    assert {f.factor_id: f for f in it._aggregate([older, newer])}["debtor_liquidity"].level_label == "unknown"


class WaiverJudge:
    """Akoustis pays; judgment entered; one passage states an appeal of this judgment is waived."""

    def _o(self, qid: str, **kw) -> SemanticObservation:
        prim = "noul" if "noul_value" in kw else "choice"
        return SemanticObservation(observation_id=f"o-{qid}", call_id="c", profile="dispute_interpretation",
                                   question_id=qid, question_version="3", primitive=prim, answer=None, **kw)

    async def read(self, obligation, evidence, direction, subject_ids):
        entered = 0.05 if subject_ids[0] == "a" else 0.95  # only "b" establishes the judgment; "a" states the waiver
        return [self._o("obligation_direction", probabilities={"borrower": 0.95, "counterparty": 0.03, "not_stated": 0.02}),
                self._o("amount_status", probabilities={"fixed": 0.9, "sought": 0.1}),
                self._o("event_judgment_entered", noul_value=entered)]

    async def relevance(self, obligation, evidence, subject_ids, factor_ids):
        assert "debtor_liquidity" not in factor_ids  # Akoustis pays: its own cash comes from the bank data
        waived = subject_ids[0] == "a"
        return [self._o(f"bears_on_{f}", noul_value=(0.9 if waived and f == "appeal_barred" else 0.05))
                for f in factor_ids]

    async def level(self, *a, **k):  # no graded factor is routed in this fixture
        raise AssertionError("unexpected level call")


def forecaster(d, fs):
    from akoustis_fixture import SETUP, basis

    return Forecaster([d], {f.finding_id: f for f in fs}, borrower=BORROWER, review=REVIEW, horizon=SETUP.horizon,
                      hydrate=lambda f: {"finding": f.finding_id}, setup=SETUP, basis=basis()[1])


class PendingWaiverJudge(WaiverJudge):
    """The waiver fixture, with the post-trial motions pending (so the appeal is still ahead)."""

    async def read(self, obligation, evidence, direction, subject_ids):
        pending = 0.05 if subject_ids[0] == "a" else 0.95  # "b" (the verdict 8-K) establishes the motions
        return [*await super().read(obligation, evidence, direction, subject_ids),
                self._o("event_post_trial_motions_pending", noul_value=pending)]


def test_a_waiver_is_forecast_evidence_and_the_appeal_branch_stays():
    from akoustis_fixture import COMPONENTS, MOTIONS

    fs = [finding("a", "q3_10q"), finding("b", "verdict_8k")]
    it = Interpreter(draft().model_copy(update={"components": COMPONENTS, "motions": MOTIONS,
                                                "commenced": date(2021, 10, 4)}),
                     fs, judge=PendingWaiverJudge(), borrower=BORROWER, sources=SOURCES, hydrate=lambda f: {})
    d = asyncio.run(it.run())
    assert d.status == "interpreted" and d.stage == "post_trial" and d.borrower_role == "debtor"
    barred = next(f for f in d.factors if f.factor_id == "appeal_barred")
    assert barred.probability == 0.9 and barred.level_label == "established"

    fc = forecaster(d, fs)
    paths = fc.all_paths()[d.instance_id][""]
    assert any(("appeal", "", "yes") in p.steps for p in paths)  # the waiver is evidence, not a pruned branch
    appeal = next(n for n in fc.nodes.values() if n.node == "appeal")
    state, fids, readings = fc.state(appeal)
    assert readings[barred.label] == {"probability_present": 0.9}  # the waiver reaches Jev's appeal question
    assert "a" in fids  # with the passage that states it, chosen only because it bears on the waiver
    settle = next(n for n in fc.nodes.values() if n.node == "settlement_offer")
    assert "a" not in fc.state(settle)[1]  # a question the waiver is not routed to never sees it
    a4 = next(n for n in fc.nodes.values() if n.node == "debtor_response")
    assert "p50" in fc.state(a4)[0]["path_facts"]["available_cash_at_decision"]  # the payer's cash is data


class FiledJudge(WaiverJudge):
    """As above, and the passages establish that Akoustis has filed its notice of appeal."""

    async def read(self, obligation, evidence, direction, subject_ids):
        return [*await super().read(obligation, evidence, direction, subject_ids),
                self._o("event_appeal_filed", noul_value=0.95)]


def test_a_filed_appeal_sets_the_stage_and_is_not_forecast():
    fs = [finding("a", "q3_10q"), finding("b", "verdict_8k")]
    d = asyncio.run(interpreter(fs, FiledJudge()).run())
    assert d.stage == "appeal_filed"
    fc = forecaster(d, fs)
    paths = fc.all_paths()[d.instance_id][""]
    assert not any(n.node == "appeal" for n in fc.nodes.values())
    assert all(p.steps[0][0] == "stay" for p in paths)  # the stay is still open; "no appeal" is not a path


class PostTrialJudge(WaiverJudge):
    """As above, and the passages establish that timely post-trial motions against the judgment are pending."""

    async def read(self, obligation, evidence, direction, subject_ids):
        return [*await super().read(obligation, evidence, direction, subject_ids),
                self._o("event_post_trial_motions_pending", noul_value=0.95)]


def test_pending_post_trial_motions_place_the_judgment_in_post_trial():
    fs = [finding("a", "q3_10q"), finding("b", "verdict_8k")]
    d = asyncio.run(interpreter(fs, PostTrialJudge()).run())
    assert d.stage == "post_trial" and d.status == "interpreted"
    assert set(d.established) == {"judgment_entered", "post_trial_motions_pending"}

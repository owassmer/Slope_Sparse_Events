"""Present-state reading of a live dispute: factor aggregation by each factor's rule, and a waiver of appeal kept as
forecast evidence rather than a pruned branch. ChromaDex owes Elysium Health the Delaware fee award (D. Del.
1:18-cv-01434); no network, the judge is a stub returning fixed readings."""

import asyncio
from datetime import date, timedelta

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

BORROWER, PAYEE = "ChromaDex Corporation", "Elysium Health, Inc."
REVIEW = date(2024, 8, 19)
SOURCES = {"q1_10q": ("sec_10q", "2024-05-08"), "q2_10q": ("sec_10q", "2024-08-07"),
           "q2_release": ("press_release", "2024-08-07")}


def finding(fid: str, source: str) -> AtomicFinding:
    span = SourceSpan(source_id=source, section_id=f"{source}#legal", item_id=f"{source}#legal", start=0, end=1, quote="q")
    return AtomicFinding(finding_id=fid, dependency_id="dep", proposition=fid, target="t", spans=(span,),
                         status="accepted")


def draft() -> DisputeInstance:
    return DisputeInstance(
        instance_id="fee", dependency_id="dep", model_id="m", model_version="3", title="Delaware fee award",
        order_reference="D. Del. 1:18-cv-01434", nature="fee_and_cost_award", counterparty=PAYEE,
        finding_ids=("a", "b", "c"), judgment_date=date(2024, 8, 13),
        amount=EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=150_000_000,
                             provenance=Provenance(basis=Basis.DOCUMENTED)))


def interpreter(findings: list[AtomicFinding], judge=None) -> Interpreter:
    return Interpreter(draft(), findings, judge=judge, borrower=BORROWER, sources=SOURCES, hydrate=lambda f: {})


def test_latest_passage_wins_and_same_date_disagreement_stays_a_conflict():
    intends, considering, none = ("The payer states it intends to appeal", "The payer is considering or reserves the "
                                  "right to appeal", "The passage states no position on an appeal")
    fs = [finding("a", "q1_10q"), finding("b", "q2_10q"), finding("c", "q2_release")]
    it = interpreter(fs)
    older = FindingReading(finding_id="a", source_date="2024-05-08", levels={"appeal_intent": {considering: 0.8, none: 0.2}})
    newer = FindingReading(finding_id="b", source_date="2024-08-07", levels={"appeal_intent": {intends: 0.9, none: 0.1}})
    by = {f.factor_id: f for f in it._aggregate([older, newer])}
    assert by["appeal_intent"].level_label == intends and not by["appeal_intent"].conflict
    assert by["appeal_intent"].distribution[considering] == 0  # the May reading is superseded, not averaged in
    assert by["appeal_intent"].decisive.finding_id == "b"

    # Two passages of the same date disagree: kept as a conflict, never resolved to either level.
    same_day = FindingReading(finding_id="c", source_date="2024-08-07", levels={"appeal_intent": {none: 0.85, intends: 0.15}})
    f = {f.factor_id: f for f in it._aggregate([older, newer, same_day])}["appeal_intent"]
    assert f.conflict and f.level_label == "conflicting readings"
    assert abs(f.distribution[intends] - 0.525) < 1e-9 and abs(f.distribution[none] - 0.475) < 1e-9
    # A factor no passage reads stays unknown, never a default level.
    assert {f.factor_id: f for f in it._aggregate([older, newer])}["debtor_liquidity"].level_label == "unknown"


class WaiverJudge:
    """ChromaDex pays; judgment entered; one passage states an appeal of this award is waived."""

    def _o(self, qid: str, **kw) -> SemanticObservation:
        prim = "noul" if "noul_value" in kw else "choice"
        return SemanticObservation(observation_id=f"o-{qid}", call_id="c", profile="dispute_interpretation",
                                   question_id=qid, question_version="3", primitive=prim, answer=None, **kw)

    async def read(self, obligation, evidence, direction, subject_ids):
        return [self._o("obligation_direction", probabilities={"borrower": 0.95, "counterparty": 0.03, "not_stated": 0.02}),
                self._o("amount_status", probabilities={"fixed": 0.9, "sought": 0.1}),
                self._o("event_judgment_entered", noul_value=0.95)]

    async def relevance(self, obligation, evidence, subject_ids):
        waived = subject_ids[0] == "a"
        return [self._o(f"bears_on_{f}", noul_value=(0.9 if waived and f == "appeal_barred" else 0.05))
                for f in ("amount_finality", "appeal_intent", "appeal_barred", "debtor_resistance", "debtor_liquidity",
                          "settlement_signals")]

    async def level(self, *a, **k):  # no graded factor is routed in this fixture
        raise AssertionError("unexpected level call")


def test_a_waiver_is_forecast_evidence_and_the_appeal_branch_stays():
    fs = [finding("a", "q2_10q"), finding("b", "q2_release")]
    d = asyncio.run(interpreter(fs, WaiverJudge()).run())
    assert d.status == "interpreted" and d.stage == "judgment_entered" and d.borrower_role == "debtor"
    barred = next(f for f in d.factors if f.factor_id == "appeal_barred")
    assert barred.probability == 0.9 and barred.level_label == "established"

    fc = Forecaster([d], {f.finding_id: f for f in fs}, borrower=BORROWER, review=REVIEW,
                    horizon=REVIEW + timedelta(days=180), hydrate=lambda f: {"finding": f.finding_id})
    paths = fc.paths(d)
    assert {"appeal_pending", "settled_during_appeal"} <= {p.outcome for p in paths}
    assert any(("appeal", "", "yes") in p.steps for p in paths)
    appeal = next(n for n in fc.nodes.values() if n.node == "appeal")
    state, fids, readings = fc.state(appeal)
    assert readings[barred.label] == {"probability_present": 0.9}  # the waiver reaches Jev's appeal forecast
    assert "a" in fids  # with the passage that states it

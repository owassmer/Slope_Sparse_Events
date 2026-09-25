"""Step 5b financial correctness: Slope products, the connected-bank baseline, the scenario engine and the dispute
scenario compiler (Jev's atomic readings -> established facts -> every permitted cash path). No network.

Dispute texts are real record text: the C.D. Cal. judgment (Dkt. 618) and ChromaDex's Q2 2024 10-Q."""

import asyncio
from datetime import date, timedelta

from app.agent.tools import _date_in_text
from app.decisions.scenarios import AMOUNT_STEP_CENTS, Request, compare, simulate, summarize
from app.disputes.evaluate import Compiler
from app.domain.investigation import (
    AtomicFinding,
    BranchCash,
    DisputeInstance,
    DisputePath,
    SemanticObservation,
    SourceSpan,
)
from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit
from app.finance.bank import load_feed, risk_features
from app.finance.slope_products import load_terms, menu, offer, tier_for

SNAP = "chromadex_20240819"
REVIEW = date(2024, 8, 19)
FUNDING = date(2024, 8, 20)
HORIZON = REVIEW + timedelta(days=180)
REQ = Request(amount_cents=200_000_000, invoice_due=date(2024, 9, 19), funding=FUNDING, requested_term_id="inst_90")

CA_JUDGMENT = "Elysium shall pay to ChromaDex the sum total of $2,500,000 ... Dated: August 13, 2024"
CA_WAIVER = "4. The parties shall not file any post-trial motions or appeals related to the Jury Claims."
DE_FEES = ("The issue is now fully briefed and awaiting a ruling by the Court. In connection with the Court's current "
           "ruling and the Company's intention to appeal this decision, management has assessed that it is reasonably "
           "possible a contingent liability will be incurred.")
DE_AMOUNT = "inclusive of ChromaDex's estimates for post-judgment interest, is approximately $9.8 million"
SOURCES = {"cacd_d618": ("C.D. Cal. Judgment (Dkt. 618)", "2024-08-13"),
           "q1_10q": ("ChromaDex Q1 2024 10-Q", "2024-05-08"), "q2_10q": ("ChromaDex Q2 2024 10-Q", "2024-08-07")}

CA_READING = {"payer": "counterparty", "status": "fixed", "events": {"judgment_entered", "amount_fixed"}}
DE_READING = {"payer": "borrower", "status": "sought", "interest": True, "events": {"entitlement_decided"},
              "bears": {"amount_finality": 2, "appeal_intent": 3}}


def ev(cents: int) -> EvidenceValue:
    return EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=cents, provenance=Provenance(basis=Basis.DOCUMENTED))


def finding(fid: str, quote: str, source: str) -> AtomicFinding:
    return AtomicFinding(finding_id=fid, dependency_id="dep_001", proposition=quote, target="ChromaDex, Inc.",
                         spans=(SourceSpan(source_id=source, section_id=f"{source}#s0", item_id=f"{source}#s0", start=0,
                                           end=len(quote), quote=quote),))


class StubJudge:
    """Answers the atomic reading questions from a fixed reading per finding (what Jev would read in that passage)."""

    def __init__(self, readings: dict[str, dict]):
        self.readings, self.calls = readings, []

    def _o(self, qid, answer, primitive):
        self.calls.append(qid)
        return SemanticObservation(observation_id=f"obs_{len(self.calls)}", call_id="c", profile="dispute_model",
                                   question_id=qid, question_version="3.11.0", primitive=primitive, answer=answer)

    async def read(self, obligation, cited, direction, subject_ids):
        r = self.readings[subject_ids[0]]
        events = ["entitlement_decided", "amount_fixed", "judgment_entered", "appeal_filed", "stay_secured", "paid"]
        return [self._o("obligation_direction", r.get("payer", "not_stated"), "choice"),
                self._o("amount_status", r.get("status", "not_stated"), "choice"),
                self._o("amount_includes_interest", r.get("interest", False), "noul"),
                *(self._o(f"event_{e}", e in r.get("events", set()), "noul") for e in events)]

    async def relevance(self, obligation, cited, subject_ids):
        bears = self.readings[subject_ids[0]].get("bears", {})
        factors = ["amount_finality", "appeal_intent", "appeal_barred", "debtor_resistance", "debtor_liquidity",
                   "settlement_signals"]
        return [self._o(f"bears_on_{f}", f in bears, "noul") for f in factors]

    async def level(self, obligation, cited, factor, rubric, subject_ids):
        return self._o("factor_level", str(self.readings[subject_ids[0]]["bears"][factor["factor_id"]]), "score")


def compile_dispute(findings, judge, amount, judgment=None, obligation="the obligation", fid_prefix="dispute_001"):
    draft = DisputeInstance(instance_id=fid_prefix, dependency_id="dep_001", model_id="m", model_version="0", title="t",
                            obligation=obligation, counterparty="Elysium Health, Inc.",
                            finding_ids=tuple(f.finding_id for f in findings), amount=ev(amount), judgment_date=judgment)
    return asyncio.run(Compiler(draft, findings, judge=judge, borrower="ChromaDex Corporation", sources=SOURCES,
                                review=REVIEW, horizon=HORIZON).run())


def compile_ca(judge, extra=()):
    return compile_dispute([finding("ca1", CA_JUDGMENT, "cacd_d618"), *extra], judge, 250_000_000, date(2024, 8, 13),
                           "the $2.5 million payment under the C.D. Cal. judgment", "dispute_ca")


def compile_de(judge, extra=()):
    return compile_dispute([finding("de1", DE_FEES, "q2_10q"), finding("de2", DE_AMOUNT, "q2_10q"), *extra], judge,
                           980_000_000, None, "the attorneys' fees and costs awarded to Elysium in D. Del.", "dispute_de")


# --- Slope products and the bank baseline -------------------------------------------------------------

def test_offers_conserve_totals_and_prorate_fees():
    terms = load_terms()
    for o in menu(terms, 200_000_000, "B"):
        sched = o.schedule(FUNDING)
        assert sum(p.amount_cents for p in sched) == o.total_cents
        assert sum(p.principal_cents for p in sched) == o.amount_cents
        assert all(p.due > FUNDING for p in sched)
        assert o.prorated_fee_cents(0) == 0 and o.prorated_fee_cents(o.days) == o.fee_cents
    assert offer(terms, "net_30", 200_000_000, "A").fee_cents == 3_200_000  # 1.60% on Net 30, the published rate


def test_bank_feed_matches_public_anchors_and_stops_at_the_review_date():
    feed = load_feed(SNAP)
    assert feed.balances[date(2024, 3, 31)] == 2_756_500_000  # Q1 10-Q cash including restricted
    assert feed.balances[date(2024, 6, 30)] == 2_788_500_000  # Q2 10-Q cash including restricted
    assert feed.balances[REVIEW] < feed.balances[date(2024, 6, 30)]  # Q2's operating drain continues, no later information
    assert feed.period_end == REVIEW and all(t["date"] <= "2024-08-19" for t in feed.transactions)
    tier, _ = tier_for(risk_features(feed), load_terms())
    assert tier == "A"


# --- the engine -----------------------------------------------------------------------------------------

def test_the_invoice_is_paid_once_including_a_partly_financed_remainder():
    feed, terms = load_feed(SNAP), load_terms()
    declined, _, _, _ = simulate(feed, REQ, None, [], "central", HORIZON)
    for amount in (200_000_000, 120_000_000):  # the full invoice, and a smaller supported amount
        o = offer(terms, "net_30", amount, "A")
        financed, coll, owed, _ = simulate(feed, REQ, o, [], "central", HORIZON)
        assert declined[HORIZON] - financed[HORIZON] == o.fee_cents  # financing costs exactly the fee
        assert sum(c for _, c in coll) == o.total_cents and not owed


def test_a_lock_lowers_cash_by_its_amount_and_zero_cash_paths_reproduce_bank_only():
    feed, terms = load_feed(SNAP), load_terms()
    lock = BranchCash(kind="lock", label="bond", amount=ev(980_000_000), window_start=FUNDING,
                      window_end=date(2024, 12, 31))
    s = offer(terms, "inst_90", 200_000_000, "A")
    base, _, _, _ = simulate(feed, REQ, s, [], "stress", HORIZON)
    locked, coll, _, _ = simulate(feed, REQ, s, [lock], "stress", HORIZON)
    assert all(base[d] - locked[d] == 980_000_000 for d in base if d >= FUNDING)
    assert sum(c for _, c in coll) <= s.total_cents
    empty = compile_ca(StubJudge({"ca1": CA_READING}))
    no_cash = [p.model_copy(update={"cash": ()}) for p in empty.paths]
    comp = compare(feed, terms, REQ, [no_cash])
    for name in list(comp.structures):
        rows = {(o.min_cash_cents, tuple(o.collections)) for o in comp.outcomes if o.structure == name}
        assert len(rows) == 1


def test_the_supported_amount_is_sized_on_its_own_paths_and_names_what_binds():
    feed, terms = load_feed(SNAP), load_terms()
    bond = BranchCash(kind="lock", label="bond", amount=ev(1_225_000_000), window_start=FUNDING,
                      window_end=date(2024, 9, 30))

    paths = [DisputePath(path_id="p1", transitions=("appeal_secured",), labels=("bond",), terminal="t", cash=(bond,))]
    comp = compare(feed, terms, REQ, [paths])
    s = summarize(comp, terms)
    rec = s["recommendation"]["event_adjusted"]
    assert not rec["requested_passes"] and not rec["at_requested"]["passes"] and rec["at_requested"]["lowest_cash_on"]
    assert rec["structure"] != "decline" and rec["at_recommended"]["passes"]
    chosen = comp.structures[rec["structure"]]
    assert chosen.amount_cents <= rec["at_recommended"]["order_limit_cents"]  # the card's own figures are consistent
    up = rec["next_amount_up"]
    assert up["amount_cents"] == chosen.amount_cents + AMOUNT_STEP_CENTS and not up["passes"]
    for sc in s["structures"]["inst_90_2000000"]["views"]["event_adjusted"]["scenarios"]:
        assert "collections" in sc and "economics" in sc  # per-branch collections and economics


# --- the dispute scenario compiler ---------------------------------------------------------------------

def test_stage_direction_and_amounts_come_from_readings_and_rules():
    ca = compile_ca(StubJudge({"ca1": CA_READING}))
    assert ca.borrower_role == "creditor" and ca.stage == "judgment_entered" and ca.amount_status == "fixed"
    [pay] = [p for p in ca.paths if p.transitions == ("pay",)]
    [c] = pay.cash
    assert c.kind == "inflow" and c.window_start == date(2024, 9, 12)  # FRCP 62(a): 30-day automatic stay
    assert c.window_end == date(2024, 10, 12) and c.amount.lower > 250_000_000  # plus § 1961 interest
    de = compile_de(StubJudge({"de1": DE_READING, "de2": DE_READING}))
    assert de.borrower_role == "debtor" and de.stage == "amount_pending" and de.amount_status == "sought"
    assert de.amount_includes_interest  # the 10-Q figure includes interest, so § 1961 is not added again
    [lock_path] = [p for p in de.paths if p.transitions == ("amount_fixed", "appeal_secured", "appeal_continues")]
    [lock] = lock_path.cash
    assert lock.kind == "lock" and lock.amount.upper == 1_225_000_000  # 125% bond, up to 100% cash collateral
    assert any(r.factor_id == "amount_status" for r in de.evidence_requests)  # sought, not fixed: get the ruling
    assert all(p.weight_bps is None for p in de.paths + ca.paths)  # no forecast weights


def test_a_confident_pay_reading_cannot_suppress_the_no_receipt_path():
    # (a) Everything in the record points to Elysium paying; the unpaid path (no receipt) is still compiled and tested.
    judge = StubJudge({"ca1": {**CA_READING, "bears": {"appeal_intent": 0, "debtor_resistance": 0, "debtor_liquidity": 3}}})
    ca = compile_ca(judge)
    [pay] = [p for p in ca.paths if p.transitions == ("pay",)]
    assert pay.points_here  # the record points to payment...
    assert any(not any(c.kind == "inflow" for c in p.cash) for p in ca.paths)  # ...but a no-receipt path remains
    comp = compare(load_feed(SNAP), load_terms(), REQ, [list(ca.paths)])
    lowest = min(o.min_cash_cents for o in comp.outcomes if o.scenario.view == "event_adjusted"
                 and o.structure == "inst_90_2000000")
    bank = min(o.min_cash_cents for o in comp.outcomes if o.scenario.view == "bank_only" and o.structure == "inst_90_2000000")
    assert lowest == bank  # the binding case for a receivable is that it never arrives


def test_a_path_the_record_does_not_point_to_still_reaches_policy_and_binds():
    # (b) ChromaDex saying it will NOT appeal (a stated intention) closes nothing: the bond lock is tested and binds.
    reading = {**DE_READING, "bears": {"amount_finality": 2, "appeal_intent": 0}}
    de = compile_de(StubJudge({"de1": reading, "de2": reading}))
    [bond] = [p for p in de.paths if p.transitions == ("amount_fixed", "appeal_secured", "appeal_continues")]
    assert not any("appeal intent" in n for n in bond.points_here)
    s = summarize(compare(load_feed(SNAP), load_terms(), REQ, [list(de.paths)]), load_terms())
    rec = s["recommendation"]["event_adjusted"]
    assert not rec["requested_passes"]
    assert any("appeals and secures a stay" in lab for lab in rec["at_requested"]["lowest_cash_scenario"])
    needs = rec["requested_needs"]
    assert needs["needs_lowest_cash_cents"] == 1_777_777_778  # $2.0M / 75% order share / 15% of lowest cash
    assert any(e["short_always"] for e in needs["paths_short_whatever_else"])


def test_newer_constrained_liquidity_beats_an_older_favourable_reading():
    # (c) 'latest' aggregation: the Q2 passage (newer) decides, not the Q1 one; same-date disagreement is a conflict.
    old = finding("de_q1", "cash, cash equivalents and restricted cash of $27.6 million", "q1_10q")
    new = finding("de_q2", "we may need to raise additional capital to fund an adverse judgment", "q2_10q")
    readings = {"de1": DE_READING, "de2": DE_READING, "de_q1": {"payer": "borrower", "bears": {"debtor_liquidity": 3}},
                "de_q2": {"payer": "borrower", "bears": {"debtor_liquidity": 1}}}
    de = compile_de(StubJudge(readings), (old, new))
    [liq] = [f for f in de.factors if f.factor_id == "debtor_liquidity"]
    assert liq.level == 1 and liq.decisive.finding_id == "de_q2" and not liq.conflict
    same = {**readings, "de_q1b": {"payer": "borrower", "bears": {"debtor_liquidity": 3}}}
    de2 = compile_de(StubJudge(same), (new, finding("de_q1b", "ample liquidity", "q2_10q")))
    [liq2] = [f for f in de2.factors if f.factor_id == "debtor_liquidity"]
    assert liq2.conflict and liq2.level is None  # kept as a conflict, and not used


def test_a_waiver_closes_only_its_own_obligation():
    # (d) The C.D. Cal. waiver covers the Jury Claims: read as covering that payment, it closes that dispute's secured
    # appeal (the unpaid path, with no receipt, remains); it does not bar the appeal of the Delaware fee award.
    ca = compile_ca(StubJudge({"ca1": CA_READING, "ca2": {"payer": "counterparty", "bears": {"appeal_barred": True}}}),
                    (finding("ca2", CA_WAIVER, "cacd_d618"),))
    assert "judgment_entered.appeal_secured" in ca.closed and "ca2" in ca.closed["judgment_entered.appeal_secured"]
    assert any(not p.cash for p in ca.paths)  # no-receipt path still tested
    de = compile_de(StubJudge({"de1": DE_READING, "de2": DE_READING}))
    assert not de.closed and any("appeal_secured" in p.transitions for p in de.paths)


def test_readings_that_disagree_on_who_pays_leave_the_dispute_unmodelled_with_a_request():
    judge = StubJudge({"ca1": CA_READING, "ca2": {"payer": "borrower"}})
    ca = compile_ca(judge, (finding("ca2", CA_WAIVER, "cacd_d618"),))
    assert ca.status == "outside_model" and not ca.paths and ca.evidence_requests[0].factor_id == "direction"


def test_judgment_dates_must_appear_in_the_record():
    assert _date_in_text(date(2024, 8, 13), "Dated: August 13, 2024 Hon. Fred W. Slaughter")
    assert _date_in_text(date(2024, 8, 13), "Document 618 Filed 08/13/24 Page 1 of 3")
    assert not _date_in_text(date(2024, 8, 13), "Filed 08/13/2023")
    assert not _date_in_text(date(2024, 8, 1), "Filed 08/13/24")


def test_an_unmodelled_or_superseded_dispute_is_never_silently_dropped_or_counted():
    import json

    from app.config import CASES_DIR
    from app.decisions.case import decide

    inputs = json.loads((CASES_DIR / SNAP / "run_inputs.json").read_text())
    de = compile_de(StubJudge({"de1": DE_READING, "de2": DE_READING}))
    unplaced = de.model_copy(update={"instance_id": "dispute_002", "status": "outside_model", "title": "unplaced",
                                     "paths": ()})
    duplicate = de.model_copy(update={"instance_id": "dispute_003", "status": "superseded"})
    s = decide(SNAP, inputs, [de, unplaced, duplicate], REVIEW)
    assert s["recommendation"]["event_adjusted"]["incomplete"] == ["unplaced"]
    assert len(s["disputes"]) == 2  # the superseded instance is neither shown nor counted
    once = decide(SNAP, inputs, [de], REVIEW)
    assert once["recommendation"]["event_adjusted"]["structure"] == s["recommendation"]["event_adjusted"]["structure"]
    text = json.dumps(s["conditions"])
    assert "sought" in text and "owed" not in text  # a sought amount is never described as owed


def test_settling_during_an_appeal_returns_the_locked_collateral_with_the_payment():
    # The bond is discharged when the settlement is paid: never the collateral and the settlement out at once.
    de = compile_de(StubJudge({"de1": DE_READING, "de2": DE_READING}))
    by = {p.transitions: p for p in de.paths}
    feed, lows = load_feed(SNAP), {}
    for key in (("amount_fixed", "appeal_secured", "appeal_continues"), ("amount_fixed", "appeal_secured", "settle")):
        daily, _, _, _ = simulate(feed, REQ, None, by[key].cash, "stress", HORIZON)
        lows[key] = min(daily.values())
    # settling swaps the (larger) lock for the payment: never lower than keeping the lock, never both at once
    assert lows[("amount_fixed", "appeal_secured", "settle")] >= lows[("amount_fixed", "appeal_secured", "appeal_continues")]

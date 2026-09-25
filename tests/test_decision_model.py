"""Step 5b financial correctness: Slope products, the connected-bank baseline, the scenario engine and the
post-judgment dispute model (rule-derived dates and amounts, path weights, refinement). No network."""

import asyncio
from datetime import date, timedelta

from app.agent.tools import _date_in_text
from app.decisions.scenarios import AMOUNT_STEP_CENTS, Request, compare, policy_checks, simulate, summarize
from app.disputes.evaluate import Evaluator
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

# Real record text: the C.D. Cal. judgment (Dkt. 618) and ChromaDex's Q2 2024 10-Q on the Delaware fee award.
CA_QUOTE = "Elysium shall pay to ChromaDex the sum total of $2,500,000 ... Dated: August 13, 2024"
DE_QUOTE = ("inclusive of ChromaDex's estimates for post-judgment interest, is approximately $9.8 million")


def ev(cents: int) -> EvidenceValue:
    return EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=cents, provenance=Provenance(basis=Basis.DOCUMENTED))


def finding(fid: str, quote: str, source: str) -> AtomicFinding:
    return AtomicFinding(finding_id=fid, dependency_id="dep_001", proposition=quote, target="ChromaDex, Inc.",
                         spans=(SourceSpan(source_id=source, section_id=f"{source}#s0", item_id=f"{source}#s0", start=0,
                                           end=len(quote), quote=quote),))


def instance(role: str, amount: int, judgment: date | None, fid: str) -> DisputeInstance:
    return DisputeInstance(instance_id="dispute_001", dependency_id="dep_001", model_id="m", model_version="0",
                           title="t", borrower_role=role, counterparty="Elysium Health, Inc.", finding_ids=(fid,),
                           amount=ev(amount), judgment_date=judgment)


class StubJudge:
    """Answers each atomic question from fixed preferences; `top` is the transition answer's share."""

    def __init__(self, stage: str, prefer: dict[str, str], top: float = 0.8):
        self.stage_id, self.prefer, self.top, self.calls = stage, prefer, top, []

    def _obs(self, qid, options, pick, top, primitive="choice"):
        keys = list(options)
        rest = (1 - top) / (len(keys) - 1)
        probs = {k: (top if k == pick else rest) for k in keys}
        self.calls.append(qid)
        return SemanticObservation(observation_id=f"obs_{len(self.calls)}", call_id="c", profile="dispute_model",
                                   question_id=qid, question_version="3.10.0", primitive=primitive, answer=pick,
                                   probabilities=probs)

    async def stage(self, dispute, record, options, subject_ids):
        return self._obs("dispute_stage", options, self.stage_id, 0.9)

    async def route(self, dispute, finding, options, subject_ids):
        return self._obs("factor_routing", options, "appeal_intent", 0.9)

    async def level(self, dispute, finding, factor, rubric, subject_ids):
        return self._obs("factor_level", {str(i): r for i, r in enumerate(rubric)}, "3", 0.9, "score")

    async def present(self, dispute, finding, factor, subject_ids):
        return self._obs("factor_present", {"true": 1, "false": 0}, "false", 0.9)

    async def transition(self, dispute, record, stage, premise, factor_results, options, subject_ids):
        top = 0.85 if factor_results else self.top
        return self._obs("dispute_transition", options, self.prefer.get(stage["stage"], next(iter(options))), top)


def evaluate(role, amount, judgment, quote, source, judge, request=200_000_000):
    f = finding("fnd_001", quote, source)
    ev_ = Evaluator(instance(role, amount, judgment, f.finding_id), [f], judge=judge, borrower="ChromaDex, Inc.",
                    source_dates={source: "2024-08-13"}, review=REVIEW, horizon=HORIZON, request_cents=request)
    return asyncio.run(ev_.run())


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
    empty = evaluate("debtor", 1_000_000, date(2024, 8, 13), CA_QUOTE, "cacd_16cv2277_d618_judgment",
                     StubJudge("judgment_entered", {"judgment_entered": "appeal_bonded", "appeal_pending": "appeal_continues"}))
    no_cash = [p.model_copy(update={"cash": ()}) for p in empty.paths]
    comp = compare(feed, terms, REQ, [no_cash])
    for name in list(comp.structures):
        rows = {(o.min_cash_cents, tuple(o.collections)) for o in comp.outcomes if o.structure == name}
        assert len(rows) == 1


def test_the_supported_amount_is_sized_on_its_own_paths_and_names_what_binds():
    feed, terms = load_feed(SNAP), load_terms()
    bond = BranchCash(kind="lock", label="bond", amount=ev(2_600_000_000), window_start=FUNDING,
                      window_end=date(2024, 9, 30))

    paths = [DisputePath(path_id="p1", transitions=("appeal_bonded",), labels=("bond",), terminal="t", weight_bps=10_000,
                         cash=(bond,))]
    comp = compare(feed, terms, REQ, [paths])
    s = summarize(comp, terms)
    rec = s["recommendation"]["event_adjusted"]
    assert not rec["requested_passes"] and rec["binding"]["on"]
    if rec["structure"] != "decline":
        chosen = comp.structures[rec["structure"]]
        assert policy_checks(comp, terms, chosen.offer_id, "event_adjusted")["passes"]
        bigger = f"inst_90_{(chosen.amount_cents + AMOUNT_STEP_CENTS) // 100}"
        assert bigger not in comp.structures or not policy_checks(comp, terms, bigger, "event_adjusted")["passes"]
    for sc in s["structures"]["inst_90_2000000"]["views"]["event_adjusted"]["scenarios"]:
        assert "collections" in sc and "economics" in sc  # per-branch collections and economics


# --- the dispute model ----------------------------------------------------------------------------------

def test_rule_derived_dates_and_amounts():
    judge = StubJudge("judgment_entered", {"judgment_entered": "pay"})
    ca = evaluate("creditor", 250_000_000, date(2024, 8, 13), CA_QUOTE, "cacd_16cv2277_d618_judgment", judge)
    pay = next(p for p in ca.paths if p.transitions == ("pay",))
    [c] = pay.cash
    assert c.kind == "inflow" and c.window_start == date(2024, 9, 12)  # FRCP 62(a): 30-day automatic stay
    assert c.window_end == date(2024, 10, 12) and c.amount.lower > 250_000_000  # plus § 1961 interest
    bonded = next(p for p in ca.paths if p.transitions[0] == "appeal_bonded")
    assert not any(x.kind == "inflow" for x in bonded.cash[:1]) or bonded.transitions[-1] == "settle"
    de = evaluate("debtor", 980_000_000, None, DE_QUOTE, "cdxc_2024q2_10q",
                  StubJudge("amount_pending", {"amount_pending": "amount_fixed", "judgment_entered": "appeal_bonded",
                                               "appeal_pending": "appeal_continues"}))
    path = next(p for p in de.paths if p.transitions == ("amount_fixed", "appeal_bonded", "appeal_continues"))
    [lock] = path.cash
    assert lock.kind == "lock" and lock.amount.upper == 1_225_000_000  # 125% bond, up to 100% cash collateral
    assert lock.window_start == date(2024, 8, 20) and lock.window_end == REVIEW + timedelta(days=150)


def test_path_weights_are_products_that_sum_to_one():
    de = evaluate("debtor", 980_000_000, None, DE_QUOTE, "cdxc_2024q2_10q",
                  StubJudge("amount_pending", {"amount_pending": "amount_fixed", "judgment_entered": "appeal_bonded",
                                               "appeal_pending": "appeal_continues", "enforcement": "collected"}))
    assert sum(p.weight_bps for p in de.paths) == 10_000
    w = {p.transitions: p.weight_bps for p in de.paths}
    # conditional weights multiply along a path: continues (80%) vs settles (20%) under the same two earlier steps
    assert abs(w[("amount_fixed", "appeal_bonded", "appeal_continues")]
               - 4 * w[("amount_fixed", "appeal_bonded", "settle")]) <= 4


def test_refinement_only_when_uncertain_and_decision_relevant():
    prefer = {"judgment_entered": "pay"}
    confident = StubJudge("judgment_entered", prefer, top=0.8)
    evaluate("creditor", 250_000_000, date(2024, 8, 13), CA_QUOTE, "cacd_16cv2277_d618_judgment", confident)
    assert "factor_routing" not in confident.calls
    uncertain = StubJudge("judgment_entered", prefer, top=0.4)
    out = evaluate("creditor", 250_000_000, date(2024, 8, 13), CA_QUOTE, "cacd_16cv2277_d618_judgment", uncertain)
    assert "factor_routing" in uncertain.calls and out.transitions[0].refined
    assert any(f.level is None for f in out.factors) or not out.evidence_requests
    small = StubJudge("judgment_entered", prefer, top=0.4)
    evaluate("creditor", 1_000_000, date(2024, 8, 13), CA_QUOTE, "cacd_16cv2277_d618_judgment", small)
    assert "factor_routing" not in small.calls  # USD 10k cannot move a USD 2M decision


def test_judgment_dates_must_appear_in_the_record():
    assert _date_in_text(date(2024, 8, 13), "Dated: August 13, 2024 Hon. Fred W. Slaughter")
    assert _date_in_text(date(2024, 8, 13), "Document 618 Filed 08/13/24 Page 1 of 3")
    assert not _date_in_text(date(2024, 8, 13), "Filed 08/13/2023")
    assert not _date_in_text(date(2024, 8, 1), "Filed 08/13/24")


def test_combined_dispute_weights_sum_to_one_and_expected_never_exceeds_contract():
    ca = evaluate("creditor", 250_000_000, date(2024, 8, 13), CA_QUOTE, "cacd_16cv2277_d618_judgment",
                  StubJudge("judgment_entered", {"judgment_entered": "pay"}, top=0.91))
    de = evaluate("debtor", 980_000_000, None, DE_QUOTE, "cdxc_2024q2_10q",
                  StubJudge("amount_pending", {"amount_pending": "amount_fixed", "judgment_entered": "appeal_bonded"}))
    terms = load_terms()
    comp = compare(load_feed(SNAP), terms, REQ, [list(ca.paths), list(de.paths)])
    central = [sc for sc in comp.scenarios if sc.view == "event_adjusted" and sc.placement == "central"]
    assert sum(sc.weight_bps for sc in central) == 10_000
    for v in summarize(comp, terms)["structures"]["inst_90_2000000"]["views"].values():
        assert sum(c["amount_cents"] for c in v["expected_collections"]) <= sum(r["amount_cents"] for r in v["contractual"])

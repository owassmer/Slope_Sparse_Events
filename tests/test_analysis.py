"""Probabilistic analysis: probability composition, the signal reaching finance, no scenario deletion, cash
conservation, paired simulation and weighted statistics. No network: judgments are supplied directly."""

import json
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from app.agent.tools import ToolError, _date_in_text, _neutral_reference
from app.analysis.core import Analysis, EventModel, stress
from app.analysis.engine import run, schedule_arrays
from app.analysis.events import Draws, EventCash, event_cash
from app.analysis.setup import DRAWS, Setup
from app.analysis.stats import weighted_quantiles
from app.config import ROOT
from app.disputes.forecast import Forecaster, Judgment, combo_probability, distributions, joint_paths
from app.disputes.rules import load_model
from app.domain.investigation import Decisive, DisputeInstance
from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit
from app.finance.bank import load_feed

SNAP = "akoustis_20240620"
REVIEW = date(2024, 6, 20)
SETUP = Setup(review=REVIEW, horizon=REVIEW + timedelta(days=180), funding=date(2024, 6, 21),
              invoice_due=date(2024, 7, 22), invoice_cents=200_000_000, amount_cents=200_000_000, fee_bps=370,
              installments=3, days=90, discount_rate_bps=800)


def ev(cents: int) -> EvidenceValue:
    return EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=cents, provenance=Provenance(basis=Basis.DOCUMENTED))


def dispute(iid: str, role: str, stage: str, amount: int, judgment: date | None = None) -> DisputeInstance:
    dec = Decisive(finding_id="f", source_date="2024-06-17", quote="q")
    return DisputeInstance(instance_id=iid, dependency_id="dep", model_id="m", model_version="3", title=iid,
                           order_reference="D. Del. 1:21-cv-01417", nature="fee_and_cost_award",
                           counterparty="Qorvo, Inc.", finding_ids=("f",), amount=ev(amount),
                           judgment_date=judgment, borrower_role=role, stage=stage, amount_status="sought",
                           amount_includes_interest=True, established={"entitlement_decided": dec})


def model_with(disputes: list[DisputeInstance], p: float = 0.5, form: dict | None = None) -> EventModel:
    """Every node answered with probability p (security form: `form`), built through the real Forecaster."""
    fc = Forecaster(disputes, {}, borrower="Akoustis Technologies, Inc.", review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {})
    per = fc.all_paths()
    judgments = {}
    for n in fc.nodes.values():
        dist = dict(form or {"cash_deposit": 0.2, "surety_bond": 0.5, "letter_of_credit": 0.3}) if len(n.branches) == 3 \
            else {"yes": p, "no": 1 - p}
        judgments[n.key] = Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                                    event=n.event, assumptions=n.assumptions, window=n.window, distribution=dist)
    return EventModel({d.instance_id: d for d in fc.disputes}, judgments, per, fc.ordered())


# Mechanics fixtures, not case facts. DE uses the amount of Qorvo's 17 Jun 2024 fee motion (D.I. 618), but its stage
# (entitlement decided, amount pending) is set for the test: at D the motion was pending and the court "may" award fees.
# CA is a creditor instance Akoustis does not hold at D; it exercises a second same-counterparty dispute conditioned
# on the first.
DE = dispute("de", "debtor", "amount_pending", 1_211_612_330)
CA = dispute("ca", "creditor", "judgment_entered", 250_000_000, date(2024, 6, 17))


# 1. Probability composition ----------------------------------------------------------------------------

def test_conditional_probabilities_compose_and_conserve_mass():
    m = model_with([DE, CA], p=0.3)
    probs = m.probs()
    assert abs(probs.sum() - 1) < 1e-12 and len(m.combos) == len(probs)
    # a hand-computable path: settle before the ruling (0.3), then the second dispute paid voluntarily, no appeal:
    # settle after judgment no (0.7), appeal no (0.7), pays yes (0.3) -> 0.3 x 0.7 x 0.7 x 0.3
    i = next(i for i, c in enumerate(m.combos) if c[0].outcome == "settled" and len(c[0].steps) == 1
             and c[1].outcome == "paid" and ("appeal", "", "no") in c[1].steps)
    assert abs(probs[i] - 0.3 * 0.7 * 0.7 * 0.3) < 1e-12
    # the second dispute's questions are conditioned on the first's outcome class, never multiplied unconditionally
    assert {p.cls for c in m.combos for p in c if p.instance_id == "ca"} == {"counterparty_receives", "no_cash"}
    # a choice node uses its full distribution; an override of one node keeps the total at one
    key = next(k for k in m.judgments if k.endswith("security_form"))
    assert abs(m.probs({key: {"cash_deposit": 1, "surety_bond": 0, "letter_of_credit": 0}}).sum() - 1) < 1e-12


# 2. The signal reaches finance --------------------------------------------------------------------------

def test_a_node_probability_moves_dated_collections_by_a_hand_computable_amount():
    # Tight cash: opening $1.0M, no operating flows, $900k financed at no fee (three $300k installments). On path A a
    # $950k payment leaves $50k, so the first installment collects $50k and nothing more; on path B all is collected.
    st = replace(SETUP, invoice_cents=90_000_000, amount_cents=90_000_000, fee_bps=0)
    days, n = 180, 4
    ops = np.zeros((n, days), dtype=np.int64)
    hit = EventCash.zeros(n, days)
    hit.cash[:, 0] = -95_000_000
    a = run(st, 100_000_000, ops, hit)
    b = run(st, 100_000_000, ops, EventCash.zeros(n, days))
    due, maturity, _, total = schedule_arrays(st)
    first = int(np.nonzero(due)[0][0])
    assert (a.collections[:, first] == 5_000_000).all() and (b.collections[:, first] == 30_000_000).all()
    assert (a.uncollected_maturity == 85_000_000).all() and (b.uncollected_maturity == 0).all()
    for p in (0.0, 0.4, 1.0):  # expected uncollected balance is linear in the node probability
        expected = p * a.uncollected_maturity.mean() + (1 - p) * b.uncollected_maturity.mean()
        assert expected == pytest.approx(p * 85_000_000)
    assert (a.lender_pv < b.lender_pv).all() and (a.dollar_days > b.dollar_days).all()  # capital tied up longer


# 3. No scenario deletion --------------------------------------------------------------------------------

def test_a_zero_probability_path_stays_in_the_stress_set():
    m = model_with([DE], p=0.0)
    probs = m.probs()
    zero = [i for i, p in enumerate(probs) if p == 0]
    assert zero and len(m.combos) == len(probs)
    rows = stress(load_feed(SNAP), SETUP, m)
    assert {r["index"] for r in rows} == set(range(len(m.combos)))  # every feasible path, including zero-probability
    bond = next(r for r in rows if any("surety bond" in p["label"] and "appeal still pending" in p["label"] for p in r["paths"]))
    assert bond["probability"] == 0 and bond["min_cash_p5_cents"] < max(r["min_cash_p5_cents"] for r in rows)


# 4. Cash conservation -----------------------------------------------------------------------------------

def test_collateral_and_credit_capacity_are_released_exactly_on_the_settlement_date():
    m, model = model_with([DE]), load_model()
    draws = Draws(DDRAWS := 64)
    for form, kind in (("surety_bond", "lock"), ("letter_of_credit", "capacity")):
        path = next(p for p in m.per["de"][""] if ("security_form", "", form) in p.steps
                    and p.outcome == "settled_during_appeal")
        ec = event_cash(DE, path, SETUP, model, draws)
        level = np.cumsum(getattr(ec, kind), axis=1)
        other = ec.capacity if kind == "lock" else ec.lock
        settled = ec.cash < 0  # the settlement payment day (debtor pays)
        delta = getattr(ec, kind)
        for d in range(DDRAWS):
            day = np.nonzero(settled[d])[0]
            if len(day):  # settled inside the period: released that day, exactly what was locked, never before the lock
                locked_on = np.nonzero(delta[d] > 0)[0]
                assert (level[d, day[0]:] == 0).all() and delta[d].sum() == 0
                assert len(locked_on) <= 1 and all(x <= day[0] for x in locked_on)  # none: locked and settled same day
        assert not other.any() and (level >= 0).all()
        if kind == "capacity":
            # a letter of credit never locks cash; cash moves only by the settlement itself (some draws fall past the period)
            assert (ec.cash <= 0).all() and ec.cash.sum(axis=1).min() < 0


def test_the_invoice_remainder_is_paid_once_and_collections_never_exceed_the_contract():
    st = replace(SETUP, amount_cents=150_000_000)
    ops = np.zeros((2, 180), dtype=np.int64)
    financed = run(st, 1_000_000_000, ops, EventCash.zeros(2, 180))
    full = run(SETUP, 1_000_000_000, ops, EventCash.zeros(2, 180))
    _, _, _, total = schedule_arrays(st)
    assert financed.collections.sum(axis=1).tolist() == [total, total]
    # financed $1.5M: the borrower pays the $500k remainder once, and less to Slope; nothing else differs
    diff = full.cash[0, -1] - financed.cash[0, -1]
    assert diff == -((full.collections[0].sum() - financed.collections[0].sum()) - 50_000_000)


def test_events_keep_the_full_window_jev_was_asked_about():
    # Enforcement runs 30-120 days after the ruling, which is drawn over the whole period: part of it falls past the
    # horizon. Those draws book nothing; no date is squeezed into the period.
    m, model = model_with([DE]), load_model()
    path = next(p for p in m.per["de"][""] if p.outcome == "collected" and ("appeal", "", "no") in p.steps)
    ec = event_cash(DE, path, SETUP, model, Draws(512))
    inside = (ec.cash != 0).any(axis=1).mean()
    assert 0.3 < inside < 0.95


# 5. Paired simulation -----------------------------------------------------------------------------------

def test_views_match_without_events_and_a_fixed_event_moves_cash_exactly():
    feed = load_feed(SNAP)
    m = model_with([DE], p=0.5)
    quiet = replace(m, per={"de": {"": [p for p in m.per["de"][""] if p.outcome in ("unresolved",)]}}, combos=[])
    a = Analysis(feed, SETUP, quiet)
    assert all((t.cash == a.bank.cash).all() for t in a.paths)  # no cash event: identical draws, identical results
    ev_ = EventCash.zeros(DRAWS, 180)
    ev_.cash[:, 29] = -300_000_000
    moved = run(SETUP, feed.available_cents, a.ops, ev_)
    assert (a.bank.cash[:, 29:] - moved.cash[:, 29:] == 300_000_000).all() and (a.bank.cash[:, :29] == moved.cash[:, :29]).all()


# 6. Weighted statistics ---------------------------------------------------------------------------------

def test_weighted_expectations_and_quantiles_match_a_fixture():
    values, weights = np.array([10.0, 20.0, 30.0, 40.0]), np.array([0.1, 0.2, 0.3, 0.4])
    assert float(values @ weights) == pytest.approx(30.0)
    assert weighted_quantiles(values, weights, (0.05, 0.3, 0.31, 0.95)).tolist() == [10.0, 20.0, 30.0, 40.0]
    grid = np.array([[1.0, 5.0], [2.0, 4.0], [3.0, 3.0]])
    assert weighted_quantiles(grid, np.array([0.5, 0.25, 0.25]), (0.5,)).tolist() == [[1.0, 4.0]]


# Guards on evidence and inputs ---------------------------------------------------------------------------

def test_the_docket_reference_cannot_pre_answer_jev():
    parties = ["Akoustis Technologies, Inc.", "Qorvo, Inc."]
    assert _neutral_reference("D. Del. 1:21-cv-01417, Dkt. 618", parties) == "D. Del. 1:21-cv-01417, Dkt. 618"
    for loaded in ("Fees payable by Akoustis to Qorvo under Dkt. 618", "D. Del. 1:21-cv-01417 $38,595,023",
                   "Dkt. 602 judgment against Akoustis", "D. Del. 1:21-cv-01417 (amount sought, Akoustis intends to appeal)"):
        with pytest.raises(ToolError):
            _neutral_reference(loaded, parties)


def test_judgment_dates_must_appear_in_the_record():
    assert _date_in_text(date(2024, 5, 17), "the jury duly rendered its verdict on May 17, 2024 (ECF No. 601)")
    assert _date_in_text(date(2024, 5, 20), "Document 602 Filed 05/20/24 Page 1 of 1")
    assert not _date_in_text(date(2024, 5, 20), "Filed 05/20/2023")


def test_bank_feed_matches_public_anchors_and_stops_at_the_review_date():
    feed = load_feed(SNAP)
    # 31 Mar: the 10-Q balance. 20 Jun: dated lumps (24 May offering, 17 Jun coupon) plus 57/63 of the undated
    # April-June remainder (Decision D1 as amended).
    assert feed.balances[date(2024, 3, 31)] == 1_520_000_000 and feed.balances[REVIEW] == 1_716_309_524
    assert feed.period_end == REVIEW and all(t["date"] <= "2024-06-20" for t in feed.transactions)
    # The USD 8.0M secured note from a key customer arrived on 26 Jun: nothing like it may sit in the feed.
    assert not any(t["amount_cents"] >= 800_000_000 and t["category"] != "equity_proceeds" for t in feed.transactions)


def test_the_line_limit_applies_slopes_rule_to_the_feed():
    # 15% x (mean monthly customer receipts - mean monthly debt service), March to May 2024, rounded down.
    feed, inputs = load_feed(SNAP), json.loads((ROOT / "cases" / SNAP / "run_inputs.json").read_text())
    line = inputs["financing_plan"]["line"]
    by_month = {m: 0 for m in ("2024-03", "2024-04", "2024-05")}
    for t in feed.transactions:
        if t["date"][:7] in by_month and t["category"] in ("customer_receipts", "debt_service"):
            by_month[t["date"][:7]] += t["amount_cents"]  # receipts positive, debt service negative
    assert line["limit_cents"] == sum(by_month.values()) * 15 // 300 == 35_717_237
    draw = inputs["financing_plan"]["supplied_terms"]["amount_cents"]
    assert draw <= line["limit_cents"] and draw == line["supplied_draw"]["amount_cents"]


def test_the_supplied_schedule_conserves_its_totals():
    o = SETUP.offer
    sched = o.schedule(SETUP.funding)
    assert sum(p.amount_cents for p in sched) == o.total_cents == 207_400_000
    assert sum(p.principal_cents for p in sched) == o.amount_cents and all(p.due > SETUP.funding for p in sched)
    assert distributions({}, None) == {} and combo_probability((), {}) == 1.0 and joint_paths({}, []) == [()]

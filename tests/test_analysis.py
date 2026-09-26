"""Probabilistic analysis: probability composition, no scenario deletion, cash conservation of event effects and
weighted statistics (the line and petition are in test_line.py). No network: judgments are supplied directly."""

import json
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from app.agent.tools import ToolError, _date_in_text, _neutral_reference
from app.analysis.core import Analysis, EventModel, neutral_overrides, stress
from app.analysis.engine import run
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


def test_events_keep_the_full_window_jev_was_asked_about():
    # Enforcement runs 30-120 days after the ruling, which is drawn over the whole period: part of it falls past the
    # horizon. Those draws book nothing; no date is squeezed into the period.
    m, model = model_with([DE]), load_model()
    path = next(p for p in m.per["de"][""] if p.outcome == "collected" and ("appeal", "", "no") in p.steps)
    ec = event_cash(DE, path, SETUP, model, Draws(512))
    inside = (ec.cash != 0).any(axis=1).mean()
    assert 0.3 < inside < 0.95


# 5. Paired simulation and attribution -------------------------------------------------------------------

def test_views_match_without_events_and_event_cash_is_conserved():
    feed = load_feed(SNAP)
    a = Analysis(feed, SETUP, EventModel({}, {}, {}, []))
    v = a.views()
    assert v["bank_only"] == v["event_adjusted"]  # no events: the same draws, the same line, identical outputs
    m = model_with([DE], p=0.5)
    quiet = replace(m, per={"de": {"": [p for p in m.per["de"][""] if p.outcome == "unresolved"]}}, combos=[])
    b = Analysis(feed, SETUP, quiet)
    assert all((t.cash == b.bank.cash).all() and (t.collections == b.bank.collections).all() for t in b.paths)
    hit = EventCash.zeros(DRAWS, 180)
    hit.cash[:, 29] = -300_000_000
    moved = run(b.line, feed.available_cents, hit)
    step = np.where(np.arange(180) >= 29, 300_000_000, 0)
    assert (b.bank.cash - moved.cash == step + np.cumsum(moved.collections - b.bank.collections, axis=1)
            - np.cumsum(moved.fundings - b.bank.fundings, axis=1)).all()


def test_attribution_is_three_reweightings_of_the_same_trajectories():
    m = model_with([DE], p=0.3)
    a = Analysis(load_feed(SNAP), SETUP, m)
    steps = a.attribution()
    assert [s["step"] for s in steps] == ["bank_only", "record", "jev"]
    assert steps[0]["metrics"] == a.views()["bank_only"]["metrics"]
    assert steps[2]["metrics"] == a.views()["event_adjusted"]["metrics"]
    neutral = neutral_overrides(m)
    assert all(sum(d.values()) == pytest.approx(1) and len(set(d.values())) == 1 for d in neutral.values())
    assert steps[1]["metrics"] == a.views(neutral)["event_adjusted"]["metrics"]
    assert steps[2]["delta"]["lender_pv_cents"] == steps[2]["metrics"]["lender_pv_cents"] - \
        steps[1]["metrics"]["lender_pv_cents"]
    key = next(iter(m.judgments))  # the event model's own neutral map, when it supplies one, is used as given
    assert neutral_overrides(replace(m, neutral={key: {"yes": 1.0, "no": 0.0}}, combos=m.combos)) == \
        {key: {"yes": 1.0, "no": 0.0}}


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

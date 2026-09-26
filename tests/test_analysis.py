"""Probabilistic analysis: probability composition, no scenario deletion, cash conservation of event effects and
weighted statistics (the line and petition are in test_line.py; the chains in test_chains.py). No network: judgments
are supplied directly."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest
from akoustis_fixture import REVIEW, SETUP, SNAP, basis, judgment

from app.agent.tools import ToolError, _date_in_text, _neutral_reference
from app.analysis.core import Analysis, EventModel, neutral_overrides, stress
from app.analysis.engine import run
from app.analysis.events import EventCash
from app.analysis.setup import DRAWS
from app.analysis.stats import weighted_quantiles
from app.config import ROOT
from app.disputes.forecast import (
    Forecaster,
    Judgment,
    combo_probability,
    distributions,
    joint_paths,
    neutral_map,
    path_probability,
)
from app.finance.bank import load_feed

# A mechanics fixture on the Akoustis docket: a $2.0M judgment entered 20 May 2024 with no motions pending (the
# amount is chosen so that paying and a self-funded bond are feasible on some trajectories; it is not a case fact).
SMALL = judgment(stage="judgment_entered", motions=(), components=(), financing=(),
                 amount=judgment().amount.model_copy(update={"value": 200_000_000}))


def model_with(disputes, p: float = 0.5) -> EventModel:
    """Every Noul answered p, every Choice uniform, built through the real Forecaster (path facts pre-simulated)."""
    fc = Forecaster(disputes, {}, borrower="Akoustis Technologies, Inc.", review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {}, setup=SETUP, basis=basis()[1])
    per = fc.all_paths()
    judgments = {n.key: Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                                 event=n.event, assumptions=n.assumptions, window=n.window,
                                 distribution={"yes": p, "no": 1 - p} if n.branches == ("yes", "no")
                                 else {b: 1 / len(n.branches) for b in n.branches}) for n in fc.nodes.values()}
    return EventModel({d.instance_id: d for d in fc.disputes}, judgments, per, fc.ordered(),
                      neutral=neutral_map(judgments))


# 1. Probability composition ----------------------------------------------------------------------------

def test_conditional_probabilities_compose_and_conserve_mass():
    m = model_with([SMALL], p=0.3)
    probs = m.probs()
    assert abs(probs.sum() - 1) < 1e-12 and len(m.combos) == len(probs)
    # a hand-computable path: stay (motion x approval), no levy before approval, settle while stayed (offer x
    # accept), no filing at tau
    i = next(i for i, c in enumerate(m.combos) if c[0].steps == (("stay", "post", "yes"), ("enforce", "post", "none"),
                                                                  ("settle", "I4", "yes"), ("cash_floor", "", "no")))
    assert abs(probs[i] - (0.3 * 0.3) * 0.7 * (0.3 * 0.3) * 0.7) < 1e-12
    # a choice node uses its full distribution; an override of one node keeps the total at one
    key = next(k for k in m.judgments if ":debtor_response" in k)
    assert abs(m.probs({key: {b: float(b == "file") for b in m.judgments[key].distribution}}).sum() - 1) < 1e-12
    dist = distributions(m.judgments)
    assert all(path_probability(c[0].edges, dist) == pytest.approx(probs[j]) for j, c in enumerate(m.combos))


# 3. No scenario deletion --------------------------------------------------------------------------------

def test_a_zero_probability_path_stays_in_the_stress_set():
    m = model_with([SMALL], p=0.0)
    probs = m.probs()
    zero = [i for i, p in enumerate(probs) if p == 0]
    assert zero and len(m.combos) == len(probs)
    rows = stress(load_feed(SNAP), SETUP, m)
    assert {r["index"] for r in rows} == set(range(len(m.combos)))  # every feasible path, including zero-probability
    assert any(r["probability"] == 0 for r in rows)


# 5. Paired simulation and attribution -------------------------------------------------------------------

def test_views_match_without_events_and_event_cash_is_conserved():
    feed = load_feed(SNAP)
    b = Analysis(feed, SETUP, EventModel({}, {}, {}, []))
    v = b.views()
    assert v["bank_only"] == v["event_adjusted"]  # no events: the same draws, the same line, identical outputs
    hit = EventCash.zeros(DRAWS, 180)
    hit.cash[:, 29] = -300_000_000
    moved = run(b.line, feed.available_cents, hit)
    step = np.where(np.arange(180) >= 29, 300_000_000, 0)
    assert (b.bank.cash - moved.cash == step + np.cumsum(moved.collections - b.bank.collections, axis=1)
            - np.cumsum(moved.fundings - b.bank.fundings, axis=1)).all()


def test_attribution_is_three_reweightings_of_the_same_trajectories():
    m = model_with([SMALL], p=0.3)
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

"""Probabilistic analysis: probability composition, no scenario deletion, cash conservation of event effects and
weighted statistics (the line and petition are in test_line.py; the chains in test_chains.py). No network: judgments
are supplied directly."""

import json
from datetime import date

import numpy as np
import pytest
from akoustis_fixture import REVIEW, SETUP, SNAP, judgment

from app.agent.tools import ToolError, _date_in_text, _neutral_reference
from app.analysis.stats import weighted_quantiles
from app.config import ROOT
from app.disputes.forecast import (
    combo_probability,
    distributions,
    joint_paths,
)
from app.finance.bank import load_feed

# A mechanics fixture on the Akoustis docket: a $2.0M judgment entered 20 May 2024 with no motions pending (the
# amount is chosen so that paying and a self-funded bond are feasible on some trajectories; it is not a case fact).
SMALL = judgment(stage="judgment_entered", motions=(), components=(), financing=(),
                 amount=judgment().amount.model_copy(update={"value": 200_000_000}))


# 1. Probability composition ----------------------------------------------------------------------------


# 3. No scenario deletion --------------------------------------------------------------------------------


# 5. Paired simulation and attribution -------------------------------------------------------------------


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

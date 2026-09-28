"""The 14 May 2024 Akoustis case inputs (spec §16.2-16.3): the feed holds nothing after the review date and no
later-reported figure, and the line's limit is Slope's rule on this feed."""

import json
from datetime import date
from decimal import ROUND_DOWN, Decimal

from app.analysis import operating
from app.analysis.engine import prepare
from app.analysis.setup import DRAWS, SEED, setup_from_inputs
from app.config import ROOT
from app.finance.bank import load_feed

SNAP = "akoustis_20240514"
REVIEW = date(2024, 5, 14)
INPUTS = json.loads((ROOT / "cases" / SNAP / "run_inputs.json").read_text())


def _month(feed, key: str, cat: str) -> int:
    return sum(t["amount_cents"] for t in feed.transactions if t["date"][:7] == key and t["category"] == cat)


def test_the_feed_stops_at_the_review_date_and_imports_no_later_flow():
    feed = load_feed(SNAP)
    assert feed.period_end == REVIEW and max(t["date"] for t in feed.transactions) <= "2024-05-14"
    assert feed.balances[date(2024, 3, 31)] == 1_520_000_000  # the 10-Q's 31 Mar balance
    assert not any(t["category"] == "debt_service" for t in feed.transactions)  # the coupon falls on 17 Jun
    equity = [t for t in feed.transactions if t["category"] == "equity_proceeds"]
    assert [(t["date"], t["amount_cents"]) for t in equity] == [("2024-01-29", 1_040_700_000)]  # no 24 May offering
    # April continues the March quarter's monthly run rate per category (quarter / 3), not the later April-June totals
    for cat, quarter in (("customer_receipts", 791_000_000), ("operations_other", -29_100_000)):
        assert _month(feed, "2024-04", cat) == round(quarter / 3)
    outflows = sum(_month(feed, "2024-04", c) for c in ("payroll", "legal_fees", "supplier_invoice"))
    assert outflows == -round(1_570_100_000 / 3)
    assert feed.closing_cents == 1_128_145_455


def test_the_limit_is_slopes_rule_on_the_14_may_feed():
    feed = load_feed(SNAP)
    line = INPUTS["financing_plan"]["line"]
    months = ("2024-02", "2024-03", "2024-04")  # the trailing three complete months
    receipts = [_month(feed, m, "customer_receipts") for m in months]
    assert line["monthly_customer_receipts_cents"] == dict(zip(months, receipts, strict=True))
    limit = int((Decimal("0.15") * Decimal(sum(receipts)) / 3).to_integral_value(ROUND_DOWN))
    assert line["limit_cents"] == limit == 39_337_365
    setup = setup_from_inputs(INPUTS, REVIEW)
    ops = operating.simulate(feed, (setup.horizon - REVIEW).days + 30, DRAWS, SEED)
    assert (prepare(setup, ops).limit[:, 0] == limit).all()
    assert setup.horizon == date(2024, 11, 10)

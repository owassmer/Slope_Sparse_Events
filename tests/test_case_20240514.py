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


def test_the_line_opens_on_the_first_day_with_three_complete_months_of_feed_history():
    feed, opened = load_feed(SNAP), INPUTS["financing_plan"]["line"]["opened"]
    first = min(t["date"] for t in feed.transactions)
    assert first[:7] == "2024-01" and opened["date"] == "2024-04-01"  # January, February, March complete
    receipts = sum(_month(feed, f"2024-0{m}", "customer_receipts") for m in (1, 2, 3))
    assert opened["limit_cents"] == int((Decimal("0.15") * Decimal(receipts) / 3).to_integral_value(ROUND_DOWN))
    assert opened["limit_cents"] > 25_000_000  # Slope's manual-review band


def test_the_lines_history_is_the_engine_on_the_feed_and_reconciles_to_the_opening_exposure():
    from app.analysis.history import replay
    from app.analysis.setup import exposure_from_json, exposure_json

    feed, line = load_feed(SNAP), INPUTS["financing_plan"]["line"]
    state = line["opening_state"]
    ex, hist = replay(feed, setup_from_inputs(INPUTS, REVIEW), date.fromisoformat(line["opened"]["date"]))
    assert exposure_json(ex) == state["exposure"] and hist == state["history"]  # recorded = recomputed, exactly
    assert setup_from_inputs(INPUTS, REVIEW).exposure == ex
    assert exposure_from_json(exposure_json(ex)) == ex
    parts = [i for d in hist["draws"] for i in d["installments"]]
    assert sum(i["amount_cents"] for i in parts) == hist["contractual_cents"]
    assert sum(i["collected_cents"] for i in parts) == hist["collected_cents"]
    assert hist["contractual_cents"] == hist["collected_cents"] + ex.owed_cents
    assert hist["funded_cents"] - hist["collected_cents"] == ex.cash_cents
    remaining = sorted((i["due"], i["amount_cents"]) for i in parts if i["status"] == "not_yet_due")
    by_due: dict = {}
    for d, c in remaining:
        by_due[d] = by_due.get(d, 0) + c
    assert [(d.isoformat(), c) for d, c in ex.installments] == sorted(by_due.items())
    assert all(i["due"] > REVIEW.isoformat() for i in parts if i["status"] == "not_yet_due")
    assert ex.past_due_cents == 0 and all(i["status"] != "past_due" for i in parts)
    for d in hist["draws"]:  # 3.7% fee, three installments, each draw a real invoice on its own date
        assert sum(i["amount_cents"] for i in d["installments"]) == d["amount_cents"] + round(d["amount_cents"] * 0.037)
        txn = next(t for t in feed.transactions if t["transaction_id"] == d["transaction_id"])
        assert (txn["date"], -txn["amount_cents"]) == (d["date"], d["amount_cents"])
    limit_now = hist["limit_by_day_cents"][max(k for k in hist["limit_by_day_cents"] if k <= REVIEW.isoformat())]
    assert limit_now == line["limit_cents"] and ex.principal_cents <= limit_now


def test_the_page_names_no_docket_entry_filed_after_the_review_date():
    """Nothing dated after 14 May reaches the 14 May page's payload (its reveal file aside): every docket entry it
    cites is at most the last one the snapshot admits."""
    import re

    run = next(p for p in sorted((ROOT / "runs" / "recorded").glob(f"{SNAP}-agent_plus_jev-*")) if (p / "page.json").exists())
    shown = json.loads((ROOT / "cases" / SNAP / "snapshot.json").read_text())["source_display"]
    admitted = {int(n) for k in shown for n in re.findall(r"_d(\d{3,4})(?:_|$)", k)}
    text = (run / "page.json").read_text()
    cited = {int(n) for n in re.findall(r"(?:D\.I\.|Dkt\.)\s*(\d+)", text)}
    cited |= {int(n) for n in re.findall(r"gov\.uscourts\.[a-z]+\.\d+\.(\d+)\.\d+\.pdf", text)}
    assert admitted and cited and max(cited) <= max(admitted), sorted(x for x in cited if x > max(admitted))

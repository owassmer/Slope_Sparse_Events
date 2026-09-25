"""Connected-bank data: the baseline Slope underwrites on.

The feed is counterparty-tagged daily transactions and balances up to the decision date (for the demo cases, a
synthetic reconstruction labelled once in the feed's `provenance`). This module reads it, derives the risk features
the price tier uses, and projects the bank-only view forward: each category's average daily flow over the trailing
13 weeks, which is what an underwriter with bank data alone would assume.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean, pstdev

from app.config import ROOT

TRAILING_DAYS = 91


@dataclass(frozen=True)
class BankFeed:
    feed_id: str
    provenance: str
    borrower: str
    restricted_cents: int
    period_end: date
    transactions: tuple[dict, ...]
    balances: dict[date, int]

    @property
    def closing_cents(self) -> int:
        return self.balances[self.period_end]

    @property
    def available_cents(self) -> int:
        return self.closing_cents - self.restricted_cents


def load_feed(snapshot_id: str) -> BankFeed:
    raw = json.loads((ROOT / "cases" / snapshot_id / "bank_feed.json").read_text())
    return BankFeed(raw["feed_id"], raw["provenance"], raw["borrower"], raw["restricted_cents"],
                    date.fromisoformat(raw["period"]["end"]), tuple(raw["transactions"]),
                    {date.fromisoformat(b["date"]): b["closing_cents"] for b in raw["daily_balances"]})


def trailing(feed: BankFeed, days: int = TRAILING_DAYS) -> list[dict]:
    start = feed.period_end - timedelta(days=days - 1)
    return [t for t in feed.transactions if date.fromisoformat(t["date"]) >= start]


def risk_features(feed: BankFeed) -> dict[str, int | str]:
    """Bank-derived features the tier uses (cents or basis points; no floats in outputs)."""
    window = trailing(feed)
    inflows = sum(t["amount_cents"] for t in window if t["amount_cents"] > 0 and t["category"] != "equity_proceeds")
    outflows = -sum(t["amount_cents"] for t in window if t["amount_cents"] < 0)
    start = feed.period_end - timedelta(days=TRAILING_DAYS - 1)
    bals = [v for d, v in feed.balances.items() if d >= start]
    by_month: dict[str, int] = defaultdict(int)
    for t in feed.transactions:
        if t["amount_cents"] > 0 and t["category"] != "equity_proceeds":
            by_month[t["date"][:7]] += t["amount_cents"]
    full_months = [v for k, v in sorted(by_month.items()) if k < feed.period_end.isoformat()[:7]]
    cv = pstdev(full_months) / mean(full_months) if len(full_months) > 1 else 0.0
    debt = -sum(t["amount_cents"] for t in window if t["category"] in ("debt_service", "loan_payment"))
    return {
        "average_balance_cents": int(mean(bals)),
        "minimum_balance_cents": min(bals),
        "monthly_inflows_cents": int(inflows * 30 / TRAILING_DAYS),
        "monthly_outflows_cents": int(outflows * 30 / TRAILING_DAYS),
        "inflow_volatility_bps": int(Decimal(str(cv)) * 10_000),
        "negative_balance_days": sum(1 for v in bals if v < 0),
        "debt_service_share_bps": int(Decimal(debt) / Decimal(max(inflows, 1)) * 10_000),
        "available_cash_cents": feed.available_cents,
    }


def daily_rates(feed: BankFeed, *, exclude_categories: frozenset[str] = frozenset()) -> dict[str, int]:
    """Average daily flow by category over the trailing window (signed cents per day), excluding one-off equity."""
    totals: dict[str, int] = defaultdict(int)
    for t in trailing(feed):
        if t["category"] == "equity_proceeds" or t["category"] in exclude_categories:
            continue
        totals[t["category"]] += t["amount_cents"]
    return {k: int(Decimal(v) / TRAILING_DAYS) for k, v in totals.items()}


def projection(feed: BankFeed, end: date, *, exclude_categories: frozenset[str] = frozenset()) -> list[tuple[date, str, int]]:
    """Bank-only forward view: each category's trailing average daily flow, every day after the feed ends."""
    rates = daily_rates(feed, exclude_categories=exclude_categories)
    out, d = [], feed.period_end + timedelta(days=1)
    while d <= end:
        out += [(d, cat, rate) for cat, rate in sorted(rates.items()) if rate]
        d += timedelta(days=1)
    return out


def counterparty_payments(feed: BankFeed, counterparty_substring: str) -> list[dict]:
    """Observed payments to a named counterparty (e.g. litigation counsel), for the reviewer and the agent."""
    key = counterparty_substring.lower()
    return [t for t in feed.transactions if key in t["counterparty"].lower()]


def summary(feed: BankFeed) -> dict:
    """What the agent's baseline shows: balances, monthly flows and the largest counterparties (trailing window)."""
    months: dict[str, dict[str, int]] = defaultdict(lambda: {"inflows_cents": 0, "outflows_cents": 0})
    for t in feed.transactions:
        k = "inflows_cents" if t["amount_cents"] > 0 else "outflows_cents"
        months[t["date"][:7]][k] += abs(t["amount_cents"])
    by_cp: dict[str, int] = defaultdict(int)
    for t in trailing(feed):
        by_cp[t["counterparty"]] += t["amount_cents"]
    return {"provenance": feed.provenance, "as_of": feed.period_end.isoformat(),
            "closing_balance_cents": feed.closing_cents, "restricted_cents": feed.restricted_cents,
            "monthly": dict(sorted(months.items())),
            "trailing_13_weeks_by_counterparty_cents": dict(sorted(by_cp.items(), key=lambda kv: kv[1])),
            "risk_features": risk_features(feed)}


def feed_path(snapshot_id: str) -> Path:
    return ROOT / "cases" / snapshot_id / "bank_feed.json"

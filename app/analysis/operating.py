"""Operating cash: a calendar-month block bootstrap of the connected-bank feed.

The background variability against which the sparse-event signal operates. The last six complete months are the
blocks (one-off equity proceeds excluded); each category is rescaled to its recent level (latest-three-month average
over six-month average); a whole month is drawn at a time, preserving the relationships among receipts, payroll,
suppliers and other payments; each transaction keeps its business-day position in the month (clamped to the target
month's last business day). Every draw starts from the available cash on the review date. The same draws serve the
bank-only and event-adjusted views.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np

from app.finance.bank import BankFeed
from app.finance.calendar import is_business_day

BLOCK_MONTHS = 6
RECENT_MONTHS = 3
EXCLUDED = frozenset({"equity_proceeds"})
MAX_BDAYS = 23


def _business_days(year: int, month: int) -> list[date]:
    d, out = date(year, month, 1), []
    while d.month == month:
        if is_business_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _month_key(d: date) -> tuple[int, int]:
    return d.year, d.month


def block_months(feed: BankFeed, months: int = BLOCK_MONTHS) -> list[tuple[int, int]]:
    """The last `months` complete calendar months before the review date."""
    y, m = feed.period_end.year, feed.period_end.month
    if (feed.period_end + timedelta(days=1)).month == m:  # the review month is incomplete
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    out = []
    for _ in range(months):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def blocks(feed: BankFeed) -> tuple[np.ndarray, dict[str, float], list[tuple[int, int]]]:
    """Per block month, the rescaled flow at each business-day position (cents, shape [months, MAX_BDAYS]), with the
    category scales and the block months."""
    months = block_months(feed)
    totals: dict[str, dict[tuple[int, int], int]] = defaultdict(lambda: defaultdict(int))
    txns = []
    for t in feed.transactions:
        d = date.fromisoformat(t["date"])
        if t["category"] in EXCLUDED or _month_key(d) not in months:
            continue
        totals[t["category"]][_month_key(d)] += t["amount_cents"]
        txns.append((d, t["category"], t["amount_cents"]))
    scales = {}
    for cat, by in totals.items():
        six = sum(by.get(m, 0) for m in months) / len(months)
        three = sum(by.get(m, 0) for m in months[-RECENT_MONTHS:]) / RECENT_MONTHS
        scales[cat] = three / six if six else 1.0
    out = np.zeros((len(months), MAX_BDAYS), dtype=np.float64)
    bdays = {m: _business_days(*m) for m in months}
    for d, cat, cents in txns:
        k = months.index(_month_key(d))
        prior = [b for b in bdays[_month_key(d)] if b <= d]  # a weekend flow sits on the preceding business day
        pos = max(len(prior) - 1, 0)
        out[k, min(pos, MAX_BDAYS - 1)] += cents * scales[cat]
    return out, scales, months


def simulate(feed: BankFeed, horizon_days: int, draws: int, seed: int, variability: float = 1.0) -> np.ndarray:
    """Daily operating flows [draws, horizon_days] in integer cents, day 0 = the day after the review date."""
    review = feed.period_end
    block, _, months = blocks(feed)
    rng = np.random.default_rng(seed)
    flows = np.zeros((draws, horizon_days), dtype=np.float64)
    first, last = review + timedelta(days=1), review + timedelta(days=horizon_days)
    y, m = first.year, first.month
    while date(y, m, 1) <= last:
        bd = _business_days(y, m)
        pick = rng.integers(0, len(months), size=draws)
        month_flows = block[pick]  # [draws, MAX_BDAYS]
        for pos in range(MAX_BDAYS):  # clamp positions beyond the month's last business day onto it
            target = bd[min(pos, len(bd) - 1)]
            if first <= target <= last:
                flows[:, (target - first).days] += month_flows[:, pos]
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    if variability != 1.0:
        mean = flows.mean(axis=0, keepdims=True)
        flows = mean + variability * (flows - mean)
    return np.rint(flows).astype(np.int64)

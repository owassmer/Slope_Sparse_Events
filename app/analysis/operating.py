"""Operating cash: a calendar-month block bootstrap of the connected-bank feed.

The background variability against which the sparse-event signal operates. The last six complete months are the
blocks (one-off equity proceeds excluded); each category is rescaled to its recent level (latest-three-month average
over six-month average); a whole month is drawn at a time, preserving the relationships among receipts, payroll,
suppliers and other payments; each transaction keeps its business-day position in the month (clamped to the target
month's last business day). Every draw starts from the available cash on the review date. The same draws serve the
bank-only and event-adjusted views.

The engine needs some categories apart: each `supplier_invoice` outflow on its own (Slope's line routes invoices one
by one), customer receipts and debt service (the line's limit rule) and legal fees. `total` holds every operating
flow, invoices included: `total` = the streams not split out + the split-out categories + the invoices, exactly.

The common financial model's controls (spec §16.3, `Setup`) act here, on the streams, so every path shares them:
- a cost plan scales every operating outflow except legal fees and debt service (the invoices too) from its start;
- financing proceeds are booked on their completion day in `total` and in `financing`, which the operating need
  leaves out; a debt booking's service is booked in `debt_service` (so the line's limit rule sees it).

The daily cash processor (QUESTIONS_20240514 §2.2) needs each day's receipts apart from its payments. `outflow` is the
day's outgoing flows (each stream's outgoing transactions, rounded per stream, and the invoices), and `inflow` is
`total - outflow`, so the two add up to `total` exactly. The split reads the same draws: the bootstrap is unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from app.analysis.setup import DRAWS, SEED, CostPlan, Financing, Setup
from app.finance.bank import BankFeed
from app.finance.calendar import is_business_day

BLOCK_MONTHS = 6
RECENT_MONTHS = 3
EXCLUDED = frozenset({"equity_proceeds"})
MAX_BDAYS = 23
INVOICE = "supplier_invoice"
SPLIT = ("customer_receipts", "debt_service", "legal_fees")  # kept apart as daily arrays (all also inside `total`)
LIMIT_CATEGORIES = ("customer_receipts", "debt_service")  # Slope's rule: receipts net of debt service
UNCUT = ("legal_fees", "debt_service")  # outflows a cost plan leaves alone (dispute spend stops on its own)
OUT = tuple(f"{k}-" for k in ("other", *SPLIT))  # each stream's outgoing part (the daily processor's payments)


@dataclass
class Operating:
    """Simulated operating flows, day 0 = the day after the review date, integer cents.

    total: every operating flow [draws, days]; invoices: each supplier invoice as a signed flow [draws, days, slots]
    (negative = an outflow; 0 = no invoice in that slot); by_category: the SPLIT categories [draws, days];
    history: customer receipts + debt service (negative) by calendar month from the feed, up to the review date."""
    total: np.ndarray
    invoices: np.ndarray
    by_category: dict[str, np.ndarray]
    history: dict[tuple[int, int], int]
    financing: np.ndarray | None = None  # [days] financing proceeds, inside `total`, outside the operating need
    inflow: np.ndarray | None = None  # [draws, days] the day's receipts (>= 0): total - outflow
    outflow: np.ndarray | None = None  # [draws, days] the day's payments (<= 0), invoices included

    @property
    def draws(self) -> int:
        return self.total.shape[0]


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
    """Up to `months` complete calendar months before the review date, only months the feed covers. A month before
    the feed's first transaction has no flows; sampling it would draw an empty month and inflate the spread."""
    first = min(_month_key(date.fromisoformat(t["date"])) for t in feed.transactions)
    y, m = feed.period_end.year, feed.period_end.month
    if (feed.period_end + timedelta(days=1)).month == m:  # the review month is incomplete
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    out = []
    while len(out) < months and (y, m) >= first:
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def history(feed: BankFeed) -> dict[tuple[int, int], int]:
    """Customer receipts + debt service (negative) per calendar month of the feed, dates up to the review date."""
    out: dict[tuple[int, int], int] = defaultdict(int)
    for t in feed.transactions:
        if t["category"] in LIMIT_CATEGORIES:
            out[_month_key(date.fromisoformat(t["date"]))] += t["amount_cents"]
    return dict(out)


def blocks(feed: BankFeed) -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, float], list[tuple[int, int]]]:
    """Per block month, the rescaled flow at each business-day position: by stream ('other' and the SPLIT categories,
    [months, MAX_BDAYS]) and each supplier invoice ([months, MAX_BDAYS, slots]); with the category scales and months."""
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
    # "cut" repeats the non-invoice outflows a cost plan scales (already inside "other"); "<stream>-" repeats each
    # stream's outgoing transactions (the daily processor's payments). Neither is a flow of its own.
    streams = {k: np.zeros((len(months), MAX_BDAYS)) for k in ("other", *SPLIT, "cut", *OUT)}
    bdays = {m: _business_days(*m) for m in months}
    slots: dict[tuple[int, int], list[float]] = defaultdict(list)
    for d, cat, cents in txns:
        k = months.index(_month_key(d))
        prior = [b for b in bdays[_month_key(d)] if b <= d]  # a weekend flow sits on the preceding business day
        pos = min(max(len(prior) - 1, 0), MAX_BDAYS - 1)
        if cat == INVOICE:
            slots[(k, pos)].append(cents * scales[cat])
        else:
            streams[cat if cat in SPLIT else "other"][k, pos] += cents * scales[cat]
            if cents < 0:
                streams[(cat if cat in SPLIT else "other") + "-"][k, pos] += cents * scales[cat]
            if cents < 0 and cat not in SPLIT and cat not in UNCUT:
                streams["cut"][k, pos] += cents * scales[cat]
    invoices = np.zeros((len(months), MAX_BDAYS, max((len(v) for v in slots.values()), default=1)))
    for (k, pos), v in slots.items():
        invoices[k, pos, :len(v)] = v
    return streams, invoices, scales, months


def _day(review: date, d: date, what: str) -> int:
    """Day index of a dated booking (day 0 = the day after the review date). History cannot be rebooked."""
    if d <= review:
        raise ValueError(f"{what} on {d} is not after the review date {review}")
    return (d - review).days - 1


def simulate(feed: BankFeed, horizon_days: int, draws: int, seed: int, variability: float = 1.0,
             cost_plan: CostPlan | None = None, financing: tuple[Financing, ...] = ()) -> Operating:
    """Daily operating flows for `horizon_days` days after the review date (one block month per calendar month),
    with the cost plan and the financing bookings of the common financial model (none: the feed's continuation)."""
    review = feed.period_end
    streams, inv_block, _, months = blocks(feed)
    rng = np.random.default_rng(seed)
    width = inv_block.shape[2]
    first, last = review + timedelta(days=1), review + timedelta(days=horizon_days)
    targets, y, m = [], first.year, first.month
    while date(y, m, 1) <= last:
        targets.append(_business_days(y, m))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    flows = {k: np.zeros((draws, horizon_days)) for k in streams}
    inv = np.zeros((draws, horizon_days, width * (MAX_BDAYS - min(len(bd) for bd in targets) + 1)))
    used = np.zeros(horizon_days, dtype=np.int64)  # invoice slots filled on each day
    for bd in targets:
        pick = rng.integers(0, len(months), size=draws)
        for pos in range(MAX_BDAYS):  # clamp positions beyond the month's last business day onto it
            target = bd[min(pos, len(bd) - 1)]
            if first <= target <= last:
                t = (target - first).days
                for k, arr in streams.items():
                    flows[k][:, t] += arr[pick, pos]
                inv[:, t, used[t]:used[t] + width] = inv_block[pick, pos]
                used[t] += width
    inv = inv[:, :, :max(int(used.max()), 1)]
    if variability != 1.0:  # linear in each stream, so the total moves the same way
        flows = {k: v.mean(axis=0, keepdims=True) + variability * (v - v.mean(axis=0, keepdims=True))
                 for k, v in flows.items()}
        inv = inv.mean(axis=0, keepdims=True) + variability * (inv - inv.mean(axis=0, keepdims=True))
    if cost_plan is not None:  # outflows other than legal fees and debt service fall by the share from its start
        t0, share = max(_day(review, max(cost_plan.start, first), "the cost plan"), 0), cost_plan.share_bps / 10_000
        flows["other"][:, t0:] -= share * flows["cut"][:, t0:]  # "cut" is negative: this adds the saving back
        flows["other-"][:, t0:] -= share * flows["cut"][:, t0:]  # the cut outflows are all inside "other-"
        inv[:, t0:] *= 1 - share
    flows.pop("cut")
    out = {k: flows.pop(k) for k in OUT}
    ints = {k: np.rint(v).astype(np.int64) for k, v in flows.items()}
    invoices = np.rint(inv).astype(np.int64)
    fin = np.zeros(horizon_days, dtype=np.int64)
    for f in financing:  # proceeds on the completion day; a debt booking's service as debt-service outflows
        if (t := _day(review, f.on, "financing")) < horizon_days:
            fin[t] += f.amount_cents
        for d, cents in f.service:
            if (t := _day(review, d, "debt service")) < horizon_days:
                ints["debt_service"][:, t] -= cents
    total = sum(ints.values()) + invoices.sum(axis=2) + fin[None, :]
    outflow = split_outflow(total, out, invoices)
    for f in financing:  # a debt booking's service is a payment (booked into debt_service above)
        for d, cents in f.service:
            if (t := _day(review, d, "debt service")) < horizon_days:
                outflow[:, t] -= cents
    outflow = np.minimum(outflow, np.minimum(total, 0))
    return Operating(total=total, invoices=invoices, by_category={k: ints[k] for k in SPLIT}, history=history(feed),
                     financing=fin if financing else None, inflow=total - outflow, outflow=outflow)


def split_outflow(total: np.ndarray, out: dict[str, np.ndarray], invoices: np.ndarray) -> np.ndarray:
    """The day's payments: each stream's outgoing part rounded as the stream is, and the invoices' outgoing slots.
    Rounding is monotone, so rint(in + out) >= rint(out) and the receipts (total - outflow) are never negative; a
    variability scaling can turn an outgoing part positive, which the caller caps (outflow <= min(total, 0))."""
    return sum(np.rint(v).astype(np.int64) for v in out.values()) + np.minimum(invoices, 0).sum(axis=2)


def simulate_for(feed: BankFeed, setup: Setup) -> Operating:
    """The analysis's operating draws for a setup: the horizon plus the reserve's look-ahead, with its controls."""
    days = (setup.horizon - setup.review).days
    return simulate(feed, days + setup.need_days, DRAWS, SEED, setup.variability, setup.cost_plan, setup.financing)

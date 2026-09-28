"""The line's realized history: one deterministic run of Slope's line on the connected feed's own flows, from the day
it was opened to the review date (spec §2, §16.2). Its state on the review date is the forecast's starting exposure.

The history is the engine itself on one trajectory (no bootstrap): the feed's transactions from the opening date to
the review date are the operating flows; each `supplier_invoice` is an invoice to route, in the feed's order within
the day; the limit reads the feed's trailing three complete months (months before the opening from the feed's
history, later months from the replayed flows, as the engine reads its own simulated months); the need looks ahead
over the replayed flows, and flows after the review date count as zero (the engine's rule past its simulated span).
Collections are allocated to installments in due-date order (then draw order) to give each draw's schedule.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta

import numpy as np

from app.analysis.engine import installment_amounts, prepare, run
from app.analysis.events import EventCash
from app.analysis.operating import EXCLUDED, INVOICE, LIMIT_CATEGORIES, SPLIT, Operating
from app.analysis.setup import Exposure, Setup
from app.finance.bank import BankFeed


def _operating(feed: BankFeed, opened: date) -> tuple[Operating, list[list[dict]]]:
    """The feed's flows from `opened` to its end as a one-draw Operating, and each day's invoice transactions."""
    review = opened - timedelta(days=1)
    days = (feed.period_end - review).days
    total = np.zeros((1, days), dtype=np.int64)
    fin = np.zeros(days, dtype=np.int64)
    cats = {k: np.zeros((1, days), dtype=np.int64) for k in SPLIT}
    hist: dict[tuple[int, int], int] = defaultdict(int)
    inv: list[list[dict]] = [[] for _ in range(days)]
    for t in feed.transactions:
        d, c = date.fromisoformat(t["date"]), t["amount_cents"]
        if d < opened:
            if t["category"] in LIMIT_CATEGORIES:
                hist[(d.year, d.month)] += c
            continue
        i = (d - opened).days
        total[0, i] += c
        if t["category"] in SPLIT:
            cats[t["category"]][0, i] += c
        if t["category"] in EXCLUDED:  # financing proceeds: in cash, outside the operating need
            fin[i] += c
        if t["category"] == INVOICE and c < 0:
            inv[i].append(t)
    width = max(max((len(x) for x in inv), default=1), 1)
    invoices = np.zeros((1, days, width), dtype=np.int64)
    for i, row in enumerate(inv):
        for k, t in enumerate(row):
            invoices[0, i, k] = t["amount_cents"]
    return Operating(total=total, invoices=invoices, by_category=cats, history=dict(hist),
                     financing=fin if fin.any() else None), inv


def replay(feed: BankFeed, setup: Setup, opened: date) -> tuple[Exposure, dict]:
    """Run the line from `opened` to the feed's end (the review date) under `setup`'s line terms and collection mode.
    Returns the opening exposure for the forecast and a record of every draw, its installments and collections."""
    review = feed.period_end
    if not opened <= review:
        raise ValueError(f"the line opens on {opened}, after the review date {review}")
    rs = replace(setup, review=opened - timedelta(days=1), horizon=review, financing=(), cost_plan=None,
                 exposure=Exposure(), line_usage=1.0)
    ops, inv = _operating(feed, opened)
    line = prepare(rs, ops)
    start_cash = feed.balances[rs.review] - feed.restricted_cents
    tr = run(line, start_cash, EventCash.zeros(1, line.days))
    day = lambda i: rs.review + timedelta(days=int(i) + 1)  # noqa: E731
    draws, sched, used = [], [], set()
    for t, amt in zip(tr.draw_days, tr.draw_amounts, strict=True):
        txn = next(x for x in inv[t] if -x["amount_cents"] == amt and x["transaction_id"] not in used)
        used.add(txn["transaction_id"])
        parts = installment_amounts(np.array([amt]), rs.fee_bps, rs.installments)[0]
        k = len(draws)
        draws.append({"date": day(t).isoformat(), "transaction_id": txn["transaction_id"],
                      "counterparty": txn["counterparty"], "amount_cents": int(amt), "installments": []})
        for j, c in enumerate(parts):
            due = day(line.due_idx[t][j])
            sched.append([due, k, int(c), 0])
    sched.sort(key=lambda x: (x[0], x[1]))
    pool = int(tr.collected[0])
    for s in sched:  # collections by due date, then draw order
        s[3] = min(s[2], pool)
        pool -= s[3]
    assert pool == 0
    by_due: dict[date, int] = defaultdict(int)
    past_due = 0
    for due, k, c, paid in sched:
        status = "collected" if paid == c else "past_due" if due <= review else "not_yet_due"
        draws[k]["installments"].append({"due": due.isoformat(), "amount_cents": c, "collected_cents": paid,
                                         "status": status})
        if due <= review:
            past_due += c - paid
        else:
            assert paid == 0
            by_due[due] += c
    cash = int(tr.fundings.sum() - tr.collections.sum())
    assert int(tr.cash[0, -1]) == feed.available_cents + cash
    ex = Exposure(installments=tuple(sorted(by_due.items())), principal_cents=int(tr.outstanding[0, -1]),
                  past_due_cents=past_due, cash_cents=cash)
    record = {"opened": opened.isoformat(), "review": review.isoformat(),
              "limit_by_day_cents": {day(i).isoformat(): int(v) for i, v in enumerate(line.limit[0])
                                     if i == 0 or v != line.limit[0, i - 1]},
              "funded_cents": int(tr.drawn[0]), "contractual_cents": int(tr.contractual[0]),
              "collected_cents": int(tr.collected[0]), "draws": draws}
    return ex, record

"""The cash and collections engine for Slope's reusable line, vectorised over operating draws (spec §2).

Per trajectory (one joint event path x one operating draw), day by day from the day after the review date:

- **Cash.** Available cash = opening available cash + operating flows + event cash - encumbered cash + the invoices
  Slope pays - Slope's collections. A deficit is reported, never funded by an imaginary source.
- **Limit** (§2.1). On each day, share x mean monthly (customer receipts - debt service) over the trailing three
  complete calendar months of that trajectory (the feed's months, then its own simulated ones), rounded down.
- **Draws** (§2.1). Each simulated `supplier_invoice` outflow the borrower routes (`line_usage`) is paid by Slope
  when outstanding principal + the invoice <= the day's limit, nothing is overdue and no petition has been filed. A
  routed invoice leaves the borrower's outflows that day and creates `installments` monthly installments of
  invoice x (1 + fee), in whole cents, the remainder on the last (the reference arithmetic of `SlopeOffer`).
- **Collections** (§2.2). On each due date, and at each month-end while anything is overdue, Slope collects
  min(owed, max(0, available - need)); `need` is the lowest point of the trajectory's cumulative operating flows over
  the next 30 days relative to today (0 if they never fall below today's level). With need 0 this is the previous rule.
- **Petition** (§2.3). From the petition day p: no collections and no draws; operating flows continue. The balance
  owed at p is a stayed claim (recovery unknown and outside the horizon, never a zero loss); collections dated in
  [p - 90, p) are preference-exposed (reported, not deducted).

The contract identity per trajectory: collected + stayed + not yet due at the horizon + uncollected = contractual.
Outstanding principal is the amount funded less collections x funded / contractual (every draw carries the same fee,
so this is exact whenever the contract is fully collected).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.analysis.events import EventCash
from app.analysis.operating import Operating
from app.analysis.setup import NEED_DAYS as SETUP_NEED_DAYS
from app.analysis.setup import SEED, Setup
from app.finance.calendar import add_months, next_business_day

NEED_DAYS = SETUP_NEED_DAYS  # the central reserve; a setup's own `need_days` is what the analysis reads
PREFERENCE_DAYS = 90  # 11 U.S.C. §547(b)(4)(A)
TRAILING_MONTHS = 3


def installment_amounts(amount: np.ndarray, fee_bps: int, n: int) -> np.ndarray:
    """[len(amount), n] installments in whole cents: total = amount + fee (half up), n equal parts (half up), the
    remainder on the last. Identical to `SlopeOffer.schedule`."""
    amount = np.asarray(amount, dtype=np.int64)
    total = amount + (2 * amount * fee_bps + 10_000) // 20_000
    base = (2 * total + n) // (2 * n)
    out = np.repeat(base[:, None], n, axis=1)
    out[:, -1] = total - base * (n - 1)
    return out


@dataclass
class Line:
    """What every view shares for one setup and one set of operating draws."""
    setup: Setup
    ops: Operating
    days: int
    limit: np.ndarray  # [draws, days] the line's limit on each day
    need: np.ndarray  # [draws, days] operating need over the next 30 days
    due_idx: np.ndarray  # [days, installments] day index of each installment of a draw made on day t
    tail: int  # length of the contractual schedule (past the horizon)
    month_end: np.ndarray  # [days]
    routes: np.ndarray  # [draws, days, slots] the invoices the borrower would route (positive cents; 0 = none)
    df: np.ndarray  # [days] discount factors


@dataclass
class Trajectories:
    """Results for one joint path across all operating draws. Arrays are [draws, days] or [draws] (cents)."""
    cash: np.ndarray  # available cash at each day's end
    collections: np.ndarray  # Slope's collections by day
    fundings: np.ndarray  # invoices Slope paid, by day
    outstanding: np.ndarray  # outstanding principal at each day's end
    due: np.ndarray  # installments falling due by day, inside the horizon (contractual)
    locked: np.ndarray  # cash encumbered at each day's end
    capacity: np.ndarray  # credit capacity committed at each day's end
    petition: np.ndarray  # petition day inside the horizon, or -1
    drawn: np.ndarray
    contractual: np.ndarray  # every installment of every draw, due inside the horizon or after it
    collected: np.ndarray
    stayed: np.ndarray  # balance owed at the petition (typed: recovery unknown, outside the horizon)
    stayed_principal: np.ndarray
    preference: np.ndarray  # collections dated in [p - 90, p)
    not_yet_due: np.ndarray  # installments due after the horizon (exposure, not loss)
    uncollected: np.ndarray  # due inside the horizon, unpaid, no petition
    lender_pv: np.ndarray
    pv_fundings: np.ndarray
    pv_collections: np.ndarray
    dollar_days: np.ndarray  # principal dollar-days (capital tied up)
    min_cash: np.ndarray
    min_headroom: np.ndarray  # lowest headroom over the due dates (int64 max where none fell due)
    headroom_rows: np.ndarray  # one entry per (draw, due date): the draw ...
    headroom: np.ndarray  # ... and available - need - amount due, before collecting
    headroom_days: np.ndarray  # ... and the day index of that due date
    draw_rows: np.ndarray  # one entry per routed invoice: the draw, the day and the amount
    draw_days: np.ndarray
    draw_amounts: np.ndarray

    @property
    def fees(self) -> np.ndarray:
        return self.contractual - self.drawn


NO_DUE = np.iinfo(np.int64).max


def month_end_mask(setup: Setup, days: int) -> np.ndarray:
    first = setup.review + timedelta(days=1)
    return np.array([(first + timedelta(days=t + 1)).month != (first + timedelta(days=t)).month for t in range(days)])


def limits(setup: Setup, ops: Operating, days: int) -> np.ndarray:
    """[draws, days] Slope's rule on each trajectory's trailing three complete months (feed history + simulation)."""
    first = setup.review + timedelta(days=1)
    dates = [first + timedelta(days=t) for t in range(days)]
    net = ops.by_category["customer_receipts"][:, :days] + ops.by_category["debt_service"][:, :days]
    sim: dict[tuple[int, int], np.ndarray] = {}
    for t, d in enumerate(dates):
        k = (d.year, d.month)
        sim[k] = sim.get(k, 0) + net[:, t]

    def month(y: int, m: int) -> np.ndarray | int:
        return ops.history.get((y, m), 0) + sim.get((y, m), 0)

    out = np.empty((ops.draws, days), dtype=np.int64)
    for t, d in enumerate(dates):
        y, m, total = d.year, d.month, 0
        for _ in range(TRAILING_MONTHS):
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
            total = total + month(y, m)
        out[:, t] = np.maximum(np.asarray(total, dtype=np.int64) * setup.share_bps // (TRAILING_MONTHS * 10_000), 0)
    return out


def needs(ops: Operating, days: int, need_days: int = NEED_DAYS) -> np.ndarray:
    """[draws, days] how far the trajectory's cumulative operating flows fall below today's level over the next
    `need_days` days (0 if they never do). Flows past the simulated span count as zero. Financing proceeds are not
    operating flows: they leave the need alone."""
    if need_days <= 0:
        return np.zeros((ops.draws, days), dtype=np.int64)
    cum = np.cumsum(ops.total if ops.financing is None else ops.total - ops.financing[None, :], axis=1)
    pad = np.concatenate([cum, np.repeat(cum[:, -1:], need_days, axis=1)], axis=1)
    low = sliding_window_view(pad[:, 1:], need_days, axis=1)[:, :days].min(axis=2)
    return np.maximum(cum[:, :days] - low, 0)


def prepare(setup: Setup, ops: Operating, need_days: int | None = None) -> Line:
    days = (setup.horizon - setup.review).days
    need_days = setup.need_days if need_days is None else need_days
    first = setup.review + timedelta(days=1)
    due_idx = np.array([[(next_business_day(add_months(first + timedelta(days=t), k)) - setup.review).days - 1
                         for k in range(1, setup.installments + 1)] for t in range(days)], dtype=np.int64)
    routes = np.maximum(-ops.invoices[:, :days], 0)
    if setup.line_usage < 1.0:  # the invoices the borrower routes, drawn once per slot (identical across views)
        routes = np.where(np.random.default_rng([SEED, 6]).random(routes.shape) < setup.line_usage, routes, 0)
    t = np.arange(1, days + 1)
    return Line(setup=setup, ops=ops, days=days, limit=limits(setup, ops, days), need=needs(ops, days, need_days),
                due_idx=due_idx, tail=int(due_idx.max()) + 1, month_end=month_end_mask(setup, days), routes=routes,
                df=(1 + setup.discount_rate_bps / 10_000) ** (-t / 365))


def with_petition(events: EventCash, day: np.ndarray | int) -> EventCash:
    """The same events with a petition on `day` (per draw), unless one comes earlier. For the stress view and tests."""
    n, days = events.cash.shape
    p = EventCash.zeros(n, days)
    p.petition[:] = day
    return events + p


def run(line: Line, opening_cents: int, events: EventCash) -> Trajectories:
    return run_many(line, opening_cents, [events])[0]


def _tiled(line: Line, b: int) -> tuple:
    """The line's need and limit [days, rows], day-major (each day's values contiguous), and per (day, slot) the rows
    with an invoice to route and its amount, for b paths stacked row-wise."""
    cache = line.__dict__.setdefault("_tiles", {})
    if b not in cache:
        if len(cache) > 2:
            cache.clear()
        days, n = line.days, line.ops.draws
        routes = line.routes[:, :days]
        slots = []
        for t in range(days):
            row = []
            for k in range(routes.shape[2]):
                r = np.flatnonzero(routes[:, t, k] > 0)
                if r.size:
                    rows = (r[None, :] + n * np.arange(b)[:, None]).ravel()
                    row.append((rows, np.tile(routes[r, t, k], b)))
            slots.append(row)
        cache[b] = (np.ascontiguousarray(np.tile(line.need[:, :days].T, (1, b))),
                    np.ascontiguousarray(np.tile(line.limit[:, :days].T, (1, b))), slots)
    return cache[b]


def run_many(line: Line, opening_cents: int, events: list[EventCash]) -> list[Trajectories]:
    """`run` for several joint paths at once: their draws are stacked through the day loop, day-major so each day's
    values are contiguous (every operation in the loop is per trajectory and in integers, so each path's rows are
    exactly what it computes alone), then each path is finished on its own rows."""
    s, n, days, b = line.setup, line.ops.draws, line.days, len(events)
    base = np.ascontiguousarray(np.concatenate([line.ops.total[:, :days] + e.cash - e.lock for e in events]).T)
    pet = np.concatenate([np.where((e.petition >= 0) & (e.petition < days), e.petition, days) for e in events])
    need, limit, slots = _tiled(line, b)
    rn = n * b
    due = np.zeros((line.tail, rn), dtype=np.int64)
    collections = np.zeros((days, rn), dtype=np.int64)
    fundings = np.zeros((days, rn), dtype=np.int64)
    cash = np.empty((days, rn), dtype=np.int64)
    outstanding = np.empty((days, rn), dtype=np.int64)
    avail = np.full(rn, opening_cents, dtype=np.int64)
    owed, funded, contract, collected = (np.zeros(rn, dtype=np.int64) for _ in range(4))
    hr_rows, hr_vals, hr_days, d_rows, d_days, d_amts = [], [], [], [], [], []

    def principal_out(r=slice(None)) -> np.ndarray:  # per trajectory; r: only these rows
        f, c = funded[r], contract[r]
        return f - np.where(c > 0, collected[r] * f // np.maximum(c, 1), 0)

    po = principal_out()
    for t in range(days):
        avail += base[t]
        live = t < pet
        owed += due[t]
        falls_due = (due[t] > 0) & live
        changed = False
        if falls_due.any():
            hr_rows.append(np.nonzero(falls_due)[0])
            hr_vals.append((avail - need[t] - owed)[falls_due])
            hr_days.append(np.full(int(falls_due.sum()), t, dtype=np.int64))
        attempt = live & (falls_due | line.month_end[t]) & (owed > 0)
        if attempt.any():
            take = np.where(attempt, np.minimum(owed, np.maximum(avail - need[t], 0)), 0)
            owed -= take
            avail -= take
            collected += take
            collections[t] = take
            changed = True
        open_ = live & (owed == 0)
        for rows, amts in slots[t]:  # only the rows with an invoice in this slot: every other row adds nothing
            sel = open_[rows]
            if not sel.any():
                continue
            rows, amts = rows[sel], amts[sel]
            ok = principal_out(rows) + amts <= limit[t][rows]
            if not ok.any():
                continue
            rows, amt = rows[ok], amts[ok]
            parts = installment_amounts(amt, s.fee_bps, s.installments)
            due[np.ix_(line.due_idx[t], rows)] += parts.T
            funded[rows] += amt
            contract[rows] += parts.sum(axis=1)
            avail[rows] += amt  # Slope pays the supplier: the invoice leaves the borrower's outflows today
            fundings[t][rows] += amt
            d_rows.append(rows)
            d_days.append(np.full(len(rows), t, dtype=np.int64))
            d_amts.append(amt)
            changed = True
        cash[t] = avail
        if changed:
            po = principal_out()
        outstanding[t] = po

    # [rows, days] again: views of the day-major arrays (integer arithmetic reads them exactly, in any order), except
    # the two the discounting multiplies by float factors, copied so the product runs exactly as on a single path
    due, cash, outstanding = due.T, cash.T, outstanding.T
    collections, fundings = np.ascontiguousarray(collections.T), np.ascontiguousarray(fundings.T)
    cat = lambda xs: np.concatenate(xs) if xs else np.zeros(0, dtype=np.int64)  # noqa: E731
    hr = (cat(hr_rows), cat(hr_vals), cat(hr_days))
    dr = (cat(d_rows), cat(d_days), cat(d_amts))
    out = []
    for j, ev in enumerate(events):
        lo, hi = j * n, (j + 1) * n
        sl = slice(lo, hi)
        mh, md = (hr[0] >= lo) & (hr[0] < hi), (dr[0] >= lo) & (dr[0] < hi)  # this path's entries, in order
        out.append(_finish(line, ev, pet[sl], due[sl], collections[sl], fundings[sl], cash[sl], outstanding[sl],
                           funded[sl], contract[sl], collected[sl], (hr[0][mh] - lo, hr[1][mh], hr[2][mh]),
                           (dr[0][md] - lo, dr[1][md], dr[2][md])))
    return out


def _finish(line: Line, events: EventCash, pet, due, collections, fundings, cash, outstanding, funded, contract,
            collected, hr, dr) -> Trajectories:
    n, days = line.ops.draws, line.days
    petitioned = pet < days
    stayed = np.where(petitioned, contract - collected, 0)
    not_yet_due = np.where(petitioned, 0, due[:, days:].sum(axis=1))
    idx = np.arange(days)
    window = petitioned[:, None] & (idx >= pet[:, None] - PREFERENCE_DAYS) & (idx < pet[:, None])
    headroom_rows, headroom = hr[0], hr[1]
    min_headroom = np.full(n, NO_DUE, dtype=np.int64)
    np.minimum.at(min_headroom, headroom_rows, headroom)
    pv_f, pv_c = fundings @ line.df, collections @ line.df
    return Trajectories(
        cash=cash, collections=collections, fundings=fundings, outstanding=outstanding, due=due[:, :days],
        locked=np.cumsum(events.lock, axis=1), capacity=np.cumsum(events.capacity, axis=1),
        petition=np.where(petitioned, pet, -1), drawn=funded, contractual=contract, collected=collected,
        stayed=stayed, stayed_principal=np.where(petitioned, outstanding[:, -1], 0),
        preference=(collections * window).sum(axis=1), not_yet_due=not_yet_due,
        uncollected=contract - collected - stayed - not_yet_due, lender_pv=pv_c - pv_f, pv_fundings=pv_f,
        pv_collections=pv_c, dollar_days=outstanding.sum(axis=1) / 100.0, min_cash=cash.min(axis=1),
        min_headroom=min_headroom, headroom_rows=headroom_rows, headroom=headroom, headroom_days=hr[2],
        draw_rows=dr[0], draw_days=dr[1], draw_amounts=dr[2])

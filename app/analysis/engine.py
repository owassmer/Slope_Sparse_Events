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
- **Collections** (§2.2, §16.3 Collection). Slope attempts collection on each due date, and at each month-end while
  anything is overdue, under the setup's `collection` mode:
  - `debit` (the central case): Slope debits the installments owed in due-date order (draw order within a date).
    Each is collected in full when available cash covers it at that point; otherwise the debit fails (no partial
    debits) and the installment stays overdue until the next attempt. `need_days` never affects collections.
  - `protect_need` (a sensitivity: the borrower keeps its operating need back): Slope collects
    min(owed, max(0, available - need)).
  `need` is the lowest point of the trajectory's cumulative operating flows over the next `need_days` days relative
  to today (0 if they never fall below today's level). It is also the cash floor that the company's decisions, Jev's
  facts and settlement and stay capacity read (through `Basis`), and the headroom metric subtracts it in both modes.
- **Opening exposure** (`Setup.exposure`, a line opened before the review date). Its installments sit in the due
  schedule on their dates and its past-due amount falls due on day 0, so they are collected, block draws while
  overdue, and are stayed on a petition exactly as a new draw's; its principal counts against the limit; the cash its
  history moved (funded - collected) is added to the opening cash. `drawn` and `fundings` are new draws only;
  `contractual` includes the opening installments, so the contract identity below still holds. Empty: a new line.
- **Petition** (§2.3). From the petition day p: no collections and no draws; operating flows continue. The balance
  owed at p is a stayed claim (recovery unknown and outside the horizon, never a zero loss); collections dated in
  [p - 90, p) are preference-exposed (reported, not deducted).

The contract identity per trajectory: collected + stayed + not yet due at the horizon + uncollected = contractual.
Outstanding principal is the amount funded less collections x funded / contractual (every draw carries the same fee,
so this is exact whenever the contract is fully collected).
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
from numba import njit
from numpy.lib.stride_tricks import sliding_window_view

from app.analysis.events import EventCash
from app.analysis.operating import Operating
from app.analysis.setup import NEED_DAYS as SETUP_NEED_DAYS
from app.analysis.setup import SEED, Setup
from app.finance.calendar import add_months, next_business_day

NEED_DAYS = SETUP_NEED_DAYS  # the central cash floor; a setup's own `need_days` is what the analysis reads
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
    need: np.ndarray  # [draws, days] operating need over the next `need_days` days (the cash floor)
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
    opening_principal: int = 0  # the opening exposure's principal (inside `contractual`, not in `drawn`)
    # [draws] failed collection attempts: under `debit`, installment debits that failed; under `protect_need`,
    # attempts that left an amount owed
    failed_debits: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    # under cash_processing = daily: the processor's arrears, first unpaid day and §3.3 day (processor.Processed).
    # Not a field: a net run's fields are exactly as before.
    processed = None

    @property
    def fees(self) -> np.ndarray:
        """Fees on everything owed from the review date: new draws' fees plus the opening installments' fee share."""
        return self.contractual - self.drawn - self.opening_principal


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
    ex = setup.exposure
    idx = [(d - setup.review).days - 1 for d, _ in ex.installments]
    if min(idx, default=0) < 0:
        raise ValueError("an opening installment is due on or before the review date: carry it as past due")
    due_idx_max = max([int(due_idx.max()), *idx])
    return Line(setup=setup, ops=ops, days=days, limit=limits(setup, ops, days), need=needs(ops, days, need_days),
                due_idx=due_idx, tail=due_idx_max + 1, month_end=month_end_mask(setup, days), routes=routes,
                df=(1 + setup.discount_rate_bps / 10_000) ** (-t / 365))


def with_petition(events: EventCash, day: np.ndarray | int) -> EventCash:
    """The same events with a petition on `day` (per draw), unless one comes earlier. For the stress view and tests."""
    n, days = events.cash.shape
    p = EventCash.zeros(n, days)
    p.petition[:] = day
    return events + p


def run(line: Line, opening_cents: int, events: EventCash, nonpayment: tuple[int, int] | None = None) -> Trajectories:
    return run_many(line, opening_cents, [events], nonpayment)[0]


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


def run_many(line: Line, opening_cents: int, events: list[EventCash], nonpayment: tuple[int, int] | None = None
             ) -> list[Trajectories]:
    """`run` for several joint paths at once: their draws are stacked through the day loop, day-major so each day's
    values are contiguous (every operation in the loop is per trajectory and in integers, so each path's rows are
    exactly what it computes alone), then each path is finished on its own rows. Under cash_processing = daily the
    daily processor runs instead (app/analysis/processor.py; `nonpayment`: the §3.3 terms, window days and share bps)."""
    if line.setup.cash_processing == "daily":
        from app.analysis.processor import run_daily

        return run_daily(line, opening_cents, events, nonpayment)
    s, n, days, b = line.setup, line.ops.draws, line.days, len(events)
    need, limit, month_end, routes, due_idx, due0, book_d, book_a, nroutes = _kernel_line(line)
    base = np.concatenate([line.ops.total[:, :days] + e.cash - e.lock for e in events]).astype(np.int64, copy=False)
    pet = np.concatenate([np.where((e.petition >= 0) & (e.petition < days), e.petition, days) for e in events]
                         ).astype(np.int64, copy=False)
    ex = s.exposure
    (cash, collections, fundings, outstanding, due, funded, contract, collected, failed, hr, dr) = _net_kernel(
        base, pet, need, limit, month_end, routes, due_idx, np.int64(s.fee_bps), s.installments,
        s.collection == "debit", due0, book_d, book_a, np.int64(opening_cents + ex.cash_cents),
        np.int64(ex.principal_cents), np.int64(ex.owed_cents), b * nroutes)
    hr, dr = _in_loop_order(hr, dr, days, routes.shape[2])
    out = []
    failed = failed.reshape(b, n)
    for j, ev in enumerate(events):
        lo, hi = j * n, (j + 1) * n
        sl = slice(lo, hi)
        mh, md = (hr[0] >= lo) & (hr[0] < hi), (dr[0] >= lo) & (dr[0] < hi)  # this path's entries, in order
        out.append(_finish(line, ev, pet[sl], due[sl], collections[sl], fundings[sl], cash[sl], outstanding[sl],
                           funded[sl], contract[sl], collected[sl], (hr[0][mh] - lo, hr[1][mh], hr[2][mh]),
                           (dr[0][md] - lo, dr[1][md], dr[2][md]), failed[j]))
    return out


_KERNEL_LINES: dict[int, tuple] = {}


def _kernel_line(line: Line) -> tuple:
    """What the compiled day loops read from the line, per draw (row r of a stacked batch reads draw r % n): need and
    limit [draws, days], the month-ends [days], the routed invoices [draws, days, slots], the installment days
    [days, installments], the opening exposure's due schedule [tail] and debit book (day, amount, in booking order),
    and the number of routed invoices (a bound on the draws per path). Cached per line object."""
    hit = _KERNEL_LINES.get(id(line))
    if hit is not None and hit[0]() is line:
        return hit[1]
    s, days, ex = line.setup, line.days, line.setup.exposure
    due_idx = np.ascontiguousarray(line.due_idx[:days], dtype=np.int64)
    if any(len(set(r)) != len(r) for r in due_idx.tolist()):
        raise ValueError("two installments of one draw fall due on the same day")  # the loops add them one by one
    due0 = np.zeros(line.tail, dtype=np.int64)
    for d, cents in ex.installments:
        due0[(d - s.review).days - 1] += cents
    due0[0] += ex.past_due_cents
    book = [(0, ex.past_due_cents)] if ex.past_due_cents else []
    book += [((d - s.review).days - 1, cents) for d, cents in ex.installments]
    routes = np.ascontiguousarray(line.routes[:, :days], dtype=np.int64)
    out = (np.ascontiguousarray(line.need[:, :days], dtype=np.int64),
           np.ascontiguousarray(line.limit[:, :days], dtype=np.int64),
           np.ascontiguousarray(line.month_end[:days], dtype=np.bool_), routes, due_idx, due0,
           np.array([d for d, _ in book], dtype=np.int64), np.array([c for _, c in book], dtype=np.int64),
           int((routes > 0).sum()))
    if len(_KERNEL_LINES) >= 8:
        _KERNEL_LINES.clear()
    _KERNEL_LINES[id(line)] = (weakref.ref(line), out)
    return out


def _in_loop_order(hr: tuple, dr: tuple, days: int, slots: int) -> tuple:
    """The kernels emit headroom and draw entries row by row; the day loop over stacked rows emitted them by day, then
    (draws) slot, then row. Returns (rows, values, days) and (rows, days, amounts) in that order."""
    (hr_r, hr_t, hr_v), (dr_t, dr_k, dr_r, dr_a) = hr, dr
    o = _stable_buckets(hr_t, days)  # rows ascending within a bucket: the kernels emit row by row
    p = _stable_buckets(dr_t * slots + dr_k, days * slots)
    return (hr_r[o], hr_v[o], hr_t[o]), (dr_r[p], dr_t[p], dr_a[p])


@njit(cache=True)
def _stable_buckets(bucket, nb):
    """The stable counting-sort order of entries by bucket (0 <= bucket < nb)."""
    start = np.zeros(nb + 1, np.int64)
    for x in bucket:
        start[x + 1] += 1
    for j in range(nb):
        start[j + 1] += start[j]
    out = np.empty(bucket.shape[0], np.int64)
    for q in range(bucket.shape[0]):
        out[start[bucket[q]]] = q
        start[bucket[q]] += 1
    return out


@njit(cache=True)
def _net_kernel(base, pet, need, limit, month_end, routes, due_idx, fee_bps, inst, debit, due0, book_d, book_a,
                opening, funded0, contract0, draw_cap):
    """`run_many`'s day loop (net cash processing), compiled, one row at a time: the operations of the vectorised loop
    per trajectory, in its order (`_debit`: each pending installment oldest first, in full or failed). Arrays are
    [rows, days]; row r reads draw r % n of the line's arrays."""
    rn, days = base.shape
    n, tail, nslot = need.shape[0], due0.shape[0], routes.shape[2]
    cash = np.empty((rn, days), np.int64)
    collections = np.zeros((rn, days), np.int64)
    fundings = np.zeros((rn, days), np.int64)
    outstanding = np.empty((rn, days), np.int64)
    due = np.empty((rn, tail), np.int64)
    funded = np.empty(rn, np.int64)
    contract = np.empty(rn, np.int64)
    collected = np.empty(rn, np.int64)
    failed = np.zeros(rn, np.int64)
    hr_r = np.empty(rn * days, np.int64)
    hr_t = np.empty(rn * days, np.int64)
    hr_v = np.empty(rn * days, np.int64)
    dr_t = np.empty(draw_cap, np.int64)
    dr_k = np.empty(draw_cap, np.int64)
    dr_r = np.empty(draw_cap, np.int64)
    dr_a = np.empty(draw_cap, np.int64)
    cap = book_d.shape[0] + days * nslot * inst  # book entries a row can hold
    e_amt = np.empty(cap, np.int64)
    e_next = np.empty(cap, np.int64)
    head = np.empty(tail, np.int64)
    last = np.empty(tail, np.int64)
    p_amt = np.empty(cap, np.int64)
    nh = 0
    nd = 0
    for r in range(rn):
        i = r % n
        for d in range(tail):
            due[r, d] = due0[d]
            head[d] = -1
            last[d] = -1
        ne = 0
        if debit:
            for q in range(book_d.shape[0]):
                d = book_d[q]
                e_amt[ne] = book_a[q]
                e_next[ne] = -1
                if head[d] < 0:
                    head[d] = ne
                else:
                    e_next[last[d]] = ne
                last[d] = ne
                ne += 1
        npend = 0
        avail = opening
        owed = np.int64(0)
        fu = funded0
        co = contract0
        cl = np.int64(0)
        fl = np.int64(0)
        pr = pet[r]
        for t in range(days):
            avail += base[r, t]
            live = t < pr
            owed += due[r, t]
            falls_due = due[r, t] > 0 and live
            if falls_due:
                hr_r[nh] = r
                hr_t[nh] = t
                hr_v[nh] = avail - need[i, t] - owed
                nh += 1
            attempt = live and (falls_due or month_end[t]) and owed > 0
            if debit:
                e = head[t]
                while e >= 0:
                    p_amt[npend] = e_amt[e]
                    npend += 1
                    e = e_next[e]
            if attempt:
                take = np.int64(0)
                if debit:
                    k2 = 0
                    for q in range(npend):
                        a = p_amt[q]
                        if avail >= a:
                            avail -= a
                            take += a
                        else:
                            fl += 1
                            p_amt[k2] = a
                            k2 += 1
                    npend = k2
                else:
                    x = avail - need[i, t]
                    if x < 0:
                        x = 0
                    take = owed if owed < x else x
                    avail -= take
                    if owed > take:
                        fl += 1
                owed -= take
                cl += take
                collections[r, t] = take
            if live and owed == 0:
                for k in range(nslot):
                    amt = routes[i, t, k]
                    if amt <= 0:
                        continue
                    po = fu - (cl * fu // co if co > 0 else 0)
                    if po + amt > limit[i, t]:
                        continue
                    total = amt + (2 * amt * fee_bps + 10_000) // 20_000  # installment_amounts
                    part = (2 * total + inst) // (2 * inst)
                    for kk in range(inst):
                        a = part if kk < inst - 1 else total - part * (inst - 1)
                        d = due_idx[t, kk]
                        due[r, d] += a
                        if debit:
                            e_amt[ne] = a
                            e_next[ne] = -1
                            if head[d] < 0:
                                head[d] = ne
                            else:
                                e_next[last[d]] = ne
                            last[d] = ne
                            ne += 1
                    fu += amt
                    co += total
                    avail += amt
                    fundings[r, t] += amt
                    dr_t[nd] = t
                    dr_k[nd] = k
                    dr_r[nd] = r
                    dr_a[nd] = amt
                    nd += 1
            cash[r, t] = avail
            outstanding[r, t] = fu - (cl * fu // co if co > 0 else 0)
        funded[r] = fu
        contract[r] = co
        collected[r] = cl
        failed[r] = fl
    return (cash, collections, fundings, outstanding, due, funded, contract, collected, failed,
            (hr_r[:nh], hr_t[:nh], hr_v[:nh]), (dr_t[:nd], dr_k[:nd], dr_r[:nd], dr_a[:nd]))


def _debit(avail: np.ndarray, pend_r: np.ndarray, pend_a: np.ndarray, attempt: np.ndarray, rn: int) -> tuple:
    """Slope's automatic debits on the attempting rows: each pending installment in order (oldest first per row) is
    collected in full when the row's available cash covers it, else it fails. Debits `avail` in place. Returns the
    amount collected per row, the indices into `pend` that were paid, and those that failed."""
    take = np.zeros(rn, dtype=np.int64)
    idx = np.flatnonzero(attempt[pend_r])
    if not idx.size:
        return take, idx, idx
    idx = idx[np.argsort(pend_r[idx], kind="stable")]  # by row, due-date order kept within each row
    r, a = pend_r[idx], pend_a[idx]
    start = np.flatnonzero(np.r_[True, r[1:] != r[:-1]])
    rank = np.arange(len(r)) - np.repeat(start, np.diff(np.r_[start, len(r)]))
    ok = np.zeros(len(r), dtype=bool)
    for k in range(int(rank.max()) + 1):  # each row's k-th installment: one per row, so the rows are distinct
        m = np.flatnonzero(rank == k)
        rows, amts = r[m], a[m]
        hit = avail[rows] >= amts
        avail[rows[hit]] -= amts[hit]
        take[rows[hit]] += amts[hit]
        ok[m[hit]] = True
    return take, idx[ok], idx[~ok]


def _finish(line: Line, events: EventCash, pet, due, collections, fundings, cash, outstanding, funded, contract,
            collected, hr, dr, failed) -> Trajectories:
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
    p0 = line.setup.exposure.principal_cents
    return Trajectories(
        opening_principal=p0, cash=cash, collections=collections, fundings=fundings, outstanding=outstanding, due=due[:, :days],
        locked=np.cumsum(events.lock, axis=1), capacity=np.cumsum(events.capacity, axis=1),
        petition=np.where(petitioned, pet, -1), drawn=funded - p0, contractual=contract, collected=collected,
        stayed=stayed, stayed_principal=np.where(petitioned, outstanding[:, -1], 0),
        preference=(collections * window).sum(axis=1), not_yet_due=not_yet_due,
        uncollected=contract - collected - stayed - not_yet_due, lender_pv=pv_c - pv_f, pv_fundings=pv_f,
        pv_collections=pv_c, dollar_days=outstanding.sum(axis=1) / 100.0, min_cash=cash.min(axis=1),
        min_headroom=min_headroom, headroom_rows=headroom_rows, headroom=headroom, headroom_days=hr[2],
        draw_rows=dr[0], draw_days=dr[1], draw_amounts=dr[2], failed_debits=failed)

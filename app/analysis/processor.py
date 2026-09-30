"""The daily cash processor (QUESTIONS_20240514 §2.2, Scenario): `engine.run_many` under `cash_processing = daily`.

Per trajectory and day, in this order:
- **Receipts post:** operating inflows and event inflows (financing proceeds, a credit); encumbrance changes too (a
  stay's security locked, or released).
- **A levy** attaches the balance: yesterday's end balance plus today's receipts, less locked cash. The Chain books the
  amount it reaches (`Chain.processing_balance`); the processor takes that amount first.
- **Scheduled obligations** clear in the order incurred, each in full or not at all: Slope's installments by automatic
  debit (the line's existing debit order and retry dates: each due date, and month-ends while anything is overdue),
  settlement installments, cash interest on the notes, a judgment payment. An event obligation goes before the first
  of Slope's installments (in their debit order) incurred after it; the notes' interest (indenture, 2022) goes first.
- **Operating outflows** are paid up to the balance. An invoice Slope pays (a draw) leaves them, as under `net`.
- **Arrears.** What stays unpaid becomes an arrear in its class (ARREARS). Slope's arrears clear only by its debit on
  its retry dates. After the day's obligations, the available balance (locked cash is already out of it) pays the
  other classes' arrears, oldest first: a scheduled arrear (settlement, notes interest, judgment) in full or not at
  all, as the day's scheduled obligations are (one the balance cannot cover stays, and a later one may still be
  paid); operating arrears up to the balance. From a petition, arrears stand as they are: nothing pays them and no
  new one arises.
- Available cash never goes below zero.

`same_day_order = operating_first` (one internal measurement, never the base) pays the operating outflows before the
scheduled obligations; the invoices Slope pays that day are decided after the debits, as in the base order, and what
the borrower paid toward them comes back once they are routed.

**General nonpayment, §7.01(j)(v)** (QUESTIONS_20240514 §3.3), tested each day t: over the preceding `window` days
(t - window .. t - 1), before any petition (its consequences are moot after one), arrears were outstanding at the
end of every day, and the obligations that fell due in those days and were still unpaid at the end of t - 1 amount to
at least `share_bps` of all obligations that fell due in them. A levy is neither an obligation falling due nor one
left unpaid. The still-unpaid part is read arrear by arrear (each keeps the day it arose; a skipped scheduled arrear
can outlive a later one), Slope's installment by installment. Before a trajectory's first unpaid
obligation nothing differs from `net`: every obligation is paid in full, so the order of the day's items cannot matter.
"""

from __future__ import annotations

import os
import weakref
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from numba import njit

from app.analysis.events import BIG, OBLIGATIONS, EventCash

ARREARS = ("slope", "settlement", "notes_interest", "judgment", "operating")  # arrears classes, Trajectories.arrears
OPERATING = ARREARS.index("operating")
NCLASS = len(ARREARS)
OPENING_INCURRED = -1  # the opening exposure's installments: the line opened before the review date, after the notes


@dataclass
class Processed:
    """The daily processor's outputs for one joint path, per draw (`Trajectories.processed`)."""
    arrears: np.ndarray  # [draws, days, ARREARS] arrears by class at each day's end
    first_unpaid: np.ndarray  # [draws] the first day an obligation went unpaid (events.BIG: none)
    nonpayment: np.ndarray | None  # [draws] the first day §3.3 is met (BIG: none); None: no terms given
    levy_unmet: np.ndarray  # [draws] levy booked beyond the balance at processing (0 whenever the Chain booked it)


def event_parts(line, events: list[EventCash]) -> dict:
    """The events stacked row-wise, day-major, split for the processor: receipts, locks, the levy, the operating
    outflow still to pay before draws (outflow less the reductions), and each obligation's amount and incurred day."""
    ops, days = line.ops, line.days
    if ops.inflow is None or ops.outflow is None:
        raise ValueError("daily cash processing needs the operating inflows and outflows apart (Operating.inflow)")
    split = [e.split() for e in events]
    parts = [p for p in split if p is not None]
    if len(parts) != len(split):
        raise ValueError("daily cash processing needs the event cash by kind (EventCash.kinds)")
    cat = lambda f: np.ascontiguousarray(np.concatenate([f(e, p) for e, p in zip(events, parts, strict=True)]).T)  # noqa: E731
    inflow = cat(lambda e, p: p[0]["inflow"])
    levy = -cat(lambda e, p: p[0]["levy"])
    obl = {c: -cat(lambda e, p, c=c: p[0][c]) for c in OBLIGATIONS}
    if (inflow < 0).any() or (levy < 0).any() or any((v < 0).any() for v in obl.values()):
        raise ValueError("event cash of the wrong sign for its kind (a receipt paid out or an obligation received)")
    b = len(events)
    return {
        "recv": np.ascontiguousarray(np.tile(ops.inflow[:, :days].T, (1, b))) + inflow,
        "lock": cat(lambda e, p: e.lock),
        "levy": levy,
        # the day's operating payments (positive) less legal spend that stopped (a reduction, never a receipt)
        "out": np.ascontiguousarray(np.tile(-ops.outflow[:, :days].T, (1, b))) - cat(lambda e, p: p[0]["reduction"]),
        "obl": obl,
        "inc": {c: np.concatenate([p[1][c] for p in parts]) for c in OBLIGATIONS},
    }


def pay_arrears(q_r: np.ndarray, q_c: np.ndarray, q_a: np.ndarray, bal: np.ndarray, by_class: np.ndarray) -> np.ndarray:
    """The available balance `bal` [rows] pays each row's arrears queue oldest first (entries are in the order they
    arose): a scheduled arrear in full or not at all (one the balance cannot cover stays, and a later one may still be
    paid, as the day's scheduled obligations are processed), operating arrears up to the balance. Updates `q_a` and
    `by_class` [classes, rows] in place; returns what each row paid."""
    left = bal.copy()
    idx = np.flatnonzero(left[q_r] > 0)
    if not idx.size:
        return bal - left
    idx = idx[np.argsort(q_r[idx], kind="stable")]
    r = q_r[idx]
    start = np.flatnonzero(np.r_[True, r[1:] != r[:-1]])
    rank = np.arange(len(r)) - np.repeat(start, np.diff(np.r_[start, len(r)]))
    order = np.argsort(rank, kind="stable")
    bounds = np.searchsorted(rank[order], np.arange(int(rank.max()) + 2))
    for k in range(len(bounds) - 1):  # each row's k-th entry: one per row, so the rows are distinct
        m = idx[order[bounds[k]:bounds[k + 1]]]
        m = m[left[q_r[m]] > 0]
        if not m.size:  # a row with a k-th entry has every earlier one: nothing is left to pay on any row
            break
        rr, a = q_r[m], q_a[m]
        p = np.where(q_c[m] == OPERATING, np.minimum(a, left[rr]), np.where(left[rr] >= a, a, 0))
        q_a[m] = a - p
        left[rr] -= p
        by_class[q_c[m], rr] -= p
    return bal - left


def order_items(rows: np.ndarray, inc: np.ndarray, cls: np.ndarray, seq: np.ndarray, rn: int) -> np.ndarray:
    """The processing order of one day's scheduled items: per row, Slope's installments (cls -1) keep their debit order
    (`seq`) and each event obligation (cls: its OBLIGATIONS index, at most one per row and class) goes before the first
    Slope installment incurred after it (a Slope installment incurred the same day goes first); event obligations at
    one place go by incurred day, then class. Returns the item indices in processing order."""
    slope = cls < 0
    pos = np.where(slope, seq, 0)
    s_idx = np.flatnonzero(slope)
    if s_idx.size and not slope.all():
        count = np.bincount(rows[s_idx], minlength=rn)
        for c in np.unique(cls[~slope]):
            ev = np.flatnonzero(cls == c)
            e_inc = np.full(rn, BIG, dtype=np.int64)
            e_inc[rows[ev]] = inc[ev]
            later = s_idx[inc[s_idx] > e_inc[rows[s_idx]]]
            first = count.copy()
            np.minimum.at(first, rows[later], seq[later])
            pos[ev] = first[rows[ev]]
    return np.lexsort((cls, np.where(slope, 0, inc), slope, pos, rows))


# Checkpoints contain the entire debit/arrears state, not just cash. The key hashes all inputs before
# the checkpoint and the obligation-incurrence metadata; later bookings can reuse that exact prefix.
_PREFIX_RUNS: OrderedDict = OrderedDict()
_PREFIX_BYTES = 0
_PREFIX_LIMIT = 64 * 2**20
PREFIX_STATS = [0, 0]  # hits, misses; reported with walk progress


def _prefix_key(line, opening, terms, arrays, petition, day):
    import xxhash

    h = xxhash.xxh3_128()
    for a in (*[v[..., :day] for v in arrays[:4]], arrays[4], np.minimum(petition, day)):
        h.update(np.ascontiguousarray(a))
    return id(line), opening, terms, day, h.digest()


def _keep_prefix(key, line, checkpoints, cash, arrears):
    global _PREFIX_BYTES
    offsets = np.r_[0, np.cumsum([a.size for a in checkpoints])].astype(np.int64)
    packed = np.concatenate(checkpoints)
    value = (weakref.ref(line), (packed, offsets), cash.copy(), arrears.copy())
    size = packed.nbytes + offsets.nbytes + value[2].nbytes + value[3].nbytes
    if size > _PREFIX_LIMIT:
        return
    old = _PREFIX_RUNS.pop(key, None)
    if old is not None:
        _PREFIX_BYTES -= old[-1]
    _PREFIX_RUNS[key] = (*value, size)
    _PREFIX_BYTES += size
    while _PREFIX_BYTES > _PREFIX_LIMIT:
        _, removed = _PREFIX_RUNS.popitem(last=False)
        _PREFIX_BYTES -= removed[-1]


def run_daily(line, opening_cents: int, events: list[EventCash], nonpayment: tuple[int, int] | None = None,
              cash_only: bool = False) -> list:
    """`engine.run_many` under daily processing (module docstring). `nonpayment` (window days, unpaid share bps): the
    §3.3 terms the contract declares; None leaves `Processed.nonpayment` uncomputed (None). The day loop is compiled
    (`_daily_kernel`), one trajectory at a time: every step is per trajectory and in integers. `cash_only`: per path
    only (cash, first_unpaid, nonpayment, arrears), exactly the arrays a full run's `tr.cash` and `tr.processed`
    carry, without the logs' reordering or `_finish` (what the walk's Chain reads)."""
    from app.analysis.engine import _finish, _in_loop_order, _kernel_line

    s, n, days, b = line.setup, line.ops.draws, line.days, len(events)
    need, limit, month_end, routes, due_idx, due0, book_d, book_a, nroutes = _kernel_line(line)
    post, levy, out, obl, inc = _rows(line, events)
    pet = np.concatenate([np.where((e.petition >= 0) & (e.petition < days), e.petition, days) for e in events]
                         ).astype(np.int64, copy=False)
    ex = s.exposure
    w, share = (int(nonpayment[0]), int(nonpayment[1])) if nonpayment is not None else (0, 0)
    checkpoint_day = days // 2 if cash_only and b == 1 and os.environ.get("SLOPE_DAILY_PREFIX", "1") == "1" else 0
    key = _prefix_key(line, opening_cents, (w, share), (post, levy, out, obl, inc), pet,
                      checkpoint_day) if checkpoint_day else None
    cached = _PREFIX_RUNS.get(key)
    if cached is not None and cached[0]() is not line:
        cached = None
    if cached is not None:
        _PREFIX_RUNS.move_to_end(key)
    if checkpoint_day:
        PREFIX_STATS[0 if cached is not None else 1] += 1
    start = checkpoint_day if cached is not None else 0
    resume = cached[1] if cached is not None else (np.empty(0, np.int64), np.empty(0, np.int64))
    (cash, collections, fundings, outstanding, due, funded, contract, collected, failed, arrears, first_unpaid, nonpay,
     levy_unmet, hr, dr, checkpoints) = _daily_kernel(
        post, levy, out, obl, inc, pet, need, limit, month_end, routes, due_idx, np.int64(s.fee_bps), s.installments,
        s.collection == "debit", s.same_day_order == "operating_first", due0, book_d, book_a,
        np.int64(opening_cents + ex.cash_cents), np.int64(ex.principal_cents), np.int64(ex.owed_cents), b * nroutes,
        np.int64(w), np.int64(share), cash_only, start, checkpoint_day, resume[0], resume[1])
    if start:
        cash[:, :start] = cached[2]
        arrears[:, :start] = cached[3]
    elif checkpoint_day:
        _keep_prefix(key, line, checkpoints, cash[:, :checkpoint_day], arrears[:, :checkpoint_day])
    if cash_only:  # with the arrears the same run computed (events.Chain._daily_run keeps them apart)
        return [(cash[j * n:(j + 1) * n], first_unpaid[j * n:(j + 1) * n].copy(),
                 None if nonpayment is None else nonpay[j * n:(j + 1) * n].copy(), arrears[j * n:(j + 1) * n])
                for j in range(b)]
    hr, dr = _in_loop_order(hr, dr, days, routes.shape[2])
    res = []
    failed = failed.reshape(b, n)
    for j, e in enumerate(events):
        lo, hi = j * n, (j + 1) * n
        sl = slice(lo, hi)
        mh, md = (hr[0] >= lo) & (hr[0] < hi), (dr[0] >= lo) & (dr[0] < hi)
        tr = _finish(line, e, pet[sl], due[sl], collections[sl], fundings[sl], cash[sl], outstanding[sl], funded[sl],
                     contract[sl], collected[sl], (hr[0][mh] - lo, hr[1][mh], hr[2][mh]),
                     (dr[0][md] - lo, dr[1][md], dr[2][md]), failed[j])
        tr.processed = Processed(arrears=arrears[sl], first_unpaid=first_unpaid[sl].copy(),
                                 nonpayment=None if nonpayment is None else nonpay[sl].copy(),
                                 levy_unmet=levy_unmet[sl].copy())
        res.append(tr)
    return res


_OPS_ROWS: dict[tuple, tuple] = {}  # (id(line), b) -> (weakref to the line, tiled inflow, tiled outflow): `_rows`


def _rows(line, events: list[EventCash]) -> tuple:
    """`event_parts`, row-major [rows, days] (the kernel reads one trajectory's days in sequence): what posts before
    anything is paid (receipts less encumbrance changes), the levy, the operating outflow still to pay before draws,
    the obligations [OBLIGATIONS, rows, days] and their incurred days [OBLIGATIONS, rows]. The operating part (the
    line's inflows and outflows tiled for b paths) depends only on the line: cached per line object and b."""
    import weakref

    ops, days = line.ops, line.days
    if ops.inflow is None or ops.outflow is None:
        raise ValueError("daily cash processing needs the operating inflows and outflows apart (Operating.inflow)")
    b = len(events)
    hit = _OPS_ROWS.get((id(line), b))
    if hit is None or hit[0]() is not line:
        if len(_OPS_ROWS) >= 8:
            _OPS_ROWS.clear()
        hit = _OPS_ROWS[(id(line), b)] = (weakref.ref(line), np.tile(ops.inflow[:, :days], (b, 1)),
                                          np.tile(-ops.outflow[:, :days], (b, 1)))
    _, ops_in, ops_out = hit  # read-only here: every use below builds a new array
    split = [e.split() for e in events]
    parts = [p for p in split if p is not None]
    if len(parts) != len(split):
        raise ValueError("daily cash processing needs the event cash by kind (EventCash.kinds)")
    cat = lambda f: np.concatenate([f(e, p) for e, p in zip(events, parts, strict=True)]).astype(np.int64, copy=False)  # noqa: E731
    inflow = cat(lambda e, p: p[0]["inflow"])
    levy = -cat(lambda e, p: p[0]["levy"])
    obl = np.stack([-cat(lambda e, p, c=c: p[0][c]) for c in OBLIGATIONS])
    if (inflow < 0).any() or (levy < 0).any() or (obl < 0).any():
        raise ValueError("event cash of the wrong sign for its kind (a receipt paid out or an obligation received)")
    post = ops_in + inflow - cat(lambda e, p: e.lock)
    out = ops_out - cat(lambda e, p: p[0]["reduction"])
    inc = np.stack([np.concatenate([p[1][c] for p in parts]) for c in OBLIGATIONS]).astype(np.int64, copy=False)
    return post, levy, out, obl, inc


@njit(cache=True)
def _book(d, a, i, e_amt, e_inc, e_next, head, last, ne):
    """Append an installment (amount a, incurred day i) to the debit book of day d; returns the new entry count."""
    e_amt[ne] = a
    e_inc[ne] = i
    e_next[ne] = -1
    if head[d] < 0:
        head[d] = ne
    else:
        e_next[last[d]] = ne
    last[d] = ne
    return ne + 1


@njit(cache=True)
def _daily_kernel(post, levy, out, obl, inc, pet, need, limit, month_end, routes, due_idx, fee_bps, inst, debit,
                  first_op, due0, book_d, book_a, opening, funded0, contract0, draw_cap, w, share, cash_only, start, checkpoint_day, resume, resume_offsets):
    """`run_daily`'s day loop, compiled, one trajectory (row) at a time, each step as the vectorised loop took it:
    the day's scheduled items in `order_items` order, `pay_arrears` oldest first, §3.3 on the window (the still-unpaid
    arrears summed in float64 in queue order and truncated, as `np.bincount` with weights did). Row r reads draw r % n
    of the line's arrays."""
    rn, days = post.shape
    n, tail, nslot, nobl = need.shape[0], due0.shape[0], routes.shape[2], obl.shape[0]
    cash = np.empty((rn, days), np.int64)
    collections = np.zeros((0, 0) if cash_only else (rn, days), np.int64)
    fundings = np.zeros((0, 0) if cash_only else (rn, days), np.int64)
    outstanding = np.empty((0, 0) if cash_only else (rn, days), np.int64)
    due = np.empty((rn, tail), np.int64)
    arrears = np.empty((rn, days, NCLASS), np.int64)
    funded = np.empty(rn, np.int64)
    contract = np.empty(rn, np.int64)
    collected = np.empty(rn, np.int64)
    failed = np.zeros(rn, np.int64)
    first_unpaid = np.full(rn, BIG, np.int64)
    nonpay = np.full(rn, BIG, np.int64)
    levy_unmet = np.zeros(rn, np.int64)
    hr_r = np.empty(0 if cash_only else rn * days, np.int64)
    hr_t = np.empty(0 if cash_only else rn * days, np.int64)
    hr_v = np.empty(0 if cash_only else rn * days, np.int64)
    dr_t = np.empty(0 if cash_only else draw_cap, np.int64)
    dr_k = np.empty(0 if cash_only else draw_cap, np.int64)
    dr_r = np.empty(0 if cash_only else draw_cap, np.int64)
    dr_a = np.empty(0 if cash_only else draw_cap, np.int64)
    cap = book_d.shape[0] + days * nslot * inst  # book entries a row can hold
    e_amt = np.empty(cap, np.int64)
    e_inc = np.empty(cap, np.int64)
    e_next = np.empty(cap, np.int64)
    head = np.empty(tail, np.int64)
    last = np.empty(tail, np.int64)
    p_amt = np.empty(cap, np.int64)  # pending installments: amount, incurred day, day fallen due
    p_inc = np.empty(cap, np.int64)
    p_day = np.empty(cap, np.int64)
    qcap = days * (nobl + 1)  # arrears queue: class, amount, day arisen
    q_c = np.empty(qcap, np.int64)
    q_a = np.empty(qcap, np.int64)
    q_d = np.empty(qcap, np.int64)
    icap = cap + nobl + 1  # the day's scheduled items
    it_amt = np.empty(icap, np.int64)
    it_inc = np.empty(icap, np.int64)
    it_cls = np.empty(icap, np.int64)
    it_seq = np.empty(icap, np.int64)
    it_pos = np.empty(icap, np.int64)
    it_ok = np.empty(icap, np.bool_)
    order = np.empty(icap, np.int64)
    fell = np.empty(days, np.int64)
    slope_due = np.empty(days, np.int64)
    bc = np.zeros(NCLASS, np.int64)
    nh = 0
    nd = 0
    checkpoints = [np.empty(0, np.int64) for _ in range(rn)]
    for r in range(rn):
        i = r % n
        for d in range(tail):
            due[r, d] = due0[d]
            head[d] = -1
            last[d] = -1
        ne = 0
        if debit:
            for q in range(book_d.shape[0]):
                ne = _book(book_d[q], book_a[q], OPENING_INCURRED, e_amt, e_inc, e_next, head, last, ne)
        for t in range(days):
            f = np.int64(0)
            for c in range(nobl):
                f += obl[c, r, t]
            fell[t] = f
        for c in range(NCLASS):
            bc[c] = 0
        npend = 0
        nq = 0
        avail = opening
        owed = np.int64(0)
        fu = funded0
        co = contract0
        cl = np.int64(0)
        fl = np.int64(0)
        lu = np.int64(0)
        fu_day = BIG
        np_day = BIG
        streak = 0
        pr = pet[r]
        if start:
            saved = resume[resume_offsets[r]:resume_offsets[r + 1]]
            avail = saved[0]
            owed = saved[1]
            fu = saved[2]
            co = saved[3]
            cl = saved[4]
            fl = saved[5]
            lu = saved[6]
            fu_day = saved[7]
            np_day = saved[8]
            streak = saved[9]
            ne = saved[10]
            npend = saved[11]
            nq = saved[12]
            off = 13
            due[r][:tail] = saved[off:off + tail]
            off += tail
            head[:tail] = saved[off:off + tail]
            off += tail
            last[:tail] = saved[off:off + tail]
            off += tail
            e_amt[:ne] = saved[off:off + ne]
            off += ne
            e_inc[:ne] = saved[off:off + ne]
            off += ne
            e_next[:ne] = saved[off:off + ne]
            off += ne
            p_amt[:npend] = saved[off:off + npend]
            off += npend
            p_inc[:npend] = saved[off:off + npend]
            off += npend
            p_day[:npend] = saved[off:off + npend]
            off += npend
            q_c[:nq] = saved[off:off + nq]
            off += nq
            q_a[:nq] = saved[off:off + nq]
            off += nq
            q_d[:nq] = saved[off:off + nq]
            off += nq
            bc[:NCLASS] = saved[off:off + NCLASS]
            off += NCLASS
            fell[:start] = saved[off:off + start]
            off += start
            slope_due[:start] = saved[off:off + start]
            off += start
        for t in range(start, days):
            if checkpoint_day and t == checkpoint_day and not start:
                checkpoints[r] = np.concatenate((np.array((avail, owed, fu, co, cl, fl, lu, fu_day, np_day, streak, ne, npend, nq), dtype=np.int64),
                                                  due[r][:tail], head[:tail], last[:tail], e_amt[:ne], e_inc[:ne], e_next[:ne], p_amt[:npend], p_inc[:npend], p_day[:npend], q_c[:nq], q_a[:nq], q_d[:nq], bc[:NCLASS], fell[:t], slope_due[:t]))

            live = t < pr
            if w > 0 and t >= w and streak >= w:  # §3.3 on the days t - w .. t - 1
                dw = np.int64(0)
                for u in range(t - w, t):
                    dw += fell[u]
                lf = 0.0
                for q in range(nq):
                    if q_d[q] >= t - w:
                        lf += q_a[q]
                left = np.int64(lf)
                if debit:
                    for q in range(npend):
                        if p_day[q] >= t - w:
                            left += p_amt[q]
                else:
                    sd_w = np.int64(0)
                    for u in range(t - w, t):
                        sd_w += slope_due[u]
                    left += min(bc[0], sd_w)
                if live and np_day == BIG and dw > 0 and left * 10_000 >= share * dw:
                    np_day = t
            avail += post[r, t]
            dt = due[r, t]
            owed += dt
            sd = dt if live else np.int64(0)
            slope_due[t] = sd
            lv = levy[r, t]  # the Chain books what the levy reaches: normally all of it
            x = avail if avail > 0 else np.int64(0)
            take = lv if lv < x else x
            lu += lv - take
            avail -= take
            falls_due = sd > 0
            if falls_due and not cash_only:
                hr_r[nh] = r
                hr_t[nh] = t
                hr_v[nh] = avail - need[i, t] - owed
                nh += 1
            attempt = live and (falls_due or month_end[t]) and owed > 0
            if debit:
                e = head[t]
                while e >= 0:
                    p_amt[npend] = e_amt[e]
                    p_inc[npend] = e_inc[e]
                    p_day[npend] = t
                    npend += 1
                    e = e_next[e]
            a0 = avail
            pay0 = np.int64(0)
            if first_op:  # operating outflows first, the invoices Slope pays still among them
                x = avail if avail > 0 else np.int64(0)
                pay0 = out[r, t] if out[r, t] < x else x
                avail -= pay0
            unpaid = False
            # the day's scheduled items: Slope's installments (debit order) and the events' (incurred order)
            ni = 0
            if attempt:
                if debit:
                    for q in range(npend):
                        it_amt[ni] = p_amt[q]
                        it_inc[ni] = p_inc[q]
                        it_cls[ni] = -1
                        it_seq[ni] = q
                        ni += 1
                else:  # protect_need: one collection, its amount read at its turn
                    it_amt[ni] = 0
                    it_inc[ni] = OPENING_INCURRED
                    it_cls[ni] = -1
                    it_seq[ni] = 0
                    ni += 1
            nslope = ni
            for c in range(nobl):
                a = obl[c, r, t]
                if a != 0:
                    it_amt[ni] = a
                    it_inc[ni] = inc[c, r]
                    it_cls[ni] = c
                    it_seq[ni] = 0
                    ni += 1
            coll = np.int64(0)
            if ni:
                for q in range(ni):  # order_items: an event obligation before the first Slope item incurred after it
                    if q < nslope:
                        it_pos[q] = it_seq[q]
                    else:
                        p = nslope
                        for s_ in range(nslope):
                            if it_inc[s_] > it_inc[q] and it_seq[s_] < p:
                                p = it_seq[s_]
                        it_pos[q] = p
                for q in range(ni):  # insertion sort on (pos, slope, incurred for events, class)
                    order[q] = q
                    j = q
                    while j > 0 and _before(order[j], order[j - 1], it_pos, it_cls, it_inc):
                        order[j], order[j - 1] = order[j - 1], order[j]
                        j -= 1
                for q in range(ni):
                    m = order[q]
                    a = it_amt[m]
                    if not debit and it_cls[m] < 0:
                        x = avail - need[i, t]
                        if x < 0:
                            x = 0
                        a = owed if owed < x else x
                        it_amt[m] = a
                    it_ok[m] = avail >= a
                    if it_ok[m]:
                        avail -= a
                k2 = 0
                for q in range(ni):
                    m = order[q]
                    slope = it_cls[m] < 0
                    if it_ok[m]:
                        if slope:
                            coll += it_amt[m]
                        continue
                    if not slope or debit:
                        unpaid = True
                    if not slope and live:  # from a petition, arrears stand as they are
                        q_c[nq] = it_cls[m] + 1
                        q_a[nq] = it_amt[m]
                        q_d[nq] = t
                        nq += 1
                        bc[it_cls[m] + 1] += it_amt[m]
                    if slope and debit:
                        fl += 1
                if debit and nslope:  # the paid installments leave the pending list (item q is pending q)
                    for q in range(npend):
                        if not it_ok[q]:
                            p_amt[k2] = p_amt[q]
                            p_inc[k2] = p_inc[q]
                            p_day[k2] = p_day[q]
                            k2 += 1
                    npend = k2
            if attempt:
                if not debit and owed > coll:
                    fl += 1
                    unpaid = True
                owed -= coll
                cl += coll
                if not cash_only:
                    collections[r, t] = coll
            g = out[r, t]  # the day's operating payments the borrower owes, less the invoices Slope pays
            if live and owed == 0:
                routed = np.int64(0)
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
                            ne = _book(d, a, t, e_amt, e_inc, e_next, head, last, ne)
                    fu += amt
                    co += total
                    routed += amt
                    if not cash_only:
                        fundings[r, t] += amt
                        dr_t[nd] = t
                        dr_k[nd] = k
                        dr_r[nd] = r
                        dr_a[nd] = amt
                        nd += 1
                g = g - routed
            if first_op:
                x = a0 if a0 > 0 else np.int64(0)
                pay = g if g < x else x
                avail += pay0 - pay  # what it paid toward invoices Slope then paid comes back
            else:
                x = avail if avail > 0 else np.int64(0)
                pay = g if g < x else x
                avail -= pay
            short = g - pay  # >= 0: unpaid operating outflow
            fell[t] += sd
            fell[t] += g if g > 0 else np.int64(0)
            if short > 0:
                unpaid = True
            if short != 0 and live:
                q_c[nq] = OPERATING
                q_a[nq] = short
                q_d[nq] = t
                nq += 1
                bc[OPERATING] += short
            if nq:  # the available balance pays the other classes' arrears, oldest first; nothing from a petition
                bal = (avail if avail > 0 else np.int64(0)) if live else np.int64(0)
                left = bal
                for q in range(nq):
                    if left <= 0:
                        break
                    a = q_a[q]
                    c = q_c[q]
                    if c == OPERATING:
                        p = a if a < left else left
                    else:
                        p = a if left >= a else np.int64(0)
                    q_a[q] = a - p
                    left -= p
                    bc[c] -= p
                paid_ = bal - left
                if paid_ != 0:
                    avail -= paid_
                    k2 = 0
                    for q in range(nq):
                        if q_a[q] > 0:
                            q_c[k2] = q_c[q]
                            q_a[k2] = q_a[q]
                            q_d[k2] = q_d[q]
                            k2 += 1
                    nq = k2
            if live:
                bc[0] = owed  # Slope's: frozen at a petition (the stayed claim)
            tot = np.int64(0)
            for c in range(NCLASS):
                arrears[r, t, c] = bc[c]
                tot += bc[c]
            streak = streak + 1 if tot > 0 else 0
            if unpaid and fu_day == BIG:
                fu_day = t
            cash[r, t] = avail
            if not cash_only:
                outstanding[r, t] = fu - (cl * fu // co if co > 0 else 0)
        funded[r] = fu
        contract[r] = co
        collected[r] = cl
        failed[r] = fl
        levy_unmet[r] = lu
        first_unpaid[r] = fu_day
        nonpay[r] = np_day
    return (cash, collections, fundings, outstanding, due, funded, contract, collected, failed, arrears, first_unpaid,
            nonpay, levy_unmet, (hr_r[:nh], hr_t[:nh], hr_v[:nh]), (dr_t[:nd], dr_k[:nd], dr_r[:nd], dr_a[:nd]), checkpoints)


@njit(cache=True)
def _before(x, y, pos, cls, inc):
    """Item x goes before item y in `order_items`' lexsort (pos, then events before Slope's, then incurred day for
    events, then class). Items of one row never tie."""
    if pos[x] != pos[y]:
        return pos[x] < pos[y]
    sx, sy = cls[x] < 0, cls[y] < 0
    if sx != sy:
        return sy
    ix = 0 if sx else inc[x]
    iy = 0 if sy else inc[y]
    if ix != iy:
        return ix < iy
    return cls[x] < cls[y]

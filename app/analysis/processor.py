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
  its retry dates. When the day's receipts exceed the day's obligations (the levy, the scheduled obligations falling due
  and the operating outflows), the surplus pays the other classes' arrears, oldest first, up to the balance.
- Available cash never goes below zero.

`same_day_order = operating_first` (one internal measurement, never the base) pays the operating outflows before the
scheduled obligations; the invoices Slope pays that day are decided after the debits, as in the base order, and what
the borrower paid toward them comes back once they are routed.

**General nonpayment, §7.01(j)(v)** (QUESTIONS_20240514 §3.3), tested each day t: over the preceding `window` days
(t - window .. t - 1), before any petition (its consequences are moot after one), arrears were outstanding at the
end of every day, and the obligations that fell due in those days and were still unpaid at the end of t - 1 amount to
at least `share_bps` of all obligations that fell due in them.
Arrears are paid oldest first, so the still-unpaid part of a window's obligations is min(arrears, arrears created in
the window) for the other classes; Slope's are read installment by installment. Before a trajectory's first unpaid
obligation nothing differs from `net`: every obligation is paid in full, so the order of the day's items cannot matter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.analysis.events import BIG, OBLIGATIONS, EventCash

ARREARS = ("slope", "settlement", "notes_interest", "judgment", "operating")  # arrears classes, Trajectories.arrears
OPERATING = ARREARS.index("operating")
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


def fifo_pay(q_r: np.ndarray, q_c: np.ndarray, q_a: np.ndarray, pay: np.ndarray, by_class: np.ndarray) -> tuple:
    """Pay each row's `pay` into its arrears queue oldest first (entries are in the order they arose), partially on
    the last one reached. Updates `by_class` [classes, rows] in place; returns the queue without settled entries."""
    idx = np.flatnonzero(pay[q_r] > 0)
    if idx.size:
        idx = idx[np.argsort(q_r[idx], kind="stable")]
        r, a = q_r[idx], q_a[idx]
        cum = np.cumsum(a)
        start = np.flatnonzero(np.r_[True, r[1:] != r[:-1]])
        base = np.repeat(cum[start] - a[start], np.diff(np.r_[start, len(r)]))
        paid = np.clip(pay[r] - (cum - a - base), 0, a)
        q_a[idx] = a - paid
        np.subtract.at(by_class, (q_c[idx], r), paid)
    keep = q_a > 0
    return q_r[keep], q_c[keep], q_a[keep]


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


def run_daily(line, opening_cents: int, events: list[EventCash], nonpayment: tuple[int, int] | None = None) -> list:
    """`engine.run_many` under daily processing (module docstring). `nonpayment` (window days, unpaid share bps): the
    §3.3 terms the contract declares; None leaves `Processed.nonpayment` uncomputed (None). Steps with nothing to do on
    a day are skipped (no levy, no obligation, no arrears): the result is the same, and most days have none."""
    from app.analysis.engine import _finish, _tiled, installment_amounts

    s, n, days, b = line.setup, line.ops.draws, line.days, len(events)
    debit, first_op = s.collection == "debit", s.same_day_order == "operating_first"
    ev = event_parts(line, events)
    recv, levy, out, obl, inc = (ev[k] for k in ("recv", "levy", "out", "obl", "inc"))
    post = recv - ev["lock"]  # what posts before anything is paid: receipts, less encumbrance changes
    levy_on = levy.any(axis=1)
    obl_on = {c: obl[c].any(axis=1) for c in OBLIGATIONS}
    fell_due = levy + sum(obl.values())  # §3.3: what falls due each day (Slope's and operating added in the loop)
    pet = np.concatenate([np.where((e.petition >= 0) & (e.petition < days), e.petition, days) for e in events])
    need, limit, slots = _tiled(line, b)
    rn = n * b
    due = np.zeros((line.tail, rn), dtype=np.int64)
    ex = s.exposure
    for d, cents in ex.installments:
        due[(d - s.review).days - 1] += cents
    due[0] += ex.past_due_cents
    # debit: book[d] lists (rows, amounts, incurred day) falling due on day d in draw order; `pend_*` hold the
    # installments due and unpaid, oldest first per row, with the day each was incurred and fell due
    book: list[list] = [[] for _ in range(line.tail)] if debit else []
    if debit:
        every = np.arange(rn)
        if ex.past_due_cents:
            book[0].append((every, np.full(rn, ex.past_due_cents, dtype=np.int64), OPENING_INCURRED))
        for d, cents in ex.installments:
            book[(d - s.review).days - 1].append((every, np.full(rn, cents, dtype=np.int64), OPENING_INCURRED))
    empty = lambda: np.zeros(0, dtype=np.int64)  # noqa: E731
    pend_r, pend_a, pend_i, pend_d = empty(), empty(), empty(), empty()
    q_r, q_c, q_a = empty(), empty(), empty()  # the other classes' arrears, in the order they arose
    collections, fundings = np.zeros((days, rn), dtype=np.int64), np.zeros((days, rn), dtype=np.int64)
    cash, outstanding = np.empty((days, rn), dtype=np.int64), np.empty((days, rn), dtype=np.int64)
    arrears = np.zeros((days, len(ARREARS), rn), dtype=np.int64)
    created, slope_due = np.zeros((days, rn), dtype=np.int64), np.zeros((days, rn), dtype=np.int64)
    avail = np.full(rn, opening_cents + ex.cash_cents, dtype=np.int64)
    owed, funded, contract, collected, failed, levy_unmet = (np.zeros(rn, dtype=np.int64) for _ in range(6))
    funded += ex.principal_cents
    contract += ex.owed_cents
    by_class = np.zeros((len(ARREARS), rn), dtype=np.int64)
    first_unpaid, nonpay = np.full(rn, BIG, dtype=np.int64), np.full(rn, BIG, dtype=np.int64)
    streak = np.zeros(rn, dtype=np.int64)  # consecutive days ending yesterday with arrears outstanding
    hr_rows, hr_vals, hr_days, d_rows, d_days, d_amts = [], [], [], [], [], []
    no_rows = np.zeros(rn, dtype=np.int64)

    def principal_out(r=slice(None)) -> np.ndarray:
        f, c = funded[r], contract[r]
        return f - np.where(c > 0, collected[r] * f // np.maximum(c, 1), 0)

    po = principal_out()
    w = nonpayment[0] if nonpayment is not None else 0
    for t in range(days):
        live = t < pet
        if w and t >= w and streak.max() >= w:  # §3.3 on the days t - w .. t - 1, while no petition has been filed
            dw = fell_due[t - w:t].sum(axis=0)
            left = np.minimum(by_class[1:].sum(axis=0), created[t - w:t].sum(axis=0))
            if debit:
                recent = pend_d >= t - w
                np.add.at(left, pend_r[recent], pend_a[recent])
            else:
                left += np.minimum(by_class[0], slope_due[t - w:t].sum(axis=0))
            met = live & (nonpay == BIG) & (streak >= w) & (dw > 0) & (left * 10_000 >= nonpayment[1] * dw)
            nonpay[met] = t
        avail += post[t]
        dt = due[t]
        owed += dt
        sd = slope_due[t]
        np.multiply(dt, live, out=sd)
        if levy_on[t]:  # the Chain books what the levy reaches: normally all of it
            take = np.minimum(levy[t], np.maximum(avail, 0))
            levy_unmet += levy[t] - take
            avail -= take
        falls_due = sd > 0
        if falls_due.any():
            hr_rows.append(np.nonzero(falls_due)[0])
            hr_vals.append((avail - need[t] - owed)[falls_due])
            hr_days.append(np.full(int(falls_due.sum()), t, dtype=np.int64))
        attempt = live & (falls_due | line.month_end[t]) & (owed > 0)
        if debit and book[t]:
            pend_r = np.concatenate([pend_r, *(r for r, _, _ in book[t])])
            pend_a = np.concatenate([pend_a, *(a for _, a, _ in book[t])])
            pend_i = np.concatenate([pend_i, *(np.full(len(r), i, dtype=np.int64) for r, _, i in book[t])])
            pend_d = np.concatenate([pend_d, *(np.full(len(r), t, dtype=np.int64) for r, _, _ in book[t])])
        if first_op:  # operating outflows first, the invoices Slope pays still among them (settled after the draws)
            a0 = avail.copy()
            pay0 = np.minimum(out[t], np.maximum(avail, 0))
            avail -= pay0
        unpaid = None  # rows where an obligation went unpaid today (None: none)
        # the scheduled obligations: Slope's installments (debit order) and the events' (incurred order)
        items = []  # (rows, amounts, incurred, class (-1: Slope), seq, index into pend (-1: none))
        any_attempt = attempt.any()
        if debit and any_attempt:
            sel = np.flatnonzero(attempt[pend_r])
            sel = sel[np.argsort(pend_r[sel], kind="stable")]
            r = pend_r[sel]
            start = np.flatnonzero(np.r_[True, r[1:] != r[:-1]]) if r.size else empty()
            seq = np.arange(len(r)) - np.repeat(start, np.diff(np.r_[start, len(r)])) if r.size else empty()
            items.append((r, pend_a[sel], pend_i[sel], np.full(len(r), -1), seq, sel))
        elif any_attempt:  # protect_need: one collection per row, its amount read at its turn
            r = np.flatnonzero(attempt)
            items.append((r, np.zeros(len(r), dtype=np.int64), np.full(len(r), OPENING_INCURRED), np.full(len(r), -1),
                          np.zeros(len(r), dtype=np.int64), np.full(len(r), -1)))
        for ci, c in enumerate(OBLIGATIONS):
            if obl_on[c][t]:
                r = np.flatnonzero(obl[c][t])
                items.append((r, obl[c][t][r], inc[c][r], np.full(len(r), ci), np.zeros(len(r), dtype=np.int64),
                              np.full(len(r), -1)))
        coll = no_rows
        if items:
            rows, amt, ii, cls, seq, pi = (np.concatenate(x) for x in zip(*items, strict=True))
            o = order_items(rows, ii, cls, seq, rn)
            rows, amt, cls, pi = rows[o], amt[o].astype(np.int64), cls[o], pi[o]
            start = np.flatnonzero(np.r_[True, rows[1:] != rows[:-1]])
            rank = np.arange(len(rows)) - np.repeat(start, np.diff(np.r_[start, len(rows)]))
            ok = np.zeros(len(rows), dtype=bool)
            for k in range(int(rank.max()) + 1):  # each row's k-th item: one per row, so the rows are distinct
                m = np.flatnonzero(rank == k) if k else start
                r, a = rows[m], amt[m]
                if not debit:
                    pn = cls[m] < 0
                    a[pn] = np.minimum(owed[r[pn]], np.maximum(avail[r[pn]] - need[t][r[pn]], 0))
                    amt[m] = a
                hit = avail[r] >= a
                avail[r[hit]] -= a[hit]
                ok[m] = hit
            sl = cls < 0
            coll = np.zeros(rn, dtype=np.int64)
            np.add.at(coll, rows[sl & ok], amt[sl & ok])
            miss = ~ok
            if miss.any():
                unpaid = np.zeros(rn, dtype=bool)
                unpaid[rows[miss & (~sl | debit)]] = True  # protect_need: read from what stays owed, below
                ev_miss = miss & ~sl
                if ev_miss.any():
                    q_r, q_c, q_a = (np.concatenate([q_r, rows[ev_miss]]), np.concatenate([q_c, cls[ev_miss] + 1]),
                                     np.concatenate([q_a, amt[ev_miss]]))
                    np.add.at(by_class, (cls[ev_miss] + 1, rows[ev_miss]), amt[ev_miss])
                    np.add.at(created[t], rows[ev_miss], amt[ev_miss])
            if debit:
                failed += np.bincount(rows[sl & miss], minlength=rn)
                keep = np.ones(len(pend_r), dtype=bool)
                keep[pi[sl & ok]] = False
                pend_r, pend_a, pend_i, pend_d = pend_r[keep], pend_a[keep], pend_i[keep], pend_d[keep]
        if any_attempt:
            if not debit:
                short_pn = attempt & (owed > coll)
                failed += short_pn
                if short_pn.any():
                    unpaid = short_pn if unpaid is None else unpaid | short_pn
            owed -= coll
            collected += coll
            collections[t] = coll
        g = out[t]  # the day's operating payments the borrower owes, less the invoices Slope pays (below)
        if slots[t]:
            routed = np.zeros(rn, dtype=np.int64)
            open_ = live & (owed == 0)
            for rows, amts in slots[t]:  # the line's draws, exactly as under net
                sel = open_[rows]
                if not sel.any():
                    continue
                rows, amts = rows[sel], amts[sel]
                okd = principal_out(rows) + amts <= limit[t][rows]
                if not okd.any():
                    continue
                rows, amt_d = rows[okd], amts[okd]
                parts = installment_amounts(amt_d, s.fee_bps, s.installments)
                due[np.ix_(line.due_idx[t], rows)] += parts.T
                if debit:
                    for k, d in enumerate(line.due_idx[t]):
                        book[d].append((rows, parts[:, k], t))
                funded[rows] += amt_d
                contract[rows] += parts.sum(axis=1)
                routed[rows] += amt_d  # Slope pays the supplier: the invoice leaves the borrower's outflows today
                fundings[t][rows] += amt_d
                d_rows.append(rows)
                d_days.append(np.full(len(rows), t, dtype=np.int64))
                d_amts.append(amt_d)
            g = g - routed
        else:
            routed = None
        if first_op:
            pay = np.minimum(g, np.maximum(a0, 0))  # g <= 0 (a receipt): min(g, .) = g
            avail += pay0 - pay  # what it paid toward invoices Slope then paid comes back
        else:
            pay = np.minimum(g, np.maximum(avail, 0))
            avail -= pay
        short = g - pay  # >= 0: unpaid operating outflow
        fd = fell_due[t]
        fd += sd
        fd += np.maximum(g, 0)
        if short.any():
            r = np.flatnonzero(short)
            unpaid = short > 0 if unpaid is None else unpaid | (short > 0)
            q_r, q_c, q_a = (np.concatenate([q_r, r]), np.concatenate([q_c, np.full(len(r), OPERATING)]),
                             np.concatenate([q_a, short[r]]))
            by_class[OPERATING] += short
            created[t] += short
        if q_r.size:  # the day's surplus over its obligations pays the other classes' arrears, oldest first
            pay = np.minimum(np.minimum(np.maximum(recv[t] - fd, 0), np.maximum(avail, 0)), by_class[1:].sum(axis=0))
            if pay.any():
                q_r, q_c, q_a = fifo_pay(q_r, q_c, q_a, pay, by_class)
                avail -= pay
        by_class[0] = np.where(live, owed, by_class[0])  # Slope's: frozen at a petition (the stayed claim)
        arrears[t] = by_class
        if q_r.size or by_class[0].any():
            streak = np.where(by_class.sum(axis=0) > 0, streak + 1, 0)
        elif w:
            streak[:] = 0
        if unpaid is not None:
            first_unpaid = np.where(unpaid & (first_unpaid == BIG), t, first_unpaid)
        cash[t] = avail
        if any_attempt or routed is not None:
            po = principal_out()
        outstanding[t] = po

    due, cash, outstanding = due.T, cash.T, outstanding.T
    collections, fundings = np.ascontiguousarray(collections.T), np.ascontiguousarray(fundings.T)
    cat = lambda xs: np.concatenate(xs) if xs else np.zeros(0, dtype=np.int64)  # noqa: E731
    hr = (cat(hr_rows), cat(hr_vals), cat(hr_days))
    dr = (cat(d_rows), cat(d_days), cat(d_amts))
    res = []
    failed = failed.reshape(b, n)
    for j, e in enumerate(events):
        lo, hi = j * n, (j + 1) * n
        sl = slice(lo, hi)
        mh, md = (hr[0] >= lo) & (hr[0] < hi), (dr[0] >= lo) & (dr[0] < hi)
        tr = _finish(line, e, pet[sl], due[sl], collections[sl], fundings[sl], cash[sl], outstanding[sl], funded[sl],
                     contract[sl], collected[sl], (hr[0][mh] - lo, hr[1][mh], hr[2][mh]),
                     (dr[0][md] - lo, dr[1][md], dr[2][md]), failed[j])
        tr.processed = Processed(arrears=np.ascontiguousarray(arrears[:, :, sl].transpose(2, 0, 1)),
                                 first_unpaid=first_unpaid[sl].copy(),
                                 nonpayment=None if nonpayment is None else nonpay[sl].copy(),
                                 levy_unmet=levy_unmet[sl].copy())
        res.append(tr)
    return res

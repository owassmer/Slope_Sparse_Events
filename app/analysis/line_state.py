"""Resumable daily line processor, one operating draw, integer cents.

The definition remains engine.py / processor.py. This is their daily loop with
its loop-carried variables in one contiguous int64 buffer. Tables are immutable
per draw; events and output/scratch buffers belong to the caller. No history is
replayed. Day 0 is the day AFTER review, as in engine.run.

prepare_draw returns (state, tables, events, outputs, draw_amounts, scratch).
advance processes [a, b); state[DAY] must equal a. copy_state saves a branch;
restore copies it back into an existing buffer. Outputs before a are untouched;
outputs at/after a belong to the branch being evaluated, not to the saved state.
Use a separate output buffer if both children's trajectories must be retained.

Limit and need are immutable line tables: trailing operating receipts do not
change with event cash, so copying that history at each decision is unnecessary.
The due schedule includes installments beyond the horizon. State keeps all
arrears' incurred days and the nonpayment window history, not just totals.
Scratch is transient and is deliberately not part of a branch copy.
"""
from collections import namedtuple

import numpy as np
from numba import njit

from app.analysis.events import BIG, OBLIGATIONS
from app.analysis.processor import NCLASS, OPENING_INCURRED, OPERATING, _before, _book

# Scalar offsets in state. The remaining buffer is described by tables.offsets.
(AVAILABLE, OWED, FUNDED, CONTRACTUAL, COLLECTED, FAILED, LEVY_UNMET,
 FIRST_UNPAID, NONPAYMENT, STREAK, NBOOK, NPENDING, NQUEUE, DAY, PETITION,
 LOCKED, CAPACITY, PREFERENCE) = range(18)
NSCALAR = 18
Tables = namedtuple("Tables", "need limit month_end routes due_idx offsets fee_bps inst debit first_op window share")
Cash = namedtuple("Cash", "post levy out obligations incurred lock capacity petition")
# outputs rows: cash, collections, fundings, outstanding, due, locked, capacity,
# headroom (only where headroom_present), headroom_present, then ARREARS classes.
OUTPUT_NAMES = ("cash", "collections", "fundings", "outstanding", "due", "locked", "capacity",
                "headroom", "headroom_present", "slope", "settlement", "notes_interest", "judgment", "operating")


def prepare_draw(line, opening_cents, events, draw=0, nonpayment=None):
    """Python boundary, using the definition's input preparation; no forecast calls.

    opening_cents excludes exposure.cash_cents (same convention as engine.run).
    Future event rows may be replaced between advances, but never a processed day.
    All mutable buffers are owned by this draw; the returned tables are read-only
    by contract. No net-processing approximation is provided.
    """
    from app.analysis.engine import _kernel_line
    from app.analysis.processor import _rows

    if line.setup.cash_processing != "daily":
        raise ValueError("line_state requires daily cash processing")
    need, limit, month_end, routes, due_idx, due0, book_d, book_a, _ = _kernel_line(line)
    post, levy, out, obl, inc = _rows(line, [events])
    days, tail, inst = line.days, line.tail, line.setup.installments
    # Exact upper bound: each positive route can be funded at most once.
    cap = len(book_d) + int(np.count_nonzero(routes[draw] > 0)) * inst
    qcap = days * (len(OBLIGATIONS) + 1)
    sizes = [tail, tail, tail, cap, cap, cap, cap, cap, cap, qcap, qcap, qcap, NCLASS, days, days, days]
    offsets = np.r_[NSCALAR, NSCALAR + np.cumsum(sizes)].astype(np.int64)
    s = line.setup
    w, share = (0, 0) if nonpayment is None else nonpayment
    tables = Tables(need[draw], limit[draw], month_end, routes[draw], due_idx, offsets,
                    s.fee_bps, inst, s.collection == "debit", s.same_day_order == "operating_first", w, share)
    state = initialize(tables, due0, book_d, book_a, opening_cents + s.exposure.cash_cents,
                       s.exposure.principal_cents, s.exposure.owed_cents)
    event = Cash(post[draw], levy[draw], out[draw], np.ascontiguousarray(obl[:, draw]),
                 np.ascontiguousarray(inc[:, draw]), events.lock[draw].copy(), events.capacity[draw].copy(),
                 int(events.petition[draw]))
    outputs = np.empty((len(OUTPUT_NAMES), days), np.int64)
    draw_amounts = np.empty_like(routes[draw])
    scratch = np.empty((7, cap + len(OBLIGATIONS) + 1), np.int64)
    return state, tables, event, outputs, draw_amounts, scratch


@njit(cache=True)
def initialize(tables, due0, book_d, book_a, opening, principal, contractual):
    o = tables.offsets
    state = np.zeros(o[-1], np.int64)
    state[AVAILABLE] = opening
    state[FUNDED] = principal
    state[CONTRACTUAL] = contractual
    state[FIRST_UNPAID] = BIG
    state[NONPAYMENT] = BIG
    state[PETITION] = tables.need.size
    state[o[0]:o[1]] = due0
    head, last = state[o[1]:o[2]], state[o[2]:o[3]]
    head[:] = -1
    last[:] = -1
    ne = 0
    if tables.debit:
        for q in range(book_d.size):
            ne = _book(book_d[q], book_a[q], OPENING_INCURRED, state[o[3]:o[4]],
                       state[o[4]:o[5]], state[o[5]:o[6]], head, last, ne)
    state[NBOOK] = ne
    return state


@njit(cache=True)
def copy_state(state):
    """Independent branch snapshot, callable directly from nopython DFS."""
    return state.copy()


@njit(cache=True)
def restore(state, saved):
    """Restore without allocating (caller owns equally sized buffers)."""
    for i in range(state.size):
        state[i] = saved[i]


@njit(cache=True)
def advance(state, tables, events, a, b, outputs, draw_amounts, scratch):
    """Advance [a,b), writing only those days into caller-owned output buffers.

    No input on day b or later is read, except immutable line tables and the
    declared petition date. The nonpayment denominator is booked as days pass,
    rather than pre-filled from future event obligations as in the batch kernel.
    """
    days = tables.need.size
    if a != state[DAY] or b < a or b > days:
        raise ValueError("advance requires the state's next day <= b <= horizon")
    o = tables.offsets
    due, head, last = state[o[0]:o[1]], state[o[1]:o[2]], state[o[2]:o[3]]
    e_amt, e_inc, e_next = state[o[3]:o[4]], state[o[4]:o[5]], state[o[5]:o[6]]
    p_amt, p_inc, p_day = state[o[6]:o[7]], state[o[7]:o[8]], state[o[8]:o[9]]
    q_c, q_a, q_d = state[o[9]:o[10]], state[o[10]:o[11]], state[o[11]:o[12]]
    bc = state[o[12]:o[13]]
    fell, slope_due, collections = state[o[13]:o[14]], state[o[14]:o[15]], state[o[15]:o[16]]
    it_amt, it_inc, it_cls, it_seq = scratch[0], scratch[1], scratch[2], scratch[3]
    it_pos, it_ok, order = scratch[4], scratch[5], scratch[6]
    need, limit, month_end, routes, due_idx = tables.need, tables.limit, tables.month_end, tables.routes, tables.due_idx
    fee_bps, inst, debit, first_op = tables.fee_bps, tables.inst, tables.debit, tables.first_op
    w, share = tables.window, tables.share
    post, levy, out, obl, inc = events.post, events.levy, events.out, events.obligations, events.incurred
    nslot, nobl = routes.shape[1], obl.shape[0]
    cash, fundings, outstanding = outputs[0], outputs[2], outputs[3]
    avail, owed, fu, co, cl = state[AVAILABLE], state[OWED], state[FUNDED], state[CONTRACTUAL], state[COLLECTED]
    fl, lu, fu_day, np_day = state[FAILED], state[LEVY_UNMET], state[FIRST_UNPAID], state[NONPAYMENT]
    streak, ne, npend, nq = state[STREAK], state[NBOOK], state[NPENDING], state[NQUEUE]
    pr = events.petition if 0 <= events.petition < days else days
    state[PETITION] = pr
    for t in range(a, b):
        outputs[:, t] = 0
        draw_amounts[t, :] = 0
        collections[t] = 0
        fell[t] = 0
        for c in range(nobl):
            fell[t] += obl[c, t]
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
        avail += post[t]
        dt = due[t]
        owed += dt
        sd = dt if live else np.int64(0)
        slope_due[t] = sd
        lv = levy[t]  # the Chain books what the levy reaches: normally all of it
        x = avail if avail > 0 else np.int64(0)
        take = lv if lv < x else x
        lu += lv - take
        avail -= take
        falls_due = sd > 0
        if falls_due:
            outputs[7, t] = avail - need[t] - owed
            outputs[8, t] = 1
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
            pay0 = out[t] if out[t] < x else x
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
            a = obl[c, t]
            if a != 0:
                it_amt[ni] = a
                it_inc[ni] = inc[c]
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
                    x = avail - need[t]
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
            collections[t] = coll
        g = out[t]  # the day's operating payments the borrower owes, less the invoices Slope pays
        if live and owed == 0:
            routed = np.int64(0)
            for k in range(nslot):
                amt = routes[t, k]
                if amt <= 0:
                    continue
                po = fu - (cl * fu // co if co > 0 else 0)
                if po + amt > limit[t]:
                    continue
                total = amt + (2 * amt * fee_bps + 10_000) // 20_000  # installment_amounts
                part = (2 * total + inst) // (2 * inst)
                for kk in range(inst):
                    a = part if kk < inst - 1 else total - part * (inst - 1)
                    d = due_idx[t, kk]
                    due[d] += a
                    if debit:
                        ne = _book(d, a, t, e_amt, e_inc, e_next, head, last, ne)
                fu += amt
                co += total
                routed += amt
                fundings[t] += amt
                draw_amounts[t, k] = amt
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
            outputs[9 + c, t] = bc[c]
            tot += bc[c]
        streak = streak + 1 if tot > 0 else 0
        if unpaid and fu_day == BIG:
            fu_day = t
        cash[t] = avail
        outstanding[t] = fu - (cl * fu // co if co > 0 else 0)
        state[LOCKED] += events.lock[t]
        state[CAPACITY] += events.capacity[t]
        outputs[1, t] = collections[t]
        outputs[4, t] = due[t]
        outputs[5, t] = state[LOCKED]
        outputs[6, t] = state[CAPACITY]
        if t == pr:
            state[PREFERENCE] = 0
            for u in range(max(0, pr - 90), pr):
                state[PREFERENCE] += collections[u]
    state[AVAILABLE], state[OWED], state[FUNDED], state[CONTRACTUAL], state[COLLECTED] = avail, owed, fu, co, cl
    state[FAILED], state[LEVY_UNMET], state[FIRST_UNPAID], state[NONPAYMENT] = fl, lu, fu_day, np_day
    state[STREAK], state[NBOOK], state[NPENDING], state[NQUEUE], state[DAY] = streak, ne, npend, nq, b


@njit(cache=True)
def claims(state, tables):
    """At the current boundary: stayed, stayed principal, preference, not yet due,
    uncollected. At the horizon these are engine._finish's exact integer claims.
    Before it, not yet due means due on/after the next unprocessed day.
    """
    petitioned = state[PETITION] < state[DAY]
    unpaid = state[CONTRACTUAL] - state[COLLECTED]
    fu, co, cl = state[FUNDED], state[CONTRACTUAL], state[COLLECTED]
    if petitioned:
        return unpaid, fu - (cl * fu // co if co > 0 else 0), state[PREFERENCE], 0, 0
    o = tables.offsets
    due = state[o[0]:o[1]]
    later = np.int64(0)
    for t in range(state[DAY], due.size):
        later += due[t]
    return 0, 0, 0, later, unpaid - later

"""The dated walk's agenda on one draw (design/002-exact-evaluator.md, The dated walk), slice 1: a history's steps
booked as `events.Chain.advance` books them, the answered decisions waiting for their own day and the pending writs
processed in date order (`until`, `_upto_dated`, `flush_levy`), and the path's end (`finish`). Each function names the
Chain method it reproduces on one draw. Stays approved and settlements agreed are slice 2 (they raise here).

The state is one `St` (arrays only, so a save is a copy of each); the case's constants on the draw are one `K`. Cash
is read from the compiled line run on the event cash booked now (`refresh`, `app/exact/cash.py`).
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np
from numba import njit

from app.exact import cash, core, equity, notes
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import fields_equity as e
from app.exact import fields_notes as n

St = namedtuple("St", "S D P takes offers atm answers parts coupons wait stays rec steps scratch before lockbuf ln")
K = namedtuple("K", "N bps pending frcp frap frcp62 briefing next_bd nbd sensitive_lag DC levy_lag petition_lag "
                    "bond_years bond_share zero_floor ec nc sched lower ldates rep_cal legal opening ops_in need "
                    "noncash claimed settle_lag settle_end settle_count months")
# a projection's workspace (`Chain.proposed_terms`): the state at the review date, and the view it is replayed into
WS = namedtuple("WS", "init scratch")
D_STAY_LOCK = f.D_STAYS  # every approved stay's lock on approval and its release (Chain.lock_cash less the booked lock)

ENFORCEMENT = 1  # events.PETITION_CAUSES
CASH_FLOOR_CAUSE = 3


# --- reads ------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def size_stay(st, k, j):
    """`Chain._size_stay` on the state now: (lock, effective) for stay j approved on its approval day: the bond
    collateral where the balance at processing less the month's need covers it, else the balance above the need; never
    more than that balance, and nothing on or after a petition or once the dispute has ended. After `forward`."""
    S = st.S
    approval = st.stays[j, a.T_APPROVAL]
    collateral = bond_collateral(st, k, approval)
    t = min(max(approval, 0), k.N - 1)
    cash_a = cash.processing_balance(st.D, st.ln, approval)
    covers = cash_a - k.need[t] >= collateral
    live = core.live(S, approval) and approval < k.N
    offer = max(cash_a - k.need[t], 0) if live and not covers else 0
    effective = live and (covers or offer > 0 or k.noncash)
    if approval > S[a.PROSPECTIVE]:
        effective = False
    lock = (collateral if covers else offer) if effective else 0
    return lock, effective


@njit(cache=True)
def restay(st, k):
    """`Chain._stay_effects`'s bookings from the stays sized so far: each approved stay's lock on approval, released
    when the dispute ends (on approval where it had ended by then: nothing left to stay); the day the judgment is
    stayed, the 'stayed' mark, the lock still held. Before `forward`."""
    S, D, N = st.S, st.D, k.N
    D[D_STAY_LOCK, :] = 0
    rel = S[f.RELEASE_AT]
    stayed, amount, day, stayed_from = f.BIG, 0, f.BIG, f.BIG
    approved = False
    for j in range(S[a.N_STAYS]):
        row = st.stays[j]
        if not row[a.T_APPROVED] or not row[a.T_DONE]:
            continue
        approved = True
        approval, own = row[a.T_APPROVAL], row[a.T_LOCK]
        held = own > 0 and rel < N
        late = held and approval >= rel
        if 0 <= approval < N and own != 0:
            D[D_STAY_LOCK, approval] += own
        r = approval if late else rel
        if held and 0 <= r < N:
            D[D_STAY_LOCK, r] -= own
        if row[a.T_EFFECTIVE]:
            stayed_from = min(stayed_from, approval)
            if 0 <= approval < N and not late:
                stayed = min(stayed, approval)
        amount = 0 if held else own
        day = approval if amount > 0 else f.BIG
    S[f.STAYED_FROM] = stayed_from
    if approved:
        S[f.MARK_STAYED] = stayed
        S[f.LOCK_AMOUNT], S[f.LOCK_DAY] = amount, day
    for t in range(N):
        st.lockbuf[t] = D[f.D_LOCK, t] + D[D_STAY_LOCK, t]


@njit(cache=True)
def refresh(st, k):
    """Run the line on the event cash booked now (`Chain.processed`): the day's close, the first unpaid day, §3.3."""
    restay(st, k)
    fu, npd = cash.forward_if_changed(st.S, st.D, st.lockbuf, st.ln)
    st.S[a.CASH_FIRST_UNPAID] = fu
    st.S[a.CASH_NONPAYMENT] = npd


@njit(cache=True)
def owed(st, k, day, enforceable):
    return core.owed_at(st.S, st.takes, k.bps, k.pending, day, enforceable)


@njit(cache=True)
def parts(st):
    return st.parts[:st.S[a.N_PARTS]]


@njit(cache=True)
def eq(st, k):
    return st.P, st.takes, parts(st), st.offers, st.atm, k.sched, k.ec, st.scratch


@njit(cache=True)
def atm_rebook(st, k):
    """`Chain._atm_rebook`."""
    equity.atm_rebook(st.S, st.D, st.P, st.takes, parts(st), st.offers, st.atm, k.sched,
                      st.coupons[:st.S[n.N_COUPONS]], k.ec, st.scratch)


@njit(cache=True)
def mark(st, k, ix, day, where):
    core.mark(st.S, ix, day, where, k.N)


@njit(cache=True)
def bond_collateral(st, k, day):
    """`Chain.bond_collateral` (`bond_collateral_cents`): the bond on the amount owed, times the collateral share."""
    o = owed(st, k, day, False)
    bond = o + np.int64(np.rint(o * k.bps / 10_000 * k.bond_years))
    return np.int64(np.rint(bond * k.bond_share))


@njit(cache=True)
def collateral_before(st, k, day):
    """`Chain.collateral_before`: the latest stay motion strictly before the day, its bond sized on the day (on its
    approval day once decided); 0 without one."""
    latest, sel = -1, -1
    for j in range(st.S[a.N_STAYS]):
        m = st.stays[j, a.T_MOTION]
        if m < day and m < k.N and m >= latest:
            latest, sel = m, j
    if sel < 0:
        return 0
    return bond_collateral(st, k, min(day, st.stays[sel, a.T_APPROVAL]))


# --- the writs (Chain.queue_levy, pending_levy, flush_levy, levy, _take) -------------------------------------------------

@njit(cache=True)
def pending_levy(st):
    out = f.BIG
    for j in range(st.S[a.N_WRITS]):
        out = min(out, st.S[a.WRITS0 + j])
    return out


@njit(cache=True)
def queue_levy(st, day):
    if day >= f.BIG:
        return
    j = st.S[a.N_WRITS]
    if j >= a.MAX_WRITS:
        raise ValueError("writ table exhausted")
    st.S[a.WRITS0 + j] = day
    st.S[a.N_WRITS] = j + 1


@njit(cache=True)
def append_take(st, day, amount):
    j = st.S[f.N_TAKES]
    if j >= st.takes.shape[0]:
        raise ValueError("takes table exhausted")
    st.takes[j, 0], st.takes[j, 1] = day, amount
    st.S[f.N_TAKES] = j + 1


@njit(cache=True)
def resolve(st, k, day, where):
    core.resolve(st.S, st.D, k.legal, day, where)


@njit(cache=True)
def take(st, k, day):
    """`Chain._take`: the writ levies the lesser of the enforceable amount and the balance at processing it reaches."""
    refresh(st, k)
    S = st.S
    ok = core.live(S, day) and day < S[f.STAYED_FROM] and day < k.N
    amt = 0
    if ok:
        amt = min(owed(st, k, day, True), max(cash.processing_balance(st.D, st.ln, day), 0))
    satisfied = amt > 0 and amt >= owed(st, k, day, False) and not S[f.RETRIAL]
    core.pay(S, st.D, day, -amt, f.D_KIND_LEVY, -1, 0)
    mark(st, k, f.MARK_LEVIED, day, amt > 0)
    S[f.TAKEN] += amt
    append_take(st, day, amt)
    if amt > 0:
        S[f.LEVIED] = 1
    if k.pending:
        resolve(st, k, day, satisfied)
        mark(st, k, f.MARK_PAID, day, satisfied)
    atm_rebook(st, k)


@njit(cache=True)
def levy(st, k, day, lagged):
    day = day + (0 if lagged else k.levy_lag)
    take(st, k, day)
    if st.S[f.INCREASE]:
        queue_levy(st, st.S[f.EI] if day < st.S[f.EI] else f.BIG)


@njit(cache=True)
def flush_levy(st, k):
    """`Chain.flush_levy`: the earliest pending writs, each levied, in the order ordered."""
    day = pending_levy(st)
    if day >= f.BIG:
        return
    S = st.S
    count, kept = 0, 0
    for j in range(S[a.N_WRITS]):
        w = S[a.WRITS0 + j]
        if w == day:
            count += 1
        else:
            S[a.WRITS0 + kept] = w
            kept += 1
    S[a.N_WRITS] = kept
    for _ in range(count):
        levy(st, k, day, True)


# --- the company's bookings (Chain.respond) ------------------------------------------------------------------------

@njit(cache=True)
def respond(st, k, booking, day, cause, occasion):
    S = st.S
    if booking == a.B_PAY:
        refresh(st, k)
        o = owed(st, k, day, False)
        ok = core.live(S, day) and day < k.N and cash.cash_at(st.ln, day) >= o
        amt = o if ok else 0
        core.pay(S, st.D, day, -amt, f.D_KIND_JUDGMENT, f.INCURRED_JUDGMENT, S[f.E_IX])
        S[f.TAKEN] += amt
        append_take(st, day, amt)
        resolve(st, k, day, ok and not S[f.RETRIAL])
        if ok and day < k.N and day < S[f.RELEASE_AT]:  # release_lock: paid in full, nothing left to secure
            S[f.RELEASE_AT] = day
        mark(st, k, f.MARK_PAID, day, ok)
        atm_rebook(st, k)
    elif booking == a.B_PETITION:
        where = day < k.N if cause == CASH_FLOOR_CAUSE else (core.live(S, day) and day < k.N)
        if core.petition(S, day, where, cause, k.petition_lag, k.N):
            atm_rebook(st, k)
    elif booking == a.B_SEEK:
        mark(st, k, f.MARK_SEEKING, day, core.live(S, day))
    elif booking == a.B_OFFER:
        lower = k.lower[day] if 0 <= day < k.N else 0
        equity.initiate(S, st.D, st.P, st.takes, parts(st), st.offers, st.atm, k.sched,
                        st.coupons[:S[n.N_COUPONS]], st.answers, k.ec, st.scratch, day, lower, occasion)


@njit(cache=True)
def book_default(st, k, ctx, branch, rows):
    """`Chain.book_default`: the §3.3 read for the holders' route comes after the acceleration and the coupons."""
    ripe, on = notes.prepare_default(st.S, st.D, st.coupons, st.takes, k.nc, ctx, branch, rows)
    refresh(st, k)
    return notes.finish_default(st.S, st.D, st.coupons, k.nc, eq(st, k), ripe, on, branch, st.S[a.CASH_NONPAYMENT])


# --- the days (Chain.response_day, distress_day, waiting_day, decision_day) -------------------------------------------

@njit(cache=True)
def response_day(st, k, ctx):
    S = st.S
    if ctx == a.R_ENTRY:
        m = S[f.E_IX] if notes.has_judgment(S, k.nc) else f.BIG
    elif ctx == a.R_I1:
        lv = pending_levy(st)
        m = lv if lv < S[f.STAYED_FROM] and lv < S[f.F] else f.BIG
    elif ctx == a.R_POST:
        ef = max(S[f.EF], 0)
        lv = f.BIG
        for j in range(S[a.N_WRITS]):
            w = S[a.WRITS0 + j]
            if w >= ef and w < lv:
                lv = w
        m = lv if lv < S[f.STAYED_FROM] and lv < k.N else f.BIG
    else:
        m = notes.default_available_day(S, st.takes, k.nc, k.N)
    return m if owed(st, k, m, False) > 0 else f.BIG


@njit(cache=True)
def new_money_after(st, k, prev):
    """`Chain._new_money_after`: an offering's close after `prev`, or the release of a stay's security."""
    out = f.BIG
    for j in range(st.S[e.N_OFFERS]):
        o = st.offers[j]
        if o[e.O_CLOSED] and o[e.O_CLOSE] > prev and o[e.O_CLOSE] < out:
            out = o[e.O_CLOSE]
    rel = st.S[f.RELEASE_AT]
    for j in range(st.S[a.N_STAYS]):
        row = st.stays[j]
        if not (row[a.T_APPROVED] and row[a.T_DONE]):
            continue
        held = row[a.T_LOCK] > 0 and rel < k.N
        r = row[a.T_APPROVAL] if held and row[a.T_APPROVAL] >= rel else rel
        if held and r > prev and r < out:
            out = r
    return out


@njit(cache=True)
def wait_day(st, k, node, ctx):
    """A waiting decision's day on the state now (BIG: not on this draw); after `refresh`."""
    S = st.S
    if node == a.CASH_FLOOR:
        if ctx == 1:
            return cash.tau(st.ln, S[a.CASH_FIRST_UNPAID], k.zero_floor, True)
        prev = S[a.FLOOR0 + ctx - 1]
        if prev >= f.BIG:
            return f.BIG
        new = new_money_after(st, k, prev)
        return cash.fall_after(st.ln, new - 1, k.zero_floor) if new < f.BIG else f.BIG
    if node == a.CASH_OUT:
        return cash.cash_out(st.ln, S[a.CASH_FIRST_UNPAID], k.zero_floor, True)
    if node == a.OFFERING:
        return equity.offering_day(S, st.offers, ctx)
    if node == a.NONPAYMENT:
        d = S[a.CASH_NONPAYMENT]
        return d if d < f.BIG and S[f.MARK_NOTES_DUE] > d else f.BIG
    if node == a.JDEFAULT:
        ripe, on = notes.judgment_default(S, st.takes, k.nc, ctx)
        return ripe if on else f.BIG
    return response_day(st, k, ctx)


@njit(cache=True)
def settlement_window(st, k, ctx):
    """`Chain.settlement_window` (a pending claim): the interval's start and end."""
    S, N = st.S, k.N
    if ctx == 0:  # I0
        return -1, S[f.V]
    deadline = S[f.E_IX] + k.frcp
    if ctx == 1:  # Ientry
        return S[f.E_IX], deadline
    if ctx == 3:  # Istay
        return (S[f.STAYED_FROM] if S[f.STAYED_FROM] < S[f.F] else f.BIG), S[f.F]
    if ctx == 4:  # Ienforce
        return S[f.MARK_LEVIED], S[f.F]
    if ctx == 5:  # Iappeal
        return S[f.MARK_APPEALED], N - 1
    if ctx == 2:  # I1
        start, end = deadline, S[f.F]
    elif ctx == 6:  # I2
        start, end = S[f.F], S[f.AD]
    elif ctx == 7:  # I3
        start, end = max(S[f.EF], S[f.AD]), N - 1
    else:  # I4
        start, end = max(S[f.STAYED_FROM], S[f.F]), N - 1
    start = max(start, -1)
    if ctx == 7 and S[f.STAYED_FROM] <= start:
        start = f.BIG
    return start, end


@njit(cache=True)
def decision_day(st, k, node, ctx):
    """`Chain.decision_day` for the steps walked (a history's own order); after `refresh`."""
    S = st.S
    if node in (a.CASH_FLOOR, a.CASH_OUT, a.OFFERING, a.NONPAYMENT, a.JDEFAULT, a.RESPONSE):
        return wait_day(st, k, node, ctx)
    if node == a.SETTLE:
        start, end = settlement_window(st, k, ctx)
        return f.BIG if end < 0 else max(start, 0)
    if node == a.EXECUTE:
        return S[f.E0]
    if node == a.STAY:
        return S[f.E0] if ctx == 0 else max(S[f.F], 0)
    if node == a.REGISTRATION:
        return S[f.E0] if ctx == 0 else S[f.F]
    if node == a.RULING:
        return S[f.F]
    if node == a.APPEAL:
        return f.BIG if S[f.AD] < 0 else max(S[f.F], 0)
    if node == a.ENFORCE:
        return max(S[f.EF], 0)
    if node == a.VERDICT:
        return S[f.V]
    if node == a.MOTIONS:
        return S[f.E_IX] + k.frcp
    if node == a.LISTING:  # the listing's first stage: the compliance deadline
        pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
        return k.ldates[n.DEADLINE] if k.ldates[n.DEADLINE] < pet else f.BIG
    if node == a.DELISTING:
        pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
        d = S[f.DELISTED]
        return d if d < pet and S[f.MARK_NOTES_DUE] > d and d < k.N else f.BIG
    raise ValueError("no decision day for this node")


# --- the waiting decisions (Chain.decide_waiting, decide_distress, _upto_dated, until, next_floor) --------------------

@njit(cache=True)
def is_distress(node):
    return node == a.CASH_FLOOR or node == a.CASH_OUT or node == a.OFFERING or node == a.NONPAYMENT


@njit(cache=True)
def decide_waiting(st, k, node, ctx, branch, t):
    S = st.S
    if node == a.OFFERING:
        equity.offering_outcome(S, st.D, st.P, st.takes, parts(st), st.offers, st.atm, k.sched,
                                st.coupons[:S[n.N_COUPONS]], st.answers, k.ec, st.scratch, ctx, branch == a.YES)
    elif node == a.NONPAYMENT:
        notes.decide_nonpayment(S, st.D, st.coupons, k.nc, eq(st, k), t, branch)
    elif node == a.CASH_FLOOR or node == a.CASH_OUT:
        if node == a.CASH_FLOOR and t < k.N:
            S[a.FLOOR0 + ctx] = t
        occasion = a.OCCASIONS_IX_FLOOR0 + ctx if node == a.CASH_FLOOR else 0
        respond(st, k, branch, t, CASH_FLOOR_CAUSE, occasion)
    elif node == a.JDEFAULT:
        book_default(st, k, ctx, branch, t < f.BIG)
    else:
        respond(st, k, branch, t, ENFORCEMENT, a.RESPONSE_OCCASION[ctx])


@njit(cache=True)
def record(st, k, i, t):
    """The step's record (`Chain.rec`): its day, the balance at processing, the amount owed, the collateral."""
    st.rec[i, 0] = t
    st.rec[i, 1] = cash.processing_balance(st.D, st.ln, t)
    st.rec[i, 2] = owed(st, k, t, False)
    st.rec[i, 3] = collateral_before(st, k, t)


@njit(cache=True)
def approval_due(st, k, j):
    """A stay's approval comes due, the day's first event: an approved stay's security is sized on the state now
    (everything dated before the day booked) and locked (`restay`); a denial is marked where the judgment is live and
    owed on the approval day (`_size_unapproved`). After `refresh`."""
    S = st.S
    row = st.stays[j]
    if row[a.T_APPROVED]:
        row[a.T_LOCK], row[a.T_EFFECTIVE] = size_stay(st, k, j)
        row[a.T_DONE] = 1
        return
    row[a.T_DONE] = 1
    if row[a.T_MOVED]:
        day = row[a.T_APPROVAL]
        on = core.live(S, day) and day < k.N and owed(st, k, day, False) > 0
        mark(st, k, f.MARK_STAY_DENIED, day, on)


@njit(cache=True)
def upto(st, k, before, every, levy_day):
    """`Chain._upto_dated`: on this draw, the earliest-dated waiting decision first (ties in walk order), one at a time,
    each day recomputed on the cash as booked so far; a stay's approval before anything else on its day. Those dated
    before `before` (a levy-day response before the levy; the others before the levy's day too); every: all left."""
    S = st.S
    moved = False
    while True:
        refresh(st, k)
        best, pick, approval = f.BIG + 1, -1, False
        for j in range(S[a.N_STAYS]):  # approvals first: the court's order is the day's first event
            row = st.stays[j]
            if row[a.T_DONE] or row[a.T_OPEN] or not (row[a.T_APPROVED] or row[a.T_MOVED]):
                continue
            t = row[a.T_APPROVAL]
            if (every or (t <= before and t <= levy_day)) and t < best:
                best, pick, approval = t, j, True
        for j in range(S[a.N_WAIT]):
            w = st.wait[j]
            if w[a.W_DONE]:
                continue
            node, ctx = w[a.W_NODE], w[a.W_CTX]
            t = wait_day(st, k, node, ctx)
            lr = node == a.RESPONSE and ctx != a.R_RIPE
            lim = before if lr else min(before, levy_day)
            if (every or t < lim) and t < best:
                best, pick, approval = t, j, False
        if pick < 0:
            break
        if approval:
            approval_due(st, k, pick)
        else:
            w = st.wait[pick]
            record(st, k, w[a.W_INDEX], best)
            decide_waiting(st, k, w[a.W_NODE], w[a.W_CTX], w[a.W_BRANCH], best)
            w[a.W_DONE] = 1
        moved = True
    kept = 0
    for j in range(S[a.N_WAIT]):
        if not st.wait[j, a.W_DONE]:
            st.wait[kept] = st.wait[j]
            kept += 1
    S[a.N_WAIT] = kept
    return moved


@njit(cache=True)
def until(st, k, bound):
    """`Chain.until`: the waiting decisions and the pending writs dated before `bound`, in date order."""
    while True:
        lv = pending_levy(st)
        moved = upto(st, k, bound, False, lv)
        if lv < bound and lv < f.BIG:
            flush_levy(st, k)
            moved = True
        if not moved:
            break


@njit(cache=True)
def next_floor(st, k):
    out = f.BIG
    refresh(st, k)
    for j in range(st.S[a.N_WAIT]):
        w = st.wait[j]
        if not w[a.W_DONE] and is_distress(w[a.W_NODE]):
            out = min(out, wait_day(st, k, w[a.W_NODE], w[a.W_CTX]))
    return out


@njit(cache=True)
def response_waiting(st, k):
    for j in range(st.S[a.N_WAIT]):
        w = st.wait[j]
        if (not w[a.W_DONE] and w[a.W_NODE] == a.RESPONSE and w[a.W_CTX] != a.R_RIPE
                and response_day(st, k, w[a.W_CTX]) < f.BIG):
            return True
    return False


# --- a step (Chain.advance and Chain.step) ---------------------------------------------------------------------------

@njit(cache=True)
def waits(node, ctx):
    """`Chain.waits` (a pending claim under the equity model)."""
    return is_distress(node) or node == a.JDEFAULT or (node == a.RESPONSE and ctx != a.R_ENTRY)


@njit(cache=True)
def answers_levy(node, ctx):
    return node == a.RESPONSE and (ctx == a.R_I1 or ctx == a.R_POST)


@njit(cache=True)
def book_step(st, k, step):
    """`Chain.step` for a step that does not wait. Returns its decision day."""
    S, N = st.S, k.N
    node, ctx, br = step[0], step[1], step[2]
    if node == a.VERDICT:
        if br == a.V_AWARD:
            return core.step_verdict_award(S, k.DC, step[3], step[4], step[5], step[6] != 0, k.next_bd, k.nbd,
                                           k.frcp, k.briefing, k.frcp62, k.frap)
        return core.step_verdict_branch(S, k.DC, step[3] != 0, step[4], step[5] != 0, k.next_bd, k.nbd, k.frcp,
                                        k.briefing, k.frcp62, k.frap)
    if node == a.MOTIONS:
        return core.step_post_trial_motions(S, k.DC, br == a.YES, k.frcp, k.briefing, k.frap, k.sensitive_lag, N)
    if node == a.SETTLE:
        if br == a.YES:
            raise NotImplementedError("a settlement agreed: slice 2")
        start, end = settlement_window(st, k, ctx)
        return f.BIG if end < 0 else max(start, 0)
    if node == a.EXECUTE:
        S[f.Q1] = br == a.YES
        if br == a.YES:
            mark(st, k, f.MARK_EXECUTING, S[f.E0], True)
        return S[f.E0]
    if node == a.STAY:
        motion = S[f.E0] if ctx == 0 else max(S[f.F], 0)
        lag = k.DC[core.DC_LAG_STAY_I1] if ctx == 0 else k.DC[core.DC_LAG_STAY_POST]
        approval = motion + k.briefing + lag
        if br == a.YES or br == a.DENIED:
            mark(st, k, f.MARK_STAY_MOVED, motion, core.live(S, motion))
        j = S[a.N_STAYS]
        if j >= a.MAX_STAYS:
            raise ValueError("stays table exhausted")
        st.stays[j, a.T_INDEX], st.stays[j, a.T_MOTION], st.stays[j, a.T_APPROVAL] = S[a.N_STEPS], motion, approval
        st.stays[j, a.T_APPROVED], st.stays[j, a.T_MOVED], st.stays[j, a.T_DONE] = br == a.YES, br != a.NO, 0
        st.stays[j, a.T_LOCK], st.stays[j, a.T_EFFECTIVE], st.stays[j, a.T_OPEN] = 0, 0, 0
        S[a.N_STAYS] = j + 1
        return motion
    if node == a.REGISTRATION:
        motion = S[f.E0] if ctx == 0 else S[f.F]
        lag = k.DC[core.DC_LAG_REG_I1] if ctx == 0 else k.DC[core.DC_LAG_REG_POST]
        if br == a.YES:
            order = motion + k.briefing + lag
            S[f.EARLY_REGISTRATION] = min(S[f.EARLY_REGISTRATION], order)
            queue_levy(st, order + k.levy_lag)
        return motion
    if node == a.RULING:  # post_trial_ruling
        F = S[f.F]
        if br == a.R_REDUCED:
            S[f.CLS_AMOUNT] = step[3]
            S[f.REMITTED] = step[3] if core.live(S, F) and F < N else 0
        elif br == a.R_SET_ASIDE:
            S[f.CLS_AMOUNT], S[f.RETRIAL] = 0, 1
            S[f.ADVERSE_UNTIL] = min(S[f.ADVERSE_UNTIL], F)
            d = max(F, 0)
            if core.live(S, F) and d < N and d < S[f.RELEASE_AT]:
                S[f.RELEASE_AT] = d
        mark(st, k, f.MARK_RULED, F, True)
        S[f.INCREASE] = 0
        S[f.EF], S[f.EI] = F, F
        return F
    if node == a.APPEAL:
        S[f.APPEALED] = br == a.YES
        day = f.BIG if S[f.AD] < 0 else max(S[f.F], 0)
        if br == a.YES:
            mark(st, k, f.MARK_APPEALED, day, core.live(S, day))
        return day
    if node == a.ENFORCE:
        if br == a.E_LEVY:
            er = S[f.EARLY_REGISTRATION]
            day = max(S[f.EF], er) if er < f.BIG else S[f.AD] + 1
            if S[f.APPEALED]:
                order = S[f.EF] + k.briefing + k.DC[core.DC_LAG_REG_POST]
                day = max(S[f.EF], er) if er < f.BIG else order
            queue_levy(st, max(day, 0) + k.levy_lag)
        return max(S[f.EF], 0)
    if node == a.LISTING:
        return notes.listing_step(S, st.D, st.coupons, k.nc, eq(st, k), k.ldates, False, 0, br)
    if node == a.DELISTING:
        return notes.delisting_notes(S, st.D, st.coupons, k.nc, eq(st, k), k.rep_cal, br)
    if node == a.RESPONSE:  # the response on entry (the others wait)
        m = response_day(st, k, ctx)
        respond(st, k, br, m, ENFORCEMENT, a.RESPONSE_OCCASION[ctx])
        return m
    raise ValueError("no booking for this step")


@njit(cache=True)
def prepare(st, k, step):
    """`Chain.advance` before the step books: a waiting step queues for its own day (returns -1); any other first books
    what precedes its day (the waiting decisions, the stays' approvals and the writs before it; a writ on its day unless
    a waiting floor or levy-day response comes first), and the balance before its own bookings is kept for its record.
    Returns the step's index."""
    S, N = st.S, k.N
    node, ctx = step[0], step[1]
    i = S[a.N_STEPS]
    if i >= a.MAX_STEPS:
        raise ValueError("step records exhausted")
    S[a.N_STEPS] = i + 1
    st.steps[i, :] = step
    if waits(node, ctx):
        st.rec[i, 0], st.rec[i, 1], st.rec[i, 2], st.rec[i, 3] = f.BIG, 0, 0, 0
        j = S[a.N_WAIT]
        if j >= a.MAX_WAIT:
            raise ValueError("waiting table exhausted")
        st.wait[j, a.W_INDEX], st.wait[j, a.W_NODE], st.wait[j, a.W_CTX] = i, node, ctx
        st.wait[j, a.W_BRANCH], st.wait[j, a.W_DONE] = step[2], 0
        S[a.N_WAIT] = j + 1
        return -1
    pending_stay = False
    for j in range(S[a.N_STAYS]):
        row = st.stays[j]
        if not row[a.T_DONE] and not row[a.T_OPEN] and (row[a.T_APPROVED] or row[a.T_MOVED]):
            pending_stay = True
    if S[a.N_WAIT] > 0 or S[a.N_WRITS] > 0 or pending_stay:
        while True:  # a stay's approval ahead of the step's day: what precedes it, and it, first (its day may move)
            refresh(st, k)
            d = f.BIG if answers_levy(node, ctx) else decision_day(st, k, node, ctx)
            bound = d if d < N else (N if d < f.BIG else -1)
            ahead = f.BIG
            for j in range(S[a.N_STAYS]):
                row = st.stays[j]
                if not row[a.T_DONE] and not row[a.T_OPEN] and (row[a.T_APPROVED] or row[a.T_MOVED]):
                    ahead = min(ahead, row[a.T_APPROVAL])
            if ahead > bound or ahead >= N:
                break
            until(st, k, ahead)
        until(st, k, bound)
        lv = pending_levy(st)
        if not answers_levy(node, ctx) and lv < f.BIG:
            if (0 <= d < f.BIG and lv <= d and lv < N and not next_floor(st, k) < lv
                    and not response_waiting(st, k)):
                flush_levy(st, k)
    refresh(st, k)
    st.before[0, :] = st.ln.outputs[0]
    st.before[1, :] = st.D[f.D_KIND_INFLOW]
    st.before[2, :] = st.lockbuf
    st.before[3, :] = st.D[f.D_KIND_LEVY]
    return i


@njit(cache=True)
def conclude(st, k, step, i, proj):
    """`Chain.advance` after `prepare`: the step booked (`proj`: an agreed settlement's projected terms, `project`),
    the at-the-market sales rebooked, and its record kept (its cash: the balance at processing before its bookings)."""
    N = k.N
    if step[0] == a.SETTLE and step[2] == a.YES:
        day = settle(st, k, step[1], proj)
    else:
        day = book_step(st, k, step)
    atm_rebook(st, k)
    refresh(st, k)
    t = min(max(day, 0), N - 1)
    prev = st.before[0, t - 1] if t > 0 else k.opening
    st.rec[i, 0] = day
    st.rec[i, 1] = prev + k.ops_in[t] + st.before[1, t] - st.before[2, t] + st.before[3, t]
    st.rec[i, 2] = owed(st, k, day, False)
    st.rec[i, 3] = collateral_before(st, k, day)


NO_PROJECTION = (False, 0, 0)


@njit(cache=True)
def advance_plain(st, k, step):
    """`advance` in a projection's view: an agreed settlement there is an earlier commitment, which no later
    settlement question can follow (the agreement ends the dispute)."""
    i = prepare(st, k, step)
    if i < 0:
        return
    if step[0] == a.SETTLE and step[2] == a.YES:
        raise NotImplementedError("an agreed settlement before a settlement question")
    conclude(st, k, step, i, NO_PROJECTION)


@njit(cache=True)
def advance(st, k, step, ws):
    """`Chain.advance`."""
    i = prepare(st, k, step)
    if i < 0:
        return
    proj = NO_PROJECTION
    if step[0] == a.SETTLE and step[2] == a.YES:
        start, end = settlement_window(st, k, step[1])
        pd = end if k.settle_end else start + k.settle_lag
        got = project(st, k, ws, i, max(start, 0), pd, False)
        proj = (got[6], got[1], got[3])
    conclude(st, k, step, i, proj)


@njit(cache=True)
def months_after(k, day, m):
    return k.months[day, m] if 0 <= day < k.N else day


@njit(cache=True)
def settle(st, k, ctx, proj):
    """`Chain.settle`, agreed (a pending claim, installments): the cash above the month's need on the settlement date,
    as projected from the state on the decision day (`proj`: live, cash, owed), capped at the amount owed (before the
    verdict, the amount claimed); paid in equal monthly installments from the settlement date, the claim released on
    it, only where the amount is positive. Returns the decision day."""
    S, N = st.S, k.N
    start, end = settlement_window(st, k, ctx)
    pd = end if k.settle_end else start + k.settle_lag
    live, cash_p, owed_p = proj
    ok = live and pd < N and start < N
    need = k.need[min(max(pd, 0), N - 1)]
    cap = k.claimed if ctx == 0 else owed_p
    bound = min(max(cash_p - need, 0), cap) if ok else 0
    ok = ok and bound > 0
    count = k.settle_count
    for m in range(count):
        part = (bound // count + (bound % count if m == count - 1 else 0)) if ok else 0
        day = months_after(k, pd, m)
        core.pay(S, st.D, day, -part, f.D_KIND_SETTLEMENT, f.INCURRED_SETTLEMENT, pd)
        j = S[a.N_PARTS]
        if j >= a.MAX_PARTS:
            raise ValueError("settlement parts table exhausted")
        st.parts[j, 0], st.parts[j, 1] = day, part
        S[a.N_PARTS] = j + 1
    resolve(st, k, pd, ok and core.live(S, pd))
    mark(st, k, f.MARK_SETTLED, pd, ok)
    atm_rebook(st, k)
    return f.BIG if end < 0 else max(start, 0)


@njit(cache=True)
def copy_into(dst, src):
    """dst <- src, every array of the state (the line's buffers are each state's own scratch)."""
    dst.S[:] = src.S
    dst.D[:] = src.D
    dst.P[:] = src.P
    dst.takes[:] = src.takes
    dst.offers[:] = src.offers
    dst.atm[:] = src.atm
    dst.answers[:] = src.answers
    dst.parts[:] = src.parts
    dst.coupons[:] = src.coupons
    dst.wait[:] = src.wait
    dst.stays[:] = src.stays
    dst.rec[:] = src.rec
    dst.steps[:] = src.steps
    dst.lockbuf[:] = src.lockbuf


@njit(cache=True)
def project(st, k, ws, upto_index, decision, payment, processing):
    """`Chain.proposed_terms` on this draw: the history replayed from the review date with only the answers reached by
    the decision day (a step dated after it left out; a court's answer due after it not given: a stay not approved, a
    registration not ordered, an appealed enforcement without its order), the waiting answers dropped, and the state
    run to the payment day through what is already ordered (the writs). Returns (day, cash, need, owed, collateral,
    offer, live) on the payment day; cash: its close (processing: its balance at processing, a stay's security)."""
    S, N = st.S, k.N
    view = ws.scratch
    copy_into(view, ws.init)
    for j in range(upto_index):
        step = st.steps[j].copy()
        node, br = step[0], step[2]
        if st.rec[j, 0] > decision:
            continue
        if node == a.SETTLE and br != a.YES:
            continue
        if node == a.STAY:
            approval = f.BIG
            for q in range(S[a.N_STAYS]):
                if st.stays[q, a.T_INDEX] == j:
                    approval = st.stays[q, a.T_APPROVAL]
            if approval > decision and br != a.NO:
                step[2] = a.DENIED
        elif node == a.REGISTRATION:
            motion = S[f.E0] if step[1] == 0 else S[f.EF]
            lag = k.DC[core.DC_LAG_REG_I1] if step[1] == 0 else k.DC[core.DC_LAG_REG_POST]
            if motion + k.briefing + lag > decision:
                step[2] = a.NO
        elif node == a.ENFORCE and S[f.APPEALED]:
            order = S[f.EF] + k.briefing + k.DC[core.DC_LAG_REG_POST]
            if order > decision and S[f.EARLY_REGISTRATION] > decision:
                step[2] = a.E_NONE
        advance_plain(view, k, step)
    until(view, k, decision)
    view.S[a.N_WAIT] = 0
    view.S[a.PROSPECTIVE] = decision
    until(view, k, payment if payment < N else -1)
    refresh(view, k)
    t = min(max(payment, 0), N - 1)
    cash_p = cash.processing_balance(view.D, view.ln, payment) if processing else cash.cash_at(view.ln, payment)
    owed_p = owed(view, k, payment, False)
    need = k.need[t]
    collateral = bond_collateral(view, k, payment)
    live = core.live(view.S, payment) and payment < N
    offer = max(cash_p - need, 0) if live and cash_p - need < collateral else 0
    return payment, cash_p, need, owed_p, collateral, offer, live


@njit(cache=True)
def finish(st, k):
    """`Chain.finish`'s bookings: each pending writ with what precedes it, then every waiting decision left."""
    while pending_levy(st) < f.BIG:
        pl = pending_levy(st)
        until(st, k, pl + 1)
    flush_levy(st, k)
    upto(st, k, f.BIG, True, f.BIG)
    refresh(st, k)

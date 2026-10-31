"""The compiled walk on one draw (design/002-exact-evaluator.md, The walk, compiled): `ChronologicalWalk`'s scheduler
(`_pending`, `_cursors`, `frontier.next_decisions` and `select`, `_loop`) over the compiled agenda's state
(`app/exact/agenda.py`). This module: the decisions open at a point, the frontier's earliest item, and a history of
the Python walk followed through them (`follow`, what the scheduler is checked by).
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np
from numba import njit

from app.exact import agenda
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import fields_notes as n
from app.exact import fields_walk as w

BIG = f.BIG
# the walk's constants on the draw: whether the notes have a listing deadline, and the verdict branches that enter
# a money judgment (by branch index)
WK = namedtuple("WK", "listing")


# --- decisions (ids, node and ctx codes) ------------------------------------------------------------------------------

@njit(cache=True)
def dec_node(d):
    """The agenda's node and ctx codes of decision d (`fields_agenda`)."""
    if d < w.DEC_VERDICT:
        return a.SETTLE, d - w.DEC_SETTLE0
    if d == w.DEC_VERDICT:
        return a.VERDICT, 0
    if d < w.DEC_MOTIONS:
        return a.RESPONSE, d - w.DEC_RESP0
    if d == w.DEC_MOTIONS:
        return a.MOTIONS, 0
    if d == w.DEC_EXECUTE:
        return a.EXECUTE, 0
    if d < w.DEC_REGISTRATION:
        return a.STAY, d - w.DEC_STAY0
    if d == w.DEC_REGISTRATION:
        return a.REGISTRATION, 0
    if d == w.DEC_RULING:
        return a.RULING, 0
    if d == w.DEC_APPEAL:
        return a.APPEAL, 0
    if d < w.DEC_ENFORCE:
        return a.JDEFAULT, d - w.DEC_JDEFAULT0
    if d == w.DEC_ENFORCE:
        return a.ENFORCE, 0
    if d == w.DEC_LISTING:
        return a.LISTING, 0
    if d == w.DEC_DELISTING:
        return a.DELISTING, 0
    if d == w.DEC_CASH_OUT:
        return a.CASH_OUT, 0
    if d == w.DEC_NONPAYMENT:
        return a.NONPAYMENT, 0
    return a.CASH_FLOOR, d - w.DEC_FLOOR0


@njit(cache=True)
def step_dec(step):
    """The decision a step answers (`steps` row, `replay.encode`); a listing step answers the compliance decision."""
    node, ctx = step[0], step[1]
    if node == a.SETTLE:
        return w.DEC_SETTLE0 + ctx
    if node == a.VERDICT:
        return w.DEC_VERDICT
    if node == a.RESPONSE:
        return w.DEC_RESP0 + ctx
    if node == a.MOTIONS:
        return w.DEC_MOTIONS
    if node == a.EXECUTE:
        return w.DEC_EXECUTE
    if node == a.STAY:
        return w.DEC_STAY0 + ctx
    if node == a.REGISTRATION:
        return w.DEC_REGISTRATION
    if node == a.RULING:
        return w.DEC_RULING
    if node == a.APPEAL:
        return w.DEC_APPEAL
    if node == a.JDEFAULT:
        return w.DEC_JDEFAULT0 + ctx
    if node == a.ENFORCE:
        return w.DEC_ENFORCE
    if node == a.LISTING:
        return w.DEC_LISTING
    if node == a.DELISTING:
        return w.DEC_DELISTING
    if node == a.CASH_OUT:
        return w.DEC_CASH_OUT
    if node == a.NONPAYMENT:
        return w.DEC_NONPAYMENT
    if node == a.CASH_FLOOR:
        return w.DEC_FLOOR0 + ctx
    return -1  # the offering's outcome: asked inside the initiating decision


@njit(cache=True)
def ans(S, d):
    return S[w.ANS0 + d]


@njit(cache=True)
def done(S, d):
    return S[w.ANS0 + d] != w.OPEN


@njit(cache=True)
def init(S):
    """The walk's state at the review date."""
    for d in range(w.N_DEC):
        S[w.ANS0 + d] = w.OPEN
    S[w.CLS_KIND], S[w.CLS_VALUE] = w.CLS_CLAIMED, 0
    S[w.STAYED] = S[w.APPEALED] = S[w.EARLY] = 0
    S[w.A4], S[w.RESP] = w.A4_OPEN, 0
    S[w.FLOOR_K], S[w.OUT_DONE], S[w.NP_DONE] = 1, 0, 0
    S[w.COMMITTED] = -1
    S[w.N_OFFERINGS] = 0
    S[w.REG_POST_DAY] = S[w.HEARING_DAY] = BIG
    S[w.VERDICT_IX] = -1
    for o in range(4):
        S[w.NOTES_DAY0 + o], S[w.NOTES_STAGE0 + o] = BIG, 0


# --- the open decisions (ChronologicalWalk._pending and _cursors) -------------------------------------------------------

@njit(cache=True)
def _add(S, out, m, d):
    if not done(S, d):
        out[m] = d
        m += 1
    return m


@njit(cache=True)
def pending(S, out):
    """`ChronologicalWalk._pending`: the litigation decisions the answers so far open, in its order; returns how
    many."""
    m = 0
    closed = S[w.ANS0 + w.DEC_RULING] == a.R_SET_ASIDE or ans(S, w.DEC_VERDICT) == w.NO_JUDGMENT
    for c in range(len(a.SETTLE_CTX)):
        closed = closed or ans(S, w.DEC_SETTLE0 + c) == a.YES
    for c in range(len(a.RESPONSE_CTX)):
        r = ans(S, w.DEC_RESP0 + c)
        closed = closed or r == a.B_PAY or r == a.B_PETITION
    if closed:
        return 0
    verdict = ans(S, w.DEC_VERDICT)
    if not done(S, w.DEC_SETTLE0 + 0):
        return _add(S, out, m, w.DEC_SETTLE0 + 0)
    if verdict == w.OPEN or verdict == w.ABSENT:
        return _add(S, out, m, w.DEC_VERDICT)
    m = _add(S, out, m, w.DEC_RESP0 + a.R_ENTRY)
    if not done(S, w.DEC_RESP0 + a.R_ENTRY):
        return m
    if not done(S, w.DEC_MOTIONS):
        m = _add(S, out, m, w.DEC_SETTLE0 + 1)  # Ientry
    m = _add(S, out, m, w.DEC_MOTIONS)
    motion = ans(S, w.DEC_MOTIONS)
    ruled = done(S, w.DEC_RULING)
    if motion == a.YES:
        if not done(S, w.DEC_EXECUTE):
            m = _add(S, out, m, w.DEC_SETTLE0 + 2)  # I1
        m = _add(S, out, m, w.DEC_EXECUTE)
        m = _add(S, out, m, w.DEC_RULING)
        if ans(S, w.DEC_EXECUTE) == a.YES:
            m = _add(S, out, m, w.DEC_STAY0 + 0)
            if done(S, w.DEC_STAY0 + 0):
                m = _add(S, out, m, w.DEC_REGISTRATION)
        if done(S, w.DEC_REGISTRATION) and not ruled:
            m = _add(S, out, m, w.DEC_RESP0 + a.R_I1)
        if ans(S, w.DEC_STAY0 + 0) == a.YES:
            m = _add(S, out, m, w.DEC_SETTLE0 + 3)  # Istay
        if done(S, w.DEC_RESP0 + a.R_I1):
            m = _add(S, out, m, w.DEC_SETTLE0 + 4)  # Ienforce
    if motion != w.OPEN and motion != w.ABSENT:
        if S[w.A4] == w.A4_SEEK:
            m = _add(S, out, m, w.DEC_RESP0 + a.R_RIPE)
        m = _add(S, out, m, w.DEC_JDEFAULT0 + 0)
    if motion == a.NO or ruled:
        if not done(S, w.DEC_APPEAL):
            m = _add(S, out, m, w.DEC_SETTLE0 + 6)  # I2
        m = _add(S, out, m, w.DEC_APPEAL)
        if ans(S, w.DEC_RULING) == a.R_REDUCED:
            m = _add(S, out, m, w.DEC_JDEFAULT0 + 2)  # ruling
        if done(S, w.DEC_APPEAL):
            if S[w.APPEALED]:
                m = _add(S, out, m, w.DEC_SETTLE0 + 5)  # Iappeal
            if not S[w.STAYED]:
                m = _add(S, out, m, w.DEC_STAY0 + 1)
            if S[w.STAYED] or done(S, w.DEC_STAY0 + 1):
                m = _add(S, out, m, w.DEC_SETTLE0 + (8 if S[w.STAYED] else 7))  # I4 / I3
                if ans(S, w.DEC_STAY0 + 0) != a.YES:
                    m = _add(S, out, m, w.DEC_ENFORCE)
                m = _add(S, out, m, w.DEC_JDEFAULT0 + 1)  # post
        if S[w.A4] != w.A4_CLOSED:
            m = _add(S, out, m, w.DEC_RESP0 + a.R_POST)
    return m


@njit(cache=True)
def pending_delisting(S, k):
    """`_pending_delisting`: the listing suspended and the holders' delisting decision not yet taken, inside the
    period."""
    return (ans(S, w.DEC_LISTING) == a.L_SUSPENDED and not done(S, w.DEC_DELISTING)
            and k.ldates[n.DELIST_SUSP] < k.N)


@njit(cache=True)
def cursors(S, k, wk, out_dec, out_chain):
    """`ChronologicalWalk._cursors`, flattened in its chain order (litigation, distress, listing, notes); returns how
    many."""
    buf = np.empty(w.N_DEC, np.int64)
    np_ = pending(S, buf)
    m = 0
    for j in range(np_):  # litigation (the responses where open; the defaults and the ripe response are the notes')
        d = buf[j]
        if d >= w.DEC_RESP0 and d < w.DEC_MOTIONS:
            if S[w.A4] == w.A4_CLOSED or (d == w.DEC_RESP0 + a.R_RIPE and S[w.A4] != w.A4_SEEK):
                continue
        if (d >= w.DEC_JDEFAULT0 and d < w.DEC_ENFORCE) or d == w.DEC_RESP0 + a.R_RIPE:
            continue
        out_dec[m], out_chain[m] = d, w.CH_LITIGATION
        m += 1
    for d in (w.DEC_FLOOR0 + S[w.FLOOR_K], w.DEC_CASH_OUT, w.DEC_NONPAYMENT):  # distress
        if d == w.DEC_CASH_OUT and S[w.OUT_DONE]:
            continue
        if d == w.DEC_NONPAYMENT and (S[w.NP_DONE] or not S[w.OUT_DONE]):
            continue
        if d == w.DEC_FLOOR0 + S[w.FLOOR_K] and S[w.FLOOR_K] > a.MAX_FLOORS:
            raise ValueError("cash floors exhausted")
        if not done(S, d):
            out_dec[m], out_chain[m] = d, w.CH_DISTRESS
            m += 1
    listed = done(S, w.DEC_LISTING)
    if not listed and wk.listing:
        out_dec[m], out_chain[m] = w.DEC_LISTING, w.CH_LISTING
        m += 1
    for j in range(np_):  # notes
        d = buf[j]
        if d >= w.DEC_RESP0 and d < w.DEC_MOTIONS:
            if S[w.A4] == w.A4_CLOSED or (d == w.DEC_RESP0 + a.R_RIPE and S[w.A4] != w.A4_SEEK):
                continue
        if (d >= w.DEC_JDEFAULT0 and d < w.DEC_ENFORCE) or d == w.DEC_RESP0 + a.R_RIPE:
            out_dec[m], out_chain[m] = d, w.CH_NOTES
            m += 1
    if listed and pending_delisting(S, k):
        out_dec[m], out_chain[m] = w.DEC_DELISTING, w.CH_NOTES
        m += 1
    return m


# --- the frontier (frontier.next_decisions and select; the court's order first) --------------------------------------

@njit(cache=True)
def decision_day(st, k, d):
    node, ctx = dec_node(d)
    return agenda.decision_day(st, k, node, ctx)


@njit(cache=True)
def frontier(st, k, wk):
    """The earliest item on the draw, after `agenda.refresh`: (kind, which, day); kind IT_NONE where nothing is left
    inside the period before a petition. Ties: a stay's approval first (the court's order is its day's first event),
    then the levy-day responses, the levy, the waiting decisions, the other decisions in cursor order, cash-out last
    (`frontier.select`'s phases)."""
    S, N = st.S, k.N
    pet = BIG if S[f.PETITION] < 0 else S[f.PETITION]
    best_day, best_kind, best_which, best_rank = BIG, w.IT_NONE, -1, (1 << 40)
    decs = np.empty(w.MAX_ITEMS, np.int64)
    chains = np.empty(w.MAX_ITEMS, np.int64)
    m = cursors(S, k, wk, decs, chains)
    order = 0
    for j in range(m):  # phase 0 (levy-day responses), 2 (decisions), 3 (cash-out), in cursor order
        d = decs[j]
        day = decision_day(st, k, d)
        if not (0 <= day < N and day < pet):
            day = BIG
        phase = 2
        if d == w.DEC_RESP0 + a.R_I1 or d == w.DEC_RESP0 + a.R_POST:
            phase = 0
        elif d == w.DEC_CASH_OUT:
            phase = 3
        rank = phase * (1 << 20) + order
        order += 1
        if day < best_day or (day == best_day and day < BIG and rank < best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_DECISION, d, rank
    lv = agenda.pending_levy(st)
    if lv < BIG:
        day = lv if 0 <= lv < N and lv < pet else BIG
        rank = 1 * (1 << 20) + order
        order += 1
        if day < best_day or (day == best_day and day < BIG and rank < best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_LEVY, -1, rank
    for j in range(S[a.N_WAIT]):
        row = st.wait[j]
        if row[a.W_DONE]:
            continue
        t = agenda.wait_day(st, k, row[a.W_NODE], row[a.W_CTX])
        day = t if 0 <= t < N and t < pet else BIG
        lr = row[a.W_NODE] == a.RESPONSE and (row[a.W_CTX] == a.R_I1 or row[a.W_CTX] == a.R_POST)
        rank = (0 if lr else 1) * (1 << 20) + order
        order += 1
        if day < best_day or (day == best_day and day < BIG and rank < best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_WAITING, j, rank
    for j in range(S[a.N_STAYS]):
        row = st.stays[j]
        if row[a.T_DONE] or row[a.T_OPEN] or not (row[a.T_APPROVED] or row[a.T_MOVED]):
            continue
        t = row[a.T_APPROVAL]
        if t < N and (t < best_day or (t == best_day and best_rank >= 0)):
            best_day, best_kind, best_which, best_rank = t, w.IT_APPROVAL, j, -1
    return best_kind, best_which, best_day


# --- the path's flags (forecast._S), as each constructor sets them ------------------------------------------------------

@njit(cache=True)
def answer_code(step):
    """The answer a step records for its decision (the verdict's: AWARD, JUDGMENT or NO_JUDGMENT)."""
    if step[0] == a.VERDICT:
        if step[2] == a.V_AWARD:
            return w.AWARD
        return w.JUDGMENT if step[3] != 0 else w.NO_JUDGMENT
    return step[2]


@njit(cache=True)
def record_step(S, step, asked):
    """A step taken: its decision's answer, and the flags its constructor sets on the path (asked: the step carries
    an edge; a grouped step on a draw it is not asked of, '@-1', sets none but the distress loop's)."""
    node = step[0]
    d = step_dec(step)
    if d >= 0:
        S[w.ANS0 + d] = answer_code(step)
    if node == a.VERDICT:
        if step[2] == a.V_AWARD:
            S[w.CLS_KIND], S[w.CLS_VALUE] = w.CLS_AWARD, step[3]
        else:
            S[w.CLS_KIND], S[w.CLS_VALUE] = w.CLS_BRANCH, step[6]
    elif node == a.RULING:
        if step[2] == a.R_REDUCED:
            S[w.CLS_KIND], S[w.CLS_VALUE] = w.CLS_REDUCED, step[3]
        elif step[2] == a.R_SET_ASIDE:
            S[w.CLS_KIND], S[w.CLS_VALUE] = w.CLS_SET_ASIDE, 0
    elif node == a.STAY and step[2] == a.YES:
        S[w.STAYED] = 1
    elif node == a.REGISTRATION and step[2] == a.YES:
        S[w.EARLY] = 1
    elif node == a.APPEAL and step[2] == a.YES:
        S[w.APPEALED] = 1
    elif node == a.RESPONSE and asked:
        b = step[2]
        S[w.A4] = w.A4_SEEK if b == a.B_NONE or b == a.B_OFFER else w.A4_CLOSED
        if b == a.B_OFFER:
            S[w.RESP] = 1
        elif b == a.B_NONE:
            S[w.RESP] = 0
    elif node == a.CASH_FLOOR:
        S[w.FLOOR_K] += 1
    elif node == a.CASH_OUT:
        S[w.OUT_DONE] = 1
    elif node == a.NONPAYMENT:
        S[w.NP_DONE] = 1
    elif node == a.OFFERING:
        S[w.N_OFFERINGS] += 1


# --- a history of the Python walk, followed -----------------------------------------------------------------------------

@njit(cache=True)
def follow(st, k, wk, ws, steps, asked, trace):
    """Drive the scheduler along a history's steps (`replay.encode` rows; asked: whether each carries an edge): at
    each point the frontier's earliest item; a deterministic item is booked through its day; a decision is the
    history's next step (booked as `Chain.advance` books it, with the offering's outcome after an initiation, which
    the same constructor asks), or else it does not arise on this draw (resolved without a step). trace[i]: (kind,
    which, day, the history step taken or -1). Returns (the history steps taken, the trace's length)."""
    S = st.S
    init(S)
    i, t = 0, 0
    while True:
        agenda.refresh(st, k)
        kind, which, day = frontier(st, k, wk)
        if kind == w.IT_NONE:
            break
        if t >= trace.shape[0]:
            raise ValueError("trace exhausted")
        if day < S[w.COMMITTED]:
            raise ValueError("an item inserted before the committed frontier")
        S[w.COMMITTED] = day
        if kind != w.IT_DECISION:
            agenda.until(st, k, day + 1)
            trace[t, 0], trace[t, 1], trace[t, 2], trace[t, 3] = kind, which, day, -1
            t += 1
            continue
        agenda.until(st, k, day)
        if i < steps.shape[0] and step_dec(steps[i]) == which:
            trace[t, 0], trace[t, 1], trace[t, 2], trace[t, 3] = kind, which, day, i
            t += 1
            agenda.advance(st, k, steps[i], ws)
            record_step(S, steps[i], asked[i])
            i += 1
            if (steps[i - 1][0] == a.RESPONSE or steps[i - 1][0] == a.CASH_FLOOR
                    or steps[i - 1][0] == a.CASH_OUT) and steps[i - 1][2] == a.B_OFFER \
                    and i < steps.shape[0] and steps[i][0] == a.OFFERING:
                agenda.advance(st, k, steps[i], ws)
                record_step(S, steps[i], asked[i])
                i += 1
            continue
        S[w.ANS0 + which] = w.ABSENT
        trace[t, 0], trace[t, 1], trace[t, 2], trace[t, 3] = kind, which, day, -1
        t += 1
    return i, t

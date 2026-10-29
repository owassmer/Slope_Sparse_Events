"""The exact evaluator's walk on one draw (design/002-exact-evaluator.md, The walk, compiled): every history, depth
first, each question asked at its own date with the answers open to it on the draw, the state saved before a question
and restored for each answer.

The scheduler is `walk.py`'s (the Python walk's cursors and frontier), extended by the elementary questions the
Python's constructors answer together with an earlier one (`fields_walk`: the court's answer to a stay motion at the
approval, the registration orders, the issuer's and the holders' petitions on the notes, the hearing request, the
offering's close): each is an item at its own date, before the levy and the other decisions of its day (where the
Python books its answer at its origin, its effect is dated that day and precedes them).

Pass 1 (`explore`): every question occurrence on the draw, as integers (`Q_*` columns): its node, context, amount
class, group and day, with the tree's size. Its record and class are `app/exact/record.py`'s.
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np
from numba import njit

from app.exact import agenda, cash, core, equity, leaf, notes, qrecord, walk
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import fields_equity as e
from app.exact import fields_notes as n
from app.exact import fields_record as fr
from app.exact import fields_walk as w

BIG = f.BIG

# the case's constants for the walk: whether the notes have a listing deadline; the verdict classes (encoded steps,
# `forecast.verdict_classes`) and their reduced bands (booked, low, high; -1: none); the question nodes' situation
# conditions (bitmask over events.MARKS); the offering bound; the holders' notice lag; per question node whether its
# class carries the appeal and the stay status (`uses_appeal_status`, `uses_stay_status`)
TK = namedtuple("TK", "listing verdicts reduced conds bound notice appeals stays")

from app.exact.fields_walk import (  # noqa: E402  (the questions' vocabulary)
    ORIGIN_NONPAYMENT,
    Q_APPEAL,
    Q_BID,
    Q_CASH_OUT,
    Q_ENFORCE,
    Q_EXECUTE,
    Q_FLOOR,
    Q_HEARING,
    Q_HOLDERS_ACT,
    Q_HOLDERS_DELIST,
    Q_HOLDERS_INV,
    Q_MOTIONS,
    Q_OFFERING,
    Q_PETITION_NOTES,
    Q_REGISTRATION,
    Q_REMITTITUR,
    Q_RESPONSE,
    Q_RULING,
    Q_SETTLE_ACCEPT,
    Q_SETTLE_OFFER,
    Q_STAY_APPROVED,
    Q_STAY_MOTION,
    Q_VERDICT,
    QCLS_KIND,
    QCLS_VALUE,
    QCOLS,
    QCTX,
    QDAY,
    QDEPTH,
    QEXTRA,
    QGROUP,
    QN,
    QNANS,
)

# --- answers ----------------------------------------------------------------------------------------------------------
MAX_ANS = 8
# the ruling's elementary answers: unchanged; reduced and the remittitur accepted; reduced and refused (a new trial);
# set aside
RU_UNCHANGED, RU_REDUCED_ACCEPTED, RU_REDUCED_REFUSED, RU_SET_ASIDE = range(4)
# a grouped decision's answers
G_PAY, G_OFFER, G_FILE, G_NONE = range(4)
# the holders' act on a delisting
H2_ACCELERATE, H2_NEITHER = 0, 1


@njit(cache=True)
def stay_phase(S, j):
    """The phase (0: I1, 1: post) of stay row j: a row per stay step, the pre-ruling one first where it was asked."""
    return 0 if j == 0 and S[w.ANS0 + w.DEC_STAY0] != w.OPEN and S[w.ANS0 + w.DEC_STAY0] != w.ABSENT else 1


@njit(cache=True)
def is_split(d):
    return d >= w.DEC_APPROVAL0


# --- the frontier with the elementary questions ----------------------------------------------------------------------

@njit(cache=True)
def reg_i1_order(st, k):
    return st.S[f.E0] + k.briefing + k.DC[core.DC_LAG_REG_I1]


@njit(cache=True)
def item_day(st, k, d):
    """A decision's date on the state now (BIG: not on this draw)."""
    S = st.S
    if d >= w.DEC_APPROVAL0 and d < w.DEC_REG_POST:
        j = d - w.DEC_APPROVAL0
        return st.stays[j, a.T_APPROVAL] if st.stays[j, a.T_OPEN] else BIG
    if d == w.DEC_REG_POST:
        return S[w.REG_POST_DAY]
    if d >= w.DEC_A5_0 and d < w.DEC_H3_0:
        o = d - w.DEC_A5_0
        return S[w.NOTES_DAY0 + o] if S[w.NOTES_STAGE0 + o] == 1 else BIG
    if d >= w.DEC_H3_0 and d < w.DEC_HEARING:
        o = d - w.DEC_H3_0
        return S[w.NOTES_DAY0 + o] if S[w.NOTES_STAGE0 + o] == 2 else BIG
    if d == w.DEC_HEARING:
        return S[w.HEARING_DAY]
    if d >= w.DEC_N1_0:
        j = d - w.DEC_N1_0
        if j >= S[e.N_OFFERS] or st.answers[st.offers[j, e.O_OCCASION]] >= 0:
            return BIG
        return st.offers[j, e.O_CLOSE]
    if d == w.DEC_REGISTRATION:
        return reg_i1_order(st, k)
    return walk.decision_day(st, k, d)


@njit(cache=True)
def frontier(st, k, wk):
    """`walk.frontier` for the dated walk: the earliest item; on a day, a stay's court answer first, then the other
    elementary questions and the early registration order (phase -1), the levy-day responses, the levy, the waiting
    answers, the decisions in cursor order, cash-out. Returns (kind, which, day)."""
    S, N = st.S, k.N
    pet = BIG if S[f.PETITION] < 0 else S[f.PETITION]
    best_day, best_kind, best_which, best_rank = BIG, w.IT_NONE, -1, np.int64(1) << 40
    order = 0

    def better(day, rank, bd, br):
        return day < BIG and (day < bd or (day == bd and rank < br))

    for j in range(S[a.N_STAYS]):  # phase -2: the court's answer to a stay motion
        d = w.DEC_APPROVAL0 + j
        day = item_day(st, k, d)
        day = day if 0 <= day < N and day < pet else BIG
        rank = -2 * (np.int64(1) << 20) + order
        order += 1
        if better(day, rank, best_day, best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_DECISION, d, rank
    for d in range(w.DEC_REG_POST, w.N_DEC):  # phase -1: the other elementary questions
        day = item_day(st, k, d)
        day = day if 0 <= day < N and day < pet else BIG
        rank = -1 * (np.int64(1) << 20) + order
        order += 1
        if better(day, rank, best_day, best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_DECISION, d, rank
    decs = np.empty(w.MAX_ITEMS, np.int64)
    chains = np.empty(w.MAX_ITEMS, np.int64)
    m = walk.cursors(S, k, wk, decs, chains)
    for j in range(m):
        d = decs[j]
        day = item_day(st, k, d)
        if not (0 <= day < N and day < pet):
            day = BIG
        phase = 2
        if d == w.DEC_RESP0 + a.R_I1 or d == w.DEC_RESP0 + a.R_POST:
            phase = 0
        elif d == w.DEC_CASH_OUT:
            phase = 3
        elif d == w.DEC_REGISTRATION:
            phase = -1
        rank = phase * (np.int64(1) << 20) + order
        order += 1
        if better(day, rank, best_day, best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_DECISION, d, rank
    lv = agenda.pending_levy(st)
    if lv < BIG:
        day = lv if 0 <= lv < N and lv < pet else BIG
        rank = (np.int64(1) << 20) + order
        order += 1
        if better(day, rank, best_day, best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_LEVY, -1, rank
    for j in range(S[a.N_WAIT]):
        row = st.wait[j]
        if row[a.W_DONE]:
            continue
        t = agenda.wait_day(st, k, row[a.W_NODE], row[a.W_CTX])
        day = t if 0 <= t < N and t < pet else BIG
        lr = row[a.W_NODE] == a.RESPONSE and (row[a.W_CTX] == a.R_I1 or row[a.W_CTX] == a.R_POST)
        rank = (0 if lr else 1) * (np.int64(1) << 20) + order
        order += 1
        if better(day, rank, best_day, best_rank):
            best_day, best_kind, best_which, best_rank = day, w.IT_WAITING, j, rank
    for j in range(S[a.N_STAYS]):  # an answered approval, processed first on its day
        row = st.stays[j]
        if row[a.T_DONE] or row[a.T_OPEN] or not (row[a.T_APPROVED] or row[a.T_MOVED]):
            continue
        t = row[a.T_APPROVAL]
        rank = -3 * (np.int64(1) << 20)
        if t < N and better(t, rank, best_day, best_rank):
            best_day, best_kind, best_which, best_rank = t, w.IT_APPROVAL, j, rank
    return best_kind, best_which, best_day


# --- projections (Chain.proposed_terms on the dated state) -------------------------------------------------------------

@njit(cache=True)
def project_now(st, k, ws, decision, payment, processing):
    """The terms a decision on `decision` prices on `payment`: the state now (every answer dated by the decision, none
    after it) run forward through what is already ordered (the writs; the court's answers not yet given are not
    given: a stay pending approval is not approved), reading the payment day's close (processing: its balance at
    processing, a stay's security). Returns (cash, need, owed, collateral, offer, live)."""
    view = ws.scratch
    agenda.copy_into(view, st)
    S = view.S
    for j in range(S[a.N_STAYS]):
        if view.stays[j, a.T_OPEN]:
            view.stays[j, a.T_OPEN] = 0
            view.stays[j, a.T_APPROVED] = 0
    S[a.PROSPECTIVE] = decision
    agenda.until(view, k, payment if payment < k.N else -1)
    agenda.refresh(view, k)
    N = k.N
    t = min(max(payment, 0), N - 1)
    cash_p = cash.processing_balance(view.D, view.ln, payment) if processing else cash.cash_at(view.ln, payment)
    owed_p = agenda.owed(view, k, payment, False)
    need = k.need[t]
    collateral = agenda.bond_collateral(view, k, payment)
    live = core.live(view.S, payment) and payment < N
    offer = max(cash_p - need, 0) if live and cash_p - need < collateral else 0
    return cash_p, need, owed_p, collateral, offer, live


@njit(cache=True)
def settle_terms(st, k, ws, ctx):
    """`Chain.settle`'s amount for the window ctx on the state now: (bound, live, cash, owed) at the settlement date;
    a settlement exists where bound > 0."""
    start, end = agenda.settlement_window(st, k, ctx)
    pd = end if k.settle_end else start + k.settle_lag
    cash_p, need, owed_p, _, _, live = project_now(st, k, ws, max(start, 0), pd, False)
    ok = live and pd < k.N and start < k.N
    cap = k.claimed if ctx == 0 else owed_p
    bound = min(max(cash_p - need, 0), cap) if ok else 0
    return bound, live, cash_p, owed_p


# --- the questions asked at a decision, and the answers open to it on the draw ----------------------------------------

@njit(cache=True)
def emit(S, out, nq, qn, ctx, extra, group, day, depth):
    out[nq, QN], out[nq, QCTX], out[nq, QCLS_KIND], out[nq, QCLS_VALUE] = qn, ctx, S[w.CLS_KIND], S[w.CLS_VALUE]
    out[nq, QEXTRA], out[nq, QGROUP], out[nq, QDAY], out[nq, QDEPTH] = extra, group, day, depth
    return nq + 1


@njit(cache=True)
def set_nans(qb, nb, m):
    """Each of a decision's questions' answer count: the settlement's offer and acceptance, the remittitur and the
    notes petitions are yes or no; the ruling's own question has the decision's answers less the remittitur's split;
    a single question has the decision's."""
    for q in range(nb):
        qn = qb[q, QN]
        if qn == Q_SETTLE_OFFER or qn == Q_SETTLE_ACCEPT or qn == Q_REMITTITUR or (
                nb == 2 and (qn == Q_PETITION_NOTES or qn == Q_HOLDERS_INV)):
            qb[q, QNANS] = 2
        elif qn == Q_RULING:
            qb[q, QNANS] = m - 1 if m == 4 else m
        else:
            qb[q, QNANS] = m


@njit(cache=True)
def qid(q, code):
    """A question's identity on the draw: its key's parts and its class (FNV-1a over the integers)."""
    h = np.uint64(14695981039346656037)
    p = np.uint64(1099511628211)
    for c in (QN, QCTX, QCLS_KIND, QCLS_VALUE, QEXTRA, QGROUP, QNANS):
        h = (h ^ np.uint64(q[c] & 0xFFFFFFFFFFFFFFFF)) * p
    for x in code:
        h = (h ^ np.uint64(x & 0xFFFFFFFFFFFFFFFF)) * p
    return np.int64(h)


@njit(cache=True)
def group_code(st, k, response, day):
    """`Chain.option_group` on the draw at `day` (bit 0: the balance can be paid; bit 1: an offering is available;
    -1: not asked); after `agenda.refresh`."""
    S = st.S
    lower = k.lower[day] if 0 <= day < k.N else 0
    return equity.option_group(S, st.P, st.takes, st.offers, st.atm, k.sched, k.ec, response, day,
                               lower, agenda.pending_levy(st), st.ln.outputs[0], k.need, k.ops_in,
                               st.D[f.D_KIND_INFLOW], st.lockbuf, st.D[f.D_KIND_LEVY])


@njit(cache=True)
def opens_nothing(st, k):
    """`_Walk._q1_opens_nothing` on the draw: the stay the execution opens is approved, and the early registration's
    levy falls, only on or after the period's end or a petition (on the state now)."""
    S = st.S
    pet = BIG if S[f.PETITION] < 0 else S[f.PETITION]
    end = min(k.N, pet)
    approval = S[f.E0] + k.briefing + k.DC[core.DC_LAG_STAY_I1]
    levy = reg_i1_order(st, k) + k.levy_lag
    return approval >= end and levy >= end


@njit(cache=True)
def options(st, k, wk, tk, ws, d, day, ans):
    """The answers open to decision d on the draw at `day` (in the Python constructor's order), how many, and whether
    the question is asked (False: it does not arise here; its one answer books as the constructor books it unasked).
    Also the option group (-1: none). After `agenda.refresh`."""
    S = st.S
    if d < w.DEC_VERDICT:  # settle
        bound = settle_terms(st, k, ws, d - w.DEC_SETTLE0)[0]
        if bound <= 0:
            ans[0] = a.NO
            return 1, False, -1
        ans[0], ans[1] = a.YES, a.NO
        return 2, True, -1
    if d == w.DEC_VERDICT:
        for j in range(tk.verdicts.shape[0]):
            ans[j] = j
        return tk.verdicts.shape[0], True, -1
    if d >= w.DEC_RESP0 and d < w.DEC_MOTIONS:
        g = group_code(st, k, True, day)
        if g < 0:
            ans[0] = G_NONE
            return 1, False, -1
        m = 0
        if g & 1:
            ans[m] = G_PAY
            m += 1
        if g & 2:
            ans[m] = G_OFFER
            m += 1
        ans[m], ans[m + 1] = G_FILE, G_NONE
        return m + 2, True, g
    if d == w.DEC_CASH_OUT or (d > w.DEC_FLOOR0 and d < w.DEC_APPROVAL0):
        g = group_code(st, k, False, day)
        if g < 0:
            ans[0] = G_NONE
            return 1, False, -1
        m = 0
        if g & 2:
            ans[m] = G_OFFER
            m += 1
        ans[m], ans[m + 1] = G_FILE, G_NONE
        return m + 2, True, g
    if d == w.DEC_RULING:
        ans[0] = RU_UNCHANGED
        m = 1
        vi = S[w.VERDICT_IX]
        if vi >= 0 and tk.reduced[vi, 0] >= 0:
            ans[1], ans[2] = RU_REDUCED_ACCEPTED, RU_REDUCED_REFUSED
            m = 3
        ans[m] = RU_SET_ASIDE
        return m + 1, True, -1
    if d == w.DEC_NONPAYMENT:
        ans[0], ans[1], ans[2] = a.NP_PETITION, a.NP_HOLDERS, a.NP_DUE
        return 3, True, -1
    if d == w.DEC_DELISTING:
        if S[f.DELISTED] + tk.notice >= k.N:  # `_declared_after`: a declaration takes effect after the period
            ans[0] = H2_NEITHER
            return 1, False, -1
        ans[0], ans[1] = H2_ACCELERATE, H2_NEITHER
        return 2, True, -1
    if d == w.DEC_STAY0 or d == w.DEC_STAY0 + 1:
        ans[0], ans[1] = a.YES, a.NO
        return 2, True, -1
    if d >= w.DEC_APPROVAL0 and d < w.DEC_REG_POST:
        ans[0], ans[1] = a.YES, a.DENIED
        return 2, True, -1
    if d == w.DEC_ENFORCE:
        ans[0], ans[1] = a.E_LEVY, a.E_NONE
        return 2, True, -1
    if d == w.DEC_LISTING:
        ans[0], ans[1] = a.YES, a.NO
        return 2, True, -1
    ans[0], ans[1] = a.YES, a.NO  # motions, execution, registration, appeal, the defaults, the elementary questions
    return 2, True, -1


@njit(cache=True)
def watched(st, k, d):
    """The depth rule's questions (QUESTIONS §1 Depth; `_Walk._watched`): the appeal always, the pre-ruling execution
    where what it opens books nothing inside the period. Walked on the no-answer first; asked where a later question
    reads the event."""
    return d == w.DEC_APPEAL or (d == w.DEC_EXECUTE and opens_nothing(st, k))


@njit(cache=True)
def questions(st, k, tk, d, day, group, out, nq, depth):
    """Emit the questions asked at decision d (pass 1)."""
    S = st.S
    if d < w.DEC_VERDICT:
        c = d - w.DEC_SETTLE0
        nq = emit(S, out, nq, Q_SETTLE_OFFER, c, 0, -1, day, depth)
        return emit(S, out, nq, Q_SETTLE_ACCEPT, c, 0, -1, day, depth)
    if d == w.DEC_VERDICT:
        return emit(S, out, nq, Q_VERDICT, 0, 0, -1, day, depth)
    if d >= w.DEC_RESP0 and d < w.DEC_MOTIONS:
        after = (1 + S[w.RESP]) if S[w.A4] == w.A4_SEEK else 0
        return emit(S, out, nq, Q_RESPONSE, d - w.DEC_RESP0, after, group, day, depth)
    if d == w.DEC_MOTIONS:
        return emit(S, out, nq, Q_MOTIONS, 0, 0, -1, day, depth)
    if d == w.DEC_EXECUTE:
        return emit(S, out, nq, Q_EXECUTE, 0, 0, -1, day, depth)
    if d == w.DEC_STAY0 or d == w.DEC_STAY0 + 1:
        return emit(S, out, nq, Q_STAY_MOTION, d - w.DEC_STAY0, 0, -1, day, depth)
    if d >= w.DEC_APPROVAL0 and d < w.DEC_REG_POST:
        return emit(S, out, nq, Q_STAY_APPROVED, stay_phase(S, d - w.DEC_APPROVAL0), 0, -1, day, depth)
    if d == w.DEC_REGISTRATION:
        return emit(S, out, nq, Q_REGISTRATION, 0, 0, -1, day, depth)
    if d == w.DEC_REG_POST:
        return emit(S, out, nq, Q_REGISTRATION, 1, 0, -1, day, depth)
    if d == w.DEC_RULING:
        return emit(S, out, nq, Q_RULING, 0, 0, -1, day, depth)
    if d == w.DEC_APPEAL:
        return emit(S, out, nq, Q_APPEAL, 0, 0, -1, day, depth)
    if d == w.DEC_ENFORCE:
        return emit(S, out, nq, Q_ENFORCE, 0, S[w.APPEALED], -1, day, depth)
    if d >= w.DEC_JDEFAULT0 and d < w.DEC_ENFORCE:
        c = d - w.DEC_JDEFAULT0
        earlier = 1 if c != 0 and S[w.ANS0 + w.DEC_JDEFAULT0] >= 0 else 0
        return emit(S, out, nq, Q_HOLDERS_ACT, c, earlier, -1, day, depth)
    if d >= w.DEC_A5_0 and d < w.DEC_H3_0:
        return emit(S, out, nq, Q_PETITION_NOTES, d - w.DEC_A5_0, 0, -1, day, depth)
    if d >= w.DEC_H3_0 and d < w.DEC_HEARING:
        return emit(S, out, nq, Q_HOLDERS_INV, d - w.DEC_H3_0, 0, -1, day, depth)
    if d == w.DEC_NONPAYMENT:
        nq = emit(S, out, nq, Q_PETITION_NOTES, ORIGIN_NONPAYMENT, 0, -1, day, depth)
        return emit(S, out, nq, Q_HOLDERS_INV, ORIGIN_NONPAYMENT, 0, -1, day, depth)
    if d == w.DEC_LISTING:
        return emit(S, out, nq, Q_BID, 0, 0, -1, day, depth)
    if d == w.DEC_HEARING:
        return emit(S, out, nq, Q_HEARING, 0, 0, -1, day, depth)
    if d == w.DEC_DELISTING:
        return emit(S, out, nq, Q_HOLDERS_DELIST, 0, 0, -1, day, depth)
    if d >= w.DEC_N1_0:
        return emit(S, out, nq, Q_OFFERING, st.offers[d - w.DEC_N1_0, e.O_OCCASION], 0, -1, day, depth)
    if d == w.DEC_CASH_OUT:
        return emit(S, out, nq, Q_CASH_OUT, 0, 0, group, day, depth)
    return emit(S, out, nq, Q_FLOOR, d - w.DEC_FLOOR0, 0, group, day, depth)


# --- bookings ----------------------------------------------------------------------------------------------------------
MOVED = 3  # a stay motion's answer while the court has not answered (the motion's answer once it has: YES, DENIED)


@njit(cache=True)
def mk(node, ctx, ans, x3=0, x4=0, x5=0, x6=0):
    out = np.zeros(a.STEP_COLS, np.int64)
    out[0], out[1], out[2], out[3], out[4], out[5], out[6] = node, ctx, ans, x3, x4, x5, x6
    return out


@njit(cache=True)
def advance(st, k, ws, step, asked):
    agenda.advance(st, k, step, ws)
    walk.record_step(st.S, step, asked)


BOOKING = np.array([a.B_PAY, a.B_OFFER, a.B_PETITION, a.B_NONE], dtype=np.int64)  # by G_*


@njit(cache=True)
def eq(st, k):
    return agenda.eq(st, k)


@njit(cache=True)
def book(st, k, tk, ws, d, day, ans, asked):
    """Book answer `ans` to decision d on its day (asked: the question was put; else its one answer books unasked)."""
    S = st.S
    if d < w.DEC_VERDICT:  # settle
        c = d - w.DEC_SETTLE0
        step = mk(a.SETTLE, c, ans)
        if ans == a.YES:
            bound, live, cash_p, owed_p = settle_terms(st, k, ws, c)
            i = agenda.prepare(st, k, step)
            agenda.conclude(st, k, step, i, (live, cash_p, owed_p))
            walk.record_step(S, step, asked)
        else:
            advance(st, k, ws, step, asked)
        return
    if d == w.DEC_VERDICT:
        advance(st, k, ws, tk.verdicts[ans].copy(), asked)
        S[w.VERDICT_IX] = ans
        return
    if d >= w.DEC_RESP0 and d < w.DEC_MOTIONS:
        advance(st, k, ws, mk(a.RESPONSE, d - w.DEC_RESP0, BOOKING[ans]), asked)
        return
    if d == w.DEC_CASH_OUT:
        advance(st, k, ws, mk(a.CASH_OUT, 0, BOOKING[ans]), asked)
        return
    if d > w.DEC_FLOOR0 and d < w.DEC_APPROVAL0:
        advance(st, k, ws, mk(a.CASH_FLOOR, d - w.DEC_FLOOR0, BOOKING[ans]), asked)
        return
    if d == w.DEC_STAY0 or d == w.DEC_STAY0 + 1:
        p = d - w.DEC_STAY0
        j = S[a.N_STAYS]
        if ans == a.YES:  # moved: the court answers at the approval
            advance(st, k, ws, mk(a.STAY, p, a.DENIED), asked)
            st.stays[j, a.T_OPEN] = 1
            S[w.ANS0 + d] = MOVED
        else:
            advance(st, k, ws, mk(a.STAY, p, a.NO), asked)
        return
    if d >= w.DEC_APPROVAL0 and d < w.DEC_REG_POST:
        j = d - w.DEC_APPROVAL0
        row = st.stays[j]
        row[a.T_OPEN] = 0
        row[a.T_APPROVED] = ans == a.YES
        S[w.ANS0 + w.DEC_STAY0 + stay_phase(S, j)] = ans
        S[w.ANS0 + d] = ans
        if ans == a.YES:
            S[w.STAYED] = 1
        return
    if d == w.DEC_RULING:
        if ans == RU_UNCHANGED:
            step = mk(a.RULING, 0, a.R_UNCHANGED)
        elif ans == RU_REDUCED_ACCEPTED:
            vi = S[w.VERDICT_IX]
            step = mk(a.RULING, 0, a.R_REDUCED, tk.reduced[vi, 0])
        else:
            step = mk(a.RULING, 0, a.R_SET_ASIDE)
        advance(st, k, ws, step, asked)
        return
    if d >= w.DEC_JDEFAULT0 and d < w.DEC_ENFORCE:
        c = d - w.DEC_JDEFAULT0
        ripe, on = notes.prepare_default(S, st.D, st.coupons, st.takes, k.nc, c,
                                         n.ACCELERATED if ans == a.YES else n.NO, True)
        S[w.ANS0 + d] = ans
        if ans == a.YES and on:
            S[w.NOTES_STAGE0 + c], S[w.NOTES_DAY0 + c] = 1, ripe + tk.notice
        return
    if d >= w.DEC_A5_0 and d < w.DEC_H3_0:
        o = d - w.DEC_A5_0
        S[w.ANS0 + d] = ans
        if ans == a.YES:
            notes._petition(S, st.D, st.coupons, k.nc, eq(st, k), day, True)
            S[w.NOTES_STAGE0 + o], S[w.NOTES_DAY0 + o] = 0, BIG
        else:
            agenda.refresh(st, k)
            route = notes.holder_route_days_path(S, k.nc, S[a.CASH_NONPAYMENT])
            S[w.NOTES_STAGE0 + o], S[w.NOTES_DAY0 + o] = 2, day + route
        return
    if d >= w.DEC_H3_0 and d < w.DEC_HEARING:
        o = d - w.DEC_H3_0
        S[w.ANS0 + d] = ans
        if ans == a.YES:
            notes._petition(S, st.D, st.coupons, k.nc, eq(st, k), day, True)
        S[w.NOTES_STAGE0 + o], S[w.NOTES_DAY0 + o] = 0, BIG
        return
    if d == w.DEC_ENFORCE:
        if ans == a.E_LEVY and S[w.APPEALED] and not S[w.EARLY]:  # the levy waits for the court's order
            advance(st, k, ws, mk(a.ENFORCE, 0, a.E_NONE), asked)
            S[w.ANS0 + d] = ans
            S[w.REG_POST_DAY] = max(S[f.EF], 0) + k.briefing + k.DC[core.DC_LAG_REG_POST]
        else:
            advance(st, k, ws, mk(a.ENFORCE, 0, ans), asked)
        return
    if d == w.DEC_REG_POST:
        S[w.ANS0 + d] = ans
        if ans == a.YES:
            agenda.queue_levy(st, max(S[w.REG_POST_DAY], 0) + k.levy_lag)
        S[w.REG_POST_DAY] = BIG
        return
    if d == w.DEC_LISTING:
        if ans == a.YES:
            advance(st, k, ws, mk(a.LISTING, 0, a.L_COMPLIANT), asked)
        else:
            S[w.ANS0 + d] = w.L_PENDING
            S[w.HEARING_DAY] = k.ldates[n.HEARING]
        return
    if d == w.DEC_HEARING:
        advance(st, k, ws, mk(a.LISTING, 0, a.L_HEARING if ans == a.YES else a.L_SUSPENDED), asked)
        S[w.ANS0 + d] = ans
        S[w.HEARING_DAY] = BIG
        return
    if d == w.DEC_DELISTING:
        if ans == H2_ACCELERATE:
            advance(st, k, ws, mk(a.DELISTING, 0, 1), asked)
            S[w.NOTES_STAGE0 + w.O_DELISTING] = 1
            S[w.NOTES_DAY0 + w.O_DELISTING] = S[f.DELISTED] + tk.notice
        else:
            advance(st, k, ws, mk(a.DELISTING, 0, 0), asked)
        return
    if d >= w.DEC_N1_0:
        j = d - w.DEC_N1_0
        equity.offering_outcome(S, st.D, st.P, st.takes, agenda.parts(st), st.offers, st.atm, k.sched,
                                st.coupons[:S[n.N_COUPONS]], st.answers, k.ec, st.scratch,
                                st.offers[j, e.O_OCCASION], ans == a.YES)
        S[w.N_OFFERINGS] += 1
        S[w.ANS0 + d] = ans
        return
    if d == w.DEC_NONPAYMENT:
        advance(st, k, ws, mk(a.NONPAYMENT, 0, ans), asked)
        return
    advance(st, k, ws, mk(walk.dec_node(d)[0], walk.dec_node(d)[1], ans), asked)  # motions, execution, registration,
    #                                                                                 appeal


# --- the walk, depth first (pass 1) -----------------------------------------------------------------------------------
MAX_DEPTH = 64
# the saved states, one per depth (every array of `agenda.St` the walk writes; the line's buffers are scratch)
Stack = namedtuple("Stack", "S D P takes offers atm answers parts coupons wait stays rec steps lockbuf")
D_ROWS = 26  # D's rows a walk writes (the core's, the equity's, the stays' lock): the rest are other areas' blocks
assert f.D_STAYS < D_ROWS and f.D_EQUITY + 2 <= D_ROWS


def stack_for(st, depth: int = MAX_DEPTH) -> Stack:
    return Stack(*(np.zeros((depth, *(x[:D_ROWS] if name == "D" else x).shape), x.dtype)
                   for name, x in zip(Stack._fields, (getattr(st, name) for name in Stack._fields), strict=True)))


@njit(cache=True)
def save(st, sk, L):
    sk.S[L] = st.S
    sk.D[L] = st.D[:D_ROWS]
    sk.P[L] = st.P
    sk.takes[L] = st.takes
    sk.offers[L] = st.offers
    sk.atm[L] = st.atm
    sk.answers[L] = st.answers
    sk.parts[L] = st.parts
    sk.coupons[L] = st.coupons
    sk.wait[L] = st.wait
    sk.stays[L] = st.stays
    sk.rec[L] = st.rec
    sk.steps[L] = st.steps
    sk.lockbuf[L] = st.lockbuf


@njit(cache=True)
def restore(st, sk, L):
    st.S[:] = sk.S[L]
    st.D[:D_ROWS] = sk.D[L]
    st.P[:] = sk.P[L]
    st.takes[:] = sk.takes[L]
    st.offers[:] = sk.offers[L]
    st.atm[:] = sk.atm[L]
    st.answers[:] = sk.answers[L]
    st.parts[:] = sk.parts[L]
    st.coupons[:] = sk.coupons[L]
    st.wait[:] = sk.wait[L]
    st.stays[:] = sk.stays[L]
    st.rec[:] = sk.rec[L]
    st.steps[:] = sk.steps[L]
    st.lockbuf[:] = sk.lockbuf[L]


@njit(cache=True)
def next_question(st, k, wk, tk, ws, ans):
    """From the state now, book every item that is not a question (the levy, the waiting answers, the court's
    approvals, a decision that does not arise on the draw) until a question comes up. Returns (decision, day, how
    many answers, the option group); decision -1: none left inside the period before a petition (a leaf)."""
    S = st.S
    while True:
        agenda.refresh(st, k)
        kind, which, day = frontier(st, k, wk)
        if kind == w.IT_NONE:
            return -1, BIG, 0, -1
        if day < S[w.COMMITTED]:
            raise ValueError("an item inserted before the committed frontier")
        S[w.COMMITTED] = day
        if kind != w.IT_DECISION:
            agenda.until(st, k, day + 1)
            continue
        agenda.until(st, k, day)
        agenda.refresh(st, k)
        m, asked, g = options(st, k, wk, tk, ws, which, day, ans)
        if not asked:
            book(st, k, tk, ws, which, day, ans[0], False)
            if which < w.N_DEC and S[w.ANS0 + which] == w.OPEN:
                S[w.ANS0 + which] = w.ABSENT
            continue
        return which, day, m, g


# the depth rule's watches: per level, what the watched event would mark and when (the counterfactual), and whether a
# later question in its no-answer subtree read it
W_NONE, W_WALKING, W_ASKED = 0, 1, 2


@njit(cache=True)
def watch_read(st, tk, lv_dec, lv_watch, lv_read, lv_cf, L, qn, day, walk_read):
    """A question (node qn, on `day`) asked below the watched levels: does it read the watched event (`_Watch`: one of
    its nodes, its walks, or a situation condition that would hold on the day had the event occurred)?"""
    S = st.S
    for lvl in range(L):
        if lv_watch[lvl] != W_WALKING:
            continue
        d = lv_dec[lvl]
        if qn == Q_ENFORCE or (walk_read and d == w.DEC_EXECUTE):
            lv_read[lvl] = 1
        elif d == w.DEC_APPEAL and (qn == Q_SETTLE_OFFER or qn == Q_SETTLE_ACCEPT):
            lv_read[lvl] = 1
        else:
            conds = tk.conds[qn]
            for c in range(2):
                mark_ix, cf = lv_cf[lvl, 2 * c], lv_cf[lvl, 2 * c + 1]
                if mark_ix < 0 or not (conds >> (mark_ix - f.MARK0)) & 1:
                    continue
                if cf <= day < S[mark_ix]:
                    lv_read[lvl] = 1


@njit(cache=True)
def watch_key(S, d):
    """The watched question's key (its node and amount class): QUESTIONS §1 Depth asks it wherever it arises when its
    answer can change something on some trajectory, so whether it is asked is decided per key over every draw."""
    qn = Q_APPEAL if d == w.DEC_APPEAL else Q_EXECUTE
    return (np.int64(qn) << 56) | (S[w.CLS_KIND] << 52) | S[w.CLS_VALUE]


@njit(cache=True)
def klass(tk, q, ints, code):
    """A question's class on the draw (`qrecord.classify`); the verdict form's questions carry none (the Python walk
    gives them no class: their key is the question)."""
    if q[QN] == Q_VERDICT:
        code[:] = 0
        return
    qrecord.classify(q, ints, code, tk.conds, tk.appeals, tk.stays)


@njit(cache=True)
def digest(ints, floats):
    """A record's 64-bit digest (FNV-1a over its integers and its floats' bits)."""
    h = np.uint64(14695981039346656037)
    p = np.uint64(1099511628211)
    for x in ints:
        h = (h ^ np.uint64(x & 0xFFFFFFFFFFFFFFFF)) * p
    for x in floats:
        h = (h ^ np.float64(x).view(np.uint64)) * p
    return np.int64(h)


@njit(cache=True)
def take(st, k, tk, ws, q, out, cls_out, dig, nq, ints, floats, code):
    """Keep question occurrence q: its row, its class, its record's digest and its identity (`qid`; counted past the
    buffers' size)."""
    qrecord.question(st, k, ws.scratch, q, ints, floats)
    klass(tk, q, ints, code)
    if nq < out.shape[0]:
        out[nq] = q
        cls_out[nq] = code
        dig[nq, 0] = digest(ints, floats)
        dig[nq, 1] = qid(q, code)
    return nq + 1


@njit(cache=True)
def leaf_range(st, k, ev_lo, ev_hi):
    """At a leaf: widen the range of the final cumulative event cash (`leaf.finalize`; what pass 2's bins span)."""
    leaf.finalize(st, k)
    agenda.restay(st, k)
    cum = 0
    for t in range(k.N):
        cum += st.D[f.D_CASH, t] - st.lockbuf[t]
        ev_lo[t] = min(ev_lo[t], cum)
        ev_hi[t] = max(ev_hi[t], cum)


@njit(cache=True)
def explore(st, k, wk, tk, ws, sk, out, cls_out, dig, budget, forced, reads, ev_lo, ev_hi):
    """Pass 1 on the draw: every question occurrence (`out`, QCOLS columns; its class `cls_out`; its record's digest
    `dig`; counted past their size) and the tree's size. forced: the watched questions' keys (sorted) asked wherever
    they arise, because their answer was read on some draw; reads: the keys read on this draw (written). Returns
    (questions, nodes, leaves, deepest, reads written)."""
    S = st.S
    walk.init(S)
    lv_dec = np.zeros(MAX_DEPTH, np.int64)
    lv_day = np.zeros(MAX_DEPTH, np.int64)
    lv_n = np.zeros(MAX_DEPTH, np.int64)
    lv_next = np.zeros(MAX_DEPTH, np.int64)
    lv_group = np.zeros(MAX_DEPTH, np.int64)
    lv_ans = np.zeros((MAX_DEPTH, MAX_ANS), np.int64)
    lv_watch = np.zeros(MAX_DEPTH, np.int64)
    lv_read = np.zeros(MAX_DEPTH, np.int64)
    lv_cf = np.full((MAX_DEPTH, 4), -1, np.int64)
    ans = np.zeros(MAX_ANS, np.int64)
    qb = np.zeros((4, QCOLS), np.int64)  # the questions of one decision
    ints = np.zeros(fr.NI, np.int64)
    floats = np.zeros(fr.NF, np.float64)
    code = np.zeros(fr.NC, np.int64)
    L, nq, nodes, leaves, deepest, nr = 0, 0, 0, 0, 0, 0
    while True:
        d, day, m, g = next_question(st, k, wk, tk, ws, ans)
        if d >= 0:
            if L >= MAX_DEPTH:
                raise ValueError("the walk is deeper than its stack")
            nodes += 1
            if nodes > budget:
                return nq, nodes, leaves, deepest, nr
            save(st, sk, L)
            lv_dec[L], lv_day[L], lv_n[L], lv_group[L] = d, day, m, g
            lv_ans[L, :m] = ans[:m]
            lv_watch[L], lv_read[L] = W_NONE, 0
            wkey = watch_key(S, d) if watched(st, k, d) else np.int64(-1)
            if wkey >= 0 and forced.size and forced[min(np.searchsorted(forced, wkey), forced.size - 1)] == wkey:
                wkey = -1  # read on another draw: asked here too, both answers walked
            if wkey >= 0:  # the no answer first, watching
                lv_watch[L] = W_WALKING
                lv_ans[L, 0], lv_ans[L, 1] = a.NO, a.YES
                lv_cf[L, :] = -1
                if d == w.DEC_EXECUTE:
                    on = core.live(S, S[f.E0])
                    lv_cf[L, 0], lv_cf[L, 1] = f.MARK_EXECUTING, S[f.E0] if on else BIG
                    lv_cf[L, 2], lv_cf[L, 3] = f.MARK_STAY_MOVED, S[f.E0] if on else BIG
                else:
                    t = max(S[f.F], 0)
                    on = S[f.AD] >= 0 and core.live(S, t)
                    lv_cf[L, 0], lv_cf[L, 1] = f.MARK_APPEALED, t if on else BIG
            else:
                nb = questions(st, k, tk, d, day, g, qb, 0, L)
                if d == w.DEC_RULING and m == 4:  # the remittitur's election, put with the reduction
                    nb = emit(S, qb, nb, Q_REMITTITUR, 0, tk.reduced[S[w.VERDICT_IX], 0], -1, day, L)
                set_nans(qb, nb, m)
                walk_read = d == w.DEC_SETTLE0 + 7 or d == w.DEC_STAY0 + 1 or d == w.DEC_ENFORCE  # 'unstayed'
                for q in range(nb):
                    watch_read(st, tk, lv_dec, lv_watch, lv_read, lv_cf, L, qb[q, QN], day, walk_read)
                    nq = take(st, k, tk, ws, qb[q], out, cls_out, dig, nq, ints, floats, code)
            book(st, k, tk, ws, d, day, lv_ans[L, 0], True)
            lv_next[L] = 1
            L += 1
            deepest = max(deepest, L)
            continue
        leaves += 1
        leaf_range(st, k, ev_lo, ev_hi)
        while L > 0:  # back to the deepest question with an answer left
            lvl = L - 1
            if lv_watch[lvl] == W_WALKING and lv_next[lvl] == 1:
                if lv_read[lvl]:  # read below: asked; its yes answer is walked too
                    lv_watch[lvl] = W_ASKED
                    if nr < reads.size:
                        reads[nr] = watch_key(sk.S[lvl], lv_dec[lvl])
                    nr += 1
                    restore(st, sk, lvl)
                    nb = questions(st, k, tk, lv_dec[lvl], lv_day[lvl], -1, qb, 0, lvl)
                    set_nans(qb, nb, 2)
                    for q in range(nb):
                        watch_read(st, tk, lv_dec, lv_watch, lv_read, lv_cf, lvl, qb[q, QN], lv_day[lvl], False)
                        nq = take(st, k, tk, ws, qb[q], out, cls_out, dig, nq, ints, floats, code)
                else:
                    lv_watch[lvl] = W_NONE
                    lv_next[lvl] = lv_n[lvl] = 1
            if lv_next[lvl] < lv_n[lvl]:
                restore(st, sk, lvl)
                book(st, k, tk, ws, lv_dec[lvl], lv_day[lvl], lv_ans[lvl, lv_next[lvl]], True)
                lv_next[lvl] += 1
                break
            L -= 1
        if L == 0:
            return nq, nodes, leaves, deepest, nr


# --- a history of the Python walk, followed through the dated walk (the check) -----------------------------------------
NO_STEP, UNASKED_STEP = -1, -3  # a script's entries: the Python has no step for the decision / a step with no edge
# the findings of a followed history (column 0 of `found`)
F_EXTRA, F_MISSING, F_NOT_OFFERED, F_ASKED = 1, 2, 3, 4


@njit(cache=True)
def scripted(st, script, n1, d):
    """The history's answer to decision d (a stay's approval by its phase; an offering's close by its occasion)."""
    S = st.S
    if d >= w.DEC_APPROVAL0 and d < w.DEC_REG_POST:
        return script[w.DEC_APPROVAL0 + stay_phase(S, d - w.DEC_APPROVAL0)]
    if d >= w.DEC_N1_0:
        return n1[st.offers[d - w.DEC_N1_0, e.O_OCCASION]]
    return script[d]


@njit(cache=True)
def follow(st, k, wk, tk, ws, script, n1, found, qout, iout, fout, cout):
    """Walk the draw along a history's answers (`script` by decision id; the approvals at DEC_APPROVAL0 + phase;
    `n1` by occasion): at each question the scripted answer. found[i]: (finding, decision, day, the scripted answer):
    F_ASKED for each question asked as scripted; F_EXTRA a question the history does not ask on the draw; F_MISSING
    a decision the history asks that does not arise here; F_NOT_OFFERED a scripted answer not open here. Returns how
    many findings. Each question asked: its row, record and class (qout, iout, fout, cout; the count is the last
    finding's... see `pass1.follow`)."""
    S = st.S
    walk.init(S)
    ans = np.zeros(MAX_ANS, np.int64)
    qb = np.zeros((4, QCOLS), np.int64)
    nf, nq = 0, 0
    while True:
        agenda.refresh(st, k)
        kind, which, day = frontier(st, k, wk)
        if kind == w.IT_NONE:
            return nf
        if day < S[w.COMMITTED]:
            raise ValueError("an item inserted before the committed frontier")
        S[w.COMMITTED] = day
        if kind != w.IT_DECISION:
            agenda.until(st, k, day + 1)
            continue
        agenda.until(st, k, day)
        agenda.refresh(st, k)
        m, asked, g = options(st, k, wk, tk, ws, which, day, ans)
        want = scripted(st, script, n1, which)
        if nf + 1 >= found.shape[0]:
            raise ValueError("findings exhausted")
        if not asked:
            if want >= 0:
                found[nf, 0], found[nf, 1], found[nf, 2], found[nf, 3] = F_MISSING, which, day, want
                nf += 1
            book(st, k, tk, ws, which, day, ans[0], False)
            if which < w.N_DEC and S[w.ANS0 + which] == w.OPEN:
                S[w.ANS0 + which] = w.ABSENT
            continue
        pick = -1
        if want >= 0:
            for j in range(m):
                if ans[j] == want:
                    pick = j
        if pick < 0:
            found[nf, 0] = F_EXTRA if want < 0 else F_NOT_OFFERED
            found[nf, 1], found[nf, 2], found[nf, 3] = which, day, want
            nf += 1
            pick = m - 1  # the quiet answer (each constructor's last), so the walk goes on
        else:
            found[nf, 0], found[nf, 1], found[nf, 2], found[nf, 3] = F_ASKED, which, day, want
            nf += 1
        nb = questions(st, k, tk, which, day, g, qb, 0, 0)
        if which == w.DEC_RULING and m == 4:
            nb = emit(S, qb, nb, Q_REMITTITUR, 0, tk.reduced[S[w.VERDICT_IX], 0], -1, day, 0)
        set_nans(qb, nb, m)
        for q in range(nb):
            if nq >= qout.shape[0]:
                raise ValueError("question buffer exhausted")
            qout[nq] = qb[q]
            qrecord.question(st, k, ws.scratch, qb[q], iout[nq], fout[nq])
            klass(tk, qb[q], iout[nq], cout[nq])
            nq += 1
        found[found.shape[0] - 1, 0] = nq
        book(st, k, tk, ws, which, day, ans[pick], True)



# --- pass 2: the walk weighted by the judge's answers -------------------------------------------------------------

@njit(cache=True)
def decision_probs(d, m, ans, nb, P, vprob, out):
    """The probability of each of decision d's answers (`ans[:m]`, in `options`' order) from its questions' answer
    distributions P[q] (each in its question's own order, the first answer 'yes' for a yes-or-no question)."""
    if d < w.DEC_VERDICT and m == 2:  # a settlement: offered and accepted
        out[0] = P[0, 0] * P[1, 0]
        out[1] = 1.0 - out[0]
    elif d == w.DEC_VERDICT:
        for j in range(m):
            out[j] = vprob[ans[j]]
    elif d == w.DEC_RULING and m == 4:  # unchanged; reduced, the remittitur accepted or refused; set aside
        out[0], out[1], out[2], out[3] = P[0, 0], P[0, 1] * P[1, 0], P[0, 1] * P[1, 1], P[0, 2]
    elif d == w.DEC_NONPAYMENT:  # the issuer files; else the holders file; else the notes are due
        a5, h3 = P[0, 0], P[1, 0]
        out[0], out[1], out[2] = a5, (1.0 - a5) * h3, (1.0 - a5) * (1.0 - h3)
    else:
        for j in range(m):
            out[j] = P[0, j]
    total = 0.0
    for j in range(m):
        if not (out[j] >= 0.0):
            raise ValueError("a negative or unknown probability")
        total += out[j]
    if abs(total - 1.0) > 1e-9:
        raise ValueError("a decision's answer probabilities do not sum to one")


@njit(cache=True)
def evaluate(st, k, wk, tk, ws, sk, lk, qhash, qprob, vprob, forced, day_acc, sc, h_cash, h_coll, h_head, ev_lo,
             ev_hi):
    """Pass 2 on the draw: every history walked, its probability the product of its answers' (`qprob` rows by
    question identity `qhash`, sorted; the verdict's classes `vprob`), each leaf's figures added weighted by it
    (`leaf.accumulate`). Every answer is walked, a zero-probability one too (it carries no weight here; what a changed
    judgment reads is walked the same way). Returns (the total weight, which is one, and the leaves)."""
    S = st.S
    walk.init(S)
    lv_dec = np.zeros(MAX_DEPTH, np.int64)
    lv_day = np.zeros(MAX_DEPTH, np.int64)
    lv_n = np.zeros(MAX_DEPTH, np.int64)
    lv_next = np.zeros(MAX_DEPTH, np.int64)
    lv_ans = np.zeros((MAX_DEPTH, MAX_ANS), np.int64)
    lv_p = np.zeros((MAX_DEPTH, MAX_ANS), np.float64)
    lv_w = np.zeros(MAX_DEPTH, np.float64)
    ans = np.zeros(MAX_ANS, np.int64)
    qb = np.zeros((4, QCOLS), np.int64)
    P = np.zeros((4, MAX_ANS), np.float64)
    ints = np.zeros(fr.NI, np.int64)
    floats = np.zeros(fr.NF, np.float64)
    code = np.zeros(fr.NC, np.int64)
    L, weight, total, leaves = 0, 1.0, 0.0, 0
    while True:
        d, day, m, g = next_question(st, k, wk, tk, ws, ans)
        if d >= 0:
            if L >= MAX_DEPTH:
                raise ValueError("the walk is deeper than its stack")
            save(st, sk, L)
            lv_dec[L], lv_day[L], lv_w[L] = d, day, weight
            wkey = watch_key(S, d) if watched(st, k, d) else np.int64(-1)
            if wkey >= 0 and not (forced.size and forced[min(np.searchsorted(forced, wkey), forced.size - 1)] == wkey):
                lv_n[L] = 1  # asked nowhere: its no answer, with certainty (QUESTIONS §1 Depth)
                lv_ans[L, 0], lv_p[L, 0] = a.NO, 1.0
            else:
                nb = questions(st, k, tk, d, day, g, qb, 0, L)
                if d == w.DEC_RULING and m == 4:
                    nb = emit(S, qb, nb, Q_REMITTITUR, 0, tk.reduced[S[w.VERDICT_IX], 0], -1, day, L)
                set_nans(qb, nb, m)
                for q in range(nb):
                    if qb[q, QN] == Q_VERDICT:
                        continue
                    qrecord.question(st, k, ws.scratch, qb[q], ints, floats)
                    klass(tk, qb[q], ints, code)
                    h = qid(qb[q], code)
                    ix = np.searchsorted(qhash, h)
                    if ix >= qhash.size or qhash[ix] != h:
                        raise ValueError("a question pass 1 did not find")
                    for j in range(qb[q, QNANS]):
                        P[q, j] = qprob[ix, j]
                lv_n[L] = m
                lv_ans[L, :m] = ans[:m]
                decision_probs(d, m, ans, nb, P, vprob, lv_p[L])
            book(st, k, tk, ws, d, day, lv_ans[L, 0], True)
            weight = lv_w[L] * lv_p[L, 0]
            lv_next[L] = 1
            L += 1
            continue
        leaves += 1
        total += weight
        if weight > 0.0:
            leaf.accumulate(st, k, lk, weight, day_acc, sc, h_cash, h_coll, h_head, ev_lo, ev_hi)
        while L > 0:
            lvl = L - 1
            if lv_next[lvl] < lv_n[lvl]:
                restore(st, sk, lvl)
                j = lv_next[lvl]
                book(st, k, tk, ws, lv_dec[lvl], lv_day[lvl], lv_ans[lvl, j], True)
                weight = lv_w[lvl] * lv_p[lvl, j]
                lv_next[lvl] += 1
                break
            L -= 1
        if L == 0:
            return total, leaves

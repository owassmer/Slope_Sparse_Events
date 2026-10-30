"""Question facts read before the answer, on one draw, entirely in nopython.

Caller advances the agenda and prepares a non-waiting step before calling record.
The scratch state is distinct from st. Projections copy this state, discard waiting
answers and undecided court orders, and advance deterministic items only; no history
replay and no own answer is read. Composite answers already booked by the current
scheduler remain an upstream limitation: this reader cannot undo them.
"""
import numpy as np
from numba import njit

from app.exact import agenda, cash, core, equity, notes
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import fields_equity as e
from app.exact import fields_notes as n
from app.exact import fields_record as r


@njit(cache=True)
def project(st, k, scratch, decision, payment, processing):
    # The current follow scheduler still books these composite answers at their
    # origin. They cannot be projected as commitments of an earlier question.
    # Splitting them is the scheduler owner's work; fail rather than leak them.
    for j in range(st.S[e.N_OFFERS]):
        o = st.offers[j]
        if o[e.O_BOOKED] and o[e.O_CLOSE] > decision:
            raise ValueError('projection requires offering outcome booked at its own close date')
    if decision < st.S[f.EARLY_REGISTRATION] < f.BIG:
        raise ValueError('projection requires registration answer booked at its own order date')
    if decision < st.S[f.PETITION] < f.BIG:
        raise ValueError('projection requires petition answer booked at its own decision date')
    agenda.copy_into(scratch, st)
    scratch.S[a.N_WAIT] = 0
    # No future court answer is a commitment at the proposal date.
    for j in range(scratch.S[a.N_STAYS]):
        row = scratch.stays[j]
        if not row[a.T_DONE]:
            row[a.T_APPROVED] = 0
            row[a.T_DONE] = 1
    agenda.until(scratch, k, payment if payment < k.N else -1)
    agenda.refresh(scratch, k)
    t = min(max(payment, 0), k.N - 1)
    balance = (cash.processing_balance(scratch.D, scratch.ln, payment) if processing
               else cash.cash_at(scratch.ln, payment))
    need = k.need[t]
    collateral = agenda.bond_collateral(scratch, k, payment)
    live = core.live(scratch.S, payment) and payment < k.N
    offer = max(balance - need, 0) if live and balance - need < collateral else 0
    return (payment, balance, need, np.int64(agenda.owed(scratch, k, payment, False)), collateral, offer, int(live))


@njit(cache=True)
def inside(day, N):
    return day if 0 <= day < N else f.BIG


@njit(cache=True)
def record(st, k, day, node, ctx, scratch, out, floats):
    """Write INT_FIELDS/FLOAT_FIELDS. node/ctx use fields_agenda's vocabulary.

    Returns nothing; buffers are owned by the caller and may be depth-stack rows.
    The result is already as_of(day), including dated marks and notes-route status.
    """
    agenda.refresh(st, k)
    S, D = st.S, st.D
    out[:] = 0
    t = min(max(day, 0), k.N - 1)
    out[r.DAY] = day
    out[r.CASH] = cash.processing_balance(D, st.ln, day)
    out[r.OWED] = agenda.owed(st, k, day, False)
    out[r.COLLATERAL] = agenda.collateral_before(st, k, day)
    out[r.PETITION] = S[f.PETITION] if S[f.PETITION] <= day else -1
    out[r.CASH_END] = max(cash.cash_at(st.ln, day), 0)
    # a judgment the clerk enters after the day is not yet a fact of it, nor are the periods its entry starts
    entered = notes.has_judgment(S, k.nc) and S[f.E_IX] <= day
    out[r.ENTRY] = S[f.E_IX] if entered else f.BIG
    filed = S[f.MOTIONS_FILED] <= day
    out[r.RULING] = S[f.F] if filed else f.BIG
    out[r.MOTIONS_FILED] = S[f.MOTIONS_FILED] if filed else f.BIG
    out[r.MOTIONS_DEADLINE] = core.motions_deadline(S, k.frcp, k.court) if entered else f.BIG
    # the deadline to appeal as the walk fixes it (FRAP 4(a)(1)(A), on the next court day: 26(a)(1)(C)), known
    # beyond the analysis period too: the class and the question read the walk's date, never a recomputed one
    out[r.ENTRY_APPEAL_DEADLINE] = core.court_day(k.court, S[f.E_IX] + k.frap) if entered else f.BIG
    out[r.DELISTED] = S[f.DELISTED] if S[f.DELISTED] <= day else f.BIG
    out[r.STAYED_FROM] = S[f.STAYED_FROM] if S[f.STAYED_FROM] <= day else f.BIG
    latest, status = -1, 0
    for j in range(S[a.N_STAYS]):
        stay = st.stays[j]
        motion = stay[a.T_MOTION]
        if stay[a.T_MOVED] and motion <= day and motion >= latest:
            latest = motion
            # An order not yet processed is pending, even on its decision day.
            status = 1 if not stay[a.T_DONE] else (3 if stay[a.T_APPROVED] else 2)
    out[r.STAY_STATUS] = 4 if latest >= 0 and out[r.OWED] <= 0 else status
    out[r.STANDING] = notes.judgment_standing(S, st.takes, k.nc, day)
    out[r.ENTERED] = notes.judgment_amount_entered(S, k.nc, k.N)
    out[r.BAND_LO], out[r.BAND_HI] = S[f.BAND_LO], S[f.BAND_HI]
    out[r.DEFAULT_AVAILABLE] = notes.default_available_day(S, st.takes, k.nc, k.N)
    due = S[f.MARK_NOTES_DUE]
    out[r.ROUTE_DAYS] = notes.holder_route_days_path(S, k.nc, S[a.CASH_NONPAYMENT]) if due <= day else -1
    out[r.REMITTED] = S[f.REMITTED]
    out[r.LISTING] = (3 if day >= S[f.DELISTED] else 2 if day >= S[f.SUSPENDED]
                      else 1 if day >= S[f.HEARING_REQUESTED] else 0)
    out[r.ATM] = equity.atm_to_date(S, D, day)
    out[r.LEDGER] = equity.ledger_left(S, st.offers, st.atm, k.sched, k.ec, day)
    floats[0] = equity.share_price_on(st.P, day)
    out[r.OFFERING_PENDING] = equity.offering_pending_on(S, st.offers, day)
    out[r.OFFERING_BOUND_REACHED] = equity.offering_bound_reached(S, st.offers, k.ec, day, k.N)
    lower = k.lower[day] if 0 <= day < k.N else 0
    lv = agenda.pending_levy(st)
    out[r.OFFER_SHORTFALL] = equity.offer_shortfall(S, st.takes, k.ec, day, lv, st.ln.outputs[0], k.need,
                                                  k.ops_in, D[f.D_KIND_INFLOW], st.lockbuf, D[f.D_KIND_LEVY])
    available = equity.offer_available(S, st.P, st.takes, st.offers, st.atm, k.sched, k.ec, day, lower, lv,
                                       st.ln.outputs[0], k.need, k.ops_in, D[f.D_KIND_INFLOW], st.lockbuf,
                                       D[f.D_KIND_LEVY])
    out[r.OFFER_AVAILABLE] = available
    out[r.HAS_GROUPS] = node == a.RESPONSE or node == a.CASH_FLOOR or node == a.CASH_OUT
    # Chain.advance resets the step's raise_offer; only response/distress
    # steps set it. The general availability is separately in sit.
    out[r.RAISE_OFFER] = available if out[r.HAS_GROUPS] or agenda.waits(node, ctx) else 0
    out[r.GROUPS] = -1
    if out[r.HAS_GROUPS]:
        out[r.GROUPS] = equity.option_group(S, st.P, st.takes, st.offers, st.atm, k.sched, k.ec,
                                           node == a.RESPONSE, day, lower, lv, st.ln.outputs[0], k.need,
                                           k.ops_in, D[f.D_KIND_INFLOW], st.lockbuf, D[f.D_KIND_LEVY])
    terms = equity.offering_terms(S, st.P, st.offers, st.atm, k.sched, k.ec, day, lower)
    for j in range(7):
        out[r.TERM0 + j] = terms[j]
    count = 0
    for j in range(S[e.N_OFFERS]):
        o = st.offers[j]
        if node == a.OFFERING and o[e.O_OCCASION] == ctx:
            for q, col in enumerate((e.O_GROSS, e.O_COSTS, e.O_NET, e.O_PRICE, e.O_SHARES, e.O_DAYS, e.O_SHELF)):
                out[r.TERM0 + q] = o[col]
        if o[e.O_BOOKED]:  # Chain.offerings contains completed attempts only
            out[r.OFFERS0 + 3 * count] = o[e.O_INIT] if o[e.O_INIT] <= day else f.BIG
            out[r.OFFERS0 + 3 * count + 1] = o[e.O_CLOSE] if o[e.O_INIT] <= day else f.BIG
            out[r.OFFERS0 + 3 * count + 2] = o[e.O_CLOSED] and o[e.O_CLOSE] <= day
            count += 1
    out[r.OFFERING_COUNT] = count
    out[r.NOTES_DUE_DAY] = due if due <= day else f.BIG
    out[r.NOTES_DUE_HOW] = S[n.NOTES_DUE_HOW] if due <= day else 0
    for j in range(5):
        out[r.ARREAR0 + j] = st.ln.outputs[9 + j, t] if 0 <= day < k.N else 0
    out[r.FIRST_UNPAID] = S[a.CASH_FIRST_UNPAID] if S[a.CASH_FIRST_UNPAID] <= day else f.BIG
    out[r.NONPAYMENT_DAY] = S[a.CASH_NONPAYMENT] if S[a.CASH_NONPAYMENT] <= day else f.BIG
    out[r.HAS_DECLARATION] = node == a.JDEFAULT or node == a.DELISTING
    out[r.DECLARATION_DEADLINE] = day + k.nc.notice if day < k.N else f.BIG
    for j in range(len(r.MARKS)):
        value = S[f.MARK0 + j]
        out[r.MARK0 + j] = value if value <= day else f.BIG
    out[r.TRIGGER0:r.TERM0] = f.BIG
    for j in range(2):
        ripe, on = notes.judgment_default(S, st.takes, k.nc, j)
        out[r.TRIGGER0 + j] = inside(ripe, k.N) if on else f.BIG
    out[r.TRIGGER0 + 2] = ((S[f.AD] if S[f.F] <= day else f.BIG) if filed else out[r.ENTRY_APPEAL_DEADLINE])
    coupon = f.BIG
    for j in range(S[n.N_COUPONS]):
        c = st.coupons[j]
        if c[n.C_DAY] < coupon:
            coupon = c[n.C_DAY]
            out[r.COUPON_CASH] = c[n.C_CASH]
    out[r.TRIGGER0 + 3] = inside(coupon, k.N)
    out[r.HAS_COUPON_CASH] = coupon < f.BIG and k.nc.coupon_mode == n.PRICED
    out[r.TRIGGER0 + 4] = inside(k.ldates[n.DEADLINE], k.N)
    if out[r.DELISTED] < k.N:
        out[r.TRIGGER0 + 5] = inside(notes.repurchase_day(k.rep_cal, out[r.DELISTED]), k.N)
    if due <= day:
        out[r.TRIGGER0 + 6] = inside(due + k.nc.route, k.N)
    if node == a.SETTLE:
        start, end = agenda.settlement_window(st, k, ctx)
        payment = end if k.settle_end else start + k.settle_lag
        p = project(st, k, scratch, day, payment, False)
        cap = k.claimed if ctx == 0 else p[3]
        out[r.SETTLE_OFFER] = min(max(p[1] - p[2], 0), cap) if p[6] and start < k.N else 0
        out[r.HAS_PRICING] = 1
        out[r.PRICING0], out[r.PRICING0 + 1], out[r.PRICING0 + 2] = payment, p[1], p[2]
    if node == a.STAY:
        lag = k.DC[core.DC_LAG_STAY_I1] if ctx == 0 else k.DC[core.DC_LAG_STAY_POST]
        p = project(st, k, scratch, day, day + k.briefing + lag, True)
        out[r.HAS_SECURITY] = 1
        for j in range(7):
            out[r.SECURITY0 + j] = p[j]
        out[r.STAY_OFFER] = p[5]


@njit(cache=True)
def situation_class(row, out, appeal=True, stay=False, context=0, group=False):
    """Same partition as forecast.situation_class; context is a prepared vocabulary id.

    Context 0 denotes no suffix. Other ids refer to the caller's exact context
    strings, not hashes (no collision/coarsening). Option groups stay distinct.
    """
    day = row[r.DAY]
    pet = f.BIG if row[r.PETITION] < 0 else row[r.PETITION]
    out[0] = day < f.BIG and day < pet
    out[1], out[2], out[3] = row[r.BAND_LO], row[r.BAND_HI], row[r.STANDING]
    out[4] = (2 if row[r.NOTES_DUE_DAY] <= day else 1 if min(row[r.DEFAULT_AVAILABLE], row[r.DELISTED],
                                                                  row[r.NONPAYMENT_DAY]) <= day else 0)
    out[5] = row[r.LISTING]
    out[6] = (5 if row[r.LISTING] == 3 else 4 if pet <= day else 3 if row[r.OFFERING_PENDING]
              else 2 if row[r.LEDGER] <= 0 else 1 if row[r.OFFER_AVAILABLE] <= 0 else 0)
    out[7] = row[r.OWED] > 0 and row[r.CASH] >= row[r.OWED]
    deadline = row[r.TRIGGER0 + 2]
    filed = row[r.MARK0 + f.MARK_APPEALED - f.MARK0]
    out[8] = ((2 if day > deadline else 1) if deadline < f.BIG else 0) + 3 * (filed < f.BIG and filed <= day)
    if not appeal:
        out[8] = -1
    out[9] = row[r.STAY_STATUS] if stay else -1
    out[10] = context
    out[11] = row[r.GROUPS] if group else -2

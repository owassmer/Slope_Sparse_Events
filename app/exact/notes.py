"""Notes and listing on one draw, operation-for-operation with events.Chain.

`eq` is (P, takes, parts, offers, atm, schedule, equity_config, scratch).
Tables are preallocated; equity receives only the used coupon rows. Cash reads
are caller inputs, not forecasts made here. In book_default, nonpayment must be
read AFTER marking acceleration and refunding coupons, before the petition;
prepare_default exposes that boundary for the compiled evaluator's processor.
"""
import numpy as np
from numba import njit

from app.exact import core, equity
from app.exact import fields as f
from app.exact import fields_notes as n


@njit(cache=True)
def has_judgment(S, c):
    return c.has_dispute and (c.recorded_judgment or (c.pending and S[f.ENTERED] > 0))


@njit(cache=True)
def standing_amount(S, c, day):
    amount = S[f.ENTERED]
    if S[f.CLS_AMOUNT] != f.NONE and day >= S[f.F]:
        amount = S[f.CLS_AMOUNT]
    if c.pending and day < S[f.E_IX]:
        amount = 0
    return amount if has_judgment(S, c) else 0


@njit(cache=True)
def judgment_amount_entered(S, c, N):
    entry = S[f.E_IX] if c.pending else c.recorded_entry
    return S[f.ENTERED] if has_judgment(S, c) and entry < N else 0


@njit(cache=True)
def judgment_standing(S, takes, c, day):
    amount = standing_amount(S, c, day)
    entered = has_judgment(S, c) and day >= (S[f.E_IX] if c.pending else c.recorded_entry)
    out = 1
    if S[f.CLS_AMOUNT] != f.NONE and S[f.CLS_AMOUNT] != S[f.ENTERED] and day >= S[f.F] and amount > 0:
        out = 4
    taken = core.taken_before(S, takes, day) if c.pending else S[f.TAKEN]
    if taken > 0:
        out = 3
    if S[f.STAYED_FROM] <= day:
        out = 2
    if entered and amount == 0:
        out = 5
    if S[f.MARK_PAID] <= day:
        out = 6
    if S[f.MARK_SETTLED] <= day:
        out = 7
    return out if entered else (7 if S[f.MARK_SETTLED] <= day else 0)


@njit(cache=True)
def _default_regular(S, takes, c, ctx):
    if not c.default_days or not c.has_dispute:
        return f.BIG, False
    if ctx == n.CTX_I1:
        ripe = S[f.EX_IX] + c.default_days
        amount = standing_amount(S, c, ripe) if c.pending else S[f.ENTERED]
        on = c.reading != n.READ_POST and has_judgment(S, c)
    else:
        ripe = max(S[f.A], S[f.EX_IX]) + c.default_days
        amount = S[f.ENTERED] if S[f.CLS_AMOUNT] == f.NONE else S[f.CLS_AMOUNT]
        on = c.reading != n.READ_ENTERED and S[f.A] >= 0 and not S[f.JD_ACTED]
    on = (on and amount - c.insured > c.threshold and S[f.STAYED_FROM] > ripe
          and core.live(S, ripe) and core.owed_at(S, takes, c.bps, c.pending, ripe, False) > 0)
    return ripe, on


@njit(cache=True)
def _default_at_ruling(S, takes, c):
    day = max(S[f.F], 0)
    changed = c.pending and S[f.CLS_AMOUNT] != f.NONE and 0 < S[f.CLS_AMOUNT] != S[f.ENTERED]
    if c.reading != n.READ_ENTERED or not changed:
        return f.BIG, False
    ripe, on = _default_regular(S, takes, c, n.CTX_I1)
    on = (on and ripe <= day and not S[f.JD_ACTED] and S[f.F] >= 0
          and S[f.CLS_AMOUNT] - c.insured > c.threshold and S[f.STAYED_FROM] > day
          and core.live(S, day) and core.owed_at(S, takes, c.bps, c.pending, day, False) > 0
          and S[f.MARK_NOTES_DUE] > day)
    return day, on


@njit(cache=True)
def judgment_default(S, takes, c, ctx):
    if not c.default_days or not c.has_dispute:
        return f.BIG, False
    if ctx == n.CTX_RULING:
        return _default_at_ruling(S, takes, c)
    return _default_regular(S, takes, c, ctx)


@njit(cache=True)
def default_available_day(S, takes, c, N):
    out = f.BIG
    for ctx in (n.CTX_I1, n.CTX_POST):
        ripe, on = judgment_default(S, takes, c, ctx)
        if on:
            out = min(out, ripe)
    return out if out < N else f.BIG


@njit(cache=True)
def holder_route_days(c):
    return c.route


@njit(cache=True)
def holder_route_days_path(S, c, nonpayment_day):
    return 0 if c.daily and nonpayment_day <= S[f.MARK_NOTES_DUE] else c.route


@njit(cache=True)
def mark(S, day, where, how, N):
    """Chain.mark('notes_due'), including strict earlier-day ownership of how."""
    if where and 0 <= day < N and day < S[f.MARK_NOTES_DUE]:
        S[n.NOTES_DUE_HOW] = how
    core.mark(S, f.MARK_NOTES_DUE, day, where, N)


@njit(cache=True)
def coupon_cash_cents(P, row, c):
    if c.coupon_mode == n.CASH:
        return c.coupon_amount
    if c.coupon_mode == n.SHARES:
        return 0
    if c.coupon_mode == n.FIXED:
        covered = c.coupon_capacity * c.coupon_price * c.coupon_ratio * c.coupon_bps // 10_000
        return c.coupon_amount - min(c.coupon_amount, covered)
    # Same pairwise numpy sum as equity.coupon_rebook / mean(axis=1).
    a0 = equity.share_price_on(P, row[3]) + equity.share_price_on(P, row[4])
    a1 = equity.share_price_on(P, row[5]) + equity.share_price_on(P, row[6])
    a2 = equity.share_price_on(P, row[7]) + equity.share_price_on(P, row[8])
    a3 = equity.share_price_on(P, row[9]) + equity.share_price_on(P, row[10])
    px = (((a0 + a1) + (a2 + a3)) + equity.share_price_on(P, row[11]) + equity.share_price_on(P, row[12])) / 10
    value = px * c.coupon_ratio * c.coupon_bps / 10_000
    shares = min(np.floor(c.coupon_amount / value), c.coupon_capacity)
    return np.int64(max(c.coupon_amount - np.rint(shares * value), 0))


@njit(cache=True)
def coupon_when_due(S, D, coupons):
    for k in range(S[n.N_COUPONS]):
        row = coupons[k]
        gone = row[n.C_KEPT] and S[f.MARK_NOTES_DUE] <= row[n.C_DAY]
        core.pay(S, D, row[n.C_DAY], row[n.C_CASH] if gone else 0, f.D_KIND_NOTES_INTEREST, -1, 0)
        if gone:
            row[n.C_KEPT] = 0


@njit(cache=True)
def _rebook(S, D, coupons, c, eq):
    if c.equity:
        P, takes, parts, offers, atm, schedule, ec, scratch = eq
        equity.atm_rebook(S, D, P, takes, parts, offers, atm, schedule, coupons[:S[n.N_COUPONS]], ec, scratch)


@njit(cache=True)
def _petition(S, D, coupons, c, eq, day, on):
    if core.petition(S, day, on, c.notes_cause, c.petition_lag, D.shape[1]):
        _rebook(S, D, coupons, c, eq)


@njit(cache=True)
def instrument_cash(S, D, coupons, c, eq, schedule, legal):
    P, takes, parts, offers, atm, sales, ec, scratch = eq
    if c.coupon_amount:
        if c.coupon_mode == n.PRICED:
            equity.reprice(S, P, takes, parts, ec)
        for j in range(len(schedule)):
            row = schedule[j]
            day = row[n.C_DAY]
            cash = coupon_cash_cents(P, row, c)
            core.pay(S, D, day, -cash, f.D_KIND_NOTES_INTEREST, f.INCURRED_NOTES_INTEREST, -f.BIG)
            if 0 <= day < D.shape[1] and (c.coupon_mode == n.PRICED or cash != 0):
                k = S[n.N_COUPONS]
                if k >= len(coupons):
                    raise ValueError('coupon table exhausted')
                coupons[k] = row
                coupons[k, n.C_CASH], coupons[k, n.C_KEPT] = cash, 1
                S[n.N_COUPONS] += 1
    if c.ordinary:
        core.resolve(S, D, legal, 0, True)
    _rebook(S, D, coupons, c, eq)
    if c.chips:
        N = D.shape[1]
        for day in range(N):
            amount = c.chips // N + (c.chips - (c.chips // N) * N if day == N - 1 else 0)
            D[f.D_CASH, day] += amount
            D[f.D_KIND_INFLOW, day] += amount


@njit(cache=True)
def prepare_default(S, D, coupons, takes, c, ctx, branch, rows):
    """First half of book_default; caller can now read the updated cash processor."""
    ripe, on = judgment_default(S, takes, c, ctx)
    on = on and rows
    if branch in (n.YES, n.HOLDERS_FILE, n.ACCELERATED):
        mark(S, ripe + c.notice, on, n.DECLARED, D.shape[1])
        if ctx in (n.CTX_I1, n.CTX_RULING):
            S[f.JD_ACTED] = S[f.JD_ACTED] or on
    coupon_when_due(S, D, coupons)
    return ripe, on


@njit(cache=True)
def finish_default(S, D, coupons, c, eq, ripe, on, branch, nonpayment_day):
    accel = ripe + c.notice
    if branch == n.YES:
        _petition(S, D, coupons, c, eq, accel, on)
    elif branch == n.HOLDERS_FILE:
        route = holder_route_days_path(S, c, nonpayment_day) if c.pending else c.route
        _petition(S, D, coupons, c, eq, accel + route, on)
    return ripe if on else f.BIG


@njit(cache=True)
def book_default(S, D, coupons, c, eq, ctx, branch, rows, nonpayment_day):
    ripe, on = prepare_default(S, D, coupons, eq[1], c, ctx, branch, rows)
    return finish_default(S, D, coupons, c, eq, ripe, on, branch, nonpayment_day)


@njit(cache=True)
def notes_due_date(S, c, N, holders, nonpayment_day):
    core.mark(S, f.MARK_RULED, S[f.F], True, N)
    due = S[f.MARK_NOTES_DUE]
    route = holder_route_days_path(S, c, nonpayment_day) if c.pending else c.route
    return due + (route if holders else 0) if due < f.BIG else f.BIG


@njit(cache=True)
def nonpayment_day(S, day):
    """Chain.distress_day('nonpayment'); processor-supplied trigger, not a cash read."""
    return day if day < f.BIG and S[f.MARK_NOTES_DUE] > day else f.BIG


@njit(cache=True)
def decide_nonpayment(S, D, coupons, c, eq, day, branch):
    """Chain.decide_distress('nonpayment'): 0=due, 1=petition, 2=holders_file."""
    on = day < D.shape[1]
    mark(S, day, on, n.AUTOMATIC_J, D.shape[1])
    coupon_when_due(S, D, coupons)
    if branch in (1, 2):
        _petition(S, D, coupons, c, eq, day, on)
    elif branch != 0:
        raise ValueError('No general nonpayment branch')
    return 0


@njit(cache=True)
def listing_dates(dates, key):
    return dates[key]


@njit(cache=True)
def repurchase_day(calendar, day):
    for k in range(len(calendar)):
        if calendar[k, 0] == day:
            return calendar[k, 1]
    raise ValueError('repurchase day absent from prepared calendar')


@njit(cache=True)
def listing_step(S, D, coupons, c, eq, dates, date_only, ctx, branch):
    """Equity listing: ctx 0=compliance, 1=hearing; branch 0=compliant, 1=hearing, 2=suspended."""
    pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
    if date_only:
        day = dates[n.DEADLINE] if ctx == 0 else dates[n.HEARING]
        return day if day < pet else f.BIG
    on = dates[n.DEADLINE] < pet
    if branch == 1:
        if dates[n.PANEL] < D.shape[1]:
            raise NotImplementedError('the panel decides inside the period: its decision is not modeled')
        if on:
            S[f.HEARING_REQUESTED] = dates[n.HEARING]
    elif branch == 2:
        if on:
            S[f.SUSPENDED] = dates[n.SUSPENSION]
            S[f.DELISTED] = dates[n.DELIST_SUSP]
        core.mark(S, f.MARK_DELISTED, S[f.DELISTED], True, D.shape[1])
        _rebook(S, D, coupons, c, eq)
    elif branch != 0:
        raise ValueError('No listing branch')
    return dates[n.DEADLINE] if on else f.BIG


@njit(cache=True)
def listing_legacy(S, N, dates, date_only, ctx, delist_key):
    """Non-equity Chain.step listing; ctx is LISTING_KEYS index or -1 ('kept')."""
    gate = dates[n.HEARING] if ctx == -1 else dates[n.VOTE_CALL]
    on = S[f.MARK_NOTES_DUE] > gate
    if date_only:
        return dates[n.HEARING if ctx == -1 else ctx] if on else f.BIG
    if delist_key >= 0:
        S[f.DELISTED] = dates[delist_key] if on else f.BIG
        core.mark(S, f.MARK_DELISTED, S[f.DELISTED], True, N)
        return (gate if ctx == -1 else dates[n.DETERMINATION]) if on else f.BIG
    return gate if on else f.BIG


@njit(cache=True)
def delisting_notes(S, D, coupons, c, eq, calendar, branch):
    """Chain.step('delisting_notes'): 0=no, 1=accelerated, 2=repurchase_unpaid,
    3=petition_delist, 4=petition_delist_holders, 5=petition_repurchase, 6=petition_repurchase_holders.
    Python marks repurchases as 'declared', not 'repurchase'; preserve that definition.
    """
    day = S[f.DELISTED]
    pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
    alive = day < pet if c.pending or c.ordinary else core.live(S, day)
    on = alive and S[f.MARK_NOTES_DUE] > day and day < D.shape[1]
    accel = day + c.notice
    rep = repurchase_day(calendar, day) if day < D.shape[1] else f.BIG
    if branch in (1, 3, 4):
        mark(S, accel, on, n.DECLARED, D.shape[1])
    elif branch in (2, 5, 6):
        mark(S, rep, on, n.DECLARED, D.shape[1])
    coupon_when_due(S, D, coupons)
    if branch in (3, 4, 5, 6):
        t = accel if branch in (3, 4) else rep
        _petition(S, D, coupons, c, eq, t + (c.route if branch in (4, 6) else 0), on)
    return day if on else f.BIG

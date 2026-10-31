"""Compiled equity operations on one draw. Python Chain is the definition.

No fastmath. Cash/need/receipt/lock/levy arrays come from the caller's forward
loan run; these functions never run the loan. Rebooking mutates only this draw.
Calendars and calibration are immutable Python-prepared inputs (export_equity),
including _atm_schedule's trading/settlement dates and calendar-year boundaries.
Call reprice after changing judgment/takes/settlement state before reading P;
atm_rebook and initiate do this themselves. Unlike Python's cross-draw cache keys,
this explicit dependency has no global versions to save on the depth stack.
Pass used-row views of the settlement and notes areas' fixed-capacity tables as
parts and coupons; the equity area does not append either table.
"""
import math

import numpy as np
from numba import njit

from app.exact import core
from app.exact import fields as f
from app.exact import fields_equity as e

UNLIMITED = np.iinfo(np.int64).max // 4


@njit(cache=True)
def price_owed(S, takes, parts, c, day):
    if not c.pending or not S[f.ENTERED]:
        return 0
    amt = S[f.ENTERED]
    if S[f.CLS_AMOUNT] != f.NONE and day >= S[f.F]:
        amt = S[f.CLS_AMOUNT]
    same = 0
    for k in range(S[f.N_TAKES]):
        if day >= takes[k, 0]:
            amt -= takes[k, 1]
        if day == takes[k, 0]:
            same += takes[k, 1]
    amt = 0 if day >= S[f.RESOLVED] else max(amt, 0)
    if day >= S[f.E_IX]:
        amt = max(core.owed_at(S, takes, c.bps, c.pending, day, False) - same, 0)
    if len(parts) and day >= S[f.MARK_SETTLED]:
        amt = 0
        for k in range(len(parts)):
            if parts[k, 0] > day:
                amt += parts[k, 1]
    return amt if day >= S[f.V] else 0


@njit(cache=True)
def price_owed_grid(S, takes, parts, c, out):
    for day in range(len(out)):
        out[day] = price_owed(S, takes, parts, c, day)


@njit(cache=True)
def reprice(S, P, takes, parts, c):
    V, notes, s, rate, T, shares = c.merton
    sq = s * math.sqrt(T)
    for day in range(P.shape[1]):
        owed = price_owed(S, takes, parts, c, day)
        price = c.close_price
        if owed > 0:
            K = float(notes + max(owed, 0))
            d1 = (math.log(V / K) + (rate + s * s / 2) * T) / sq
            n1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
            n2 = 0.5 * (1.0 + math.erf((d1 - sq) / math.sqrt(2.0)))
            price = (V * n1 - K * math.exp(-rate * T) * n2) / shares
        P[e.P_PRICE, day] = price


@njit(cache=True)
def share_price_on(P, day):
    return P[e.P_PRICE, min(max(day, 0), P.shape[1] - 1)]


@njit(cache=True)
def offer_shares_on(S, offers, day):
    out = 0
    for k in range(S[e.N_OFFERS]):
        o = offers[k]
        if day >= o[e.O_INIT] and (day < o[e.O_CLOSE] or o[e.O_CLOSED]):
            out += o[e.O_SHARES]
    return out


@njit(cache=True)
def lockup(S, offers, c, day):
    if c.lock_on:
        for k in range(S[e.N_OFFERS]):
            o = offers[k]
            if (o[e.O_INIT] < f.BIG and day >= o[e.O_INIT] + c.pricing_days
                    and (day < o[e.O_CLOSE] or (o[e.O_CLOSED] and day <= o[e.O_CLOSE] + c.lock_days))):
                return True
    return False


@njit(cache=True)
def atm_to_date(S, D, day):
    if not S[e.HAVE_ATM] or day < 0:
        return 0
    return D[e.D_ATM_CUM, min(day, D.shape[1] - 1)]


@njit(cache=True)
def atm_shares_to_date(S, atm, schedule, day):
    if not S[e.HAVE_ATM]:
        return 0
    i = np.searchsorted(schedule[:, 0], day, side='right')
    return atm[i - 1, e.A_CSOLD] if i > 0 else 0


@njit(cache=True)
def ledger_left(S, offers, atm, schedule, c, day):
    return max(c.ledger - atm_shares_to_date(S, atm, schedule, day) - offer_shares_on(S, offers, day), 0)


@njit(cache=True)
def limited(P, c, day):
    return c.baby and day >= c.update and math.floor(c.nonaffiliate * share_price_on(P, c.update)) < 7_500_000_000


@njit(cache=True)
def offering_usage(S, P, offers, c, day, lower, reserve):
    used = 0
    for k in range(S[e.N_OFFERS]):
        o = offers[k]
        sold = o[e.O_CLOSED] and o[e.O_CLOSE] <= day and o[e.O_CLOSE] > lower
        pending = reserve and o[e.O_INIT] < day and day < o[e.O_CLOSE]
        if limited(P, c, o[e.O_INIT]) and (sold or pending):
            used += o[e.O_GROSS]
    return used


@njit(cache=True)
def shelf_capacity(S, P, offers, atm, schedule, c, day, lower):
    if not c.baby:
        return UNLIMITED
    ceiling = np.int64(np.floor(c.nonaffiliate * share_price_on(P, day) / 3))
    used = offering_usage(S, P, offers, c, day, lower, False)
    if S[e.HAVE_ATM]:
        prev = 0
        for j in range(len(schedule)):
            q = atm[j, e.A_CSOLD] - prev
            prev = atm[j, e.A_CSOLD]
            t = schedule[j, 0]
            if t >= c.update and t <= day and t > lower:
                used += np.int64(np.rint(q * share_price_on(P, t)))
    return max(ceiling - used, 0) if limited(P, c, day) else UNLIMITED


@njit(cache=True)
def offering_price_x1e4(P, c, day):
    close = np.int64(np.rint(share_price_on(P, day) * 10_000))
    return (close * c.january_price * 10_000 * 2 // c.january_close + 1) // 2


@njit(cache=True)
def offering_terms(S, P, offers, atm, schedule, c, day, lower):
    """gross, costs, net, price_cents_x1e4, shares, close_days, shelf_capacity."""
    price = offering_price_x1e4(P, c, day)
    full = (c.gross * 10_000 * 2 // price + 1) // 2
    shares = min(full, ledger_left(S, offers, atm, schedule, c, day))
    gross = shares * price // 10_000 if shares < full else c.gross
    net = gross * c.net // c.gross if shares < full else c.net
    return (gross, gross - net, net, price, shares, c.close_days,
            shelf_capacity(S, P, offers, atm, schedule, c, day, lower))


@njit(cache=True)
def offering_pending_on(S, offers, day):
    for k in range(S[e.N_OFFERS]):
        if day >= offers[k, e.O_INIT] and day < offers[k, e.O_CLOSE]:
            return True
    return False


@njit(cache=True)
def offering_bound_reached(S, offers, c, day, N):
    count = 0
    for k in range(S[e.N_OFFERS]):
        t = offers[k, e.O_INIT]
        count += 0 <= t < N and t <= day
    return count >= c.bound


@njit(cache=True)
def offering_available(S, P, offers, atm, schedule, c, day, lower):
    pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
    return (0 <= day < P.shape[1] and day < pet and day < S[f.SUSPENDED] and day < S[f.DELISTED]
            and not offering_pending_on(S, offers, day)
            and not offering_bound_reached(S, offers, c, day, P.shape[1])
            and ledger_left(S, offers, atm, schedule, c, day) > 0
            and offering_terms(S, P, offers, atm, schedule, c, day, lower)[0]
            <= shelf_capacity(S, P, offers, atm, schedule, c, day, lower))


@njit(cache=True)
def offer_shortfall(S, takes, c, day, pending_levy, cum, need, op_inflow, inflow, lock, levy):
    t = min(max(day, 0), len(cum) - 1)
    cash = cum[t]
    if pending_levy == day and core.live(S, day) and day < S[f.STAYED_FROM] and day < len(cum):
        reach = cash
        if c.daily:
            prev = cum[t - 1] if t > 0 else c.opening
            reach = prev + op_inflow[t] + inflow[t] - lock[t] + levy[t]
        cash -= min(core.owed_at(S, takes, c.bps, c.pending, day, True), max(reach, 0))
    return max(need[t] - cash, 0)


@njit(cache=True)
def offer_available(S, P, takes, offers, atm, schedule, c, day, lower, pending_levy,
                    cum, need, op_inflow, inflow, lock, levy):
    net = 0
    if offering_available(S, P, offers, atm, schedule, c, day, lower):
        net = offering_terms(S, P, offers, atm, schedule, c, day, lower)[2]
    if c.covers and net < offer_shortfall(S, takes, c, day, pending_levy, cum, need, op_inflow, inflow, lock, levy):
        net = 0
    return net


@njit(cache=True)
def option_group(S, P, takes, offers, atm, schedule, c, response, day, lower, pending_levy,
                 cum, need, op_inflow, inflow, lock, levy):
    pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
    on = 0 <= day < len(cum) and day < pet
    pay = False
    if response:
        owed = core.owed_at(S, takes, c.bps, c.pending, day, False)
        on = on and core.live(S, day) and owed > 0
        pay = on and cum[min(max(day, 0), len(cum) - 1)] >= owed
    offer = on and offer_available(S, P, takes, offers, atm, schedule, c, day, lower, pending_levy,
                                  cum, need, op_inflow, inflow, lock, levy) > 0
    return int(pay) + 2 * int(offer) if on else -1


@njit(cache=True)
def coupon_rebook(S, D, P, coupons, c):
    """Dependency of Chain._atm_rebook: its priced cash coupon (notes table supplied by caller)."""
    if not c.coupon_priced:
        return
    for k in range(len(coupons)):
        o = coupons[k]
        # numpy's pairwise sum for ten floats, as mean(axis=1) in coupon_cash_cents.
        a0 = share_price_on(P, o[3]) + share_price_on(P, o[4])
        a1 = share_price_on(P, o[5]) + share_price_on(P, o[6])
        a2 = share_price_on(P, o[7]) + share_price_on(P, o[8])
        a3 = share_price_on(P, o[9]) + share_price_on(P, o[10])
        px = (((a0 + a1) + (a2 + a3)) + share_price_on(P, o[11]) + share_price_on(P, o[12])) / 10
        value = px * c.coupon_ratio * c.coupon_bps / 10_000
        shares = min(np.floor(c.coupon_amount / value), c.coupon_capacity)
        new = np.int64(max(c.coupon_amount - np.rint(shares * value), 0))
        delta = new - o[e.C_CASH] if o[e.C_KEPT] else 0
        day = o[e.C_DAY]
        if delta and 0 <= day < D.shape[1]:
            D[f.D_CASH, day] -= delta
            D[f.D_KIND_NOTES_INTEREST, day] -= delta
            S[f.INCURRED_NOTES_INTEREST] = min(S[f.INCURRED_NOTES_INTEREST], -f.BIG)
        o[e.C_CASH] = new


@njit(cache=True)
def atm_rebook(S, D, P, takes, parts, offers, atm, schedule, coupons, c, scratch):
    """Chain._atm_rebook. scratch: preallocated int64 [sales] gross history.

    Daily proceeds are replaced in place; no horizon arrays allocated during traversal.
    """
    reprice(S, P, takes, parts, c)
    coupon_rebook(S, D, P, coupons, c)
    N = D.shape[1]
    for t in range(N):
        D[f.D_CASH, t] -= D[e.D_ATM, t]
        D[f.D_KIND_INFLOW, t] -= D[e.D_ATM, t]
        D[e.D_ATM, t] = 0
    value = np.int64(np.floor(c.nonaffiliate * share_price_on(P, c.update)))
    active = c.baby and value < 7_500_000_000
    ceiling = value // 3
    pet = UNLIMITED if S[f.PETITION] < 0 else S[f.PETITION]
    stop = min(pet, S[f.DELISTED])
    total = 0
    stopped = False
    for j in range(len(schedule)):
        t, settle, lower = schedule[j]
        on = t < stop and not lockup(S, offers, c, t) and not stopped
        stopped = stopped or (on and total + c.quantity + offer_shares_on(S, offers, t) > c.ledger)
        quantity = c.quantity if on and not stopped else 0
        price = share_price_on(P, t)
        if c.baby and t >= c.update:
            used = 0
            for i in range(j):
                if schedule[i, 0] >= c.update and schedule[i, 0] > lower:
                    used += scratch[i]
            room = max(ceiling - used - offering_usage(S, P, offers, c, t, lower, True), 0)
            affordable = np.int64(np.floor(min(c.quantity, room / price)))
            if active:
                quantity = min(quantity, affordable)
        total += quantity
        atm[j, e.A_SOLD] = quantity > 0
        atm[j, e.A_CSOLD] = total
        gross = np.int64(np.rint(quantity * price))
        scratch[j] = gross
        if 0 <= settle < N:
            D[e.D_ATM, settle] += gross * (10_000 - c.commission) // 10_000
    run = 0
    for t in range(N):
        run += D[e.D_ATM, t]
        D[e.D_ATM_CUM, t] = run
        D[f.D_CASH, t] += D[e.D_ATM, t]
        D[f.D_KIND_INFLOW, t] += D[e.D_ATM, t]
    S[e.HAVE_ATM] = 1


@njit(cache=True)
def close(S, D, offers, answers, k):
    o = offers[k]
    yes = answers[o[e.O_OCCASION]]
    if yes < 0 or o[e.O_BOOKED]:
        return
    o[e.O_BOOKED] = 1
    pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
    closed = bool(yes) and o[e.O_CLOSE] < pet and o[e.O_CLOSE] < D.shape[1]
    o[e.O_CLOSED] = closed
    day = o[e.O_CLOSE]
    if closed and 0 <= day < D.shape[1]:
        D[f.D_CASH, day] += o[e.O_NET]
        D[f.D_KIND_INFLOW, day] += o[e.O_NET]
    core.mark(S, f.MARK_RAISED, day, closed, D.shape[1])


@njit(cache=True)
def initiate(S, D, P, takes, parts, offers, atm, schedule, coupons, answers, c, scratch, day, lower, occasion):
    reprice(S, P, takes, parts, c)
    if not offering_available(S, P, offers, atm, schedule, c, day, lower):
        return False
    terms = offering_terms(S, P, offers, atm, schedule, c, day, lower)
    k = S[e.N_OFFERS]
    if k >= len(offers):
        raise ValueError('offering table exhausted')
    o = offers[k]
    o[e.O_OCCASION], o[e.O_INIT], o[e.O_CLOSE] = occasion, day, day + terms[5]
    o[e.O_SHARES], o[e.O_NET] = terms[4], terms[2]
    o[e.O_CLOSED], o[e.O_BOOKED] = 0, 0
    o[e.O_GROSS], o[e.O_COSTS], o[e.O_PRICE], o[e.O_DAYS], o[e.O_SHELF] = terms[0], terms[1], terms[3], terms[5], terms[6]
    S[e.N_OFFERS] += 1
    close(S, D, offers, answers, k)
    atm_rebook(S, D, P, takes, parts, offers, atm, schedule, coupons, c, scratch)
    return True


@njit(cache=True)
def offering_outcome(S, D, P, takes, parts, offers, atm, schedule, coupons, answers, c, scratch, occasion, closes):
    answers[occasion] = closes
    out = f.BIG
    for k in range(S[e.N_OFFERS]):
        if offers[k, e.O_OCCASION] == occasion:
            close(S, D, offers, answers, k)
            out = offers[k, e.O_INIT]
    atm_rebook(S, D, P, takes, parts, offers, atm, schedule, coupons, c, scratch)
    return out


@njit(cache=True)
def offering_day(S, offers, occasion):
    out = f.BIG
    for k in range(S[e.N_OFFERS]):
        if offers[k, e.O_OCCASION] == occasion:
            out = offers[k, e.O_INIT]
    return out

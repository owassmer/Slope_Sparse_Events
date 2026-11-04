"""Python-only preparation and export for the compiled equity block.

Calendars, calibrated Merton parameters and occasion IDs are prepared once outside
numba. A missing occasion is an error, never a new ID during compiled traversal.
"""
from collections import namedtuple
from datetime import date, timedelta

import numpy as np

from app.analysis import baby_shelf
from app.analysis.events import trading_day
from app.disputes.recurrence import RecurrenceBounds
from app.exact import fields as f
from app.exact import fields_equity as e
from app.exact.export import chain_state

Config = namedtuple('Config', 'ledger quantity commission close_price january_price january_close gross net '
                    'close_days bound lock_on pricing_days lock_days baby update nonaffiliate covers '
                    'pending bps daily opening merton coupon_priced coupon_amount coupon_capacity coupon_ratio coupon_bps')


def config(ch):
    p = ch.m['parameters']
    o, a = p['offering_price'], p['atm_pace_bps']
    lock = p.get('offering_lockup', {})
    baby = p.get('baby_shelf')
    if baby is not None and int(baby['nonaffiliate_shares']) <= 0:
        raise ValueError('baby shelf needs a positive, established non-affiliate share count')
    bound = RecurrenceBounds.from_model(ch.m, ch.sens).offering
    if bound is None or bound > e.MAX_OFFERS:
        raise ValueError('exact equity requires an offering bound at most two')
    m = ch.merton
    if m is None:
        raise ValueError('exact equity requires the structural share-price model')
    c = p.get('coupon_cash_share', {})
    return Config(int(p['share_ledger']['value']), ch._atm_schedule()[2], int(a['commission_bps']),
                  float(a['price_cents']), int(o['january_price_cents']), int(o['january_prior_close_cents_x1e4']),
                  int(o['gross_cents']), int(o['net_cents']), int(o['close_days']), bound,
                  bool(lock) and not lock.get('atm_carved_out', False), int(lock.get('pricing_days', 0)),
                  int(lock.get('value', 0)), baby is not None,
                  ch.ix(date.fromisoformat(baby['annual_update'])) if baby else f.BIG,
                  int(baby['nonaffiliate_shares']) if baby else 0,
                  ch.initiation_rule() == 'covers_shortfall', ch.pending, ch.bps, ch.daily, ch.basis.opening,
                  (m.V, m.notes_cents, m.asset_vol, m.rate, m.T, m.shares),
                  ch._priced_coupon(), ch.fin.coupon_cents if ch.fin else 0,
                  min(c['share_capacity'], c['share_limit']) // (c.get('split_ratio') or 1) if c else 0,
                  c.get('split_ratio') or 1, c.get('share_value_bps', 0))


def atm_schedule(ch):
    """Chain._atm_schedule: immutable calendar arrays plus calendar-year lower bounds.

    Fixed table capacity is exactly the schedule length; the case never appends sales.
    """
    sale, settle, _, _ = ch._atm_schedule()
    lower = baby_shelf.year_start(ch, sale).astype(np.int64)
    return np.column_stack((sale, settle, lower))


def year_start(ch, day):
    """Prepare the calendar lower bound for a scalar accessor day (including BIG)."""
    return int(baby_shelf.year_start(ch, np.array(day)))


def equity_state(ch, r, occasions):
    """S,D,P,takes,offers,atm,answers,parts,coupons; no accessors mutate the reference.

    Global cache versions are deliberately absent: they are not financial state and
    can change when another draw changes. Repricing/rebooking is deterministic.
    """
    S, D, takes = chain_state(ch, r)
    P = np.zeros((f.NP, ch.N), dtype=np.float64)
    if ch.share_price is not None:
        P[e.P_PRICE] = ch.share_price[r]
    offers = np.zeros((e.MAX_OFFERS, e.O_COLS), dtype=np.int64)
    for o in ch._offers:
        if not o['rows'][r]:
            continue
        k = S[e.N_OFFERS]
        if k >= e.MAX_OFFERS:
            raise ValueError('more than two active offerings on this draw')
        terms = o['terms']
        offers[k] = (occasions[o['occasion']], o['init'][r], o['close'][r], o['shares'][r], o['net'][r],
                     o['closed'][r], o['booked'], *(terms[key][r] for key in
                     ('gross', 'costs', 'price_cents_x1e4', 'close_days', 'shelf_capacity')))
        S[e.N_OFFERS] += 1
    atm = np.zeros((len(ch._atm_schedule()[0]), 2), dtype=np.int64)
    S[e.HAVE_ATM] = ch._atm is not None
    if ch._atm is not None:
        D[e.D_ATM], D[e.D_ATM_CUM] = ch._atm[r], ch._atm_cum[r]
        atm[:, e.A_SOLD], atm[:, e.A_CSOLD] = ch._atm_sold[r], ch._atm_csold[r]
    answers = np.full(len(occasions), -1, dtype=np.int64)
    for name, yes in ch._n1.items():
        answers[occasions[name]] = yes
    parts = np.array([(ch.per_draw(t)[r], ch.per_draw(a)[r]) for t, a in ch.settlement_parts],
                     dtype=np.int64).reshape(-1, 2)
    coupons = np.zeros((len(ch.coupons), e.C_COLS), dtype=np.int64)
    for i, (day, cash, kept, when) in enumerate(ch.coupons):
        days, d = [], when - timedelta(days=1)
        while len(days) < 10:
            if trading_day(d):
                days.append(ch.ix(d))
            d -= timedelta(days=1)
        coupons[i] = (day, ch.per_draw(cash)[r], kept[r], *days)
    return S, D, P, takes, offers, atm, answers, parts, coupons

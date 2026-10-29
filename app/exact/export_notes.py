"""Python-only notes constants, calendars and one-draw export.

Cash-processor reads are deliberately not exported as state. The caller supplies
nonpayment_day at the point it is read (after coupon refunds for a holder route).
"""
from collections import namedtuple
from datetime import timedelta

import numpy as np

from app.analysis.events import PETITION_CAUSES, next_business_day, trading_day
from app.exact import export_equity
from app.exact import fields_notes as n

Config = namedtuple('NotesConfig', 'pending daily equity ordinary has_dispute recorded_judgment recorded_entry '
                    'default_days insured threshold reading notice route petition_lag notes_cause bps '
                    'coupon_amount coupon_mode coupon_capacity coupon_ratio coupon_bps coupon_price chips')


def config(ch):
    fin = ch.fin
    p = ch.m['parameters']['coupon_cash_share']
    mode = ch.sens.get('coupon_cash_share') or p['value']
    mode = n.CASH if mode is True or mode == 'all_cash' else n.SHARES if mode == 'all_shares' else (
        n.PRICED if p.get('share_price') == 'model' else n.FIXED)
    ratio = p.get('split_ratio') or 1
    amount = fin.coupon_cents if fin is not None else 0
    if amount is None and fin.kind == 'convertible_notes':
        raise ValueError('the convertible notes coupon is unknown')
    reading = {'entered': n.READ_ENTERED, 'post_ruling': n.READ_POST, 'both': n.READ_BOTH}[ch.p('judgment_default_reading')]
    recorded = ch.d is not None and ch.d.judgment_date is not None
    return Config(ch.pending, ch.daily, ch.equity, ch.ordinary, ch.d is not None, recorded,
                  ch.ix(ch.d.judgment_date) if recorded else 0,
                  int(fin.judgment_default_days or 0) if fin else 0,
                  int(fin.insured_cents) if fin else 0, int(fin.judgment_default_threshold_cents) if fin else 0,
                  reading, int(ch.p('holder_notice_lag_days')), ch.holder_route_days(),
                  int(ch.p('petition_lag_days')), PETITION_CAUSES.index('notes'), ch.bps,
                  amount or 0, mode, min(p['share_capacity'], p['share_limit']) // ratio,
                  ratio, p['share_value_bps'], p.get('share_price_cents', 0), int(ch.p('chips_credit_cents')))


def coupon_schedule(ch):
    """One immutable row per contractual interest date, including outside the horizon."""
    dates = ch.fin.interest_dates if ch.fin else ()
    out = np.zeros((len(dates), n.C_COLS), dtype=np.int64)
    for i, when in enumerate(dates):
        out[i, n.C_DAY] = ch.ix(next_business_day(when))
        days, d = [], when - timedelta(days=1)
        while len(days) < 10:
            if trading_day(d):
                days.append(ch.ix(d))
            d -= timedelta(days=1)
        out[i, n.C_PRICE_DAYS:] = days
    return out


def listing_dates(ch):
    dates = ch.listing_dates()
    return np.array([dates[key] for key in n.LISTING_KEYS], dtype=np.int64)


def repurchase_calendar(ch, days):
    """Prepare exactly the delisting dates the walk/accessor will query, once."""
    days = sorted(set(int(day) for day in days))
    return np.array([(day, ch.repurchase_day(day)) for day in days], dtype=np.int64).reshape(-1, 2)


def notes_state(ch, r, occasions):
    """Equity-compatible tuple, with fixed-capacity notes table and its scalar count.

    Equity consumes coupons[:S[N_COUPONS]], never the unused capacity.
    """
    state = list(export_equity.equity_state(ch, r, occasions))
    S, old = state[0], state[-1]
    coupons = np.zeros((len(ch.fin.interest_dates) if ch.fin else 0, n.C_COLS), dtype=np.int64)
    if len(old) > len(coupons):
        raise ValueError('coupon table exhausted: instrument_cash must run only once')
    coupons[:len(old)] = old
    S[n.N_COUPONS] = len(old)
    S[n.NOTES_DUE_HOW] = n.HOW.index(str(ch.notes_due_how[r]))
    state[-1] = coupons
    return tuple(state)

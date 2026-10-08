"""Form S-3 I.B.6 dollar capacity, separate from the authorized-share ledger.

All prices are cents, all receipts integer cents. The declared ownership projection
and annual-update date live in the case, not in a forecast answer.
"""
from datetime import date

import numpy as np

UNLIMITED = np.iinfo(np.int64).max // 4


def annual(ch):
    p = ch.m["parameters"]["baby_shelf"]
    day = ch.ix(date.fromisoformat(p["annual_update"]))
    shares = int(p["nonaffiliate_shares"])
    if shares <= 0:
        raise ValueError("baby shelf needs a positive, established non-affiliate share count")
    value = np.floor(shares * ch.share_price_on(ch.per_draw(day))).astype(np.int64)
    return day, shares, value


def limited(ch, day):
    update, _, value = annual(ch)
    return (np.asarray(day) >= update) & (value < 7_500_000_000)


def year_start(ch, day):
    """Exclusive lower boundary of the preceding twelve calendar months."""
    from datetime import timedelta
    # ix uses review + 1 as day zero.
    review = ch.s.review
    def before(t):
        d = review + timedelta(days=int(t) + 1)
        try:
            d = d.replace(year=d.year - 1)
        except ValueError:
            d = d.replace(year=d.year - 1, day=28)
        return ch.ix(d)
    values, inverse = np.unique(np.asarray(day), return_inverse=True)
    return np.array([before(t) for t in values])[inverse].reshape(np.shape(day))


def offering_usage(ch, day, *, reserve=False):
    """Actual I.B.6 sales, plus (optionally) outstanding concurrent reservations.

    A failed offering never consumes sales capacity. Pending reservations prevent
    the ATM from spending room promised to another continuous offering.
    """
    day = ch.per_draw(day)
    lower = year_start(ch, day)
    used = np.zeros(ch.n, dtype=np.int64)
    for o in ch._offers:
        eligible = o["rows"] & limited(ch, o["init"])
        sold = o["closed"] & (o["close"] <= day) & (o["close"] > lower)
        # Initiation reads capacity after that day's ATM trade; do not erase
        # an already sold lot when the new reservation is added.
        pending = (o["init"] < day) & (day < o["close"]) if reserve else False
        used += np.where(eligible & (sold | pending), o["terms"]["gross"], 0)
    return used


def capacity(ch, day):
    """Room immediately before a new takedown, after today's ATM trades.

    Prior I.B.1 sales, unsold reservations and failed offerings are not sales.
    """
    day = ch.per_draw(day)
    if "baby_shelf" not in ch.m["parameters"]:
        return np.full(ch.n, UNLIMITED, dtype=np.int64)
    update, shares, _ = annual(ch)
    ceiling = np.floor(shares * ch.share_price_on(day) / 3).astype(np.int64)
    used = offering_usage(ch, day)
    if ch._atm is not None:
        sale, _, _, _ = ch._atm_schedule()
        quantities = np.diff(ch._atm_csold, axis=1, prepend=0)
        prices = ch.share_price_on(np.broadcast_to(sale, quantities.shape))
        gross = np.rint(quantities * prices).astype(np.int64)
        lower = year_start(ch, day)
        counted = ((sale[None] >= update) & (sale[None] <= day[:, None])
                   & (sale[None] > lower[:, None]))
        used += np.where(counted, gross, 0).sum(axis=1)
    return np.where(limited(ch, day), np.maximum(ceiling - used, 0), UNLIMITED)


def atm_book(ch):
    """Chronological ATM trades, with a reduced final whole-share lot.

    The continuous offering is resized at the annual update; its dollar ceiling
    cannot grow by treating each execution as a new takedown (C&DI 116.22).
    """
    sale, settle, q, _ = ch._atm_schedule()
    update, _, value = annual(ch)
    active = value < 7_500_000_000
    ceiling = value // 3
    other = ch._offer_shares_on(np.broadcast_to(sale, (ch.n, sale.size)))
    prices = ch.share_price_on(np.broadcast_to(sale, other.shape))
    lock = ch._lockup(sale)
    ledger = int(ch.m["parameters"]["share_ledger"]["value"])
    pet = np.where(ch.ev.petition < 0, UNLIMITED, ch.ev.petition)
    stop = np.minimum(pet, ch.delisted)
    quantities = np.zeros_like(other)
    gross = np.zeros_like(other)
    total = np.zeros(ch.n, dtype=np.int64)
    stopped = np.zeros(ch.n, dtype=bool)
    comm = int(ch.m["parameters"]["atm_pace_bps"]["commission_bps"])
    new = np.zeros((ch.n, ch.N), dtype=np.int64)
    for j, t in enumerate(sale):
        on = (t < stop) & ~lock[:, j] & ~stopped
        # Preserve the pre-existing authorized-share channel-stop convention.
        stopped |= on & (total + q + other[:, j] > ledger)
        quantity = np.where(on & ~stopped, q, 0)
        if t >= update:
            lower = year_start(ch, ch.per_draw(t))
            counted = (sale[:j][None] >= update) & (sale[:j][None] > lower[:, None])
            used = np.where(counted, gross[:, :j], 0).sum(axis=1)
            room = np.maximum(ceiling - used - offering_usage(ch, t, reserve=True), 0)
            affordable = np.floor(np.minimum(q, room / prices[:, j])).astype(np.int64)
            quantity = np.where(active, np.minimum(quantity, affordable), quantity)
        quantities[:, j] = quantity
        total += quantity
        gross[:, j] = np.rint(quantity * prices[:, j]).astype(np.int64)
        if 0 <= settle[j] < ch.N:
            new[:, settle[j]] += gross[:, j] * (10_000 - comm) // 10_000
    old = ch._atm if ch._atm is not None else np.zeros_like(new)
    return quantities > 0, quantities.cumsum(axis=1), new, new.cumsum(axis=1), new - old

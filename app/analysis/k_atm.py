"""Compiled kernel of `Chain._atm_rebook` (events.py): the at-the-market booking on the path's current state, one pass
per draw over the sales. It reproduces the Python it replaces operation for operation (`Chain._atm_rebook_py`, with
`_lockup` and the two-dimensional branch of `_offer_shares_on`): int64 shares and cents, the sale's gross as the
float64 product q x share price rounded half to even, the commission by floor division."""
from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def atm_book(sale, stop, q, led, init, close, closed, shares, lock_on, big, pricing_days, lock_value,
             j, days, start, sp, use_close, close_price, n_days, comm, old, have_old):
    """Returns (sold [draws, sales] bool, cumulative sales sold int64, net proceeds [draws, days] int64 by settlement
    day, their cumsum, the change against `old`).

    sale [sales] the sale days; stop [draws] the first day with no sale (petition, delisting); q the shares a sale;
    led the share ledger; init/close/closed/shares [offers, draws] the stacked offerings (zero offers: none);
    lock_on whether the lock-up applies, with its pricing days and length; j, days, start the settling sales in
    settlement order, their distinct settlement days and where each starts (`_atm_columns`); sp [draws, days] the
    share price (use_close: `close_price` on every cell instead); comm the commission in bps; old the booking before
    (have_old False: none, zeros)."""
    n = stop.shape[0]
    S = sale.shape[0]
    n_off = init.shape[0]
    J = j.shape[0]
    D = days.shape[0]
    sold = np.zeros((n, S), dtype=np.bool_)
    csold = np.zeros((n, S), dtype=np.int64)
    new = np.zeros((n, n_days), dtype=np.int64)
    cum = np.zeros((n, n_days), dtype=np.int64)
    delta = np.zeros((n, n_days), dtype=np.int64)
    keep = np.int64(10_000) - comm
    for r in range(n):
        k = np.int64(0)
        failed = False
        cs = np.int64(0)
        for s in range(S):
            d = sale[s]
            other = np.int64(0)  # the offerings' shares held on the ledger on the sale day
            for o in range(n_off):
                if d >= init[o, r] and (d < close[o, r] or closed[o, r]):
                    other += shares[o, r]
            locked = False  # an offering's lock-up covers the sale day
            if lock_on:
                for o in range(n_off):
                    if (init[o, r] < big and d >= init[o, r] + pricing_days
                            and (d < close[o, r] or (closed[o, r] and d <= close[o, r] + lock_value))):
                        locked = True
                        break
            on = d < stop[r] and not locked
            if on:
                k += 1
                if k * q + other > led:  # the ledger cannot cover the day's shares: the channel stops
                    failed = True
            ok = on and not failed
            sold[r, s] = ok
            if ok:
                cs += 1
            csold[r, s] = cs
        for di in range(D):  # each settlement day's sales at the sale day's share price, net of commission
            b = start[di + 1] if di + 1 < D else J
            acc = np.int64(0)
            for c in range(start[di], b):
                col = j[c]
                if sold[r, col]:
                    if use_close:
                        p = close_price
                    else:
                        t = sale[col]
                        t = 0 if t < 0 else (n_days - 1 if t > n_days - 1 else t)
                        p = sp[r, t]
                    gross = np.int64(np.rint(q * p))
                    acc += gross * keep // 10_000
            new[r, days[di]] = acc
        run = np.int64(0)
        for t in range(n_days):
            run += new[r, t]
            cum[r, t] = run
            delta[r, t] = new[r, t] - old[r, t] if have_old else new[r, t]
    return sold, csold, new, cum, delta

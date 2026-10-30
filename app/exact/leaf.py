"""A history's figures at its leaf, on one draw (design/002-exact-evaluator.md, Two passes: pass 2): what
`core.Reduction.add` records for a path on a draw, from the line run on the history's final event cash.

The final event cash is `Chain.finish`'s: every waiting decision and writ booked (`agenda.finish`), then nothing
collected from or paid by the estate after a petition (§362: every kind zeroed from the petition day), except the
legal spend a dispute that has ended no longer incurs (`resolve`'s reduction, kept). The lock, every stay's security
included, and the capacity stand. The line is then run on it from the review date (`cash.forward`), as the analysis
runs `engine.run` on the trace's events.
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np
from numba import njit

from app.analysis import line_state as ls
from app.analysis.events import KINDS
from app.exact import agenda, cash
from app.exact import fields as f
from app.exact import fields_equity as e

KIND_ROWS = np.array([f.D_KIND[c] for c in KINDS], dtype=np.int64)

# per-day rows (core.DAILY, then the arrears classes)
(L_CASH, L_BACKUP, L_COLLECTED, L_DUE_CUM, L_FUNDINGS, L_COLLECTIONS, L_OUTSTANDING, L_LOCKED, L_CAPACITY,
 L_PETITIONED, L_FROZEN, L_FROZEN_DUE, L_PAST_DUE, L_CLAWBACK, L_ARREARS0) = range(15)
NDAY = L_ARREARS0 + 5
DAILY_NAMES = ("cash", "backup", "collected", "due_cum", "fundings", "collections", "outstanding", "locked",
               "capacity", "petitioned", "frozen", "frozen_due", "past_due", "clawback")
# scalars (core.SCALARS, core._scalars' derived figures, the equity proceeds)
SCALAR_NAMES = ("lender_pv", "pv_fundings", "pv_collections", "dollar_days", "drawn", "fees", "contractual",
                "collected", "stayed", "stayed_principal", "preference", "not_yet_due", "uncollected", "min_cash",
                "petition_p", "shortfall_p", "shortfall", "peak_outstanding", "avg_outstanding", "peak_locked",
                "peak_capacity", "unrecovered", "recovered_all", "horizon_cash", "atm_proceeds", "offering_proceeds",
                "min_headroom_has", "headroom_count", "headroom_negative")
NSC = len(SCALAR_NAMES)
SC = {name: i for i, name in enumerate(SCALAR_NAMES)}


def nf(months: int) -> int:
    """A leaf's figure vector: the scalars, then each per-day row summed by month (row-major: row r, month m at
    NSC + r * months + m). What a question's terms hold (`tree.evaluate`)."""
    return NSC + NDAY * months
PREFERENCE_DAYS = 90  # 11 U.S.C. §547(b)(4)(A) (engine.PREFERENCE_DAYS)

# the analysis's constants on the draw: discount factors, the facility, the opening principal, the month of each day,
# and the bins (lo, width per day or month; their counts)
LK = namedtuple("LK", "df facility p0 month cash_lo cash_w coll_lo coll_w head_lo head_w nbins_cash nbins_coll "
                      "nbins_head low_lo low_w nbins_low")
# the per-trajectory figures the page reads as percentiles or a first day (core.Reduction: min_cash, min_headroom,
# floor_day): histograms of the lowest cash and the lowest headroom at a due date (one bin set each, `low_*`: row 0
# the cash, row 1 the headroom, its last bin 'no due date'), and of the first day cash is below the operating need
# (its last slot: never in the period)
LOW_CASH, LOW_HEAD = 0, 1


@njit(cache=True)
def finalize(st, k):
    """`Chain.finish`'s event cash on the state now (in place: the leaf's state is discarded on the way back)."""
    agenda.finish(st, k)
    S, D, N = st.S, st.D, k.N
    pet = S[f.PETITION]
    if pet < 0 or pet >= N:
        return
    ended = S[f.RESOLVED]
    for t in range(pet, N):
        for j in range(KIND_ROWS.size):
            D[KIND_ROWS[j], t] = 0
        D[f.D_CASH, t] = 0
        if t >= ended:
            D[f.D_KIND_REDUCTION, t] = -k.legal[t]
            D[f.D_CASH, t] = -k.legal[t]


@njit(cache=True)
def proceeds(st, k):
    """The equity proceeds by channel (`EventCash.proceeds`): the at-the-market receipts before a petition and the
    offerings closed before it."""
    S, N = st.S, k.N
    pet = S[f.PETITION]
    stop = f.BIG if pet < 0 else pet
    atm = 0
    if S[e.HAVE_ATM]:
        for t in range(N):
            if pet < 0 or t < pet:
                atm += st.D[e.D_ATM, t]
    off = 0
    for j in range(S[e.N_OFFERS]):
        o = st.offers[j]
        if o[e.O_CLOSED] and o[e.O_CLOSE] < stop:
            off += o[e.O_NET]
    return atm, off


@njit(cache=True)
def _bin(x, lo, w, n):
    j = np.int64((x - lo) / w)
    return min(max(j, 0), n - 1)


@njit(cache=True)
def _row(day, F, r, t, m, months, w, x):
    """Day t's value x of per-day row r: added to the accumulators weighted by w, and to the leaf's vector by month."""
    day[r, t] += w * x
    F[NSC + r * months + m] += x


@njit(cache=True)
def accumulate(st, k, lk, w, day, sc, h_cash, h_coll, h_head, h_low, h_floor, ev_lo, ev_hi, F):
    """Run the line on the leaf's final event cash and add its figures, weighted by w, to the draw's accumulators;
    widen the cumulative event cash range (ev_lo, ev_hi: what the bins span); write the leaf's own figures, unweighted,
    to F (`nf`)."""
    F[:] = 0.0
    months = (F.size - NSC) // NDAY
    finalize(st, k)
    agenda.restay(st, k)
    S, D, N, ln = st.S, st.D, k.N, st.ln
    cash.forward(S, D, st.lockbuf, ln, ln.initial, 0)
    out = ln.outputs
    s = ln.state
    cum = 0
    for t in range(N):
        cum += D[f.D_CASH, t] - st.lockbuf[t]
        if cum < ev_lo[t]:
            ev_lo[t] = cum
        if cum > ev_hi[t]:
            ev_hi[t] = cum
    stayed, stayed_principal, preference, not_yet_due, uncollected = ls.claims(s, ln.tables)
    pet = s[ls.PETITION]
    petitioned = pet < N
    funded, contract, collected = s[ls.FUNDED], s[ls.CONTRACTUAL], s[ls.COLLECTED]
    cum_due, cum_coll, cum_fund = 0, 0, 0
    pv_f, pv_c, dollar, min_cash, peak_o, sum_o, peak_l, peak_c = 0.0, 0.0, 0.0, out[0, 0], 0, 0, out[5, 0], out[6, 0]
    min_head, head_n, head_neg = np.iinfo(np.int64).max, 0, 0
    floor = N
    for t in range(N):
        c, coll, fund, outs, due = out[0, t], out[1, t], out[2, t], out[3, t], out[4, t]
        cum_due += due
        cum_coll += coll
        cum_fund += fund
        by_day = petitioned and pet <= t
        window = petitioned and t >= pet - PREFERENCE_DAYS and t < pet
        mo = lk.month[t]
        _row(day, F, L_CASH, t, mo, months, w, c)
        _row(day, F, L_BACKUP, t, mo, months, w, c + max(lk.facility - out[6, t], 0))
        _row(day, F, L_COLLECTED, t, mo, months, w, cum_coll)
        _row(day, F, L_DUE_CUM, t, mo, months, w, cum_due)
        _row(day, F, L_FUNDINGS, t, mo, months, w, fund)
        _row(day, F, L_COLLECTIONS, t, mo, months, w, coll)
        _row(day, F, L_OUTSTANDING, t, mo, months, w, outs)
        _row(day, F, L_LOCKED, t, mo, months, w, out[5, t])
        _row(day, F, L_CAPACITY, t, mo, months, w, out[6, t])
        if by_day:
            _row(day, F, L_PETITIONED, t, mo, months, w, 1)
            _row(day, F, L_FROZEN, t, mo, months, w, contract - collected)
            _row(day, F, L_FROZEN_DUE, t, mo, months, w, cum_due - cum_coll)
        else:
            _row(day, F, L_PAST_DUE, t, mo, months, w, cum_due - cum_coll)
        if window:
            _row(day, F, L_CLAWBACK, t, mo, months, w, coll)
        for j in range(5):
            _row(day, F, L_ARREARS0 + j, t, mo, months, w, out[9 + j, t])
        pv_f += fund * lk.df[t]
        pv_c += coll * lk.df[t]
        dollar += outs
        min_cash = min(min_cash, c)
        if floor == N and c < k.need[t]:
            floor = t
        peak_o = max(peak_o, outs)
        sum_o += outs
        peak_l = max(peak_l, out[5, t])
        peak_c = max(peak_c, out[6, t])
        h_cash[t, _bin(c, lk.cash_lo[t], lk.cash_w[t], lk.nbins_cash)] += w
        h_coll[t, _bin(cum_coll, lk.coll_lo[t], lk.coll_w[t], lk.nbins_coll)] += w
        if out[8, t]:
            hr = out[7, t]
            m = lk.month[t]
            h_head[m, _bin(hr, lk.head_lo[m], lk.head_w[m], lk.nbins_head)] += w
            min_head = min(min_head, hr)
            head_n += 1
            head_neg += hr < 0
    h_low[LOW_CASH, _bin(min_cash, lk.low_lo[LOW_CASH], lk.low_w[LOW_CASH], lk.nbins_low)] += w
    if head_n > 0:
        h_low[LOW_HEAD, _bin(min_head, lk.low_lo[LOW_HEAD], lk.low_w[LOW_HEAD], lk.nbins_low - 1)] += w
    else:
        h_low[LOW_HEAD, lk.nbins_low - 1] += w
    h_floor[floor] += w
    drawn = funded - lk.p0
    atm, off = proceeds(st, k)
    unrecovered = stayed + uncollected
    i = 0
    for v in (pv_c - pv_f, pv_f, pv_c, dollar / 100.0, float(drawn), float(contract - drawn - lk.p0), float(contract),
              float(collected), float(stayed), float(stayed_principal), float(preference), float(not_yet_due),
              float(uncollected), float(min_cash), 1.0 if petitioned else 0.0, 1.0 if min_cash < 0 else 0.0,
              float(max(-min_cash, 0)), float(peak_o), sum_o / N, float(peak_l), float(peak_c), float(unrecovered),
              1.0 if unrecovered == 0 else 0.0, float(out[0, N - 1]), float(atm), float(off),
              1.0 if head_n > 0 else 0.0, float(head_n), float(head_neg)):
        sc[i] += w * v
        F[i] = v
        i += 1

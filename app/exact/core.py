"""The exact evaluator's chain core for one draw (design/002-exact-evaluator.md, port order 2): the judgment's timeline
and amounts, §1961 interest, the amount owed. Each function reproduces the `events.Chain` method it names on one
draw, operation for operation (int64 cents; interest in float64 as `interest_1961` computes it, `k_owed._i1961`).

The case's constants (`Case`) and each draw's own (`draw_constants`: the verdict day and the ruling lags, from the
seeded uniforms `Draws.u` keys) are computed in Python once; the kernels read them as arrays.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit

from app.analysis.k_owed import _i1961
from app.exact import fields as f

# per-draw constants (draw_constants): column indices
DC_V = 0             # Chain._timeline_pending: the verdict day
DC_LAG_COMMON = 1    # Draws.lag(model, iid, "common")
DC_LAG_SECOND = 2    # Draws.lag(model, iid, "second")
DC_LAG_ENTRY = 3     # Draws.lag(model, iid, "entry")
DC_LAG_REG_I1 = 4    # Draws.lag(model, iid, "registration_I1")
DC_LAG_REG_POST = 5  # Draws.lag(model, iid, "registration_post")
DC_LAG_STAY_I1 = 6   # Draws.lag(model, iid, "stay_I1")
DC_LAG_STAY_POST = 7  # Draws.lag(model, iid, "stay_post")
NDC = 8
LAG_KEYS = {DC_LAG_COMMON: "common", DC_LAG_SECOND: "second", DC_LAG_ENTRY: "entry",
            DC_LAG_REG_I1: "registration_I1", DC_LAG_REG_POST: "registration_post",
            DC_LAG_STAY_I1: "stay_I1", DC_LAG_STAY_POST: "stay_post"}


@dataclass(frozen=True)
class Case:
    """What every draw shares (the Chain's settings for a pending claim): §1961's rate, the rules' days and the
    parameters the core reads. `kernel()` is the tuple the compiled functions take."""
    bps: int                  # Chain.bps
    frcp_50b_59: int          # rules frcp_50b_59_deadline
    frap_4a1a: int            # rules frap_4a1a
    frcp_62a: int             # rules frcp_62a
    briefing_days: int        # parameter briefing_days_new_motion
    entry_next_business_day: bool  # parameter judgment_entry == "next_business_day"
    sensitive_lag: bool       # Chain.sens["ruling_lag_days"]: the later of two lag draws
    stay_restart_days: int    # parameter stay_restart_on_increase_days value
    stay_restart_whole: bool  # its mode (sensitivity or base) == "whole_amount"
    pending: bool             # Chain.pending
    petition_lag: int = 0     # parameter petition_lag_days

    @classmethod
    def of(cls, chain) -> Case:
        m, p = chain.m, chain.p
        restart = m["parameters"]["stay_restart_on_increase_days"]
        mode = restart["sensitivity"] if chain.sens.get("stay_restart_on_increase_days") else restart["base"]
        return cls(bps=int(chain.bps), frcp_50b_59=int(m["rules"]["frcp_50b_59_deadline"]["value"]),
                   frap_4a1a=int(m["rules"]["frap_4a1a"]["value"]), frcp_62a=int(m["rules"]["frcp_62a"]["value"]),
                   briefing_days=int(p("briefing_days_new_motion")),
                   entry_next_business_day=p("judgment_entry") == "next_business_day",
                   sensitive_lag=bool(chain.sens.get("ruling_lag_days")),
                   stay_restart_days=int(restart["value"]), stay_restart_whole=mode == "whole_amount",
                   pending=bool(chain.pending), petition_lag=int(p("petition_lag_days")))


def draw_constants(chain) -> np.ndarray:
    """[draws, NDC]: each draw's verdict day and ruling lags (the Chain's seeded uniforms), in its draws' order."""
    out = np.zeros((chain.n, NDC), dtype=np.int64)
    out[:, DC_V] = chain.V
    for col, key in LAG_KEYS.items():
        out[:, col] = chain.dr.lag(chain.m, chain.iid, key)
    return out


def entry_days(chain) -> np.ndarray:
    """[draws]: the entry day under next_business_day (`Chain._enter`): the next business day after the verdict, a
    calendar computation done in Python per verdict day."""
    from datetime import timedelta

    from app.analysis.events import business_days_after

    nxt = {int(v): chain.ix(business_days_after(chain.s.review + timedelta(days=int(v) + 1), 1))
           for v in np.unique(chain.V)}
    return np.array([nxt[int(v)] for v in chain.V], dtype=np.int64)


# --- the amount owed (Chain._owed_gross, taken_before, owed_at, live) -----------------------------------------------

@njit(cache=True)
def owed_gross(S, bps, pending, day, enforceable):
    """`Chain._owed_gross` on this draw at `day` (a pending claim: has_judgment is entered > 0)."""
    entered = S[f.ENTERED]
    has_j = (not pending) or entered > 0
    se_f = 0.0
    if has_j:
        se = day - S[f.E_IX]
        se_f = np.float64(se if se > 0 else 0)
    ce = entered * bps / 10_000
    out = entered + _i1961(ce, se_f, 0.0, 0.0)
    if S[f.CLS_AMOUNT] != f.NONE:
        cls = S[f.CLS_AMOUNT]
        base = cls if cls < entered else entered
        sa = day - S[f.F]
        after = cls + _i1961(base * bps / 10_000, se_f, (cls - base) * bps / 10_000, np.float64(sa if sa > 0 else 0))
        if day < S[f.FEE_DAY]:
            after -= S[f.CLS_FEES]
        if enforceable and S[f.INCREASE] != 0 and day < S[f.EI]:
            b2 = base + _i1961(base * bps / 10_000, se_f, 0.0, 0.0)
            after = after if after < b2 else b2
        if day >= S[f.F]:
            out = after
    if pending and day < S[f.E_IX]:
        out = 0
    return out


@njit(cache=True)
def taken_before(S, T_TAKES, day):
    """`Chain.taken_before` for a pending claim: levied or paid toward the judgment before the day."""
    out = 0
    for k in range(S[f.N_TAKES]):
        if T_TAKES[k, 0] < day:
            out += T_TAKES[k, 1]
    return out


@njit(cache=True)
def owed_at(S, T_TAKES, bps, pending, day, enforceable):
    """`Chain.owed_at` on this draw at `day` (a pending claim)."""
    if day >= S[f.RESOLVED]:
        return 0
    v = owed_gross(S, bps, pending, day, enforceable) - taken_before(S, T_TAKES, day)
    return v if v > 0 else 0


@njit(cache=True)
def live(S, day):
    """`Chain.live`: no petition before the day and the judgment not resolved by it."""
    pet = f.BIG if S[f.PETITION] < 0 else S[f.PETITION]
    return day < pet and day < S[f.RESOLVED]


# --- the judgment's timeline (Chain._enter and the verdict, post_trial_motions, post_trial_ruling steps) -------------

@njit(cache=True)
def court_day(court, t):
    """`Chain.court_day`: the first day on or after day t that is not a weekend or legal holiday (FRAP 26(a)(1)(C));
    `court` maps each day index of the case's calendar to it (beyond it: t)."""
    return court[t] if 0 <= t < court.size else t


@njit(cache=True)
def enter(S, DC, case_next_bd, entry_nbd, frcp_50b_59, briefing, frcp_62a, frap_4a1a, court):
    """`Chain._enter`: the entry day, execution from entry + Rule 62(a) + 1, the appeal deadline."""
    if case_next_bd:
        S[f.E_IX] = entry_nbd
    else:
        S[f.E_IX] = DC[DC_V] + frcp_50b_59 + briefing + DC[DC_LAG_ENTRY]
    S[f.EX_IX] = S[f.E_IX] + frcp_62a + 1
    S[f.E0] = S[f.EX_IX] if S[f.EX_IX] > 0 else 0
    S[f.AD] = court_day(court, S[f.E_IX] + frap_4a1a)


@njit(cache=True)
def step_verdict_award(S, DC, total, lo, hi, top, case_next_bd, entry_nbd, frcp_50b_59, briefing, frcp_62a,
                       frap_4a1a, court):
    """`Chain.step('verdict', ..., 'award:<total>:<lo>:<hi>')`: the award entered and its band; the claimant's amount
    (hi 'top') is the adverse branch. Returns the decision day (the verdict day)."""
    S[f.ENTERED] = total
    S[f.BAND_LO] = lo
    S[f.BAND_HI] = f.NONE if top else hi
    enter(S, DC, case_next_bd, entry_nbd, frcp_50b_59, briefing, frcp_62a, frap_4a1a, court)
    if top:
        S[f.ADVERSE_FROM] = S[f.E_IX]
    return DC[DC_V]


@njit(cache=True)
def step_verdict_branch(S, DC, judgment, amount, adverse, case_next_bd, entry_nbd, frcp_50b_59, briefing, frcp_62a,
                        frap_4a1a, court):
    """`Chain.step('verdict', ..., <template branch>)`: a branch with a judgment enters its amount (`verdict_amount`,
    computed in Python); one without books nothing. Returns the verdict day."""
    if judgment:
        S[f.ENTERED] = amount
        enter(S, DC, case_next_bd, entry_nbd, frcp_50b_59, briefing, frcp_62a, frap_4a1a, court)
        if adverse:
            S[f.ADVERSE_FROM] = S[f.E_IX]
    return DC[DC_V]


@njit(cache=True)
def mark(S, name_ix, day, where, N):
    """`Chain.mark` (not notes_due's how: the notes area's)."""
    if where and day >= 0 and day < N and day < S[name_ix]:
        S[name_ix] = day


@njit(cache=True)
def step_post_trial_motions(S, DC, yes, frcp_50b_59, briefing, frap_4a1a, sensitive_lag, N, court):
    """`Chain.step('post_trial_motions', '', yes/no)`. Returns the filing deadline (the decision day)."""
    filed = S[f.E_IX] + frcp_50b_59
    if yes:
        S[f.MOTIONS_FILED] = filed
        lag = DC[DC_LAG_COMMON]
        if sensitive_lag and DC[DC_LAG_SECOND] > lag:
            lag = DC[DC_LAG_SECOND]
        S[f.F] = filed + briefing + lag
    else:
        S[f.F] = filed
        mark(S, f.MARK_RULED, filed, True, N)
    S[f.AD] = court_day(court, (S[f.F] if yes else S[f.E_IX]) + frap_4a1a)
    S[f.A] = S[f.F] if yes else S[f.E_IX]
    S[f.EF] = S[f.F] if S[f.F] > S[f.E0] else S[f.E0]
    S[f.EI] = S[f.EF]
    return filed


# --- booking (Chain.book, pay, petition, resolve): what each writes on this draw ------------------------------------
# The Chain's versions and memos (`_touch`, `_eq_v`, `_cv`) are not state: the compiled state is read directly.

@njit(cache=True)
def book(D, row, day, cents):
    """`Chain.book` into the per-day array D[row]: inside the period and nonzero only."""
    if day >= 0 and day < D.shape[1] and cents != 0:
        D[row, day] += cents


@njit(cache=True)
def pay(S, D, day, cents, kind_row, incurred_ix, incurred):
    """`Chain.pay`: the cash and its kind (D[kind_row]); an obligation (incurred_ix >= 0) also keeps the earliest day
    it was incurred, where the amount is booked."""
    book(D, f.D_CASH, day, cents)
    book(D, kind_row, day, cents)
    if incurred_ix >= 0 and day >= 0 and day < D.shape[1] and cents != 0 and incurred < S[incurred_ix]:
        S[incurred_ix] = incurred


@njit(cache=True)
def petition(S, day, where, cause, lag, N):
    """`Chain.petition` (cause: its PETITION_CAUSES index). Returns whether it is the petition now (the caller then
    rebooks the at-the-market sales, which stop at a petition: `equity.atm_rebook`)."""
    day = day + lag
    cur = S[f.PETITION]
    win = where and day >= 0 and day < N and (cur < 0 or day < cur)
    if win:
        S[f.PETITION] = day
        S[f.PET_CAUSE] = cause
    return win


@njit(cache=True)
def resolve(S, D, legal, day, where):
    """`Chain.resolve`: the dispute ends on the day where it does (and inside the period): the stay's security is
    released then (`release_lock` under daily processing: `release_at`), and the legal spend in the operating feed
    (`legal`, this draw's, negative) stops from it, added back as a reduction."""
    N = D.shape[1]
    if not (where and day < N):
        day = f.BIG
    if where and day < N and day < S[f.RELEASE_AT]:
        S[f.RELEASE_AT] = day
    old = S[f.RESOLVED]
    if day < old:
        S[f.RESOLVED] = day
    for t in range(max(S[f.RESOLVED], 0), min(old, N)):
        D[f.D_CASH, t] -= legal[t]
        D[f.D_KIND_REDUCTION, t] -= legal[t]


def court_table(chain, span: int = 1200) -> np.ndarray:
    """`court_day` for every day index 0..span of the chain's calendar (`Chain.court_day`)."""
    return np.ascontiguousarray(chain.court_day(np.arange(span, dtype=np.int64)), dtype=np.int64)

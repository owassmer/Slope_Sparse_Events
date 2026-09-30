"""Compiled kernels for the amount owed and the share price (`Chain.owed_at`, `Chain._price_owed_grid`,
`Merton.price` on a [draws, days] grid). Each reproduces the Python it replaces operation for
operation: int64 cents; the §1961 interest in float64 as `interest_1961` computes it (the scalar coefficient
principal * bps / 10_000 computed in Python by the caller, exactly as the expression does, then per element
x max(since, 0) / 365, the two terms added, rint half to even, truncated to int64); no fastmath. Each Python-side
wrapper returns None where an input is outside what the kernel reproduces exactly (the caller then runs the Python).
The Python each replaces is kept beside it in events.py / share_price.py with a _py suffix (shadow.py checks them)."""
from __future__ import annotations

import numpy as np
from numba import njit
from numba.core import types
from numba.typed import Dict

from app.analysis.share_price import _prices


@njit(cache=True)
def _i1961(c1, se_f, c2, sa_f):
    """`interest_1961` at one element: c1 = principal * bps / 10_000, c2 = increase * bps / 10_000 (Python floats),
    se_f / sa_f = float(max(since, 0))."""
    return np.int64(np.rint(c1 * se_f / 365.0 + c2 * sa_f / 365.0))


@njit(cache=True)
def _gross(d, j, has_j, entry_pd, entry_arr, entry_s, entered, ce, cz, has_cls, cls, base, cb, cinc, fees, F,
           fee_day, enf, EI, pending, E_ix):
    """`Chain._owed_gross` at day d on draw j."""
    se_f = 0.0  # no judgment: since_entry is np.zeros (float), max(0.0, 0) = 0.0
    if has_j:
        se = d - (entry_arr[j] if entry_pd else entry_s)
        se_f = np.float64(se if se > 0 else 0)
    out = entered + _i1961(ce, se_f, cz, 0.0)  # before
    if has_cls:
        sa = d - F[j]
        after = cls + _i1961(cb, se_f, cinc, np.float64(sa if sa > 0 else 0))
        if d < fee_day[j]:
            after = after - fees
        if enf and d < EI[j]:
            b2 = base + _i1961(cb, se_f, cz, 0.0)
            after = after if after < b2 else b2
        if d >= F[j]:
            out = after
    if pending and d < E_ix[j]:
        out = 0
    return out


@njit(cache=True)
def owed_kernel(day2, has_j, entry_pd, entry_arr, entry_s, entered, ce, cz, has_cls, cls, base, cb, cinc, fees, F,
                fee_day, enf, EI, pending, E_ix, resolved, taken, T, A):
    """`Chain.owed_at` on days [rows, draws]: the gross amount less what was taken before the day (pending: the takes
    (T, A) [takes, draws] dated before it; else `taken`), 0 from the resolution."""
    R, n = day2.shape
    out = np.empty((R, n), dtype=np.int64)
    K = T.shape[0]
    for r in range(R):
        for j in range(n):
            d = day2[r, j]
            if d >= resolved[j]:
                out[r, j] = 0
                continue
            g = _gross(d, j, has_j, entry_pd, entry_arr, entry_s, entered, ce, cz, has_cls, cls, base, cb, cinc, fees,
                       F, fee_day, enf, EI, pending, E_ix)
            if pending:
                tk = np.int64(0)
                for k in range(K):
                    if T[k, j] < d:
                        tk += A[k, j]
            else:
                tk = taken[j]
            v = g - tk
            out[r, j] = v if v > 0 else 0
    return out


@njit(cache=True)
def grid_kernel(N, has_j, E_ix, entered, ce, cz, has_cls, cls, base, cb, cinc, fees, F, fee_day, resolved, V, TT, TA,
                has_settle, ST, SA, settled):
    """`Chain._price_owed_grid` [draws, days] (a pending claim with an amount entered)."""
    n = resolved.shape[0]
    out = np.empty((n, N), dtype=np.int64)
    addT = np.zeros(N + 1, dtype=np.int64)
    addS = np.zeros(N + 1, dtype=np.int64)
    for j in range(n):
        addT[:] = 0
        addS[:] = 0
        for k in range(TT.shape[0]):  # `_through`: each event at its day clipped to [0, N]
            t = min(max(TT[k, j], 0), N)
            addT[t] += TA[k, j]
        total = np.int64(0)
        for k in range(ST.shape[0]):
            total += SA[k, j]
            t = min(max(ST[k, j], 0), N)
            addS[t] += SA[k, j]
        tk = np.int64(0)
        ts = np.int64(0)
        for d in range(N):
            tk += addT[d]
            ts += addS[d]
            amt = entered
            if has_cls and d >= F[j]:
                amt = cls
            if d >= resolved[j]:
                amt = 0
            else:
                amt = amt - tk
                amt = amt if amt > 0 else 0
            if d >= E_ix[j]:
                if d >= resolved[j]:
                    amt = 0
                else:
                    g = _gross(np.int64(d), j, has_j, True, E_ix, 0, entered, ce, cz, has_cls, cls, base, cb, cinc,
                               fees, F, fee_day, False, F, True, E_ix) - tk
                    amt = g if g > 0 else 0
            if has_settle and d >= settled[j]:
                amt = total - ts
            out[j, d] = amt if d >= V[j] else 0
    return out


@njit(cache=True)
def merton_grid(a, V, notes, s, rate, T, shares):
    """`Merton.price` on [draws, days]: a row equal to an earlier row copies its prices; else each run of equal
    amounts along the row is priced once by `_prices` (the same compiled scalar code, so the same bits)."""
    n, m = a.shape
    out = np.empty((n, m), dtype=np.float64)
    buf = np.empty(m, dtype=np.int64)
    seen = Dict.empty(key_type=types.uint64, value_type=types.int64)
    for i in range(n):
        h = np.uint64(14695981039346656037)
        for j in range(m):
            h = (h ^ np.uint64(a[i, j])) * np.uint64(1099511628211)
        if h in seen:
            r = seen[h]
            eq = True
            for j in range(m):
                if a[i, j] != a[r, j]:
                    eq = False
                    break
            if eq:
                out[i, :] = out[r, :]
                continue
        else:
            seen[h] = i
        k = 0
        for j in range(m):
            if j == 0 or a[i, j] != a[i, j - 1]:
                buf[k] = a[i, j]
                k += 1
        vals = _prices(buf[:k], V, notes, s, rate, T, shares)
        k = -1
        for j in range(m):
            if j == 0 or a[i, j] != a[i, j - 1]:
                k += 1
            out[i, j] = vals[k]
    return out


# --- the Python side: the chain's inputs as the kernels' arguments (None: run the Python) ---------------------------

def _int(x) -> bool:
    return isinstance(x, (int, np.integer)) and not isinstance(x, bool)


def _arr(x, n: int):
    """x as int64 [n] (a scalar or a length-1 array filled, as broadcasting reads it), or None: not an integer array
    of a shape broadcasting to [n]."""
    a = np.asarray(x)
    if a.dtype.kind != "i":
        return None
    if a.shape == (n,):
        return a if a.dtype == np.int64 else a.astype(np.int64)
    if a.ndim <= 1 and a.size == 1:
        return np.full(n, a.reshape(()), dtype=np.int64)
    return None


def _gross_args(ch, enforceable: bool):
    """`Chain._owed_gross`'s inputs; the §1961 coefficients computed by the same expressions `interest_1961` uses."""
    n, bps, entered = ch.n, ch.bps, ch.entered
    resolved = _arr(ch.resolved, n)
    if resolved is None or not _int(bps) or not _int(entered):
        return None
    dummy = resolved  # an int64 [n] the kernel does not read
    has_j = bool(ch.has_judgment())
    entry_pd, entry_arr, entry_s = False, dummy, 0
    if has_j:
        e = ch.entry_ix()
        if np.ndim(e) == 0:
            if not _int(e):
                return None
            entry_s = int(e)
        else:
            entry_pd, entry_arr = True, _arr(e, n)
            if entry_arr is None:
                return None
    ce, cz = float(entered * bps / 10_000), float(0 * bps / 10_000)
    cls = ch.cls_amount
    has_cls, c_, base, cb, cinc, fees, F, fee_day = False, 0, 0, 0.0, 0.0, 0, dummy, dummy
    if cls is not None:
        fees = ch.cls_fees
        if not _int(cls) or not _int(fees):
            return None
        has_cls, c_, base = True, cls, min(cls, entered)
        cb, cinc = float(base * bps / 10_000), float((cls - base) * bps / 10_000)
        F, fee_day = _arr(ch.F, n), _arr(ch.fee_day, n)
        if F is None or fee_day is None:
            return None
    enf = bool(enforceable and ch.increase) and has_cls
    EI = _arr(ch.EI, n) if enf else dummy
    pending = bool(ch.pending)
    E_ix = _arr(ch.E_ix, n) if pending else dummy
    if EI is None or E_ix is None:
        return None
    return (has_j, entry_pd, entry_arr, entry_s, entered, ce, cz, has_cls, c_, base, cb, cinc, fees, F, fee_day, enf,
            EI, pending, E_ix), resolved


def _stack_takes(takes, n: int):
    """The (day, amount) takes as int64 [takes, draws] (None: a day or amount not an integer [draws] or one)."""
    T = np.empty((len(takes), n), dtype=np.int64)
    A = np.empty((len(takes), n), dtype=np.int64)
    for k, (t, a) in enumerate(takes):
        t, a = np.asarray(t), np.asarray(a)
        for x in (t, a):
            if x.dtype.kind != "i" or x.ndim > 1 or (x.ndim == 1 and x.shape[0] not in (1, n)):
                return None
        T[k], A[k] = t, a
    return T, A


def owed_at(ch, day, enforceable: bool = False):
    """`Chain.owed_at_py` compiled (None: an input the kernel does not reproduce; run the Python)."""
    day = np.asarray(day)
    n = ch.n
    if ch.d is None:
        return np.zeros(n, dtype=np.int64)
    if day.dtype.kind != "i":
        return None
    if day.ndim == 0 or day.shape == (1,):
        d2, shape = np.full((1, n), day.reshape(()), dtype=np.int64), (n,)
    elif day.shape == (n,):
        d2, shape = day.reshape(1, n), (n,)
    elif day.ndim == 2 and day.shape[1] == n:
        d2, shape = day, day.shape
    else:
        return None
    if d2.dtype != np.int64:
        d2 = d2.astype(np.int64)
    ga = _gross_args(ch, enforceable)
    if ga is None:
        return None
    g, resolved = ga
    if ch.pending:
        st = _stack_takes(ch.takes, n)
        if st is None:
            return None
        taken, (T, A) = resolved, st
    else:
        taken = _arr(ch.taken, n)
        if taken is None:
            return None
        T = A = np.empty((0, n), dtype=np.int64)
    return owed_kernel(d2, *g, resolved, taken, T, A).reshape(shape)


def _stack_per_draw(ch, events):
    """(day, amount) events as `Chain.per_draw` reads them (int64 [draws]), stacked [events, draws]."""
    T = np.empty((len(events), ch.n), dtype=np.int64)
    A = np.empty((len(events), ch.n), dtype=np.int64)
    for k, (t, a) in enumerate(events):
        T[k], A[k] = ch.per_draw(t), ch.per_draw(a)
    return T, A


def price_owed_grid(ch):
    """`Chain._price_owed_grid_py` compiled (None: run the Python)."""
    n, N = ch.n, ch.N
    if not ch.pending or not ch.entered:
        return np.zeros((n, N), dtype=np.int64)
    ga = _gross_args(ch, False)
    if ga is None:
        return None
    g, resolved = ga
    has_j, _, _, _, entered, ce, cz, has_cls, cls, base, cb, cinc, fees, F, fee_day, _, _, _, E_ix = g
    V = _arr(ch.V, n)
    if V is None:
        return None
    TT, TA = _stack_per_draw(ch, ch.takes)
    has_settle = bool(ch.settlement_parts)
    ST, SA = _stack_per_draw(ch, ch.settlement_parts)
    settled = ch.per_draw(ch.marks["settled"]) if has_settle else resolved
    return grid_kernel(N, has_j, E_ix, entered, ce, cz, has_cls, cls, base, cb, cinc, fees, F, fee_day, resolved, V,
                       TT, TA, has_settle, ST, SA, settled)


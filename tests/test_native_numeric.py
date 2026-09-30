"""Rust's numerical operations against independently retained Numba/Python references.

These exercise branch boundaries, rounding, clipping, empty dimensions, strided
buffers, cumulative state and the complete calibrated-price calculation.
"""
from itertools import product

import numpy as np
import pytest

from app.analysis import k_atm, k_owed, shadow
from app.analysis.native import native_function
from app.analysis.share_price import Merton, _prices_numba, closes, equity_vol_py
from app.disputes.rules import load_model


@pytest.fixture(autouse=True)
def select_native_for_differential_tests(monkeypatch):
    """These tests call Rust directly even when the application suite uses the oracle."""
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND","rust")


def rust(name):
    kernel = native_function(name)
    assert kernel is not None, "these differential tests require SLOPE_EXECUTION_BACKEND=rust"
    return kernel


def exact(actual, expected):
    assert shadow.same(actual, expected), shadow.where(actual, expected)


@pytest.mark.parametrize("n", [0, 1, 7])
def test_owed_matches_at_all_judgment_enforcement_and_resolution_boundaries(n):
    rng = np.random.default_rng(1961)
    days = rng.integers(-3, 26, (11, n), dtype=np.int64)
    entry = rng.integers(0, 8, n, dtype=np.int64)
    f = rng.integers(7, 14, n, dtype=np.int64)
    fee = f + 2
    ei = f + 4
    resolved = rng.integers(17, 25, n, dtype=np.int64)
    taken = rng.integers(0, 300, n, dtype=np.int64)
    tt = rng.integers(-2, 23, (4, n), dtype=np.int64)
    ta = rng.integers(0, 300, (4, n), dtype=np.int64)
    # Half-cent rates deliberately hit both odd and even tie cases.
    for has_j, per_draw, has_cls, enforceable, pending in product((False, True), repeat=5):
        args = (days, has_j, per_draw, entry, 3, 1000, 182.5, 0.0, has_cls,
                1300, 1000, 182.5, 547.5, 100, f, fee, enforceable, ei, pending,
                entry, resolved, taken, tt, ta)
        exact(rust("owed_kernel")(*args), k_owed.owed_kernel_numba(*args))


@pytest.mark.parametrize("n_days,n", [(0, 3), (20, 0), (1, 1), (31, 9)])
def test_price_owed_grid_matches_clipped_takes_and_settlement_remainders(n_days, n):
    rng = np.random.default_rng(1998)
    entry = rng.integers(-3, 16, n, dtype=np.int64)
    f = rng.integers(7, 23, n, dtype=np.int64)
    fee = f + 3
    resolved = rng.integers(19, 30, n, dtype=np.int64)
    verdict = rng.integers(-3, 6, n, dtype=np.int64)
    tt = rng.integers(-5, 40, (5, n), dtype=np.int64)
    ta = rng.integers(-5, 240, (5, n), dtype=np.int64)
    st = rng.integers(-5, 40, (3, n), dtype=np.int64)
    sa = rng.integers(10, 550, (3, n), dtype=np.int64)
    settled = rng.integers(10, 26, n, dtype=np.int64)
    for has_j, has_cls, has_settle in product((False, True), repeat=3):
        args = (n_days, has_j, entry, 1000, 182.5, 0.0, has_cls, 1400, 1000,
                182.5, 365.0, 100, f, fee, resolved, verdict, tt, ta, has_settle,
                st, sa, settled)
        exact(rust("grid_kernel")(*args), k_owed.grid_kernel_numba(*args))


@pytest.mark.parametrize("use_close,have_old,lock_on", list(product((False, True), repeat=3)))
def test_atm_matches_lockup_ledger_stop_and_signed_half_even_settlements(use_close, have_old, lock_on):
    n, n_days = 5, 13
    sale = np.array([-2, 0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 17], dtype=np.int64)
    stop = np.array([100, 9, 7, 4, 0], dtype=np.int64)
    init = np.array([[-2, 0, 2, 4, 100], [2, 3, 4, 100, 100]], dtype=np.int64)
    close = init + 3
    closed = np.array([[True, False, True, False, True], [True, True, False, True, False]])
    shares = np.array([[2, 4, 2, 7, 1], [1, 1, 3, 4, 4]], dtype=np.int64)
    j = np.array([0, 2, 1, 3, 4, 5, 6, 7, 8, 9], dtype=np.int64)
    days = np.array([1, 4, 8, 12], dtype=np.int64)
    start = np.array([0, 3, 5, 8], dtype=np.int64)
    sp = np.tile(np.array([0.5, 1.5, 2.5, -0.5, -1.5, 12.5, 3.5, 4.5, 1., 3., 8., 2., 0.5]), (n, 1))
    old = np.arange(n * n_days, dtype=np.int64).reshape(n, n_days)
    args = (sale, stop, 1, 8, init, close, closed, shares, lock_on, 100, 1, 2,
            j, days, start, sp, use_close, 12.5, n_days, 275, old, have_old)
    exact(rust("atm_book")(*args), k_atm.atm_book_numba(*args))


@pytest.mark.parametrize("n,n_days", [(0, 12), (3, 0), (1, 1)])
def test_atm_empty_sales_and_offers(n, n_days):
    z = np.empty(0, dtype=np.int64)
    offers = np.empty((0, n), dtype=np.int64)
    args = (z, np.full(n, 100, dtype=np.int64), 1, 8, offers, offers, offers.astype(bool), offers,
            False, 100, 0, 0, z, z, z, np.zeros((1, 1)), True, 44.0, n_days, 300,
            np.zeros((1, 1), dtype=np.int64), False)
    exact(rust("atm_book")(*args), k_atm.atm_book_numba(*args))


def test_merton_prices_match_for_distinct_repeated_empty_and_strided_amounts():
    args = (5.2e9, 4_400_000_000, 0.48, 0.0516, 1.0, 98_669_282)
    values = np.array([-100, 0, 1, 10000, 142641200, 3_860_000_000, 5_000_000_000], dtype=np.int64)
    exact(rust("prices")(values, *args), _prices_numba(values, *args))
    for grid in (np.empty((0, 7), dtype=np.int64), np.empty((4, 0), dtype=np.int64),
                 np.tile(values, (4, 1)), np.tile(values, (4, 1))[:, ::-1],
                 np.array([[0, 0, 100, 100], [0, 0, 100, 100], [100, 100, 0, 0]], dtype=np.int64)):
        exact(rust("merton_grid")(grid, *args), k_owed.merton_grid_numba(grid, *args))


def test_native_calibration_volatility_and_scalar_call_match_python_float_bits():
    px = [44.0, 42.0, 47.0, 43.0, 45.0, 52.0, 38.0, 41.0]
    vol = rust("equity_vol")(px)
    exact(vol, equity_vol_py(px))
    model = Merton(98_669_282, 44, 4_400_000_000, 0.0516, 1.0, vol)
    reference = model.calibrated_py()
    v, s = rust("merton_calibrate")(model.shares, model.close_cents, model.notes_cents,
                                      model.rate, model.T, model.equity_vol)
    exact(v, reference.V)
    exact(s, reference.asset_vol)
    exact(rust("merton_call")(v, model.notes_cents, s, model.rate, model.T), model.call_py(v, model.notes_cents, s))


def test_real_price_table_volatility_and_calibration_match_independent_python_bits():
    params=load_model("akoustis_20240514")["parameters"]["share_price"]
    px=closes(params["price_table"])
    vol=rust("equity_vol")(px)
    exact(vol,equity_vol_py(px))
    model=Merton(int(params["shares_outstanding"]),int(params["close_cents"]),
                 int(params["notes_face_cents"]),int(params["rate_bps"])/10000,
                 float(params["horizon_years"]),vol)
    ref=model.calibrated_py()
    v,s=rust("merton_calibrate")(model.shares,model.close_cents,model.notes_cents,
                                  model.rate,model.T,model.equity_vol)
    exact(v,ref.V)
    exact(s,ref.asset_vol)


def test_native_numeric_validates_dimensions_before_releasing_the_interpreter():
    with pytest.raises(ValueError, match="draws"):
        rust("owed_kernel")(np.zeros((1, 2), dtype=np.int64), False, False,
                            np.zeros(1, dtype=np.int64), 0, 100, 0., 0., False,
                            0, 0, 0., 0., 0, *([np.zeros(2, dtype=np.int64)] * 2),
                            False, np.zeros(2, dtype=np.int64), False,
                            *([np.zeros(2, dtype=np.int64)] * 3),
                            np.zeros((0, 2), dtype=np.int64), np.zeros((0, 2), dtype=np.int64))


@pytest.mark.parametrize("arguments", [
    (5.2e9, 4_400_000_000, 0.0, 0.05, 1.0, 100),
    (5.2e9, 4_400_000_000, 0.5, 0.05, 0.0, 100),
    (5.2e9, 4_400_000_000, 0.5, 0.05, 1.0, 0),
    (5.2e9, 0, 0.5, 0.05, 1.0, 100),
])
def test_native_price_division_failures_match_the_numba_reference(arguments):
    owed=np.array([0],dtype=np.int64)
    with pytest.raises(ZeroDivisionError):
        _prices_numba(owed,*arguments)
    with pytest.raises(ZeroDivisionError):
        rust("prices")(owed,*arguments)
    # An empty input executes no per-price divisions in the reference.
    empty=np.empty(0,dtype=np.int64)
    exact(rust("prices")(empty,*arguments),_prices_numba(empty,*arguments))


@pytest.mark.parametrize("px,error", [
    ([0.,1.,2.],ZeroDivisionError),
    ([1.,-1.,2.],ValueError),
    ([1.,0.,2.],ValueError),
    ([1.,2.],ZeroDivisionError),
])
def test_native_volatility_domain_failures_match_python(px,error):
    with pytest.raises(error):
        equity_vol_py(px)
    with pytest.raises(error):
        rust("equity_vol")(px)


@pytest.mark.parametrize("v,k,s,t,error", [
    (1.,0.,0.5,1.,ZeroDivisionError),
    (1.,1.,0.,1.,ZeroDivisionError),
    (1.,1.,0.5,0.,ZeroDivisionError),
    (0.,1.,0.5,1.,ValueError),
    (1.,1.,0.5,-1.,ValueError),
])
def test_native_scalar_call_domain_failures_match_python(v,k,s,t,error):
    model=Merton(100,1,1,0.05,t,1.)
    with pytest.raises(error):
        model.call_py(v,k,s)
    with pytest.raises(error):
        rust("merton_call")(v,k,s,model.rate,t)


@pytest.mark.parametrize("notes,v", [(1,5e9),(np.iinfo(np.int64).max,5e9),(1,-1.)])
def test_merton_array_prices_preserve_int64_wrapping_and_domain_nan_bits(notes,v):
    owed=np.array([np.iinfo(np.int64).min,-1,0,1,np.iinfo(np.int64).max],dtype=np.int64)
    arguments=(v,int(notes),.5,.05,1.,100)
    exact(rust("prices")(owed,*arguments),_prices_numba(owed,*arguments))

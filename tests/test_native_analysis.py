"""Financial reduction boundaries: integer cents, masks, ties and float bits."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from app import _native
from app.analysis.core import Bins, Reduction
from app.analysis.engine import installment_amounts
from app.analysis.shadow import same, where


def test_installments_preserve_integer_wrap_and_last_payment_remainder(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    amounts = np.array([0, 1, -1, 23, -23, 1_000_000, -(1 << 63), (1 << 63) - 1], dtype=np.int64)
    for values in (amounts, amounts[::-2]):
        for fee in (0, 370, 10_000):
            for count in (1, 3, 7):
                expected = installment_amounts(values, fee, count)
                actual = _native.analysis_installments(values, fee, count)
                assert same(actual, expected), (values, fee, count)
    with pytest.raises(ValueError):
        _native.analysis_installments(amounts, 370, 0)


def test_linear_cent_quantiles_match_numpy_interpolation_and_overflow_bits():
    rng = np.random.default_rng(781)
    cases = [np.array([-(1 << 63), (1 << 63) - 1], dtype=np.int64), np.zeros(9, dtype=np.int64)]
    cases += [rng.integers(-9_000_000_000_000_000, 9_000_000_000_000_000, size=n, dtype=np.int64)
              for n in (1, 2, 3, 4, 17, 128, 129)]
    for values in cases:
        for x in (values, values[::-1]):
            for q in (0.0, 0.05, 0.1, 0.5, 0.95, 1.0):
                with np.errstate(over="ignore"):
                    expected = float(np.quantile(x, q))
                assert same(_native.analysis_quantile(x, q), expected), (x, q)
    for q in (-0.1, 1.1, np.nan):
        with pytest.raises(ValueError):
            _native.analysis_quantile(cases[0], q)
    with pytest.raises(IndexError):
        _native.analysis_quantile(np.zeros(0, dtype=np.int64), 0.5)


def test_limit_lower_quantile_and_mean_keep_dtype_order_and_noncontiguous_inputs():
    rng = np.random.default_rng(899)
    values = rng.integers(-1000, 10_000, (29, 7), dtype=np.int64)
    for limit in (values, values[::-2, ::2], values.astype(np.float64)):
        expected = (limit.mean(axis=0), np.quantile(limit, 0.05, axis=0, method="lower"))
        assert same(_native.analysis_limit_summary(limit), expected)


def test_first_floor_keeps_mask_normalization_stable_ties_and_no_floor(monkeypatch):
    r = Reduction.__new__(Reduction)
    r.draws, r.days = 4, 12
    r.floor_day = np.array([[0, 0, -1, 11], [1, -1, 1, -1], [-1, -1, -1, -1]], dtype=np.int16)
    r.mask = np.array([[True, False, True, False], [False, True, False, True], [True] * 4])
    for r.masked in (False, True):
        for probabilities in (np.array([0.2, 0.7, 0.1]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])):
            monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
            expected = r.first_floor(probabilities)
            weights = r.draw_weights(probabilities)
            assert same(_native.analysis_first_floor(r, probabilities), expected)
            native_weights = _native.analysis_draw_weights(r, probabilities)
            assert same(native_weights, weights)
            assert native_weights.flags.writeable == weights.flags.writeable


def test_reduction_preserves_distinct_masked_integer_sum_and_unmasked_float_mean(monkeypatch):
    """The full draw denominator and sum-vs-mean rule must survive extraction."""
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    n, days = 3, 3
    huge = np.array([(1 << 62) + 1, (1 << 62) + 3, (1 << 62) + 7], dtype=np.int64)
    zeros = np.zeros(n, dtype=np.int64)
    matrix = np.zeros((n, days), dtype=np.int64)
    t = SimpleNamespace(**{k: huge.copy() for k in (
        "lender_pv", "pv_fundings", "pv_collections", "dollar_days", "drawn", "fees", "contractual", "collected",
        "stayed", "stayed_principal", "preference", "not_yet_due", "uncollected", "min_cash")})
    t.fees = t.contractual - t.drawn
    t.opening_principal = 0
    for k in ("cash", "due", "collections", "fundings", "outstanding", "locked", "capacity"):
        setattr(t, k, matrix.copy())
    t.cash[:, -1] = huge
    t.min_headroom, t.petition = np.full(n, np.iinfo(np.int64).max), np.full(n, -1, dtype=np.int64)
    t.headroom, t.headroom_rows, t.headroom_days = zeros[:0], zeros[:0], zeros[:0]
    t.processed = None
    bins = {"cash": Bins(np.zeros(days), np.full(days, 1e18), 128),
            "collected": Bins(np.zeros(days), np.ones(days), 128), "headroom": Bins(np.zeros(1), np.ones(1), 1024)}
    for mask in (None, np.array([True, True, False]), np.zeros(n, dtype=bool)):
        reference = Reduction(1, n, days, SimpleNamespace(facility_cents=0), matrix, bins, np.zeros(days, dtype=np.int64))
        native = copy.deepcopy(reference)
        reference.add(0, t, mask=mask)
        _native.analysis_reduce_add(native, 0, t, None, mask)
        assert same(native, reference), where(native, reference)
    # Distinguish the overflowing integer sum from the unmasked float mean.
    assert float(huge.mean()) != float(huge.sum()) / n

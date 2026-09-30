"""Differential contracts for the native financial recurrences.

These exercise both collection policies, both same-day orderings, branching
batches, petition freezes, distinct obligation classes, retry ordering, routed
invoices, opening exposure, and float64 queue accumulation. Every output and
every ordered log is compared byte for byte to the independent Numba oracle.
"""
from __future__ import annotations

import numpy as np
import pytest

from app import _native as native
from app.analysis.engine import _net_kernel, _stable_buckets
from app.analysis.processor import _daily_kernel


def exact(actual, expected):
    if isinstance(expected, tuple):
        assert isinstance(actual, tuple)
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected, strict=True):
            exact(a, e)
        return
    assert actual.dtype == expected.dtype
    assert actual.shape == expected.shape
    assert actual.tobytes() == expected.tobytes()


def inputs(seed, debit, first_op, strided=False):
    rng = np.random.default_rng(seed)
    n, rn, days, inst, slots = 3, 6, 18, 3, 2
    tail = days + 8
    need = rng.integers(0, 250, (n, days), dtype=np.int64)
    limit = rng.integers(0, 2500, (n, days), dtype=np.int64)
    routes = rng.integers(0, 201, (n, days, slots), dtype=np.int64)
    routes[rng.random(routes.shape) < 0.6] = 0
    due_idx = np.arange(days, dtype=np.int64)[:, None] + np.array([1, 3, 6], dtype=np.int64)
    book_d = np.array([0, 2, 2, 6, 16, tail - 1], dtype=np.int64)
    book_a = rng.integers(10, 200, len(book_d), dtype=np.int64)
    due0 = np.zeros(tail, dtype=np.int64)
    np.add.at(due0, book_d, book_a)
    post = rng.integers(0, 550, (rn, days), dtype=np.int64)
    levy = rng.integers(0, 81, (rn, days), dtype=np.int64)
    out = rng.integers(0, 300, (rn, days), dtype=np.int64) + np.tile(routes.sum(axis=2), (2, 1))
    obl = rng.integers(30, 501, (3, rn, days), dtype=np.int64)
    obl[rng.random(obl.shape) < 0.9] = 0
    inc = rng.integers(-200, days, (3, rn), dtype=np.int64)
    pet = rng.choice(np.array([0, 5, days, days, days], dtype=np.int64), rn)
    month_end = (np.arange(days) % 5 == 4)
    base = post - out - levy - obl.sum(axis=0)
    fee = int(rng.integers(0, 1400))
    opening = int(rng.integers(0, 1500))
    funded0, contract0 = int(due0.sum() * 0.9), int(due0.sum())
    cap = int((routes > 0).sum()) * (rn // n)
    if strided:
        def stride(a):
            shape = (*a.shape[:-1], a.shape[-1] * 2)
            doubled = np.zeros(shape, dtype=a.dtype)
            doubled[..., ::2] = a
            return doubled[..., ::2]
        post, levy, out, obl, inc, pet, need, limit, month_end, routes, due_idx, due0, book_d, book_a, base = (
            stride(a) for a in (post, levy, out, obl, inc, pet, need, limit, month_end, routes,
                               due_idx, due0, book_d, book_a, base))
    daily = (post, levy, out, obl, inc, pet, need, limit, month_end, routes, due_idx, fee, inst, debit,
             first_op, due0, book_d, book_a, opening, funded0, contract0, cap, 3, 6000)
    net = (base, pet, need, limit, month_end, routes, due_idx, fee, inst, debit, due0, book_d,
           book_a, opening, funded0, contract0, cap)
    return daily, net


@pytest.mark.parametrize("debit", [False, True])
@pytest.mark.parametrize("first_op", [False, True])
@pytest.mark.parametrize("strided", [False, True])
def test_daily_and_net_match_all_arrays_and_ordered_logs(debit, first_op, strided):
    for seed in range(16):
        daily, net = inputs(seed, debit, first_op, strided)
        expected = _daily_kernel(*daily)
        exact(native.daily_kernel(*daily), expected)
        exact(native.daily_cash_kernel(*daily), (expected[0], expected[10], expected[11], expected[9]))
        exact(native.net_kernel(*net), _net_kernel(*net))


@pytest.mark.parametrize("debit", [False, True])
@pytest.mark.parametrize("first_op", [False, True])
def test_daily_nonpayment_queue_float_conversion_and_wrapping(debit, first_op):
    daily, _ = inputs(27, debit, first_op)
    args = list(daily)
    args[0].fill(0)
    args[1].fill(0)
    args[2].fill(0)
    args[3].fill(0)
    args[3][0, :, 0] = 2**53 + 1
    args[3][1, :, 0] = 1
    args[3][2, :, 1] = 2**53 + 3
    args[5].fill(args[0].shape[1])
    args[9].fill(0)
    args[18] = 0
    args[22] = 2
    args[23] = 7500
    expected = _daily_kernel(*args)
    exact(native.daily_kernel(*args), expected)
    exact(native.daily_cash_kernel(*args), (expected[0], expected[10], expected[11], expected[9]))


def test_kernels_do_not_mutate_shared_inputs():
    daily, net = inputs(71, True, False)
    arrays = [a for a in daily if isinstance(a, np.ndarray)] + [net[0]]
    before = [a.tobytes() for a in arrays]
    for a in arrays:
        a.flags.writeable = False
    exact(native.daily_kernel(*daily), _daily_kernel(*daily))
    expected = _daily_kernel(*daily)
    exact(native.daily_cash_kernel(*daily), (expected[0], expected[10], expected[11], expected[9]))
    exact(native.net_kernel(*net), _net_kernel(*net))
    assert [a.tobytes() for a in arrays] == before


def test_native_rejects_invalid_indices_before_releasing_interpreter():
    _, net = inputs(12, True, False)
    args = list(net)
    args[6] = args[6].copy()
    args[6][0, 0] = -1
    with pytest.raises(ValueError, match="invalid cash-kernel"):
        native.net_kernel(*args)


def test_log_capacity_hint_does_not_change_results_or_overallocate():
    daily, net = inputs(31, True, False)
    daily_large, net_large = list(daily), list(net)
    daily_large[21] = net_large[16] = np.iinfo(np.uintp).max
    expected = _daily_kernel(*daily)
    exact(native.daily_kernel(*daily_large), expected)
    exact(native.daily_cash_kernel(*daily_large), (expected[0], expected[10], expected[11], expected[9]))
    exact(native.net_kernel(*net_large), _net_kernel(*net))


def test_native_log_bucket_order_is_stable_and_matches_oracle():
    rng = np.random.default_rng(82)
    for count, length in ((0, 0), (1, 200), (30, 1000), (180 * 12, 7000)):
        bucket = rng.integers(0, count, length, dtype=np.int64) if length else np.zeros(0, dtype=np.int64)
        exact(native.stable_buckets(bucket, count), _stable_buckets(bucket, count))
        exact(native.stable_buckets(bucket[::-2], count), _stable_buckets(bucket[::-2], count))
    with pytest.raises(ValueError, match="within the bucket count"):
        native.stable_buckets(np.array([-1], dtype=np.int64), 5)
    with pytest.raises(ValueError, match="bucket count is too large"):
        native.stable_buckets(np.zeros(0, dtype=np.int64), np.iinfo(np.uintp).max)

"""Byte contracts for the sharded reducer, including masked full-draw denominators."""
from __future__ import annotations

import copy
import heapq
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from app import _native
from app.analysis import reduce
from app.analysis.core import ARREARS_KEYS, SCALARS, Bins


def exact(actual, expected):
    if isinstance(expected, np.ndarray):
        assert actual.dtype == expected.dtype and actual.shape == expected.shape
        if expected.dtype == np.dtype(np.longdouble) and np.finfo(np.longdouble).nmant == 63:
            a = actual.ravel().view(np.uint8).reshape(-1, expected.dtype.itemsize)[:, :10]
            e = expected.ravel().view(np.uint8).reshape(-1, expected.dtype.itemsize)[:, :10]
            assert a.tobytes() == e.tobytes()
        else:
            assert actual.tobytes() == expected.tobytes()
    elif isinstance(expected, dict):
        assert list(actual) == list(expected)
        for k in expected:
            exact(actual[k], expected[k])
    elif isinstance(expected, (tuple, list)):
        assert isinstance(actual, type(expected)) and len(actual) == len(expected)
        for a, e in zip(actual, expected, strict=True):
            exact(a, e)
    elif isinstance(expected, float):
        assert type(actual) is float
        assert struct.pack("d", actual) == struct.pack("d", expected)
    elif isinstance(expected, (SimpleNamespace, Bins)):
        exact(vars(actual), vars(expected))
    elif isinstance(expected, np.generic):
        assert type(actual) is type(expected)
        a, e = actual.tobytes(), expected.tobytes()
        if isinstance(expected, np.longdouble) and np.finfo(np.longdouble).nmant == 63:
            a, e = a[:10], e[:10]  # x87 storage has six non-value padding bytes
        assert a == e
    else:
        assert type(actual) is type(expected) and actual == expected


def fixture(processed=True, extreme=False, strided=False):
    rng = np.random.default_rng(347)
    n, days = 17, 11
    values = {name: rng.integers(-20, 100, (n, days), dtype=np.int64)
              for name in ("cash", "due", "collections", "fundings", "outstanding", "locked", "capacity")}
    if extreme:
        for name in ("due", "collections", "fundings", "outstanding"):
            values[name][::2] = np.iinfo(np.int64).max - 17
    for name in SCALARS:
        values.setdefault(name, rng.integers(-200, 1000, n, dtype=np.int64))
    values["opening_principal"] = 37
    values["fees"] = values["contractual"] - values["drawn"] - values["opening_principal"]
    values["min_cash"] = values["cash"].min(axis=1)
    values["petition"] = np.array([-1, 0, 1, 3, 6, 10, 11] * 3, dtype=np.int64)[:n]
    values["min_headroom"] = rng.integers(-40, 80, n, dtype=np.int64)
    values["min_headroom"][::4] = np.iinfo(np.int64).max
    values["headroom"] = rng.integers(-40, 80, 43, dtype=np.int64)
    values["headroom_rows"] = rng.integers(0, n, 43, dtype=np.int64)
    values["headroom_days"] = rng.integers(0, days, 43, dtype=np.int64)
    # Float scalar reductions exercise NumPy's pairwise tree and signed zero.
    values["lender_pv"] = np.resize(np.array([1e16, 1., -1e16, -0., 1e-11, 2.]), n)
    values["pv_fundings"] = rng.random(n) * 13
    values["pv_collections"] = rng.random(n) * 71
    values["dollar_days"] = rng.random(n) * 3
    values["processed"] = SimpleNamespace(arrears=rng.integers(0, 99, (n, days, len(ARREARS_KEYS)),
                                                               dtype=np.int64)) if processed else None
    if strided:
        for key, a in list(values.items()):
            if isinstance(a, np.ndarray):
                expanded = np.empty((*a.shape[:-1], a.shape[-1] * 2), dtype=a.dtype)
                expanded[..., ::2] = a
                values[key] = expanded[..., ::2]
    t = SimpleNamespace(**values)
    bins = {
        "cash": Bins(np.full(days, -100.), np.full(days, 20.), 16),
        "collected": Bins(np.full(days, -1000.), np.full(days, 100.), 16),
        "headroom": Bins(np.full(3, -100.), np.full(3, 20.), 16),
    }
    ranges = {"min_cash": (-20., 1.), "collected": (-100., 1.), "min_headroom": (-30., 1.)}
    tables = reduce.Tables(["central", "endpoint"], ["yes", "no"], n, days, bins,
                           np.arange(days, dtype=np.int64) % 3, np.full((n, days), 10, dtype=np.int64),
                           ranges, 200)
    p = SimpleNamespace(steps=(("settle", "I0", "no"), ("verdict", "", "award"),
                               ("post_trial_ruling", "", "same")), outcome="survives")
    ev = SimpleNamespace(proceeds={"atm_proceeds": rng.random(n) * 19,
                                   "offering_proceeds": rng.random(n) * 3})
    masks = (None, np.arange(n) % 3 == 0, np.zeros(n, dtype=bool))
    groups = [(mask, np.array([.731, 0.]) if j == 0 else np.array([0., 1.]), np.array([.213, .787]),
               [("Q", "yes", .071), ("Q", "no", -.31), ("Q", "yes", .011), ("Z", "x", 0.)])
              for j, mask in enumerate(masks)]
    return tables, p, t, ev, groups


@pytest.mark.parametrize("processed,strided,extreme", [(False, False, False), (True, False, False),
                                                       (True, True, False), (True, False, True)])
def test_tables_add_merge_and_readers_preserve_all_bytes(monkeypatch, processed, strided, extreme):
    table, p, t, ev, groups = fixture(processed, extreme, strided)
    ref = copy.deepcopy(table)
    rows = [{"min_cash_p5_cents": v, "label": str(i)} for i, v in enumerate((-.0, 31., -77., 31.))]
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    with np.errstate(over="ignore", invalid="ignore"):
        for row in rows:
            ref.add(p, t, ev, groups, row)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    with np.errstate(over="ignore", invalid="ignore"):
        for row in rows:
            table.add(p, t, ev, groups, row)
    exact(vars(table), vars(ref))
    for name, args in (("expected", (0,)), ("metrics", (0,)), ("first_floor", (0,)),
                       ("daily", (0, t.outstanding)), ("override", ("Q", {"yes": 1., "no": 0.},
                                                                         {"yes": .213, "no": .787}))):
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
        with np.errstate(over="ignore", invalid="ignore"):
            expected = getattr(ref, name)(*args)
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
        with np.errstate(over="ignore", invalid="ignore"):
            exact(getattr(table, name)(*args), expected)
    other, _, _, _, _ = fixture(processed, extreme, strided)
    other.skeys = ref.skeys
    other.dscal = {("new", "answer"): np.arange(len(ref.skeys), dtype=np.float64)}
    other.dser = {("new", "answer"): np.arange(4 * table.days, dtype=np.float64).reshape(4, table.days)}
    other.stress = [(float(i % 7), i, {"min_cash_p5_cents": -float(i % 7)}) for i in range(513)]
    heapq.heapify(other.stress)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    ref.merge(other)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    assert table.merge(other) is table
    exact(vars(table), vars(ref))
    assert not np.shares_memory(table.dscal[("new", "answer")], other.dscal[("new", "answer")])


def test_fine_quantiles_zero_mass_endpoints_nan_and_ranges(monkeypatch):
    table, *_ = fixture()
    qs = [0., .05, .5, .95, 1., 1.5, np.nan]
    for row in (np.zeros(reduce.FINE), np.zeros(reduce.FINE)):
        row[[0, 8, 100, reduce.FINE - 1]] = [0., .125, .375, .5]
        table.fine["min_cash"][0] = row
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
        expected = table._fine_q("min_cash", 0, qs)
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
        exact(table._fine_q("min_cash", 0, qs), expected)
    table.fine["min_cash"].fill(0)
    assert table._fine_q("min_cash", 0, qs) is None
    for lo, hi in ((-0., 0.), (-4.8, 37.2), (1e20, 1e20 + 1e9), (np.nan, 7.)):
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
        expected = reduce._fine(lo, hi)
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
        exact(reduce._fine(lo, hi), expected)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    expected = reduce.fine_ranges(table.bins)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    exact(reduce.fine_ranges(table.bins), expected)


def test_zero_floor_and_invalid_group_do_not_mutate_buffers(monkeypatch):
    table, p, t, ev, groups = fixture()
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    with np.errstate(invalid="ignore"):
        expected = table.first_floor()
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    with np.errstate(invalid="ignore"):
        exact(table.first_floor(), expected)
    wrong = [(np.ones(table.draws - 1, dtype=bool), *groups[0][1:])]
    with pytest.raises(ValueError, match="group mask"):
        _native.tables_add(table, p, t, ev, wrong, None)
    assert table.paths == table.groups == 0 and table.skeys is None
    assert not table.fine["min_cash"].any() and not table.floor.any()


@pytest.mark.parametrize("dtype", [np.float32, np.longdouble])
def test_incoming_probability_scalar_dtypes_preserve_rounding_and_override(monkeypatch, dtype):
    table, p, t, ev, groups = fixture()
    groups = [(mask, probs.astype(dtype), low.astype(dtype), [(k, a, dtype(dp)) for k, a, dp in atoms])
              for mask, probs, low, atoms in groups]
    ref = copy.deepcopy(table)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    ref.add(p, t, ev, groups)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    table.add(p, t, ev, groups)
    exact(vars(table), vars(ref))
    dist = {"yes": dtype(.95), "no": dtype(.05)}
    base = {"yes": dtype(.213), "no": dtype(.787)}
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    expected = ref.override("Q", dist, base)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    exact(table.override("Q", dist, base), expected)


@pytest.mark.parametrize("bad", ["headroom_row", "headroom_day", "headroom_month", "bin_width"])
def test_invalid_indices_and_bin_metadata_are_atomic(monkeypatch, bad):
    table, p, t, ev, groups = fixture()
    if bad == "headroom_row":
        t.headroom_rows[0] = table.draws
    elif bad == "headroom_day":
        t.headroom_days[0] = table.days
    elif bad == "headroom_month":
        table.month_of_day[0] = len(table.bins["headroom"].lo)
    else:
        table.bins["cash"].width = np.ones(table.days - 1)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    with pytest.raises(ValueError):
        table.add(p, t, ev, groups)
    assert table.paths == table.groups == 0 and table.skeys is None
    assert not table.means and not table.counts["cash"].any() and not table.floor.any()


def test_merge_validates_derivative_axes_before_any_update(monkeypatch):
    table, *_ = fixture()
    other, *_ = fixture()
    table.skeys = other.skeys = ["one"]
    table.means["one"] = np.zeros(2)
    other.means["one"] = np.ones(2)
    other.per_day["cash"].fill(1)
    other.dscal[("Q", "yes")] = np.ones(2)
    other.dser[("Q", "yes")] = np.ones((4, table.days))
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    with pytest.raises(ValueError, match="merged scalar derivative"):
        table.merge(other)
    assert not table.means["one"].any() and not table.per_day["cash"].any()


def test_fine_rounding_and_quantile_threshold_keep_extended_scalar_precision(monkeypatch):
    low = np.nextafter(np.longdouble(2), np.longdouble(1))
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    expected = reduce._fine(low, np.longdouble(3))
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    exact(reduce._fine(low, np.longdouble(3)), expected)
    table, *_ = fixture()
    table.fine["min_cash"][0, [0, 1]] = [.5, .5]
    eps = np.finfo(np.longdouble).eps
    q = np.longdouble(.5) + np.longdouble(1e-12) + eps
    for span in ((-20., 1.), (np.longdouble(-20), np.longdouble(1) + eps)):
        table.ranges["min_cash"] = span
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
        expected = table._fine_q("min_cash", 0, [q])
        monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
        exact(table._fine_q("min_cash", 0, [q]), expected)


def test_no_due_no_need_preserves_optional_histogram_readers(monkeypatch):
    table, p, t, ev, groups = fixture(processed=False)
    table.need = None
    t.headroom = t.headroom_rows = t.headroom_days = np.zeros(0, dtype=np.int64)
    t.min_headroom.fill(np.iinfo(np.int64).max)
    ref = copy.deepcopy(table)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    ref.add(p, t, None, groups)
    expected = ref.metrics()
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    table.add(p, t, None, groups)
    exact(vars(table), vars(ref))
    exact(table.metrics(), expected)
    assert table._fine_q("min_headroom", 0, [0., 1.]) is None

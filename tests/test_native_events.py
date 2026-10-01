"""Native event transitions reject oracle fallback and preserve their arrays."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from app import _native
from app.analysis.events import BIG, MARKS, EventCash, interest_1961


def state(n=3, days=5):
    return dict(n=n, N=days, ev=EventCash.zeros(n, days, kinds=True), _cv=0, _av={}, _hd={}, _keys={},
                marks={k: np.full(n, BIG, dtype=np.int64) for k in MARKS},
                notes_due_how=np.full(n, "", dtype="<U11"), _how="declared")


def test_native_transition_missing_method_has_no_oracle_fallback():
    chain = _native.NativeChain(state())
    with pytest.raises(ValueError, match="refusing Python fallback"):
        chain.call("not_implemented")


@pytest.mark.parametrize("dtype", [np.int64, np.int32, np.int8, np.bool_, np.float64])
def test_native_per_draw_honors_requested_dtype(dtype):
    chain = _native.NativeChain(state())
    scalar = 1.5 if dtype == np.float64 else 1
    actual = chain.call("per_draw", scalar, dtype)
    expected = np.full(3, np.asarray(scalar, dtype=dtype), dtype=dtype)
    assert actual.dtype == expected.dtype and actual.shape == expected.shape
    assert actual.tobytes() == expected.tobytes()
    assert chain.call("per_draw", actual, dtype) is actual


def test_native_classified_booking_keeps_versions_and_first_incurred_day():
    s = state()
    chain = _native.NativeChain(s)
    day = np.array([0, 4, BIG], dtype=np.int64)
    amount = np.array([-11, -23, -37], dtype=np.int64)
    chain.call("pay", day, amount, "judgment", np.array([-1, 2, 0], dtype=np.int64))
    assert s["_cv"] == 3
    assert s["_av"] == {"cash": 1, "k:judgment": 2, "i:judgment": 3}
    assert s["ev"].cash[0, 0] == -11 and s["ev"].cash[1, 4] == -23
    assert s["ev"].incurred["judgment"].tolist() == [-1, 2, BIG]
    chain.call("pay", day, amount, "judgment", np.array([1, 3, -2], dtype=np.int64))
    assert s["_cv"] == 5
    assert s["ev"].incurred["judgment"].tolist() == [-1, 2, BIG]
    assert s["ev"].cash.tobytes() == s["ev"].kinds["judgment"].tobytes()


def test_native_event_writer_copies_only_the_array_first_written():
    s = state()
    shared = s["ev"].cash
    shared.flags.writeable = False
    s["_ev_own"] = set()
    chain = _native.NativeChain(s)
    first = chain.call("_evw", "cash")
    assert not np.shares_memory(first, shared)
    assert first.flags.writeable and not shared.flags.writeable
    assert chain.call("_evw", "cash") is first
    assert s["_ev_own"] == {"cash"}
    chain.call("book", first, np.zeros(3, dtype=np.int64), np.ones(3, dtype=np.int64))
    assert (shared == 0).all()
    assert first[:, 0].tolist() == [1, 1, 1]


def test_native_external_readonly_booking_rejects_write_before_mutating():
    s = state()
    target = np.zeros((3, 5), dtype=np.int64)
    target.flags.writeable = False
    day = np.zeros(3, dtype=np.int64)
    with pytest.raises(ValueError):
        _native.NativeChain(s).call("book", target, day, 1)
    assert not target.any() and not target.flags.writeable and s["_cv"] == 0


def test_native_mark_keeps_earliest_date_and_notes_due_reason():
    s = state()
    chain = _native.NativeChain(s)
    chain.call("mark", "notes_due", np.array([3, 1, BIG], dtype=np.int64), None)
    s["_how"] = "automatic"
    chain.call("mark", "notes_due", np.array([2, 4, 0], dtype=np.int64), np.array([True, True, False]))
    assert s["marks"]["notes_due"].tolist() == [2, 1, BIG]
    assert s["notes_due_how"].tolist() == ["automatic", "declared", ""]


def event_arrays(events):
    return [events.cash, events.lock, events.capacity, events.petition,
            *(events.kinds or {}).values(), *(events.incurred or {}).values(), *(events.proceeds or {}).values()]


def test_native_event_composition_adds_cash_once_and_preserves_earliest_dates(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    left, right = EventCash.zeros(3, 4, kinds=True), EventCash.zeros(3, 4, kinds=True)
    left.kinds["inflow"][:, 0] = [100, 200, 300]
    left.kinds["settlement"][:, 1] = [-10, -20, -30]
    right.kinds["levy"][:, 2] = [-5, -15, -25]
    right.kinds["settlement"][:, 3] = [-30, -20, -10]
    left.cash[:] = sum(left.kinds.values())
    right.cash[:] = sum(right.kinds.values())
    left.lock[:, 1] = [3, 7, 11]
    right.capacity[:, 2] = [13, 17, 19]
    left.petition[:] = [-1, 2, 3]
    right.petition[:] = [1, -1, 0]
    left.incurred["settlement"][:] = [1, 2, 3]
    right.incurred["settlement"][:] = [0, 4, -1]
    left.proceeds = {"atm_proceeds": np.array([29, 31, 37], dtype=np.int64)}
    right.proceeds = {"offering_proceeds": np.array([41, 43, 47], dtype=np.int64)}
    expected = left + right
    before = [a.copy() for a in [*event_arrays(left), *event_arrays(right)]]
    for a in [*event_arrays(left), *event_arrays(right)]:
        a.flags.writeable = False
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    actual = left + right
    assert actual.petition.tolist() == [1, 2, 0]
    assert actual.incurred["settlement"].tolist() == [0, 2, -1]
    assert actual.cash.tobytes() == sum(actual.kinds.values()).tobytes()
    assert list(actual.kinds) == list(expected.kinds) and list(actual.proceeds) == list(expected.proceeds)
    for a, e in zip(event_arrays(actual), event_arrays(expected), strict=True):
        assert a.dtype == e.dtype and a.shape == e.shape and a.tobytes() == e.tobytes()
    for a, e in zip([*event_arrays(left), *event_arrays(right)], before, strict=True):
        assert a.tobytes() == e.tobytes() and not np.shares_memory(a, actual.cash)


def test_native_event_split_keeps_classification_only_for_empty_or_classified_cash():
    classified = EventCash.zeros(2, 3, kinds=True)
    kinds, incurred = classified.split()
    assert kinds is classified.kinds and incurred is classified.incurred
    empty = EventCash.zeros(2, 3)
    kinds, incurred = empty.split()
    assert all(not a.any() for a in kinds.values())
    assert all((a == BIG).all() for a in incurred.values())
    result = classified + empty
    assert result.kinds is not None and result.incurred is not None
    empty.cash[0, 0] = 1
    assert empty.split() is None
    result = classified + empty
    assert result.kinds is None and result.incurred is None and result.cash[0, 0] == 1


def test_native_zero_event_buffers_are_independent():
    events = EventCash.zeros(2, 3, kinds=True)
    arrays = event_arrays(events)
    for i, a in enumerate(arrays):
        assert a.dtype == np.int64 and a.flags.writeable
        for b in arrays[i + 1:]:
            assert not np.shares_memory(a, b)
    events.cash[0, 0] = 1
    assert not events.lock.any() and all(not a.any() for a in events.kinds.values())


def large_financial_state():
    s = state(2)
    review = date(2024, 5, 14)
    s.update(d=SimpleNamespace(judgment_date=review + timedelta(days=1)),
             s=SimpleNamespace(review=review, collateral_share=(0.5,)), pending=False,
             entered=427_155_806_170_096_947, bps=6151, cls_amount=None, cls_fees=0,
             F=np.zeros(2, dtype=np.int64), fee_day=np.full(2, BIG, dtype=np.int64),
             EI=np.zeros(2, dtype=np.int64), resolved=np.full(2, BIG, dtype=np.int64),
             taken=np.zeros(2, dtype=np.int64), takes=[],
             m={"parameters": {"bond_forward_interest_years": {"value": 1}}}, sens={})
    return s


def test_native_interest_coefficient_preserves_scalar_integer_true_division():
    s = large_financial_state()
    days = np.array([90, 365], dtype=np.int64)
    expected = s["entered"] + interest_1961(s["entered"], 0, days, 0, s["bps"])
    actual = _native.NativeChain(s).call("_owed_gross", days, False)
    assert actual.tobytes() == expected.tobytes()
    # This valid large-cent amount exposes double rounding when an integer
    # product is cast to float64 before dividing it by 10,000.
    assert s["entered"] * s["bps"] / 10_000 != float(s["entered"] * s["bps"]) / 10_000


def test_native_interest_preserves_large_cent_amounts_across_rates():
    rng = np.random.default_rng(71003)
    days = np.array([90, 365], dtype=np.int64)
    for principal in rng.integers(1, 2**59, size=80, dtype=np.int64):
        for bps in (1, 50, 6151, 10_000):
            s = large_financial_state()
            s.update(entered=int(principal), bps=bps)
            expected = s["entered"] + interest_1961(s["entered"], 0, days, 0, bps)
            actual = _native.NativeChain(s).call("_owed_gross", days, False)
            assert actual.tobytes() == expected.tobytes(), (int(principal), bps)


def test_native_bond_keeps_numpy_integer_product_before_float_division():
    s = large_financial_state()
    owed = np.full(2, s["entered"], dtype=np.int64)
    bond = owed + np.rint(owed * s["bps"] / 10_000 * 1).astype(np.int64)
    expected = np.rint(bond * 0.5).astype(np.int64)
    actual = _native.NativeChain(s).call("bond_collateral", np.zeros(2, dtype=np.int64))
    assert actual.tobytes() == expected.tobytes()


def test_native_owed_queries_borrow_readonly_strided_inputs_without_mutation():
    s = large_financial_state()
    s.update(entered=10_000, bps=500, cls_amount=12_000, cls_fees=2_000)
    # Every field retains a differently strided view of its own owner, including
    # negative strides, so an optimization cannot assume contiguous storage.
    s["F"] = np.array([9, 10, 19, 20], dtype=np.int64)[1::2]
    s["fee_day"] = np.array([50, 49, 60, 59], dtype=np.int64)[::2]
    s["EI"] = np.array([80, 79, 70, 69], dtype=np.int64)[::2][::-1]
    s["taken"] = np.array([300, 299, 100, 99], dtype=np.int64)[::2][::-1]
    s["resolved"] = np.array([BIG, 0, 100, 0], dtype=np.int64)[::2]
    days = np.array([[30, 99, 40, 99], [80, 99, 110, 99]], dtype=np.int64)[:, ::2]
    before = {k: s[k].copy() for k in ("F", "fee_day", "EI", "taken", "resolved")}
    for key in before:
        s[key].flags.writeable = False
    days.flags.writeable = False
    chain = _native.NativeChain(s)
    base = min(s["cls_amount"], s["entered"])
    gross = s["cls_amount"] + interest_1961(base, s["cls_amount"] - base, days,
                                           days - s["F"], s["bps"])
    gross = gross - np.where(days < s["fee_day"], s["cls_fees"], 0)
    enforceable = np.where(days < s["EI"],
                          np.minimum(gross, base + interest_1961(base, 0, days, 0, s["bps"])), gross)
    expected = np.where(days >= s["resolved"], 0, np.maximum(enforceable - s["taken"], 0))
    actual = chain.call("owed_at", days, True)
    assert actual.dtype == expected.dtype and actual.shape == expected.shape
    assert actual.tobytes() == expected.tobytes()
    for key, value in before.items():
        assert s[key].tobytes() == value.tobytes() and not s[key].flags.writeable


def test_virtual_financial_days_and_invalid_cash_state_fail_in_an_isolated_process():
    # A virtual array has one real cell. The address-space limit makes any
    # accidental attempt to materialize its exabyte shape fail harmlessly in
    # this child, and disabled core files keep allocator aborts contained.
    script = textwrap.dedent("""
        import resource
        import numpy as np
        from app import _native
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
        huge = 1 << 59
        one = np.array([0], dtype=np.int64)
        def rejects(state, method, *args):
            try:
                _native.NativeChain(state).call(method, *args)
            except (ValueError, MemoryError):
                return
            raise AssertionError((state, method))
        rejects({'n': 1, 'pending': False}, 'price_owed', np.broadcast_to(one, (huge,)))
        rejects({'n': 1, 'pending': False}, 'price_owed', np.broadcast_to(one, (1, 1, huge)))
        rejects({'n': 1, 'pending': False}, 'price_owed', np.broadcast_to(one, (1, huge)))
        rejects({'n': 1}, 'owed_at', np.broadcast_to(one, (huge, 1)), False)
        rejects({'n': 1, 'pending': True}, 'taken_before', np.broadcast_to(one, (huge, 1)))
        rejects({'n': 1 << 63}, 'decide_floor', '', '', 0)
        rejects({'n': huge}, 'decide_floor', '', '', np.broadcast_to(one, (huge,)))
        # An already matching virtual vector is a zero-allocation view, as in
        # Python. Scalar broadcasting needs a real buffer and must fail safely.
        virtual = np.broadcast_to(one, (huge,))
        assert _native.NativeChain({'n': huge}).call('per_draw', virtual) is virtual
        for dtype in (np.int64, np.int8, np.int32, np.bool_, np.float64):
            rejects({'n': huge}, 'per_draw', 0, dtype)
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=20, env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
    assert result.returncode == 0, result.stdout + result.stderr

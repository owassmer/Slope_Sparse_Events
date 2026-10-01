"""Native execution parity must preserve intermediate state, not just totals.

These tests require a built extension; a missing native backend is a failure,
never a skip or a comparison of Python against itself. Full production-sized
verification is available via scripts/verify_native.py --draws 512 --horizon 180.
"""
from __future__ import annotations

import gc
import importlib.util
import pickle
import weakref
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_native.py"
SPEC = importlib.util.spec_from_file_location("native_verification", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


def test_unreachable_native_state_cycles_are_collected():
    from app import _native

    class Holder:
        pass

    holder = Holder()
    state = {"holder": holder}
    holder.owner = _native.NativeChain(state)
    reference = weakref.ref(holder)
    del holder, state
    gc.collect()
    assert reference() is None


def test_serialized_chain_restores_native_dispatch_without_serializing_the_owner():
    from app.analysis.rust_chain import RustChain

    chain = RustChain._from_state({"basis": None, "n": 3})
    restored = pickle.loads(pickle.dumps(chain, protocol=pickle.HIGHEST_PROTOCOL))
    assert restored.__dict__ == chain.__dict__
    assert restored.__dict__ is not chain.__dict__
    assert "_native_chain" not in restored.__dict__
    assert restored.per_draw(7).tolist() == [7, 7, 7]


def test_exact_comparator_rejects_dtype_shape_order_and_float_bit_changes():
    exact, diff = verification.exact, verification.difference
    assert diff(exact(np.array([1], dtype=np.int64)), exact(np.array([1], dtype=np.int32)))
    assert diff(exact(np.array([1, 2])), exact(np.array([[1, 2]])))
    assert diff(exact(0.0), exact(-0.0))
    assert diff(exact({"a": 1, "b": 2}), exact({"b": 2, "a": 1}))
    assert diff(exact([1]), exact((1,)))
    assert diff(exact(np.array(["same"], dtype=object)), exact(np.array(["same"], dtype=object))) is None
    # Distinct NaN payloads are different; identical payloads compare equal.
    bits = np.array([0x7FF8000000000001, 0x7FF8000000000002], dtype=np.uint64).view(np.float64)
    assert diff(exact(bits[:1]), exact(bits[:1].copy())) is None
    assert diff(exact(bits[:1]), exact(bits[1:]))


def test_large_probability_digest_retains_shape_dtype_bits_and_all_endpoints():
    array = np.array([0.0, -0.0, 0.5], dtype=np.float64)
    digest = verification.probability_digest
    assert digest(array) == digest(array.copy())
    assert digest(array) != digest(array.astype(np.float32))
    assert digest(array) != digest(array.reshape(1, -1))
    assert digest(array) != digest(np.array([-0.0, 0.0, 0.5]))
    raw = {"probabilities": {"base": (array, array), ("q", 0): array, ("q", 100): array[::-1],
                             ("all", 0): (array, array)}, "facts": [array]}
    hashed = {**raw, "probabilities": {**raw["probabilities"], ("q", 0): digest(array),
                                       ("q", 100): digest(array[::-1])}}
    actual = verification.normalize_walk_probabilities(verification.exact(raw))
    assert actual == verification.exact(hashed)
    assert verification.normalize_walk_probabilities(actual) == actual


def test_late_record_identity_preserves_prefix_multiplicity_without_pickle_aliases():
    from app.disputes.forecast import Forecaster, Rows, pack_row, record_digest

    a = np.array([0, 1], dtype=np.int64)
    label = "the same recorded situation " * 10
    row = {"day": a, "petition": np.full(2, -1, dtype=np.int64), "cash": a,
           "sit": {"cash": a, "owed": np.array([11, 23], dtype=np.int64), "label": label, "context": label}}
    copied = {"day": a.copy(), "petition": row["petition"].copy(), "cash": a.copy(),
              "sit": {"cash": a.copy(), "owed": np.array([11, 23], dtype=np.int64),
                      "label": label.encode().decode(), "context": label.encode().decode()}}
    assert pack_row(row) != pack_row(copied)  # object graph, not facts
    assert record_digest(row) == record_digest(copied)
    assert Forecaster.late_key("q", ("p",), row) == Forecaster.late_key("q", ("p",), copied)
    assert Forecaster.late_key("q", ("p",), row) != Forecaster.late_key("q", ("other",), row)
    assert Forecaster.late_key("q", ("p",), row) != Forecaster.late_key("other", ("p",), row)
    changed = {**copied, "sit": {**copied["sit"], "owed": np.array([11, 24], dtype=np.int64)}}
    assert record_digest(row) != record_digest(changed)
    # First occurrence is kept; an identical row at another prefix is kept,
    # and different situations at this prefix are also kept in their order.
    fc = object.__new__(Forecaster)
    fc._late_seen, fc.facts = set(), {}
    for prefix, r in ((("p",), row), (("p",), copied), (("other",), copied), (("p",), changed)):
        fc._keep_late("q", prefix, r)
    assert isinstance(fc.facts["q"], Rows)
    assert len(fc.facts["q"]) == 3
    assert [r["sit"]["owed"].tolist() for r in fc.facts["q"]] == [[11, 23], [11, 23], [11, 24]]


@pytest.mark.parametrize("scenario", ["cash", "chains", "regions", "reduction", "analysis"])
def test_native_core_exactly_matches_reference(scenario, tmp_path):
    reference = verification.capture(scenario, "python", work=tmp_path)
    native = verification.capture(scenario, "rust", work=tmp_path)
    assert native["metadata"]["backend"] == "rust"
    if scenario == "cash":
        assert {"net_kernel", "daily_kernel"} <= set(native["metadata"]["native_calls"])
    elif scenario == "chains":
        assert {"NativeChain.run", "NativeChain.owed_at", "NativeChain._price_owed_grid", "NativeChain.clone"} <= (
            set(native["metadata"]["native_calls"]))
    elif scenario == "reduction":
        assert {"bins_flat", "sparse_counts", "weighted_counts", "histogram_quantiles", "weighted_quantiles"} <= (
            set(native["metadata"]["native_calls"]))
    elif scenario == "analysis":
        assert {"NativeWalk", "NativeChain.advance", "NativeChain.finish", "daily_kernel", "bins_flat", "walk_reach",
                "sparse_counts", "weighted_counts", "weighted_quantiles", "walk_verdict_classes",
                "walk_verdict_lines", "walk_equity_inflows", "analysis_reduce_add", "analysis_metrics",
                "analysis_daily", "analysis_first_floor", "analysis_bins_from", "analysis_event_range",
                "analysis_quantile", "probability_dist_missing"} <= set(native["metadata"]["native_calls"])
    assert verification.difference(reference["result"], native["result"]) is None


def test_complete_case_tree_and_parallel_reconstruction_match_reference(tmp_path):
    reference = verification.capture("walk", "python", work=tmp_path)
    assert reference["metadata"]["coverage"]["capped"] is False
    assert reference["metadata"]["coverage"]["paths"] > 0
    assert reference["metadata"]["coverage"]["fact_occurrences"] > 0
    for backend, processes in (("rust", 1), ("python", 2), ("rust", 2)):
        candidate = verification.capture("walk", backend, processes=processes, work=tmp_path)
        assert candidate["metadata"]["backend"] == backend
        if backend == "rust" and processes == 1:
            assert {"NativeWalk", "walk_bank", "walk_merge", "walk_expand_classes", "edge_products", "walk_reach"} <= (
                set(candidate["metadata"]["native_calls"]))
        assert verification.difference(reference["result"], candidate["result"]) is None


def test_production_chain_cow_buffers_survive_fork_write_slice_and_drop(monkeypatch):
    """The production NativeChain facade has separate storage from EventLedger."""
    import akoustis_20240514_fixture as fx

    from app.analysis.events import Draws, SubDraws
    from app.analysis.rust_chain import RustChain

    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    setup, basis, _feed = verification.case_basis(4, 180)
    draws = Draws(4, basis=basis)
    parent = RustChain(fx.pending(), setup, fx.model(), draws)
    cash, inflow = parent.ev.cash, parent.ev.kinds["inflow"]
    expected = cash.copy()
    external = np.zeros_like(cash)
    external.flags.writeable = False
    version = parent._cv
    with pytest.raises(ValueError):
        parent.book(external, np.zeros(4, dtype=np.int64), 1)
    assert parent._cv == version and np.array_equal(cash, expected)
    fork = parent.clone()
    assert np.shares_memory(cash, fork.ev.cash)
    assert not cash.flags.writeable and not inflow.flags.writeable
    with pytest.raises(ValueError):
        cash[0, 0] = 123
    fork.pay(np.arange(4, dtype=np.int64), 17, "inflow")
    assert not np.shares_memory(cash, fork.ev.cash)
    assert np.array_equal(cash, expected)
    assert np.array_equal(inflow, np.zeros_like(inflow))
    assert fork.ev.cash[np.arange(4), np.arange(4)].tolist() == [17] * 4
    parent.pay(np.full(4, 5, dtype=np.int64), 29, "inflow")
    assert np.array_equal(cash, expected)
    assert not np.shares_memory(parent.ev.cash, fork.ev.cash)
    retained = fork.ev.cash
    fork_again = fork.clone()
    fork_again.pay(np.full(4, 10, dtype=np.int64), 31, "inflow")
    assert np.array_equal(retained[:, 10], np.zeros(4, dtype=np.int64))
    sel = np.array([3, 1, 3], dtype=np.int64)
    sliced = fork.sliced(sel, SubDraws(draws, sel))
    assert np.array_equal(sliced.ev.cash, retained[sel])
    assert not np.shares_memory(sliced.ev.cash, retained)
    sliced.pay(np.full(3, 12, dtype=np.int64), 37, "inflow")
    assert np.array_equal(retained[:, 12], np.zeros(4, dtype=np.int64))
    del parent, fork, fork_again, sliced
    gc.collect()
    assert np.array_equal(cash, expected)
    assert retained[np.arange(4), np.arange(4)].tolist() == [17] * 4


def test_native_event_snapshots_own_their_storage_across_fork_write_slice_and_drop():
    from app import _native

    ledger = _native.EventLedger(3, 5)
    before = ledger.snapshot()
    fork = ledger.fork()
    days = np.array([0, 1, 4], dtype=np.int64)
    cents = np.array([11, 23, 37], dtype=np.int64)
    ledger.book("cash", days, cents)
    after = ledger.array("cash")
    assert not before["cash"].flags.writeable and not after.flags.writeable
    assert (before["cash"] == 0).all() and (fork.array("cash") == 0).all()
    assert after[[0, 1, 2], days].tolist() == cents.tolist()
    # A Python alias must not be able to bypass the immutable Arc snapshot.
    with pytest.raises(ValueError):
        after.setflags(write=True)
    sub = ledger.sliced(np.array([2, 0, 2], dtype=np.int64))
    retained = sub.snapshot()
    fork.book("cash", days, -cents)
    sub.book("cash", np.zeros(3, dtype=np.int64), np.ones(3, dtype=np.int64))
    assert (before["cash"] == 0).all()
    assert retained["cash"][0, 4] == 37 and retained["cash"][1, 0] == 11
    assert np.shares_memory(after, ledger.array("cash"))
    assert not np.shares_memory(after, fork.array("cash"))
    expected = after.copy()
    del ledger, fork, sub
    gc.collect()
    assert np.array_equal(after, expected)
    assert retained["cash"][2, 4] == 37


def test_native_malformed_event_input_is_rejected_atomically():
    from app import _native

    ledger = _native.EventLedger(2, 3)
    snapshot = verification.exact(ledger.snapshot())
    day, cents = np.zeros(2, dtype=np.int64), np.ones(2, dtype=np.int64)
    for call in (
        lambda: ledger.book("cash", day[:1], cents),
        lambda: ledger.book("cash", day, cents[:1]),
        lambda: ledger.book("missing", day, cents),
        lambda: ledger.replace_column("cash", np.zeros((2, 2), dtype=np.int64)),
        lambda: ledger.replace_vector("petition", np.zeros(3, dtype=np.int64)),
        lambda: ledger.pay("settlement", day, cents, day[:1]),
        lambda: ledger.pay("inflow", day, cents, day),
        lambda: ledger.add_delta(["cash", "missing"], np.ones((2, 3), dtype=np.int64)),
    ):
        with pytest.raises(ValueError):
            call()
        assert verification.exact(ledger.snapshot()) == snapshot
    with pytest.raises(IndexError):
        ledger.sliced(np.array([-1], dtype=np.int64))
    with pytest.raises(IndexError):
        ledger.sliced(np.array([2], dtype=np.int64))


def test_native_malformed_probability_and_class_arrays_raise_value_errors():
    from app import _native

    # PyO3 PanicException derives from BaseException, so ValueError assertions
    # catch a validation defect instead of accepting an unwound Rust panic.
    with pytest.raises(ValueError):
        _native.edge_products(np.array([[-1]], dtype=np.int64), np.ones(1, dtype=np.float64))
    with pytest.raises(ValueError):
        _native.edge_products(np.array([[1]], dtype=np.int64), np.ones(1, dtype=np.float64))
    with pytest.raises(ValueError):
        _native.walk_class_entry("q", ["#a"], [True, False])
    with pytest.raises(ValueError):
        _native.walk_group_classes(["#a"], [0, 1])
    with pytest.raises(ValueError):
        _native.walk_merge([], [[0]], {})
    with pytest.raises(ValueError):
        _native.walk_expand_classes([("i", (), "o", (), "", b"", (("q", ("#a",), b"\x00"),))],
                                    ["q|#a"], 1, [])
    with pytest.raises(ValueError):
        _native.histogram_quantiles(np.ones((1, 2), dtype=np.float64), np.empty(0), np.ones(1), [0.5])
    with pytest.raises(ValueError):
        _native.weighted_quantiles(np.ones(2, dtype=np.float64), np.ones(1, dtype=np.float64), [0.5])
    with pytest.raises(ValueError):
        _native.sparse_counts(np.array([2], dtype=np.int64), 2)


def test_impossible_allocation_shapes_raise_values_before_rust_panics():
    from app import _native

    # These shapes cannot be represented by ndarray/Vec, even though a zero
    # axis makes the ordinary element product zero. No actual large allocation
    # is attempted. A PanicException is a BaseException and fails these checks.
    for draws, days in ((2**63, 0), (0, 2**63)):
        with pytest.raises(ValueError):
            _native.EventLedger(draws, days)
        with pytest.raises(ValueError):
            _native.event_cash_zeros(draws, days, False)
    with pytest.raises(ValueError):
        _native.NativeChain({"n": 2**63}).call("per_draw", 7)
    with pytest.raises(ValueError):
        _native.sparse_counts(np.empty(0, dtype=np.int64), 2**63)
    for rows, bins in ((2**63, 1), (2**64 - 1, 2)):
        with pytest.raises(ValueError):
            _native.weighted_counts(np.empty(0, dtype=np.int32), np.empty(0, dtype=np.uint32),
                                    np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64), rows, bins, False)
    z = np.empty(0, dtype=np.int64)
    zz = np.empty((0, 0), dtype=np.int64)
    with pytest.raises(ValueError):
        _native.grid_kernel(2**63, False, z, 0, 0., 0., False, 0, 0, 0., 0., 0,
                            z, z, z, z, zz, zz, False, zz, zz, z)
    with pytest.raises(ValueError):
        _native.atm_book(z, z, 1, 8, zz, zz, zz.astype(bool), zz, False, 100, 0, 0, z, z, z,
                         np.zeros((1, 1)), True, 44., 2**63, 300, np.zeros((1, 1), dtype=np.int64), False)

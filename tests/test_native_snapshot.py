"""Independent mutable branch state and conservative dated divergence."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app import _native
from app.analysis import events, operating
from app.analysis.engine import prepare
from app.analysis.events import BIG, Basis, Draws, PythonChain, SubDraws
from app.analysis.setup import SEED
from app.finance.bank import load_feed


@pytest.fixture
def reference_chain(monkeypatch):
    # Original clone constructs through the module's Chain name; keep its type
    # on this oracle-only fixture while exercising NativeChain directly.
    monkeypatch.setattr(events, "Chain", PythonChain)
    s = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=180))
    feed = load_feed(fx.SNAP)
    ops = operating.simulate(feed, 180 + s.need_days, 4, SEED, s.variability, s.cost_plan, s.financing)
    line = prepare(s, ops)
    basis = Basis.of(ops, line.need, feed.available_cents + s.exposure.cash_cents, line)
    return PythonChain(fx.pending(), s, fx.model(), Draws(4, basis=basis))


def test_native_clone_isolates_mutable_records_and_shares_readonly_numerical_inputs(reference_chain):
    ch = reference_chain
    ch.rec[0].append(np.arange(ch.n, dtype=np.int64))
    ch.waiting = [(0, "cash_floor", "file", np.zeros(ch.n, dtype=bool), "1")]
    ch._offers = [{"init": np.arange(ch.n, dtype=np.int64), "closed": np.zeros(ch.n, dtype=bool)}]
    ch.late = {0: {"petition": np.full(ch.n, BIG), "raise_offer": np.zeros(ch.n, dtype=np.int64)}}
    ch._ops_total = ch.basis.line.ops.total
    ch._kernel_line = (ch._ops_total,)
    clone = _native.NativeChain(ch.__dict__).call("clone")
    assert np.shares_memory(clone["ev"].cash, ch.ev.cash)
    assert not clone["ev"].cash.flags.writeable
    assert clone["basis"] is ch.basis and clone["_ops_total"] is ch._ops_total
    assert clone["_kernel_line"] is ch._kernel_line
    assert clone["rec"][0] is not ch.rec[0]
    assert clone["rec"][0][0] is ch.rec[0][0] and not ch.rec[0][0].flags.writeable
    clone["waiting"][0][3][0] = True
    clone["_offers"][0]["init"][0] = 99
    clone["late"][0]["raise_offer"] = np.ones(ch.n, dtype=np.int64)
    assert not ch.waiting[0][3].any()
    assert ch._offers[0]["init"][0] == 0
    assert not ch.late[0]["raise_offer"].any()
    # Writing either branch goes through the exact first-write COW boundary.
    owner = _native.NativeChain(clone)
    writable = owner.call("_evw", "cash")
    writable[0, 4] = 123
    assert ch.ev.cash[0, 4] == 0
    assert not np.shares_memory(clone["ev"].cash, ch.ev.cash)


def test_native_slice_keeps_original_draw_identity_and_rebinds_financial_inputs(reference_chain):
    ch = reference_chain
    sel = np.array([3, 0, 3], dtype=np.int64)
    sub = SubDraws(ch.dr, sel)
    ch.ev.cash[:, 7] = np.arange(ch.n, dtype=np.int64)
    expected = ch.sliced(sel, sub)
    actual = _native.NativeChain(ch.__dict__).call("sliced", sel, sub)
    assert actual["n"] == len(sel)
    assert actual["dr"] is sub and actual["basis"] is sub.basis
    assert np.array_equal(actual["ev"].cash, expected.ev.cash)
    assert np.array_equal(actual["V"], expected.V)
    assert np.array_equal(actual["rows"], expected.rows)
    for key, source in (("_ops_total", "total"), ("_ops_inflow", "inflow"), ("_ops_outflow", "outflow")):
        assert actual[key] is getattr(sub.basis.line.ops, source)
    assert _native.NativeChain(actual).call("clone")["_ops_total"] is actual["_ops_total"]


def test_missing_question_interface_is_explicit(reference_chain):
    owner = _native.NativeChain(reference_chain.__dict__)
    with pytest.raises(NotImplementedError, match="step-9 interface"):
        owner.call("c_read", "unavailable_accessor")


@pytest.mark.parametrize("method", ["trigger_days", "divergence"])
def test_impossible_snapshot_capacity_is_a_python_error(method):
    state = {"n": 1 << 63, "N": 0}
    owner = _native.NativeChain(state)
    with pytest.raises((MemoryError, ValueError)):
        if method == "divergence":
            owner.call(method, state)
        else:
            owner.call(method)


def test_impossible_slice_capacity_is_rejected_before_numpy_indexing():
    class ImpossibleSelection:
        def __len__(self):
            return 1 << 60

    owner = _native.NativeChain({"n": 1, "N": 1})
    with pytest.raises(ValueError, match="addressable memory"):
        owner.call("sliced", ImpossibleSelection(), None)


@pytest.mark.parametrize("change,wait", [
    ("cash", None), ("mark", None), ("resolved", None), ("waiting_branch", None),
    ("waiting_branch", 0), ("waiting_done", 0), ("plain", None), ("floor_integer_key", None),
])
def test_native_dated_divergence_matches_reference(reference_chain, change, wait):
    ch = reference_chain
    ch.waiting = [(0, "cash_floor", "file", np.zeros(ch.n, dtype=bool), "1")]
    sibling = ch.clone()
    if change == "cash":
        sibling.ev.cash = sibling.ev.cash.copy()
        sibling.ev.cash[1, 13] = 1
    elif change == "mark":
        sibling.marks["notes_due"][2] = 19
    elif change == "resolved":
        sibling.resolved[3] = 25
    elif change == "waiting_branch":
        i, node, _, done, ctx = sibling.waiting[0]
        sibling.waiting[0] = (i, node, "neither", done, ctx)
    elif change == "waiting_done":
        sibling.waiting[0][3][0] = True
    elif change == "plain":
        sibling.entered = 1
    elif change == "floor_integer_key":
        ch.floor_days[1] = np.full(ch.n, 20, dtype=np.int64)
        sibling.floor_days[1] = np.full(ch.n, 21, dtype=np.int64)
        sibling.floor_days["later"] = np.full(ch.n, 23, dtype=np.int64)
    expected = ch.divergence(sibling, wait)
    actual = _native.NativeChain(ch.__dict__).call("divergence", sibling.__dict__, wait)
    assert actual.dtype == expected.dtype
    assert np.array_equal(actual, expected)

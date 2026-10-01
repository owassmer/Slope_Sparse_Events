"""Native Chain cash and default reads against a separately executed reference."""
from __future__ import annotations

from dataclasses import replace

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app import _native as native
from app.analysis import events, processor
from app.analysis import native as backend
from app.analysis.engine import _kernel_line


def reference_chain(monkeypatch, sens=None):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    basis = events._rows_basis(fx.basis(), np.array([0, 3, 7, 11], dtype=np.int64))
    cls = getattr(events, "PythonChain", getattr(events, "ReferenceChain", events.Chain))
    return cls(fx.pending(), fx.setup(), fx.model(), events.Draws(4, basis=basis), sens)


def native_state(reference):
    state = dict(reference.__dict__)
    state.update(_cum=None, _keys={}, _hd={}, _tau=None, _out=None)
    line = reference.basis.line
    state["_kernel_line"] = _kernel_line(line)
    state["_ops_inflow"] = line.ops.inflow[:, :line.days]
    state["_ops_outflow"] = line.ops.outflow[:, :line.days]
    state["_ops_total"] = line.ops.total[:, :line.days]
    return state


def exact(a, b):
    if isinstance(b, (tuple, list)):
        assert isinstance(a, type(b)) and len(a) == len(b)
        for x, y in zip(a, b, strict=True):
            exact(x, y)
    elif isinstance(b, dict):
        assert set(a) == set(b)
        for k in b:
            exact(a[k], b[k])
    elif isinstance(b, np.ndarray):
        assert a.dtype == b.dtype and a.shape == b.shape
        if b.dtype == object:
            assert a.tolist() == b.tolist()
        else:
            assert a.tobytes() == b.tobytes()
    else:
        assert a == b


def test_bank_standing_does_not_read_pending_verdict_dates(monkeypatch):
    ref = reference_chain(monkeypatch)
    bank = events.PythonChain(None, ref.s, ref.m, ref.dr, fin=ref.fin)
    assert "E_ix" not in bank.__dict__
    day = np.array([-1, 0, 30, bank.N + 3], dtype=np.int64)
    exact(native.NativeChain(native_state(bank)).call("standing_amount", day), bank.standing_amount(day))


@pytest.mark.parametrize("rule", ["covers_shortfall", "any_proceeds", None])
@pytest.mark.parametrize("daily", [False, True])
@pytest.mark.parametrize("pending_levy", [False, True])
def test_offering_initiation_respects_shortfall_and_pending_levy(monkeypatch, rule, daily, pending_levy):
    ref = reference_chain(monkeypatch)
    ref.daily = daily
    if not daily:
        ref.s = replace(ref.s, cash_processing="net")
        ref.basis.line = replace(ref.basis.line, setup=ref.s)
    if rule is None:
        del ref.m["parameters"]["offering_materiality"]
    else:
        ref.m["parameters"]["offering_materiality"]["value"] = rule
    ref.instrument_cash()
    ref.step("verdict", "", "award:4458000000:0:top")
    day = ref.E_ix + 40
    if pending_levy:
        ref.pending_levy = np.where(np.arange(ref.n) == 2, events.BIG, day)
        ref.stayed_from[3] = day[3]  # a stay starts on this row's decision day
    cash = ref.cash_at(day)
    net = ref.offering_terms(day)["net"]
    levy = np.zeros(ref.n, dtype=np.int64)
    if pending_levy:
        on = (ref.pending_levy == day) & ref.live(day) & (day < ref.stayed_from) & (day < ref.N)
        reach = ref.processing_balance(day) if daily else cash
        levy = np.where(on, np.minimum(ref.owed_at(day, enforceable=True), np.maximum(reach, 0)), 0)
    # One cent below, exactly at and one cent above the initiation threshold;
    # the last row already covers its operating need.
    ref.basis.need = ref.basis.need.copy()
    ref.basis.need[ref.rows, day] = cash - levy + np.array([net[0] - 1, net[1], net[2] + 1, -1])
    owner = native.NativeChain(native_state(ref))
    assert owner.call("initiation_rule") == ref.initiation_rule()
    for query_day in (day, 0, np.array([-1, 0, ref.N, ref.N + 3], dtype=np.int64)):
        exact(owner.call("offer_shortfall", query_day), ref.offer_shortfall(query_day))
        exact(owner.call("offer_available", query_day), ref.offer_available(query_day))
    np.testing.assert_array_equal(ref.offer_shortfall(day), [net[0] - 1, net[1], net[2] + 1, 0])
    if rule == "covers_shortfall":
        assert ref.offer_available(day)[2] == 0
    if ref.offering_available(day)[:2].all():
        np.testing.assert_array_equal(ref.offer_available(day)[:2], net[:2])
    if daily:
        exact(owner.call("c_situation", day), ref.c_situation(day))
    else:
        for snapshot in (lambda: owner.call("c_situation", day), lambda: ref.c_situation(day)):
            with pytest.raises(ValueError, match="arrears are the daily cash processor's"):
                snapshot()
    fork = native.NativeChain(owner.call("clone"))
    oracle = ref.clone()
    exact(fork.call("decide_distress", "cash_floor", "1", "neither", day),
          oracle.decide_distress("cash_floor", "1", "neither", day))


@pytest.mark.parametrize("sens", [None, {"cash_floor": True}, {"nonpayment_window_days": 15}])
def test_cash_reads_and_shared_run_cache_match_reference(monkeypatch, sens):
    ref = reference_chain(monkeypatch, sens)
    ref.instrument_cash()
    state = native_state(ref)
    chain = native.NativeChain(state)
    for method in ("nonpayment_terms", "processed", "cum", "tau", "cash_out", "nonpayment_day", "_arrears"):
        expected = getattr(ref, method)()
        exact(chain.call(method), expected)
    day = np.array([-1, 0, 30, ref.N + 3], dtype=np.int64)
    for method in ("processing_balance", "decision_cash", "standing_amount", "judgment_standing", "arrears_by_class"):
        exact(chain.call(method, day), getattr(ref, method)(day))
    key = ref._run_key("daily", ref.DAILY_KEY, np.array(ref.nonpayment_terms(), dtype=np.int64).tobytes())
    assert state["_keys"]["daily"][1] == key
    assert chain.call("processed")[0] is ref.basis.runs[key][0]
    assert chain.call("_arrears") is ref.basis.arrears_runs[key]


@pytest.mark.parametrize("terms", [None, (15, 5000)])
def test_public_cash_only_dispatch_preserves_tuple4_and_optional_nonpayment(monkeypatch, terms):
    ref = reference_chain(monkeypatch, {"coupon_cash_share": True})
    ref.instrument_cash()
    ev = ref.ev
    line = ref.basis.line
    opening = ref.basis.opening - line.setup.exposure.cash_cents
    expected = processor.run_daily(line, opening, [ev], terms, cash_only=True)
    calls = []
    resolve = backend.native_function

    def observed(name):
        function = resolve(name)
        if function is None:
            return None

        def call(*args):
            calls.append(name)
            return function(*args)

        return call

    monkeypatch.setattr(backend, "native_function", observed)
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")
    exact(processor.run_daily(line, opening, [ev], terms, cash_only=True), expected)
    assert "daily_cash_kernel" in calls and "daily_kernel" not in calls


def test_default_and_judgment_standing_match_after_verdict(monkeypatch):
    ref = reference_chain(monkeypatch)
    ref.step("verdict", "", "award:4458000000:0:top")
    ref.step("post_trial_motions", "", "no")
    state = native_state(ref)
    chain = native.NativeChain(state)
    for ctx in ("I1", "post", "ruling"):
        exact(chain.call("judgment_default", ctx), ref.judgment_default(ctx))
    exact(chain.call("default_available_day"), ref.default_available_day)
    exact(chain.call("holder_route_days_path"), ref.holder_route_days_path)
    exact(chain.call("judgment_amount_entered"), ref.judgment_amount_entered)
    for offset in (-1, 0, 10, 100):
        day = ref.E_ix + offset
        exact(chain.call("standing_amount", day), ref.standing_amount(day))
        exact(chain.call("judgment_standing", day), ref.judgment_standing(day))


@pytest.mark.parametrize("day_only", [False, True])
def test_waiting_decisions_preserve_dates_facts_and_occurrence_order(monkeypatch, day_only):
    ref = reference_chain(monkeypatch, {"coupon_cash_share": True, "atm_pace_bps": 1000,
                                      "nonpayment_window_days": 15})
    ref.instrument_cash()
    trace = events.Trace(ref.ev)
    for node, context, branch in (("settle", "I0", "yes"), ("listing", "", "compliant"),
                                   ("cash_floor", "1", "neither"), ("cash_out", "", "neither")):
        ref.advance(trace, node, context, branch)
    state = native.NativeChain(native_state(ref)).call("clone")
    chain = native.NativeChain(state)
    if day_only:
        ref._book_to_day()
        chain.call("_book_to_day")
        exact(state["_booked_to"], ref._booked_to)
    else:
        assert chain.call("upto", None, True) == ref.upto(None, every=True)
    for field in ("rec", "late", "floor_days", "grec"):
        exact(state[field], getattr(ref, field))
    assert len(state["waiting"]) == len(ref.waiting)
    exact(state["ev"].cash, ref.ev.cash)
    exact(state["ev"].lock, ref.ev.lock)
    exact(state["ev"].petition, ref.ev.petition)

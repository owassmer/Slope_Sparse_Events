"""Differential contracts for native depth-first traversal and typed path algebra."""
import json
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from app.disputes import forecast as F


def extension():
    from app import _native

    return _native


def with_backend(monkeypatch, value):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", value)


@pytest.mark.parametrize("cash", [
    np.array([[1, -2, 9007199254740993], [-3, 4, 0]], dtype=np.int64)[:, ::2],
    np.array([[0, np.iinfo(np.uint64).max]], dtype=np.uint64),
    np.array([[True, False]], dtype=np.bool_),
    np.array([[-3.75, 4.875, -0.0]], dtype=np.float64),
    np.array([[-2.5, 1.25]], dtype=np.float32),
    np.array([[-2.5, 1.25]], dtype=np.float16),
    np.array([np.longdouble("9223372036854775809"), np.longdouble(1)]),
])
def test_native_reach_bound_preserves_integer_precision_and_float_truncation(cash):
    assert extension().walk_reach(cash) == int(cash.max())


@pytest.mark.parametrize("cash,error", [
    (np.empty((2, 0), dtype=np.int64), ValueError),
    (np.array([[3.0, np.nan, 4.0]], dtype=np.float64), ValueError),
    (np.array([[3.0, np.inf]], dtype=np.float64), OverflowError),
])
def test_native_reach_bound_retains_empty_and_nonfinite_errors(cash, error):
    for compute in (lambda: extension().walk_reach(cash), lambda: int(cash.max())):
        with pytest.raises(error):
            compute()


def test_selected_rust_forecaster_uses_native_reach_bound(monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    native = extension()
    seen = []
    original = native.walk_reach

    def observed(cash):
        seen.append(cash)
        return original(cash)

    monkeypatch.setattr(native, "walk_reach", observed)
    with_backend(monkeypatch, "rust")
    cash = np.array([[1, 13], [-4, 7]], dtype=np.int64)
    fc = F.Forecaster([], {}, borrower="A", review=date(2024, 5, 14), horizon=date(2024, 5, 15),
                      hydrate=lambda _: {}, model={"templates": {}, "parameters": {}},
                      basis=SimpleNamespace(cash=cash))
    assert fc.reach == 13 and len(seen) == 1 and seen[0] is cash


def test_composite_unicode_is_python_json_byte_identical():
    native = extension()
    parts = [[("mérite\x7f 😀", "はい\n"), ("\\\"node", "no")], []]
    assert native.walk_composite(parts) == "=" + json.dumps(parts, separators=(",", ":"))


def test_native_complement_and_expansion(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    branches = {"a": ("yes", "no"), "b": ("x", "y", "z"), "c": ("yes", "no")}
    parts = ((('a', 'yes'), ('b', 'x')), (('a', 'no'), ('c', 'yes')))
    assert native.walk_complement(parts, branches) == F._complement(parts, branches)
    edges = ((F.composite([list(c) for c in parts]), "no"), ("b", "z"))
    assert native.walk_expand(edges, branches) == F._expand(edges, branches)
    assert native.walk_complement([], branches) == [[]]
    assert native.walk_complement([[]], branches) == []


def test_native_merge_preserves_edge_multiplicity_and_classes(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    paths = [F.DisputePath("d", (), "same", (("common", "yes"), ("common", "yes"), ("a", "yes")),
                           classes=(("q", ("#x",), None),)),
             F.DisputePath("d", (), "same", (("common", "yes"), ("b", "yes"), ("b", "yes")),
                           classes=(("q", ("#x",), None),)),
             F.DisputePath("d", (), "same", (("common", "yes"), ("a", "no")),
                           classes=(("q", ("#y",), None),))]
    branches = {"a": ("yes", "no"), "b": ("yes", "no"), "common": ("yes", "no")}
    ref = F.merge_equivalent(paths, [0, 0, 0], branches)
    actual = [F._from_native_path(p) for p in native.walk_merge([F._to_native_path(p) for p in paths], [[0, 1, 2]], branches)]
    assert actual == ref
    assert len(actual) == 2
    assert actual[0].edges[0] == ("common", "yes")
    assert json.loads(actual[0].edges[-1][0][1:])[1] == [["b", "yes"], ["b", "yes"]]


@pytest.mark.parametrize("n", [1, 7, 8, 9, 19])
def test_native_classes_keep_live_draw_ids_dead_classes_and_sorted_partitions(monkeypatch, n):
    native = extension()
    with_backend(monkeypatch, "python")
    rng = np.random.default_rng(41 + n)
    mask = rng.random(n) > .25
    tags = ("#a", "#b", "#c")
    values = np.array(["" if i % 5 == 0 else tags[i % 3] for i in range(n)], dtype=object)
    klass = F.class_entry("q", values, mask)
    paths = [F.DisputePath("d", (), "same", (("q", "yes"), (F.composite([[('q', 'no'), ('r', 'yes')]]), "yes")),
                           mask=F.pack_mask(mask), classes=(klass,))]
    known = ["q|#a", "q|#b", "q|#c"]
    dead = {"q|#b"}
    for first in (None, F.class_firsts(known, dead)):
        ref = F.expand_classes(paths, known, n, dead, first)
        actual = [F._from_native_path(p) for p in native.walk_expand_classes([F._to_native_path(p) for p in paths], known, n, list(dead), first)]
        assert actual == ref
    key, got_tags, codes = native.walk_class_entry("q", values.tolist(), mask.tolist())
    assert (key, tuple(got_tags), None if codes is None else bytes(codes)) == klass


def test_native_group_class_labels(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    groups = np.array([-1, 0, 1, 2, 3], dtype=np.int8)
    cls = np.array(["#gone", "#x", "", "#y", "#y"], dtype=object)
    for c in (None, cls):
        assert native.walk_group_classes(None if c is None else c.tolist(), groups.tolist()) == F.group_classes(c, groups).tolist()


def make_forecasters(monkeypatch, stage, days=30):
    import akoustis_fixture as fx

    from app.analysis.events import _rows_basis
    with_backend(monkeypatch, "python")
    setup = replace(fx.SETUP, horizon=fx.REVIEW + timedelta(days=days))
    b = _rows_basis(fx.basis(setup)[1], np.arange(3))
    d = fx.judgment(stage=stage, motions=(), financing=())
    def create():
        return F.Forecaster([d], {}, borrower="A", review=fx.REVIEW, horizon=setup.horizon,
                            hydrate=lambda f: {}, setup=setup, basis=b)
    return create(), create(), d


@pytest.mark.parametrize("stage", ["judgment_entered", "appeal_filed", "appeal_pending", "enforcement"])
def test_native_walk_keeps_ordered_paths_nodes_and_record_bytes(monkeypatch, stage):
    native = extension()
    a, b, d = make_forecasters(monkeypatch, stage)
    ref = F._Walk(a, d).run()
    actual = native.NativeWalk(b, d).run()
    assert actual == ref
    assert list(b.nodes.items()) == list(a.nodes.items())
    assert list(b.facts) == list(a.facts)
    for k in a.facts:
        assert b.facts[k]._b == a.facts[k]._b
    assert b._qcanon.keys() == a._qcanon.keys()


def test_native_bank_walk_ordered_paths_and_facts(monkeypatch):
    native = extension()
    a, b, _ = make_forecasters(monkeypatch, "judgment_entered")
    ref = F._BankWalk(a).run()
    actual = native.walk_bank(b)
    assert actual == ref
    assert list(b.bank_nodes.items()) == list(a.bank_nodes.items())
    from app.analysis.shadow import same
    assert same(a.bank_facts, b.bank_facts)

@pytest.mark.parametrize("days", [30, 60])
def test_native_pending_equity_keeps_masks_classes_late_rows_and_equivalence_keys(monkeypatch, days):
    import akoustis_20240514_fixture as fx

    from app.analysis.build import basis_for
    from app.analysis.events import _rows_basis
    from app.analysis.shadow import same
    from app.finance.bank import load_feed
    native = extension()
    with_backend(monkeypatch, "python")
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=days))
    basis = _rows_basis(basis_for(load_feed(fx.SNAP), setup), np.arange(2))
    d = fx.pending().model_copy(update={"status": "interpreted"})
    def create():
        return F.Forecaster([d], {}, borrower="A", review=fx.REVIEW, horizon=setup.horizon,
                            hydrate=lambda f: {}, setup=setup, model=fx.model(), basis=basis)
    a, b = create(), create()
    wa, wb = F._Walk(a, d), native.NativeWalk(b, d)
    reference = wa.run()
    monkeypatch.setattr(F, "native_function", lambda name: getattr(native, name, None))
    actual = wb.run()
    assert actual == reference
    assert wb.keys == wa.keys
    assert list(b.nodes.items()) == list(a.nodes.items())
    assert list(a.facts) == list(b.facts)
    assert same(a._qcanon, b._qcanon)
    assert same(a._qcls, b._qcls)
    assert same(a.ev_range, b.ev_range)
    for k in a.facts:
        assert same(list(a.facts[k]), list(b.facts[k]))
    a, b = create(), create()
    assert native.walk_bank(b) == F._OrdinaryWalk(a).run()
    assert same(a.bank_facts, b.bank_facts)


def test_native_control_flow_does_not_execute_python_branch_methods(monkeypatch):
    native = extension()
    _, fc, d = make_forecasters(monkeypatch, "judgment_entered")
    def unavailable(*args, **kwargs):
        raise AssertionError("Python traversal was called")
    for name in ("run", "settle", "verdict", "entry", "motions", "q1", "stay_i1", "j9_stayed", "j9_i1",
                 "a4_i1", "a4", "a4_grouped", "notes_petition", "ripe_i1", "ruling", "ruling_pending", "post",
                 "appeal", "stay_post", "stayed_tail", "i3", "a4_post", "enforce", "ripe_post", "tail", "kept",
                 "delisting_notes", "listing", "delisting", "distress", "ask_distress", "offer", "nonpayment",
                 "first", "_first_distress", "_end", "floor", "cash_out"):
        monkeypatch.setattr(F._Walk, name, unavailable)
    assert native.NativeWalk(fc, d).run()


def test_native_situation_classes_as_of_day_and_grouped_live_mask(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    n = 7
    row = {"day": np.array([1, 3, 5, 7, 9, 11, 10**6], dtype=np.int64),
           "petition": np.array([-1, -1, 5, -1, -1, -1, -1], dtype=np.int64),
           "cash": np.array([10, 8, 6, 4, 2, 0, 0], dtype=np.int64),
           "owed": np.array([8, 8, 8, 0, 1, 0, 0], dtype=np.int64),
           "sit": {"band": "100-200", "standing": np.array(["entered", "entered", "ruled", "paid", "ruled", "claimed", "none"]),
                   "notes_due_day": np.array([10**6, 3, 10**6, 10**6, 10**6, 10**6, 10**6], dtype=np.int64),
                   "default_available": np.array([10**6, 10**6, 2, 10**6, 10**6, 10**6, 10**6], dtype=np.int64),
                   "listing": np.array(["listed", "listed", "listed", "delisted", "listed", "listed", "listed"]),
                   "offering_pending": np.array([False, False, False, False, True, False, False]),
                   "ledger": np.array([1, 1, 1, 1, 1, 0, 0], dtype=np.int64)}}
    for live in (None, np.array([True] * n), np.array([False, True, False, True, False, True, False])):
        actual = native.walk_situation_class(row, live)
        assert actual == F.situation_class(row, live).tolist()
    sparse = {**row, "sit": {**row["sit"], "standing": np.array([None, "entered", b"ruled", None, "ruled", None, None], dtype=object)}}
    for live in (None, np.ones(n, dtype=bool)):
        assert native.walk_situation_class(sparse, live) == F.situation_class(sparse, live).tolist()
    assert native.walk_situation_class({}) is None


@pytest.mark.parametrize("net", [
    np.array([-1, -1, -1, -1, 0, -7, 2, 1], dtype=np.int64),
    np.array([-1, -1, -1, -1, -0.0, -0.125, 0.125, np.nan], dtype=np.float64),
    np.array([False, False, False, False, False, False, True, True]),
    None, 0, [0] * 8, np.array([0], dtype=np.int64),
])
def test_native_situation_offering_shortfall_priority_and_column_fallback(monkeypatch, net):
    native = extension()
    with_backend(monkeypatch, "python")
    n = 8
    row = {"day": np.full(n, 5, dtype=np.int64),
           "petition": np.array([-1, 5, -1, -1, -1, -1, -1, -1], dtype=np.int64),
           "cash": np.full(n, 10, dtype=np.int64), "owed": np.full(n, 8, dtype=np.int64),
           "sit": {"listing": np.array(["delisted", *(["listed"] * (n - 1))]),
                   "offering_pending": np.array([False, False, True, False, False, False, False, False]),
                   "ledger": np.array([1, 1, 1, 0, 1, 1, 1, 1], dtype=np.int64)}}
    if net is not None:
        row["sit"]["offer_available"] = net
    for live in (None, np.ones(n, dtype=bool), np.arange(n) % 2 == 0):
        actual = native.walk_situation_class(row, live)
        assert actual == F.situation_class(row, live).tolist()
    all_live = native.walk_situation_class(row, np.ones(n, dtype=bool))
    assert [value.split(".")[4] for value in all_live[:4]] == ["delisted", "petition", "pending", "nocapacity"]
    tail = [value.split(".")[4] for value in all_live[4:]]
    assert tail == (["insufficient", "insufficient", "available", "available"]
                    if isinstance(net, np.ndarray) and net.shape == (n,) else ["available"] * 4)


@pytest.mark.parametrize("mark_dtype", [np.int32, np.int64, np.bool_])
def test_native_situation_tags_all_draw_rule_and_context_suppressions(monkeypatch, mark_dtype):
    from types import SimpleNamespace
    native = extension()
    with_backend(monkeypatch, "python")
    conds = ["ruled", "settled", "stayed", "stay_moved", "seeking"]
    fc = SimpleNamespace(days=20)
    def situation(d, steps, conditions, tr=None):
        day, pet = tr.day[-1], tr.petition
        inside = (day < 20) & (day < np.where(pet < 0, np.iinfo(np.int64).max, pet))
        return ({c for c in conditions if np.all(tr.marks[c][inside] <= day[inside])},
                {c for c in conditions if not np.any(tr.marks[c][inside] <= day[inside])})
    fc.situation = situation
    w = object.__new__(F._Walk)
    w.fc, w.d, w.N, w.pend = fc, None, 20, True
    tr = F._Prefix(day=[np.array([5, 8, 12], dtype=np.int64)], cash=[], owed=[], collateral=[],
                   petition=np.array([-1, -1, 5], dtype=np.int64), digest=None,
                   marks={"ruled": np.array([1, 1, 1]), "settled": np.array([10**6, 10**6, 1]),
                          "stayed": np.array([2, 2, 2]), "stay_moved": np.array([1, 1, 1]),
                          "seeking": np.array([1, 9, 1])})
    tr = replace(tr, marks={k: v.astype(mark_dtype) for k, v in tr.marks.items()})
    for cls in ("claimed", "entered", "reduced100"):
        for ctx in ((), ("I4",), ("stay_pending",), ("entered",), ("after_seek",)):
            s = F._S(cls=cls)
            assert tuple(native.walk_tags(w, s, conds, ctx, tr, ())) == w._tags(s, conds, ctx, tr, ())


def test_native_walk_python_reference_cycle_is_collectable(monkeypatch):
    import gc
    import weakref
    native = extension()
    _, fc, d = make_forecasters(monkeypatch, "judgment_entered")
    driver = native.NativeWalk(fc, d)
    helper = driver.reference
    ref = weakref.ref(helper)
    helper.native_driver = driver
    del driver, helper
    gc.collect()
    assert ref() is None


@pytest.mark.parametrize("reach", [None, 0, 100_000_000_000])
def test_native_ruling_product_and_class_metadata_keep_python_order(monkeypatch, reach):
    import akoustis_fixture as fx
    native = extension()
    with_backend(monkeypatch, "python")
    a, b, _ = make_forecasters(monkeypatch, "post_trial")
    d = fx.judgment()
    a.reach = b.reach = reach
    expected = a.ruling_classes(d)
    actual = native.walk_ruling_classes(b, d)
    assert list(actual.items()) == list(expected.items())
    assert b.class_members == a.class_members
    assert b.class_range == a.class_range
    assert b.remit_classes == a.remit_classes
    assert list(b.nodes.items()) == list(a.nodes.items())


@pytest.mark.parametrize("days,collateral", [(30, None), (60, (.75, .9))])
def test_native_verdict_producers_match_cut_bounds_questions_and_equity_buffers(monkeypatch, days, collateral):
    import akoustis_20240514_fixture as fx

    from app.analysis.build import basis_for
    from app.analysis.events import _rows_basis
    from app.analysis.shadow import same
    from app.finance.bank import load_feed
    native = extension()
    with_backend(monkeypatch, "python")
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=days), collateral_share=collateral)
    basis = _rows_basis(basis_for(load_feed(fx.SNAP), setup), np.arange(2))
    d = fx.pending().model_copy(update={"status": "interpreted"})
    def create():
        return F.Forecaster([d], {}, borrower="A", review=fx.REVIEW, horizon=setup.horizon,
                            hydrate=lambda f: {}, setup=setup, model=fx.model(), basis=basis)
    a, b = create(), create()
    expected_inflows = a.equity_inflows(d)
    expected_lines = a.verdict_lines(d, expected_inflows)
    expected_classes = a.verdict_classes(d)
    actual_inflows = native.walk_equity_inflows(b, d)
    actual_lines = native.walk_verdict_lines(b, d, actual_inflows)
    assert same(actual_inflows, expected_inflows)
    assert actual_lines == expected_lines
    assert native.walk_equity_inflows(b, d) is actual_inflows
    assert native.walk_verdict_lines(b, d, actual_inflows) is actual_lines
    assert native.walk_verdict_lines(b, d) == a.verdict_lines(d)
    fractional_inflows = np.full((2, days), .75, dtype=np.float64)
    assert native.walk_verdict_lines(b, d, fractional_inflows) == a.verdict_lines(d, fractional_inflows)
    # All nested producers use only the native entry points in this proof.
    monkeypatch.setattr(F.Forecaster, "equity_inflows", lambda self, event: native.walk_equity_inflows(self, event))
    monkeypatch.setattr(F.Forecaster, "verdict_lines", lambda self, event, inflows=None: native.walk_verdict_lines(self, event, inflows))
    actual_classes = native.walk_verdict_classes(b, d)
    assert list(actual_classes.items()) == list(expected_classes.items())
    assert b.verdict_asks == a.verdict_asks
    assert list(b.nodes.items()) == list(a.nodes.items())
    with pytest.raises(ValueError, match="shapes"):
        native.walk_verdict_lines(b, d, np.zeros((1, days), dtype=np.int64))


def predicate_walk():
    from types import SimpleNamespace
    tr = F._Prefix(day=[np.array([5, 8, 12, 25], dtype=np.int64)],
                   cash=[np.array([100, 100, 100, 100], dtype=np.int64)],
                   owed=[np.array([10, 200, 0, 10], dtype=np.int64)], collateral=[],
                   petition=np.array([-1, 5, 3, -1], dtype=np.int64), digest=b"trace",
                   marks={"stayed": np.array([6, 1, 1, 1], dtype=np.int32),
                          "seeking": np.array([3, 1, 1, 1], dtype=np.int32)})
    fc = SimpleNamespace(days=20, m={"parameters": {"levy_lag_days": {"value": 5},
                                                   "holder_notice_lag_days": {"value": 3},
                                                   "offering_price": {"close_days": 7}}}, sens={},
                         spec={"question": {"situation": ["stayed", "seeking"]}})
    fc.trace = lambda d, steps, *args, **kwargs: tr
    fc.bank_trace = lambda steps: tr
    fc.situation = lambda d, steps, conds, tr=None: F.Forecaster.situation(fc, d, steps, conds, tr)
    fc.arises = lambda d, steps, step: F.Forecaster.arises(fc, d, steps, step)
    w = object.__new__(F._Walk)
    w.fc, w.d, w.N, w.pend, w.resp, w.quiet = fc, None, 20, True, "judgment_response", "none"
    w._watch = []
    w._trace = lambda steps, full=False: tr
    w.mask_of = lambda steps: None
    return w, tr


def test_native_trace_predicates_preserve_horizon_petition_and_payment_rules(monkeypatch):
    from types import SimpleNamespace
    native = extension()
    with_backend(monkeypatch, "python")
    w, tr = predicate_walk()
    s, step = F._S(), ("question", "", "no")
    assert native.walk_inside(w, (step,)) == w.inside((step,))
    assert native.walk_bank_inside(SimpleNamespace(fc=w.fc), (step,)) == F._BankWalk.inside(SimpleNamespace(fc=w.fc), (step,))
    assert native.walk_arises(w, s, step) == w.arises(s, step)
    assert native.walk_fc_arises(w.fc, None, (), step) == F.Forecaster.arises(w.fc, None, (), step)
    assert native.walk_pay_possible(w.fc, None, (), step) == F.Forecaster.pay_possible(w.fc, None, (), step)
    conds = ["stayed", "seeking"]
    assert native.walk_fc_situation(w.fc, None, (), conds, tr) == F.Forecaster.situation(w.fc, None, (), conds, tr)
    assert native.walk_fc_situation(w.fc, None, (), [], tr) == (set(), set())
    row = {"day": tr.day[-1], "petition": tr.petition, "owed": tr.owed[-1]}
    for name, context in (("judgment_response", "I0"), ("judgment_response", "entry"), ("listing_kept", "")):
        node = SimpleNamespace(node=name, context=context)
        assert np.array_equal(native.walk_live(w.fc, node, row), F.Forecaster.live(w.fc, node, row))
    assert native.walk_raise_available(row, np.array([1, 0, 0, 0], dtype=np.int64), 20) is True
    assert native.walk_raise_available(row, np.array([0, 1, 1, 1], dtype=np.int64), 20) is False
    dead = replace(tr, day=[np.full(4, 10**6, dtype=np.int64)])
    assert native.walk_fc_situation(w.fc, None, (), conds, dead) == (set(), set(conds))
    w.pend = False
    assert native.walk_arises(w, s, step) == w.arises(s, step)
    with pytest.raises(ValueError, match="shapes"):
        native.walk_inside(type("Walk", (), {"N": 20, "_trace": lambda self, steps: replace(tr, petition=tr.petition[:2])})(), ())


def test_native_event_window_predicates_and_watcher_counterfactual(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    w, tr = predicate_walk()
    s = F._S()
    yes = ("execute_pre_ruling", "I1", "yes")
    assert native.walk_q1_opens_nothing(w, s, yes) == w._q1_opens_nothing(s, yes)
    assert native.walk_levy_first(w, s) == w.levy_first(s)
    assert native.walk_declared_after(w, s, yes) == w._declared_after(s, yes)
    for mask in (None, np.zeros(4, dtype=bool), np.array([True, False, False, False])):
        w.mask_of = lambda steps, mask=mask: mask
        assert native.walk_closes_after(w, s, "entry") == w._closes_after(s, "entry")
    w.mask_of = lambda steps: None
    w._watch = [F._Watch({"stayed": np.zeros(4, dtype=np.int32)})]
    expected = w.situation(s, yes, "question", ())
    expected_read = w._watch[0].read
    w._watch = [F._Watch({"stayed": np.zeros(4, dtype=np.int32)})]
    assert tuple(native.walk_situation(w, s, yes, "question", ())) == expected
    assert w._watch[0].read == expected_read is True


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("fired", [None, 10**6, np.array([1, 15, 10**6], dtype=np.int64)])
def test_native_sibling_cache_checks_waiting_booking_and_read_dates(monkeypatch, full, fired):
    from types import SimpleNamespace
    native = extension()
    with_backend(monkeypatch, "python")
    div = np.array([10, 20, 10**6], dtype=np.int64)
    v = SimpleNamespace(day=[np.array([8, 9, 20], dtype=np.int64)], as_of=np.array([8, 18, 10**6], dtype=np.int64))
    changed = div if fired is None else np.minimum(div, np.where(np.asarray(fired) < 10**6, np.asarray(fired) + 4, 10**6))
    expected = bool((changed >= 10**6).all()) if full else bool(np.where(v.day[-1] < 20, v.as_of < changed, changed >= 10**6).all())
    assert native.walk_cache_valid(v, div, full, 20, fired, 4) == expected
    if not full:
        v.as_of = None
        assert native.walk_cache_valid(v, div, full, 20, fired, 4) is False


@pytest.mark.parametrize("on_rows", [False, True])
def test_native_equivalence_digest_and_cumulative_range_keep_dtype_bits(monkeypatch, on_rows):
    from types import SimpleNamespace
    native = extension()
    with_backend(monkeypatch, "python")
    w, _ = predicate_walk()
    w.N = 5
    mask = np.array([True, False, True])
    full_cash = np.array([[10, -20, 40, -50, 60], [1, 2, 3, 4, 5], [-10, 20, -40, 50, -60]], dtype=np.int64)
    cash = full_cash[mask] if on_rows else full_cash
    lock = np.full_like(cash, 3)
    ev = SimpleNamespace(cash=cash, lock=lock, capacity=np.zeros_like(cash),
                         petition=np.full(len(cash), -1, dtype=np.int64), kinds={"z": cash, "a": lock},
                         incurred={"due": np.full(len(cash), 5, dtype=np.int64)}, proceeds={"offering": cash})
    tr = SimpleNamespace(events=ev, rows=np.array([0, 2]) if on_rows else None,
                         cause=np.array(["issuer", "", "holders"]),
                         marks={k: np.array([1, 5, 10**6], dtype=np.int32) for k in ("stayed", "ruled", "paid", "settled", "raised")})
    s = F._S(steps=(("verdict", "I0", "award:50:0:100"), ("post_trial_ruling", "", "entered")))
    assert native.walk_equivalence(w, s, "unresolved", tr, mask) == w.equivalence(s, "unresolved", tr, mask)
    fc = type("Forecast", (), {})()
    native.walk_event_range(fc, tr, mask)
    cumulative = np.cumsum(cash - lock, axis=1)
    if not on_rows:
        cumulative = cumulative[mask]
    assert np.array_equal(fc.ev_range[0], np.minimum(0., cumulative.min(axis=0)))
    assert np.array_equal(fc.ev_range[1], np.maximum(0., cumulative.max(axis=0)))
    native.walk_event_range(fc, tr, mask)
    assert np.array_equal(fc.ev_range[0], np.minimum(0., cumulative.min(axis=0)))


def test_native_ordinary_cartesian_paths_keep_repeated_edges_and_order(monkeypatch):
    import itertools
    native = extension()
    with_backend(monkeypatch, "python")
    choices = [[[("α", "yes"), ("α", "yes")], [("α", "no")]], [[], [("β", "yes")]]]
    edges = [(F.composite(options), "yes") for options in choices]
    expected = [[edge for part in combination for edge in part] for combination in itertools.product(*choices)]
    assert native.walk_ordinary_expand(edges) == expected
    assert native.walk_ordinary_expand([]) == [[]]
    with pytest.raises(ValueError, match="'yes'"):
        native.walk_ordinary_expand([(edges[0][0], "no")])


@pytest.mark.parametrize("variant", ["same", "mark_inside", "mark_after", "missing_mark", "cause_inside", "cause_outside", "divergent", "subset_divergent"])
def test_native_join_equivalence_reads_only_selected_draws_and_in_horizon_marks(monkeypatch, variant):
    from types import SimpleNamespace

    from app.analysis import events
    native = extension()
    with_backend(monkeypatch, "python")
    w, ta = predicate_walk()
    w.fin = None
    w.fc.setup = None
    w.fc.draws = SimpleNamespace(n=4)
    ta = replace(ta, cause=np.array(["issuer", "", "issuer", ""]))
    tb = replace(ta, cause=ta.cause.copy(), marks={k: v.copy() for k, v in ta.marks.items()})
    div = np.full(4, 10**6, dtype=np.int64)
    if variant == "mark_inside":
        tb.marks["stayed"][0] += 1
    elif variant == "mark_after":
        ta.marks["stayed"][0] = 2 * 10**6
        tb.marks["stayed"][0] = 3 * 10**6
    elif variant == "missing_mark":
        ta.marks["extra"] = np.full(4, 10**6, dtype=np.int32)
    elif variant.startswith("cause_"):
        tb.cause[0 if variant == "cause_inside" else 1] = "holders"
    elif variant == "divergent":
        div[0] = 1
    elif variant == "subset_divergent":
        div = np.array([10**6, 1], dtype=np.int64)
    monkeypatch.setattr(events, "event_chain", lambda *args, **kwargs: SimpleNamespace(divergence=lambda other: div))
    w._trace = lambda steps, full=False: ta if steps[-1][2] == "a" else tb
    a, b = ("question", "", "a"), ("question", "", "b")
    mask = np.array([True, False, True, False])
    assert native.walk_same_after(w, F._S(), a, b, mask) == w._same_after(F._S(), a, b, mask)


def test_native_entered_class_and_reduced_band_select_the_reference_amount(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    fc, _, d = make_forecasters(monkeypatch, "judgment_entered")
    w = F._Walk(fc, d)
    for reach in (None, 0, 100_000_000_000):
        fc.reach = reach
        assert native.walk_entered_class(w) == w._entered_class()
    fc.equity_inflows = lambda event: None
    fc.verdict_lines = lambda event, inflows: {"bands": [(0, 0, 0), (0, 10, 5), (10, 20, 15), (20, 100, 60)]}
    for label in ("no_award", "award:5:0:10", "award:15:10:20", "award:60:20:100"):
        s = F._S(steps=(("verdict", "I0", label),))
        assert native.walk_reduced_band(w, s) == w.reduced_band(s)


def test_native_path_conversion_shares_large_repeated_prefix_objects(monkeypatch):
    import sys
    native = extension()
    with_backend(monkeypatch, "python")
    name = "node_" + "x" * 60_000
    key = F.composite([[(name, "yes")]])
    steps, edges = ((name, "phase", "no"),), ((key, "yes"),)
    paths = [F.DisputePath("d", steps, "unresolved", edges) for _ in range(100)]
    wire = [F._to_native_path(p) for p in paths]
    outputs = (native.walk_merge(wire, [[i] for i in range(100)], {}),
               native.walk_expand_classes(wire, [], 1, []))
    for rows in outputs:
        first_step, first_edge = rows[0][1][0], rows[0][3][0]
        assert all(row[1][0] is first_step and row[3][0] is first_edge for row in rows)
        converted = [F._from_native_path(row) for row in rows]
        assert converted == paths
        assert all(path.steps is steps and path.edges is edges for path in converted)
        todo, seen, retained = [rows], set(), 0
        while todo:
            obj = todo.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            retained += sys.getsizeof(obj)
            if isinstance(obj, (list, tuple)):
                todo.extend(obj)
        assert retained < 200_000


@pytest.mark.parametrize("operation", ["merge", "expand"])
def test_native_path_algebra_keeps_shared_long_prefixes_under_address_space_bound(operation):
    """A bounded child catches full-tree string extraction before it can exhaust the test runner."""
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    code = textwrap.dedent(r'''
        import gc
        import json
        import resource
        import sys
        from app import _native
        from app.disputes.forecast import _from_native_path

        operation = sys.argv[1]
        name = "shared_" + "x" * 1_000_000
        history = ((name, name, "yes"),) * 24
        common_key = "=" + json.dumps([[(name, "yes"), ("q", "yes")]], separators=(",", ":"))
        common_edge = (common_key, "yes")
        classes = (("q", ("#a", "#b"), bytes([0, 1])),)
        count = 128
        rows = [("d", history, "unresolved", (common_edge, ("answer", "yes" if i % 2 == 0 else "no")),
                 "claimed", None, classes) for i in range(count)]
        gc.collect()
        with open("/proc/self/status") as status:
            memory = {line.split(":", 1)[0]: int(line.split()[1]) for line in status
                      if line.startswith(("VmSize:", "VmHWM:"))}
        before = memory["VmHWM"]
        vmsize = memory["VmSize"] * 1024
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (vmsize + 128 * 1024**2,) * 2)
        if operation == "merge":
            output = _native.walk_merge(rows, [[0, 1], *[[i] for i in range(2, count)]], {})
            assert len(output) == count - 1
            assert output[0][3][0] is common_edge
            assert output[0][3][-1] == ('=[[["answer","yes"]],[["answer","no"]]]', "yes")
            assert all(output[i - 1] is rows[i] for i in range(2, count))
        else:
            output = _native.walk_expand_classes(rows, ["q|#a", "q|#b"], 2, [])
            assert len(output) == count * 2
            assert output[0][5] == b"\x80" and output[1][5] == b"@"
            keys = ["=" + json.dumps([[(name, "yes"), ("q|" + tag, "yes")]], separators=(",", ":"))
                    for tag in ("#a", "#b")]
            for i, row in enumerate(output):
                assert row[3][0][0] == keys[i % 2] and not row[6]
                assert row[3][0][0] is output[i % 2][3][0][0]
        assert all(row[1] is history for row in output)
        converted = [_from_native_path(row) for row in output]
        assert all(path.steps is history for path in converted)
        with open("/proc/self/status") as status:
            peak_delta_kib = next(int(line.split()[1]) for line in status if line.startswith("VmHWM:")) - before
        assert peak_delta_kib < 64 * 1024, peak_delta_kib
        print(json.dumps({"operation": operation, "peak_delta_kib": peak_delta_kib, "rows": len(output)}))
    ''')
    result = subprocess.run([sys.executable, "-c", code, operation],
                            cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
                            text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["operation"] == operation


def test_native_merge_class_sort_uses_text_and_composite_no_keeps_last_duplicate_key(monkeypatch):
    native = extension()
    with_backend(monkeypatch, "python")
    classes = (("😀", ("#z",), None), ("é", ("#a",), None), ("a", ("#first",), None))
    composite = F.composite([[('q', 'no'), ('q', 'yes'), ('r', 'yes')]])
    paths = [F.DisputePath("d", (), "same", ((composite, "no"),), classes=classes),
             F.DisputePath("d", (), "same", ((composite, "yes"),), classes=classes)]
    branches = {"q": ("yes", "no"), "r": ("yes", "no")}
    reference = F.merge_equivalent(paths, [0, 0], branches)
    actual = [F._from_native_path(row) for row in native.walk_merge([F._to_native_path(p) for p in paths], [[0, 1]], branches)]
    assert actual == reference
    assert [c[0] for c in actual[0].classes] == ["a", "é", "😀"]


def test_native_path_adapter_keeps_tuple_prefixes_and_normalizes_lists():
    steps, edges = (("node", "phase", "yes"),), (("q", "no"),)
    wire = ("d", steps, "same", edges, "claimed", None, ())
    path = F._from_native_path(wire)
    assert path.steps is steps and path.edges is edges
    lists = ("d", [list(step) for step in steps], "same", [list(edge) for edge in edges], "claimed", None, [])
    assert F._from_native_path(lists) == path
    assert type(F._from_native_path(lists).steps) is tuple
    assert all(type(row) is tuple for row in F._from_native_path(lists).steps)

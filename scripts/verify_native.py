#!/usr/bin/env python3
"""Byte-exact execution-core verification in isolated Python/Rust interpreters.

Run ``python scripts/verify_native.py`` for the bounded real-case tree and all
financial fixtures. ``--draws 512 --horizon 180`` verifies the production-sized
case without a leaf cap; it can take a long time. The default tree is complete
for its explicitly shortened horizon, never a prefix mistaken for a full run.
No network or Jev call is made. The Python baseline is the retained reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def exact(value):
    """Lossless deterministic form: include order, dtype, shape and float bits.

    Object arrays serialize their values, not process-specific pointer bytes.
    Cache contents and performance counters are deliberately not part of the
    execution result; each caller explicitly selects all semantic outputs.
    """
    import numpy as np

    name = f"{type(value).__module__}.{type(value).__qualname__}"
    if isinstance(value, np.ndarray):
        data = tuple(exact(x) for x in value.ravel().tolist()) if value.dtype.hasobject else (
            np.ascontiguousarray(value).tobytes())
        return ("array", value.dtype.str, value.shape, data)
    if isinstance(value, (float, np.floating)):
        return ("float", name, np.asarray(value).dtype.str, np.asarray(value).tobytes())
    if isinstance(value, Mapping):
        # Mapping insertion order controls first-occurrence facts and nodes.
        return ("mapping", tuple((exact(k), exact(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return (name, tuple(exact(x) for x in value))
    if isinstance(value, (set, frozenset)):
        return (name, tuple(sorted((exact(x) for x in value), key=repr)))
    if is_dataclass(value):
        # Trajectories.processed is intentionally a dynamic field.
        state = {f.name: getattr(value, f.name) for f in fields(value)}
        if hasattr(value, "processed"):
            state["processed"] = value.processed
        return (name, exact(state))
    if hasattr(value, "model_dump"):
        return (name, exact(value.model_dump(mode="python")))
    if isinstance(value, np.generic):
        return (name, value.dtype.str, value.tobytes())
    if value is None or isinstance(value, (str, bytes, bool, int, date)):
        return (name, value)
    raise TypeError(f"No exact serialization for {name}")


def probability_digest(value):
    """Keep dtype/shape and hash every probability byte without retaining it."""
    import numpy as np

    return ("probability-bytes", value.dtype.str, value.shape,
            hashlib.sha256(np.ascontiguousarray(value).tobytes()).digest())


def normalize_walk_probabilities(snapshot):
    """Convert a saved raw endpoint snapshot to the large-run digest form.

    The default 45-day run retains complete probability arrays. Longer runs
    still evaluate every individual 0/100 endpoint, but retain a byte digest
    instead of thousands of dense probability vectors. This also lets a worker
    started before that optimization compare its complete saved arrays.
    """
    assert snapshot[0] == "mapping"
    out = []
    for key, value in snapshot[1]:
        if key == ("builtins.str", "probabilities"):
            probabilities = []
            for question, probability in value[1]:
                if question[0] == "builtins.tuple" and question[1][0] != ("builtins.str", "all"):
                    if probability[0] == "array":
                        _, dtype, shape, data = probability
                        probability = exact(("probability-bytes", dtype, shape, hashlib.sha256(data).digest()))
                probabilities.append((question, probability))
            value = ("mapping", tuple(probabilities))
        out.append((key, value))
    return ("mapping", tuple(out))


def difference(a, b, at="result"):
    """First structural or bit-level difference, with concise diagnostics."""
    if type(a) is not type(b):
        return f"{at}: types {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, tuple) and isinstance(b, tuple) and a and b and a[0] == b[0]:
        if a[0] == "mapping" and len(a) == len(b) == 2:
            if [k for k, _ in a[1]] != [k for k, _ in b[1]]:
                return f"{at}: mapping keys or order differ"
            for (key, x), (_, y) in zip(a[1], b[1], strict=True):
                label = key[1] if len(key) == 2 and key[0] in ("builtins.str", "builtins.int") else repr(key)
                if msg := difference(x, y, f"{at}[{label!r}]"):
                    return msg
            return None
        if a[0] in ("builtins.list", "builtins.tuple") and len(a) == len(b) == 2:
            return difference(a[1], b[1], at)
        if isinstance(a[0], str) and a[0].startswith("app.") and len(a) == len(b) == 2:
            return difference(a[1], b[1], at)
        if a[0] == "array" and len(a) == len(b) == 4:
            if a[1:3] != b[1:3]:
                return f"{at}: array dtype/shape {a[1:3]!r} != {b[1:3]!r}"
            msg = difference(a[3], b[3], at)
            if msg and isinstance(a[3], bytes):
                import numpy as np

                dtype = np.dtype(a[1])
                x, y = np.frombuffer(a[3], dtype=dtype), np.frombuffer(b[3], dtype=dtype)
                # Byte equality locates signed-zero and NaN-payload changes too.
                changed = np.flatnonzero(np.any(x.view(np.uint8).reshape(-1, dtype.itemsize) !=
                                               y.view(np.uint8).reshape(-1, dtype.itemsize), axis=1))
                i = int(changed[0])
                index = np.unravel_index(i, a[2])
                return f"{at}{index}: {x[i]!r} != {y[i]!r} ({msg})"
            return msg
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{at}: lengths {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if msg := difference(x, y, f"{at}[{i}]"):
                return msg
        return None
    if isinstance(a, dict):
        if list(a) != list(b):
            return f"{at}: mapping keys or order differ"
        for k in a:
            if msg := difference(a[k], b[k], f"{at}[{k!r}]"):
                return msg
        return None
    if a != b:
        if isinstance(a, bytes):
            offset = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
            return f"{at}: byte mismatch at {offset}, lengths {len(a)}/{len(b)}"
        return f"{at}: {a!r} != {b!r}"
    return None


def case_basis(draws, horizon, setup=None):
    import akoustis_20240514_fixture as fx

    from app.analysis import operating
    from app.analysis.engine import prepare
    from app.analysis.events import Basis
    from app.analysis.setup import SEED
    from app.finance.bank import load_feed

    s = replace(setup or fx.setup(), horizon=fx.REVIEW + timedelta(days=horizon))
    feed = load_feed(fx.SNAP)
    ops = operating.simulate(feed, horizon + s.need_days, draws, SEED, s.variability, s.cost_plan, s.financing)
    line = prepare(s, ops)
    return s, Basis.of(ops, line.need, feed.available_cents + s.exposure.cash_cents, line), feed


def cash_snapshot(draws):
    """Real 180-day operating inputs with adversarial typed event bookings.

    Exercise both processors, collection modes, same-day orders, opening
    exposure, batch vs standalone, petitions on boundaries, all obligation
    types, locks/releases, discount floats, and nonpayment window boundaries.
    """
    import numpy as np

    from app.analysis.engine import prepare, run, run_many
    from app.analysis.events import INCURRED_BEFORE, KINDS, EventCash

    s, b, feed = case_basis(draws, 180)
    output = {}
    rng = np.random.default_rng(982376)
    events = [EventCash.zeros(draws, 180, kinds=True) for _ in range(4)]
    for k, ev in enumerate(events[1:], 1):
        ev.kinds["inflow"][:, [0, 31, 90, 179]] = rng.integers(0, 400_000_000, (draws, 4), dtype=np.int64)
        ev.kinds["levy"][:, [15, 100]] = -rng.integers(0, 2_000_000, (draws, 2), dtype=np.int64)
        for j, kind in enumerate(("settlement", "notes_interest", "judgment")):
            ev.kinds[kind][:, [1, 29, 30, 60, 120]] = -rng.integers(0, 250_000_000, (draws, 5), dtype=np.int64)
            ev.incurred[kind][:] = INCURRED_BEFORE if kind == "notes_interest" else j
        ev.kinds["reduction"][:, 45:55] = np.minimum(-b.legal[:, 45:55], 100_000)
        ev.cash = sum((ev.kinds[x] for x in KINDS), np.zeros_like(ev.cash))
        ev.lock[:, 10] = 100
        ev.lock[:, 11] = -100
        if k == 2:
            ev.petition[:] = np.resize(np.array([-1, 0, 1, 30, 90, 179, 180], dtype=np.int64), draws)
        elif k == 3:
            ev.petition[:] = 179
    for processor in ("net", "daily"):
        for collection in ("debit", "protect_need"):
            for order in ("scheduled_first", "operating_first"):
                ss = replace(s, cash_processing=processor, collection=collection, same_day_order=order)
                line = prepare(ss, b.line.ops)
                opening = feed.available_cents
                for terms in (None, (1, 2500), (15, 2500), (30, 0), (30, 10000)):
                    got = run_many(line, opening, events, terms)
                    solo = [run(line, opening, ev, terms) for ev in events]
                    if msg := difference(exact(got), exact(solo)):
                        raise AssertionError(f"batch vs standalone {processor}/{collection}/{order}/{terms}: {msg}")
                    for t in got:
                        assert np.array_equal(t.collected + t.stayed + t.not_yet_due + t.uncollected, t.contractual)
                        if processor == "daily":
                            assert (t.cash >= 0).all()
                    key = (processor, collection, order, terms)
                    output[key] = got
                    if processor == "daily":
                        output[("cash_only", *key)] = run_many(line, opening, events, terms, cash_only=True)
    return exact(output), {"cash_modes": 8, "event_paths": len(events), "cash_terms": 5, "days": 180}


def chain_snapshot(draws):
    """Full-horizon path facts, COW branches, keyed subdraws and stress draws."""
    import akoustis_20240514_fixture as fx
    import numpy as np

    from app.analysis.engine import run
    from app.analysis.events import BIG, Chain, Draws

    s, b, feed = case_basis(draws, 180)
    m, d = fx.model(), fx.pending()
    paths = [
        (),
        (("settle", "I0", "yes"), ("listing", "", "compliant"), ("cash_floor", "1", "neither"),
         ("cash_out", "", "neither")),
        (("verdict", "I0", "no_award"), ("listing", "", "compliant")),
        (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "file")),
        (("verdict", "I0", "without_principal_measure"), ("judgment_response", "entry", "none"),
         ("post_trial_motions", "", "no")),
        (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "none"),
         ("post_trial_motions", "", "yes"), ("execute_pre_ruling", "I1", "yes"), ("stay", "I1", "no"),
         ("registration_early", "I1", "yes"), ("judgment_response", "I1", "file")),
        (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "none"),
         ("post_trial_motions", "", "yes"), ("execute_pre_ruling", "I1", "no"),
         ("post_trial_ruling", "", "set_aside"), ("listing", "", "compliant")),
        (("listing", "", "compliant"), ("cash_floor", "1", "initiate_offering"),
         ("offering", "floor1", "yes"), ("cash_out", "", "neither")),
        (("listing", "", "compliant"), ("cash_floor", "1", "initiate_offering"),
         ("offering", "floor1", "no"), ("cash_out", "", "neither")),
        (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "initiate_offering"),
         ("offering", "entry", "yes"), ("post_trial_motions", "", "no"), ("listing", "", "compliant")),
    ]
    sensitivities = ({}, {"coupon_cash_share": True, "atm_pace_bps": 1000, "nonpayment_window_days": 15},
                     {"settlement_payment": True}, {"lower_award_amount": True}, {"offering_materiality": True})
    assert m["parameters"]["offering_materiality"]["value"] == "covers_shortfall"
    assert m["parameters"]["offering_materiality"]["sensitivity"] == "any_proceeds"
    legacy = {**m, "parameters": {k: v for k, v in m["parameters"].items() if k != "offering_materiality"}}
    configurations = [("configured", m, sens) for sens in sensitivities] + [("legacy", legacy, {})]
    out = []
    for stress in (False, True):
        for model_kind, model, sens in configurations:
            dr = Draws(draws, stress=stress, basis=b)
            dr.prefixes = {}
            mask = np.arange(draws) % 2 == 0
            for steps in paths:
                ch = Chain(d, s, model, dr, sens)
                tr = ch.run(steps)
                rule = ch.initiation_rule()
                assert rule == ("any_proceeds" if model_kind == "legacy" or sens.get("offering_materiality")
                                else "covers_shortfall")
                days = np.array([-1, 0, 1, 29, 30, 90, 179, 180, BIG], dtype=np.int64)
                owing = [ch.owed_at(np.full(draws, int(day), dtype=np.int64), enforceable=e)
                         for e in (False, True) for day in days]
                price = ch._price_owed_grid()
                offers = []
                for day in (np.full(draws, t, dtype=np.int64) for t in (0, 30, 90, 179)):
                    shortfall, available = ch.offer_shortfall(day), ch.offer_available(day)
                    assert shortfall.shape == available.shape == (draws,)
                    assert (shortfall >= 0).all()
                    if rule == "covers_shortfall":
                        assert ((available == 0) | (available >= shortfall)).all()
                    offers.append((day, shortfall, available, ch.option_group("cash_floor", day), ch.c_situation(day)))
                financial = run(b.line, feed.available_cents, tr.events, ch.nonpayment_terms())
                # A clone's bookings must not mutate its parent's arrays.
                before = exact(tr)
                cloned = ch.clone()
                cloned.ev.cash = cloned.ev.cash.copy()
                cloned.ev.cash[:, 0] += 1
                assert exact(tr) == before
                sub = Chain(d, s, model, dr.sub(mask), sens).run(steps)
                assert np.array_equal(sub.events.cash, tr.events.cash[mask])
                assert np.array_equal(sub.events.petition, tr.events.petition[mask])
                out.append((stress, model_kind, sens, steps, tr, owing, price, offers, financial, sub))
            out.append(("draws", stress, model_kind, sens, dr.cache))
    return exact(out), {"chain_paths": len(paths), "sensitivities": len(sensitivities),
                        "model_configurations": len(configurations), "stress_modes": 2, "days": 180,
                        "initiation_rules": ["covers_shortfall", "any_proceeds", "missing-parameter-any_proceeds"]}


def walk_snapshot(draws, horizon, processes):
    """Whole real-case tree, ordered question records, probabilities and masks."""
    import akoustis_20240514_fixture as fx
    import numpy as np

    from app.analysis import core
    from app.analysis.events import Chain, Draws
    from app.disputes.forecast import Forecaster, Judgment, expand_classes, path_mask

    # EventModel class expansion uses the configured operating draw count.
    core.DRAWS = draws
    s, b, feed = case_basis(draws, horizon)
    d = fx.pending().model_copy(update={"status": "interpreted"})
    fc = Forecaster([d], {}, borrower="Akoustis Technologies, Inc.", review=s.review, horizon=s.horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=s, basis=b)
    print(json.dumps({"stage": "tree", "draws": draws, "horizon": horizon, "processes": processes}), flush=True)
    if processes == 1:
        per = fc.all_paths()
    else:
        from app.disputes.parallel import all_paths

        per = all_paths(fc, processes)
    bank = fc.bank_paths()
    paths = per[d.instance_id][""]
    print(json.dumps({"stage": "probabilities", "paths": len(paths), "nodes": len(fc.nodes),
                      "facts": sum(map(len, fc.facts.values()))}), flush=True)
    js = {}
    for i, (key, n) in enumerate({**fc.bank_nodes, **fc.nodes}.items()):
        weights = np.arange(1, len(n.branches) + 1, dtype=float) + i % 7
        js[key] = Judgment(key=key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                           event=n.event, assumptions=n.assumptions, window=n.window,
                           distribution=dict(zip(n.branches, weights / weights.sum(), strict=True)))
    em = core.EventModel({d.instance_id: d}, {k: js[k] for k in fc.nodes}, per, fc.ordered(),
                         bank_paths=bank, bank_judgments={k: js[k] for k in fc.bank_nodes})
    from app.disputes.forecast import _conjunctions

    for combo in em.combos:
        for p in combo:
            for edge, _branch in p.edges:
                if not edge.startswith("="):
                    continue
                for conj in _conjunctions(edge):
                    for atom, answer in conj:
                        if atom in js and answer not in js[atom].distribution:
                            print(json.dumps({"missing_answer": answer, "key": atom,
                                              "declared_branches": fc.nodes[atom].branches,
                                              "distribution": js[atom].distribution, "edge": edge,
                                              "path_steps": p.steps}, sort_keys=True), flush=True)
                            raise AssertionError("Class expansion references an answer absent from its declared question")
    probabilities = {"base": (em.probs(), em.bank_probs())}
    # Every question at both endpoints; repeated simultaneous overrides include
    # classed and composite-dependent questions, not just one arbitrary key.
    for endpoint in (0, -1):
        overrides = {key: {x: float(x == n.branches[endpoint]) for x in n.branches}
                     for key, n in {**fc.bank_nodes, **fc.nodes}.items()}
        probabilities[("all", endpoint)] = (em.probs(overrides), em.bank_probs(overrides))
    digest_endpoints = horizon > 45
    for i, (key, n) in enumerate(fc.nodes.items()):
        for endpoint, branch in ((0, n.branches[0]), (100, n.branches[-1])):
            p = em.probs({key: {x: float(x == branch) for x in n.branches}})
            probabilities[(key, endpoint)] = probability_digest(p) if digest_endpoints else p
        if (i + 1) % 128 == 0:
            print(json.dumps({"stage": "probabilities", "questions_checked": i + 1,
                              "questions_total": len(fc.nodes)}), flush=True)
    for key, (p, bp) in [(k, v) for k, v in probabilities.items() if k in ("base", ("all", 0), ("all", -1))]:
        for combos, probs in ((em.combos, p), (em.bank_combos, bp)):
            cover = np.zeros(draws)
            for combo, weight in zip(combos, probs, strict=True):
                from app.disputes.forecast import combo_mask

                mask = combo_mask(combo, draws)
                cover += weight * (1.0 if mask is None else mask)
            assert np.abs(cover - 1).max() < 1e-9, (key, cover)
    # Sample every resulting path family deterministically; full financial
    # regression is performed separately by the full-horizon chain fixtures.
    traces = []
    stride = max(1, len(paths) // 16)
    for p in paths[::stride][:16]:
        tr = Chain(d, s, fc.m, Draws(draws, basis=b)).run(p.steps)
        traces.append((p.steps, tr, path_mask(p, draws)))
    expanded = expand_classes(paths, set(js), draws)
    print(json.dumps({"stage": "serialize", "expanded_paths": len(expanded)}), flush=True)
    output = {
        "paths": per, "bank_paths": bank, "expanded_classes": expanded,
        "nodes": fc.nodes, "bank_nodes": fc.bank_nodes,
        "facts": {k: list(v) for k, v in fc.facts.items()},
        "bank_facts": {k: list(v) for k, v in fc.bank_facts.items()},
        "classed": fc.classed, "node_group": fc.node_group,
        "class_members": fc.class_members, "class_range": fc.class_range,
        "remit_classes": fc.remit_classes, "remitted": fc.remitted,
        "probabilities": probabilities, "traces": traces,
        "draws": {key: fc.draws.u(d.instance_id, *key) for key in
                  (("verdict", "window"), ("appeal", "lag"), ("stay", "collateral"), ("cash_floor", "offer"))},
    }
    # Dict insertion order in class metadata and draw caches is an execution
    # detail: parallel workers discover these in a different order. Semantic
    # question/fact/path insertion order above remains strict.
    for k in ("node_group", "class_members", "class_range", "remitted", "draws"):
        output[k] = dict(sorted(output[k].items()))
    return exact(output), {"draws": draws, "horizon": horizon, "paths": len(paths), "expanded_paths": len(expanded),
                           "nodes": len(fc.nodes), "fact_occurrences": sum(map(len, fc.facts.values())),
                           "processes": processes, "capped": False,
                           "probability_storage": "sha256-each-endpoint" if digest_endpoints else "full-arrays"}


def region_snapshot():
    """Speculative watches: transitive reads, first order and duplicate units."""
    from app.disputes.parallel import _live

    stream = [
        ((0, 1, 0, 0), "node", ("top",), ()),
        ((1, 0, 0, 0), "read", ("A",), ()),
        ((1, 0, 0, 1), "node", ("A-node",), ("A",)),
        ((1, 0, 1, 0), "read", ("B",), ("A",)),
        ((1, 0, 1, 1), "path", ("AB-path",), ("A", "B")),
        ((2, 0, 2, 0), "read", ("C",), ("missing",)),
        ((2, 0, 2, 1), "node", ("C-node",), ("C",)),
        ((3, 0, 3, 0), "path", ("unconditional",), ()),
    ]
    first, second = {"k": 0, "events": stream[::2]}, {"k": 1, "events": stream[1::2]}
    got = _live([second, first])
    assert [e[2][0] for e in got] == ["top", "A-node", "AB-path", "unconditional"]
    try:
        _live([first, first])
    except AssertionError:
        duplicate_rejected = True
    else:
        duplicate_rejected = False
    assert duplicate_rejected, "A subtree contributed twice without an error"
    return exact((got, duplicate_rejected)), {"watched_regions": 4, "kept_events": len(got)}


def analysis_snapshot(draws, horizon):
    """Every path's financial reduction and the reweighted reporting views."""
    import akoustis_20240514_fixture as fx
    import numpy as np

    from app.analysis import core, operating
    from app.analysis.page import chart_view, path_scalars
    from app.disputes.forecast import Forecaster, Judgment

    core.DRAWS = operating.DRAWS = draws
    setup, basis, feed = case_basis(draws, horizon)
    dispute = fx.pending().model_copy(update={"status": "interpreted"})
    model = fx.model()
    fc = Forecaster([dispute], {}, borrower="Akoustis Technologies, Inc.", review=setup.review,
                    horizon=setup.horizon, hydrate=lambda _: {}, model=model, setup=setup, basis=basis)
    paths, bank_paths = fc.all_paths(), fc.bank_paths()
    judgments = {}
    all_nodes = {**fc.bank_nodes, **fc.nodes}
    for i, (key, node) in enumerate(all_nodes.items()):
        weights = np.arange(1, len(node.branches) + 1, dtype=float) + i % 7
        judgments[key] = Judgment(key=key, instance_id=node.instance_id, node=node.node,
                                  question_id=node.question_id, event=node.event, assumptions=node.assumptions,
                                  window=node.window,
                                  distribution=dict(zip(node.branches, weights / weights.sum(), strict=True)))
    em = core.EventModel({dispute.instance_id: dispute}, {key: judgments[key] for key in fc.nodes}, paths,
                         fc.ordered(), bank_paths=bank_paths,
                         bank_judgments={key: judgments[key] for key in fc.bank_nodes})
    print(json.dumps({"stage": "analysis", "paths": len(em.combos), "draws": draws, "horizon": horizon}), flush=True)
    analysis = core.Analysis(feed, setup, em, dispute_model=model)

    def reduction_state(reduced):
        return {key: {name: vars(value) for name, value in data.items()} if key in ("bins", "counts") else data
                for key, data in vars(reduced).items()}

    summaries = {}
    for endpoint in (None, 0, -1):
        overrides = None if endpoint is None else {
            key: {branch: float(branch == node.branches[endpoint]) for branch in node.branches}
            for key, node in all_nodes.items()}
        probabilities, bank_probabilities = em.probs(overrides), em.bank_probs(overrides)
        summaries[endpoint] = {
            "probabilities": (probabilities, bank_probabilities), "views": analysis.views(overrides),
            "attribution": analysis.attribution(overrides), "scenarios": analysis.scenarios(overrides),
            "chart": chart_view(analysis.r.for_reweight(), probabilities, analysis.months),
            "bank_chart": chart_view(analysis.bank_r.for_reweight(), bank_probabilities, analysis.months),
            "first_floor": (analysis.r.first_floor(probabilities), analysis.bank_r.first_floor(bank_probabilities)),
        }
    output = {"event": reduction_state(analysis.r), "bank": reduction_state(analysis.bank_r),
              "reweight": reduction_state(analysis.r.for_reweight()), "months": analysis.months,
              "path_scalars": path_scalars(analysis.r), "summaries": summaries, "operating": analysis.ops}
    return exact(output), {"draws": draws, "horizon": horizon, "event_paths": len(em.combos),
                           "bank_paths": len(em.bank_combos), "reweight_cases": len(summaries), "capped": False}


def reduction_snapshot():
    """Sparse counts and float normalization across NumPy reduction boundaries."""
    import numpy as np

    from app.analysis.core import Bins, Counts
    from app.analysis.stats import weighted_quantiles

    rng = np.random.default_rng(775834)
    out = []
    rows, paths, draws = 7, 6, 17
    qs = (0.0, 0.05, 0.5, 0.95, 1.0)
    for nbin in (1, 7, 8, 127, 128, 129, 1024):
        lo = np.arange(rows, dtype=np.float64) * -7711
        width = np.arange(1, rows + 1, dtype=np.float64) * 317.13
        bins = Bins(lo, width, nbin)
        counts = Counts(rows, nbin)
        flats = []
        for _ in range(paths):
            values = np.asfortranarray(rng.integers(-50000, 900000, (draws, rows))).astype(np.float64)
            flat = bins.flat(values)
            counts.add(flat)
            # Irregular one-dimensional samples assign arbitrary day/month rows.
            sample_rows = rng.integers(0, rows, 19, dtype=np.int64)
            flats.append((flat, bins.flat(values.ravel()[:19], sample_rows)))
        counts.finish()
        weighted = []
        for p in (np.zeros(paths), np.array([1., 0., 0., 0., 0., 0.]), rng.dirichlet(np.ones(paths))):
            for normalise in (False, True):
                hist = counts.weighted(p, normalise=normalise)
                weighted.append((p, normalise, hist, bins.quantiles(hist, qs)))
        out.append((nbin, flats, counts.idx, counts.cnt, counts.lens, weighted))
    # Stable tie order matters when +0/-0 or NaN payloads share a sort key.
    values = np.array([0.0, -0.0, 2., 2., -3., np.inf, np.nan, -np.inf, 1.])
    weights = np.array([0., 0.2, 0.1, 0., 0.2, 0.2, 0., 0., 0.3])
    for x in (values, values[::-1], np.column_stack((values, values[::-1])),
              np.asfortranarray(np.column_stack((values, values)))):
        out.append((x, weighted_quantiles(x, weights, qs)))
    return exact(out), {"histogram_bin_counts": [1, 7, 8, 127, 128, 129, 1024],
                       "probability_modes": 3, "normalization_modes": 2, "quantiles": list(qs)}


SNAPSHOTS = {"cash": cash_snapshot, "chains": chain_snapshot, "regions": region_snapshot,
             "reduction": reduction_snapshot, "walk": walk_snapshot, "analysis": analysis_snapshot}


def worker(args):
    import numpy as np

    from app.analysis import native, shadow

    selected = native.backend()
    if selected != args.backend:
        raise AssertionError(f"Requested {args.backend}, selected {selected}")
    if selected == "rust":
        native._extension()  # fail before expensive work if it has not been built
    native_calls = {}
    resolve = native.native_function

    def audited_function(name, function):
        @wraps(function)
        def call(*a, **kw):
            native_calls[name] = native_calls.get(name, 0) + 1
            return function(*a, **kw)

        return call

    # Rust controllers also call exported numerical primitives directly. Wrap
    # the actual extension functions so those calls count once too; constructors
    # remain types and are witnessed through the selector below.
    exported = set()
    if selected == "rust":
        import inspect

        extension = native._extension()
        for name, function in tuple(vars(extension).items()):
            if inspect.isbuiltin(function):
                setattr(extension, name, audited_function(name, function))
                exported.add(name)

    def audited_resolve(name):
        function = resolve(name)
        return function if function is None or name in exported else audited_function(name, function)

    # Install before importing the engine modules, including ``from ... import
    # native_function`` consumers. Counts represent actual calls, not lookups.
    native.native_function = audited_resolve
    if selected == "rust":
        from app.analysis.events import Chain as RustChain

        def audited_method(name, function):
            @wraps(function)
            def call(*a, **kw):
                key = f"NativeChain.{name}"
                native_calls[key] = native_calls.get(key, 0) + 1
                return function(*a, **kw)

            return call

        # The Chain facade invokes NativeChain directly rather than resolving
        # a standalone kernel. Audit those entry points separately, including
        # property reads; absent native transitions still raise in Rust.
        for name, function in tuple(vars(RustChain).items()):
            if name.startswith("__") or name == "_bind_inputs":
                continue
            if isinstance(function, property):
                setattr(RustChain, name, property(audited_method(name, function.fget)))
            elif callable(function) and not isinstance(function, (classmethod, staticmethod)):
                setattr(RustChain, name, audited_method(name, function))
    start = time.perf_counter()
    if args.scenario == "walk":
        value, coverage = walk_snapshot(args.draws, args.horizon, args.processes)
    elif args.scenario == "analysis":
        value, coverage = analysis_snapshot(args.draws, args.horizon)
    elif args.scenario in ("cash", "chains"):
        value, coverage = SNAPSHOTS[args.scenario](args.draws)
    else:
        value, coverage = SNAPSHOTS[args.scenario]()
    # numpy version belongs in diagnostics, never in the semantic comparison.
    result = {"result": value, "metadata": {"backend": selected, "seconds": time.perf_counter() - start,
              "coverage": coverage, "numpy": np.__version__, "shadow_counts": dict(shadow.counts),
              "native_calls": native_calls}}
    with args.output.open("wb") as f:
        pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)


def capture(scenario, backend, draws=4, horizon=45, processes=1, timeout=1800, work=None):
    """Run an isolated backend worker; no selector can silently fall back."""
    folder = Path(work) if work else Path(tempfile.mkdtemp(prefix="native_verify_"))
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{scenario}-{backend}-{processes}.pkl"
    log = output.with_suffix(".log")
    env = {**os.environ, "SLOPE_EXECUTION_BACKEND": backend, "SLOPE_SHADOW": "1" if backend == "rust" else "0",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--scenario", scenario, "--backend", backend,
           "--draws", str(draws), "--horizon", str(horizon), "--processes", str(processes), "--output", str(output)]
    with log.open("w") as f:
        cp = subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    if cp.returncode:
        raise AssertionError(f"{scenario}/{backend}/{processes} failed ({cp.returncode}):\n{log.read_text()[-12000:]}")
    with output.open("rb") as f:
        return pickle.load(f)


def verify(scenarios, draws=4, horizon=45, processes=2, timeout=1800):
    report = {"verified": [], "draws": draws, "tree_horizon": horizon, "leaf_cap": None,
              "full_production_tree": draws == 512 and horizon == 180}
    with tempfile.TemporaryDirectory(prefix="native_verify_") as work:
        for scenario in scenarios:
            baseline = capture(scenario, "python", draws, horizon, 1, timeout, work)
            candidate = capture(scenario, "rust", draws, horizon, 1, timeout, work)
            if msg := difference(baseline["result"], candidate["result"]):
                raise AssertionError(f"{scenario}: native != reference: {msg}")
            metadata = candidate["metadata"]
            entry = {"scenario": scenario, "python_seconds": baseline["metadata"]["seconds"],
                     "rust_seconds": metadata["seconds"], "coverage": metadata["coverage"],
                     "sha256": hashlib.sha256(pickle.dumps(baseline["result"], protocol=5)).hexdigest(),
                     "shadow_counts": metadata["shadow_counts"], "native_calls": metadata["native_calls"]}
            if scenario == "walk" and processes > 1:
                for backend in ("python", "rust"):
                    parallel = capture(scenario, backend, draws, horizon, processes, timeout, work)
                    if msg := difference(baseline["result"], parallel["result"]):
                        raise AssertionError(f"walk: {backend} parallel != reference serial: {msg}")
                    entry[f"{backend}_parallel_seconds"] = parallel["metadata"]["seconds"]
            report["verified"].append(entry)
            print(json.dumps(entry, sort_keys=True), flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", choices=[*SNAPSHOTS, "all"], default="all")
    p.add_argument("--draws", type=int, default=4)
    p.add_argument("--horizon", type=int, default=45)
    p.add_argument("--processes", type=int, default=2)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--output", type=Path)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--backend", choices=("python", "rust"), default="rust", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.draws < 1 or args.horizon < 1 or args.processes < 1:
        p.error("draws, horizon and processes must be positive")
    if args.worker:
        if args.output is None or args.scenario == "all":
            p.error("worker requires output and one scenario")
        worker(args)
        return
    scenarios = list(SNAPSHOTS) if args.scenario == "all" else [args.scenario]
    report = verify(scenarios, args.draws, args.horizon, args.processes, args.timeout)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

"""Unbilled saved-root comparison: uv run python tests/benchmark_chronological.py ROOT.

ROOT: saved, none279, offer7, other0 (and the other saved draw/index suffixes).
Synthetic distributions are arithmetic checks, never model judgments.
"""
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import akoustis_20240514_fixture as fx
import numpy as np
from test_dated_answer_domains import SAVED
from test_ripe_after_levy import NONE, OFFER

from app.analysis.events import Draws, event_trace, group_branches, plain
from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import _S, Dist, Forecaster, _Walk, path_mask, path_probability


def root(name):
    if name == "saved":
        return SAVED[:8], 145, "award6752641200"
    if name.startswith("none"):
        return NONE[:8], int(name[4:]), "award1065000100"
    if name.startswith("offer"):
        return OFFER[:9], int(name[5:]), "award1065000100"
    # Exact prefixes/rows from var/diag/rewalk_other.py; retain them without
    # requiring another checkout to have that gitignored diagnostic directory.
    index = int(name.removeprefix("other"))
    if index in (0, 1, 2):
        return root("none279")
    if index == 3:
        return root("offer7")
    if index in (4, 5):
        return OFFER[:8], 7 if index == 4 else 63, "award1065000100"
    if index in (6, 7):
        return root("saved")
    raise ValueError(f"unknown saved root {name}")


def distribution(branches, mode, key=""):
    if mode == "keyed":
        weights = [1 + int.from_bytes(hashlib.sha256(f"{key}/{b}".encode()).digest()[:4], "big") % 97
                   for b in branches]
        return dict(zip(branches, (w / sum(weights) for w in weights), strict=True))
    weights = ([1] * len(branches) if mode == "uniform" else list(range(1, len(branches) + 1))
               if mode == "tilted" else [int(i == (0 if mode == "first" else len(branches) - 1))
                                         for i in range(len(branches))])
    return dict(zip(branches, (w / sum(weights) for w in weights), strict=True))


def check(walk, row, prefix):
    fc = walk.fc
    bad, cash, probability_errors = [], {}, []
    masses = dict.fromkeys(("uniform", "tilted", "first", "last", "keyed"), 0.0)
    base = {mode: {k: distribution(n.branches, mode, k) for k, n in fc.nodes.items()} for mode in masses}
    draws = Draws(fc.draws.n, basis=fc.draws.basis)
    draws.prefixes = {}  # independent of the walk's speculative question/probe caches
    for p in walk.out:
        mask = path_mask(p, fc.draws.n)
        assert mask[row] and mask.sum() == 1
        branches = {}
        for key, tags, codes in p.classes:
            code = 0 if codes is None else int(np.frombuffer(codes, dtype=np.int8)[0])
            if code >= 0:
                branches[key] = fc.class_key(key, tags[code])
        for mode in masses:
            if masses[mode] is None:
                continue
            dist = Dist(base[mode])
            dist.update({k: base[mode][target] for k, target in branches.items()})
            try:
                masses[mode] += path_probability(p.edges, dist)
            except KeyError as error:
                probability_errors.append(dict(mode=mode, missing=str(error), steps=p.steps))
                masses[mode] = None  # infeasible stored edge: not a probability of zero
        tr = event_trace(walk.d, p, fc.setup, fc.m, draws, fc.sens, rows=tuple(mask for _ in p.steps))
        for i, (node, ctx, answer) in enumerate(p.steps):
            if i < prefix or node not in ("cash_floor", "cash_out", "judgment_response"):
                continue
            q = tr.questions[i]
            day, group = int(q["day"][row]), int(q["groups"][row])
            if day < fc.days and group >= 0 and plain(answer) not in group_branches(node, group):
                bad.append(dict(step=i, node=node, ctx=ctx, day=day, group=group, answer=answer,
                                steps=p.steps))
        # An omitted post-ruling response in the old walk has the cash of the
        # new response's explicit 'none', not of its payment/offering/filing.
        signature = tuple(sorted((n, c, plain(b)) for i, (n, c, b) in enumerate(p.steps)
                                 if 0 <= tr.day[i][row] < fc.days and not b.startswith("@-1=")
                                 and (n, c, plain(b)) != ("judgment_response", "post", "none")))
        h = hashlib.sha256()
        arrays = {}
        fields = {field: getattr(tr.events, field) for field in ("cash", "lock", "capacity", "petition")}
        for group in ("kinds", "incurred", "proceeds"):
            values = getattr(tr.events, group)
            if values is None:
                raise ValueError(f"missing {group} in the equity-model cash comparison")
            fields.update({f"{group}:{k}": v for k, v in sorted(values.items())})
        for field, a in fields.items():
            arrays[field] = a[row if len(a) == fc.draws.n else 0].copy()
            h.update(field.encode())
            h.update(arrays[field].tobytes())
        dates = {(n, c): int(tr.day[i][row]) for i, (n, c, _) in enumerate(p.steps)}
        cash.setdefault(signature, {})[h.hexdigest()] = (arrays, dates, p.steps)
    return dict(paths=len(walk.out), infeasible=len(bad), examples=bad[:2], masses=masses,
                probability_errors=probability_errors), cash


def compare(name, recheck=False, reuse_current=False):
    steps, row, cls = root(name)
    outputs, flows = {}, {}
    folder = Path("var/diag/001-1f")
    folder.mkdir(parents=True, exist_ok=True)
    for label, factory in (("current", _Walk), ("chronological", ChronologicalWalk)):
        t = time.perf_counter()
        d = fx.pending(instance_id="dispute_002")
        fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                        hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
        w = factory(fc, d)
        setup = time.perf_counter() - t
        state = _S(steps=steps, cls=cls, a4="seek", stayed=True, early=True)
        support = np.arange(fc.draws.n) == row
        artifact = folder / f"{name}-{label}.pkl"
        if recheck or (reuse_current and label == "current"):  # this benchmark's artifacts, never model inputs
            with artifact.open("rb") as source:
                fc.nodes, w.out, setup, elapsed = pickle.load(source)
        else:
            t = time.perf_counter()
            if label == "current":
                w._population = support
                w.a4_i1(state)
            else:
                w.run_from(state, support)
            elapsed = time.perf_counter() - t
            with artifact.open("wb") as target:
                pickle.dump((fc.nodes, w.out, setup, elapsed), target, protocol=pickle.HIGHEST_PROTOCOL)
        print(json.dumps(dict(root=name, walker=label, stage="walked", paths=len(w.out), walk_s=elapsed)), flush=True)
        result, flows[label] = check(w, row, len(steps))
        outputs[label] = dict(result, setup_s=setup, walk_s=elapsed)
        print(json.dumps(dict(root=name, walker=label, **outputs[label])), flush=True)
    a, b = flows.values()
    common = a.keys() & b.keys()
    differences = []
    for k in sorted(common):
        if a[k].keys() == b[k].keys():
            continue
        old = a[k][next(iter(a[k].keys() - b[k].keys()), next(iter(a[k])))]
        new = b[k][next(iter(b[k].keys() - a[k].keys()), next(iter(b[k])))]
        changes = {}
        for field in old[0]:
            oa, na = old[0][field], new[0][field]
            changed = np.flatnonzero(oa != na)
            if len(changed):
                if np.ndim(oa):
                    day = int(changed[0])
                    changes[field] = dict(first_day=day, current=int(oa[day]), chronological=int(na[day]))
                else:
                    changes[field] = dict(current=int(oa), chronological=int(na))
        dates = [dict(node=n, ctx=c, current=old[1].get((n, c)), chronological=new[1].get((n, c)))
                 for n, c in sorted(old[1].keys() | new[1].keys()) if old[1].get((n, c)) != new[1].get((n, c))]
        differences.append(dict(cash_changes=changes, changed_decision_dates=dates,
                                current_steps=old[2], chronological_steps=new[2]))
    summary = dict(root=name, matching_histories=len(common),
                   equal_cash=len(common) - len(differences), different_cash=len(differences),
                   current_only=len(a.keys() - b.keys()), chronological_only=len(b.keys() - a.keys()))
    report = Path("var/diag/001-1f") / f"{name}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(dict(results=outputs, comparison=summary, differences=differences), indent=2) + "\n")
    print(json.dumps(dict(summary, report=str(report))), flush=True)
    return outputs, summary


if __name__ == "__main__":
    compare(sys.argv[1], recheck="--recheck" in sys.argv[2:], reuse_current="--reuse-current" in sys.argv[2:])

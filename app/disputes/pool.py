"""The walk's parts carried to Jev at the tree's size, on runners (app/disputes/parallel.py `shard` writes the parts).

- split: one walk job's parts; the recorded rows leave the event stream, filed in BUCKETS by question.
- control: every part's remaining events (paths, nodes, watches); the watched regions decided as the single walk
  decides them (`parallel._live`), the questions and the walk's dictionaries merged, the paths with their watch
  edges written in blocks for the reduction pass.
- facts: one bucket's rows in the single walk's order (a late record once per `Forecaster.late_key`), and each of the
  bucket's questions' Jev state built from them (`Forecaster.state`), rows read field by field (`forecast.Rows.LAZY`).

`python -m app.disputes.pool split|control|facts ...`.
"""
from __future__ import annotations

import glob
import gzip
import hashlib
import json
import os
import pickle
import sys
import time
from dataclasses import replace
from pathlib import Path

BUCKETS = 16


def bucket(key: str) -> int:
    return int(hashlib.blake2b(key.encode(), digest_size=4).hexdigest(), 16) % BUCKETS


def _parts(folder: str) -> list[str]:
    return sorted(glob.glob(os.path.join(folder, "**", "part*.pkl"), recursive=True))


def split(parts: str, out: str) -> None:
    """Each part as its control events (out/control/part<k>.pkl) and its rows by bucket (out/rows<b>/part<k>.pkl:
    [(event key, kind, question, late key, row, regions)])."""
    t0 = time.time()
    os.makedirs(os.path.join(out, "control"), exist_ok=True)
    for f in _parts(parts):
        with open(f, "rb") as fh:
            p = pickle.load(fh)
        ctrl, rows = [], [[] for _ in range(BUCKETS)]
        for ev in p["events"]:
            ekey, kind, x, cond = ev
            if kind == "rec":
                if x[1] is not None:
                    for k in x[0]:
                        rows[bucket(k)].append((ekey, "rec", k, None, x[1], cond))
            elif kind == "late":
                rows[bucket(x[0])].append((ekey, "late", x[0], x[1], x[2], cond))
            else:
                ctrl.append(ev)
        name = os.path.basename(f)
        with open(os.path.join(out, "control", name), "wb") as fh:
            pickle.dump({**p, "events": ctrl}, fh, protocol=pickle.HIGHEST_PROTOCOL)
        for b, r in enumerate(rows):
            os.makedirs(os.path.join(out, f"rows{b}"), exist_ok=True)
            with open(os.path.join(out, f"rows{b}", name), "wb") as fh:
                pickle.dump(r, fh, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"{time.time() - t0:7.0f}s split {name}: {len(ctrl)} control events, "
              f"{sum(map(len, rows))} rows", file=sys.stderr, flush=True)


def _holds(reads: dict):
    """`parallel._live`'s rule: a region (a watched question asked) holds where the watch was read by a read whose
    own regions all hold."""
    held: dict = {}

    def holds(r) -> bool:
        if r not in held:
            held[r] = False
            held[r] = any(all(holds(c) for c in cond) for cond in reads.get(r, ()))
        return held[r]
    return holds


def control(folder: str, out: str) -> None:
    """The merged walk as `parallel.walk` leaves it but its rows (they stay in the buckets), streamed part by part so
    no process holds the tree: out/control.pkl (the questions, the walk's dictionaries, the watch reads) and
    out/paths/part<k>.pkl, each part's paths the single walk keeps, their watch edges applied.
    Pass 1 decides the regions (`parallel._live`'s rule), the questions (the first live creation of each, in the
    single walk's order) and each watch's edges; pass 2 writes the paths. Every path under a watch precedes its edge in
    the single walk's order (the edge is logged when the watch's no-event branch is done), so a path takes each of
    its watches' edges, in their order."""
    from app.analysis.setup import DRAWS
    from app.disputes import parallel
    from app.disputes.forecast import atoms, class_entry, path_mask, qcls_best

    t0 = time.time()
    files = _parts(folder)
    reads, node_ev, edge_ev, raised, heads = {}, [], [], [], []
    for f in files:
        with open(f, "rb") as fh:
            p = pickle.load(fh)
        for ekey, kind, x, cond in p["events"]:
            if kind == "read":
                reads.setdefault(x[0], []).append(cond)
            elif kind == "node":
                node_ev.append((ekey, x[0], x[1], cond))
            elif kind == "edge":
                edge_ev.append((ekey, x, cond))
            elif kind == "raise":
                raised.append((x[0], cond))
        heads.append({k: v for k, v in p.items() if k != "events"})
        del p
    assert len({h["clock"] for h in heads}) == 1 and len({h["nseg"] for h in heads}) == 1, "the tops differ"
    holds = _holds(reads)
    live = lambda cond: all(holds(c) for c in cond)  # noqa: E731
    nodes = {}
    for _ekey, k, n, cond in sorted(node_ev, key=lambda e: e[0]):
        if live(cond):
            nodes.setdefault(k, n)
    edges: dict = {}
    for _ekey, x, cond in sorted(edge_ev, key=lambda e: e[0]):
        if live(cond):
            wid, at, edge, qcls = x if len(x) > 3 else (*x, {})
            edges.setdefault(wid, []).append((at, edge, qcls))
    raised_live = {x for x, cond in raised if live(cond)}

    def asked(field):
        return {x: v for x, v in parallel._union(heads, field).items() if x in nodes}
    ctl = {"nodes": nodes, "reads": reads, "raised": raised_live,
           "node_group": asked("node_group"), "remitted": asked("remitted"),
           "class_members": parallel._union(heads, "class_members"),
           "class_range": parallel._union(heads, "class_range"),
           "remit_classes": set().union(*(h["remit_classes"] for h in heads)),
           "verdict_asks": asked("verdict_asks"), "classed": set().union(*(h.get("classed", ()) for h in heads))}
    os.makedirs(os.path.join(out, "paths"), exist_ok=True)
    total, missing = 0, set()
    for f in files:
        with open(f, "rb") as fh:
            p = pickle.load(fh)
        kept = []
        for _ekey, kind, x, cond in sorted(p["events"], key=lambda e: e[0]):  # noqa: B007
            if kind != "path" or not live(cond):
                continue
            w = x[0]
            for wid in x[2]:
                for at, edge, qcls in edges.get(wid, ()):
                    have = {c[0] for c in w.classes}
                    add = tuple(class_entry(k, cls, path_mask(w, DRAWS)) for k, entries in qcls.items()
                                if k not in have and (cls := qcls_best(entries, w.steps)) is not None)
                    w = replace(w, edges=w.edges[:at] + (edge,) + w.edges[at:], classes=w.classes + add)
            kept.append(w)
            for key, _ in w.edges:  # every question a path reads is a merged question
                missing |= atoms(key) - nodes.keys()
        total += len(kept)
        with open(os.path.join(out, "paths", os.path.basename(f)), "wb") as fh:
            pickle.dump(kept, fh, protocol=pickle.HIGHEST_PROTOCOL)
        del p
    ctl["paths"] = total
    if missing and os.environ.get("SLOPE_POOL_ALLOW_MISSING") != "1":
        raise SystemExit(f"control: {len(missing)} questions the paths read were never logged, e.g. {sorted(missing)[:3]}")
    with open(os.path.join(out, "control.pkl"), "wb") as fh:
        pickle.dump(ctl, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"{time.time() - t0:7.0f}s control: {len(files)} parts, {total} paths, {len(nodes)} questions "
          f"({len(ctl['classed'])} asked per class), raise re-offered at {len(raised_live)} prefixes",
          file=sys.stderr, flush=True)


def forecaster(run_id: str, ctl: dict):
    """The run's Forecaster as the walk built it (parallel.shard), with the merged questions and dictionaries."""
    from app.analysis.build import basis_for, run_context
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    ctx = run_context(run_id, Path("runs/recorded"))
    setup, sens = _variant(ctx)
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup, basis=basis_for(ctx["feed"], setup),
                    slots=ctx["slots"], model=ctx["m"], sens=sens)
    fc.nodes = dict(ctl["nodes"])
    fc.node_group.update(ctl["node_group"])
    fc.remitted.update(ctl["remitted"])
    fc.class_members.update(ctl["class_members"])
    fc.class_range.update(ctl["class_range"])
    fc.remit_classes |= ctl["remit_classes"]
    fc.verdict_asks = dict(ctl["verdict_asks"])
    fc.classed |= ctl["classed"]
    return fc


def bucket_facts(folder: str, ctl: dict) -> dict:
    """A bucket's rows per question in the single walk's order, as `parallel.walk` keeps them."""
    from app.disputes.forecast import Rows

    rows = []
    for f in _parts(folder):
        with open(f, "rb") as fh:
            rows += pickle.load(fh)
    rows.sort(key=lambda r: r[0])
    holds, out, seen = _holds(ctl["reads"]), {}, set()
    for _ekey, kind, k, lk, b, cond in rows:
        if not all(holds(c) for c in cond):
            continue
        if kind == "late":
            if lk in seen:
                continue
            seen.add(lk)
        out.setdefault(k, Rows()).append_blob(b)
    return out


def facts(run_id: str, folder: str, control_file: str, b: int, out: str, check: bool = False) -> None:
    """Bucket b's questions' Jev states (the state, its findings, its readings), from its rows (`folder`: every
    split's rows<b>), written to out/states<b>.json.gz. check: each state also built from rows widened at once
    (`forecast.unpack_row`) and compared."""
    from app.disputes.forecast import CLASS_TAG, Rows

    t0 = time.time()
    with open(control_file, "rb") as fh:
        ctl = pickle.load(fh)
    fc = forecaster(run_id, ctl)
    fc.facts = bucket_facts(folder, ctl)
    states, errors, differ, never = {}, {}, [], []
    mine = sorted(k for k in fc.nodes if bucket(k) == b and k not in fc.classed)
    for k in mine:
        if CLASS_TAG in k and not any(fc.live(fc.nodes[k], r).any() for r in fc.facts.get(k, ())):
            never.append(k)  # a class live on no path: its answer moves no figure (`forecast.expand_classes`)
            continue
        try:
            Rows.LAZY = True
            st, fids, readings = fc.state(fc.nodes[k])
            text = json.dumps(st, sort_keys=True, default=str)
            if check:
                Rows.LAZY = False
                if json.dumps(fc.state(fc.nodes[k])[0], sort_keys=True, default=str) != text:
                    differ.append(k)
            states[k] = {"state": json.loads(text), "fids": list(fids), "readings": readings}
        except Exception as e:  # noqa: BLE001 (a question whose state does not build is reported, not skipped)
            errors[k] = f"{type(e).__name__}: {e}"
        finally:
            Rows.LAZY = False
    os.makedirs(out, exist_ok=True)
    with gzip.open(os.path.join(out, f"states{b}.json.gz"), "wt") as fh:
        json.dump({"states": states, "errors": errors, "differ": differ, "never_live": never}, fh, default=str)
    print(f"{time.time() - t0:7.0f}s facts bucket {b}: {len(mine)} questions, {len(states)} states, "
          f"{len(errors)} errors, {len(never)} classes never live"
          + (f", {len(differ)} differ from the eager build" if check else ""),
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "split":
        split(sys.argv[2], sys.argv[3])
    elif cmd == "control":
        control(sys.argv[2], sys.argv[3])
    elif cmd == "facts":
        facts(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6],
              check=os.environ.get("SLOPE_POOL_CHECK") == "1")
    else:
        raise SystemExit(f"unknown stage {cmd!r}")

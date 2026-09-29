"""The pending claim's dispute tree walked by several processes on a subtree queue (build setting walk_processes).

Every process walks the tree's shared top and claims units (O_EXCL files): a unit is the subtree below the first state
on a path at least CUT steps below the verdict, or where a watched question starts (q1, appeal: their no-branch walk
decides whether they are asked, so a watch never spans units), or an emitted leaf. Only the claimer walks a unit.
Each process logs what the walk adds to the Forecaster (a node, a recorded fact, a whole path's late fact, a path and
its equivalence key), keyed by its place in the single walk's order: a top event by the number of logged calls made at
the top so far (the same in every process: the top is walked identically), an event of a unit's walk (a segment) by
the top count when the segment started and the segment's number (every process numbers every segment, walked or
not). Replaying process 0's top events and each owner's segments in key order gives the single walk's order: nodes in
creation order (every process's copy asserted equal), facts per node in the single walk's order (late facts once per
`Forecaster.late_key`, as `_keep_late` keeps them), and the paths and keys `merge_equivalent` merges.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import os
import pickle
import resource
import sys
import tempfile
import time

CUT = int(os.environ.get("SLOPE_WALK_CUT", "4"))  # a unit starts this many steps below the verdict
STARTS = ("q1", "appeal", "emit", "_end")  # walks that always start a unit


def _child(fc, d, k: int, run: str, log) -> None:
    """Process k's walk (after fork): logs events and writes them to run/part<k>.pkl."""
    import app.disputes.forecast as F

    t0 = time.time()
    st = {"seg": None, "clock": 0, "nseg": 0, "vdepth": 0, "roots": {}, "seq": 0, "mine": set(), "j": 0, "n": 0}
    events: list = []

    shards = int(os.environ.get("SLOPE_WALK_SHARDS", "0"))  # shards on separate machines: unit seq mod shards

    def claim(root, seq: int) -> bool:
        if shards:
            return seq % shards == k
        name = "c_" + hashlib.blake2b(repr(root).encode(), digest_size=12).hexdigest()
        try:
            os.close(os.open(os.path.join(run, name), os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            return False
        return True

    def resolve(name, W, s):
        """The unit root the state belongs to: (seq, mine), or None where it is walked at the top."""
        steps = s.steps
        for L in range(len(steps) + 1):
            r = st["roots"].get(steps[:L])
            if r is not None:
                return r
        if W._watch:
            return None
        if len(steps) - st["vdepth"] >= CUT or name in STARTS:
            r = st["roots"][steps] = (st["seq"], claim(steps, st["seq"]))
            st["seq"] += 1
            return r
        return None

    def log_event(kind, *payload) -> None:
        if st["seg"] is None:
            if k == 0:
                events.append(((st["clock"], 1, 0, 0), kind, payload))
            st["clock"] += 1
        else:
            events.append(((st["seg"][1], 0, st["seg"][0], st["j"]), kind, payload))
            st["j"] += 1

    def wrap(name, f):
        @functools.wraps(f)
        def w(self, s, *a, **kw):
            if type(self) is not F._Walk or not isinstance(s, F._S) or st["seg"] is not None:
                return f(self, s, *a, **kw)
            r = resolve(name, self, s)
            if r is None:
                return f(self, s, *a, **kw)
            seg = (st["nseg"], st["clock"])
            st["nseg"] += 1
            if not r[1]:
                return None  # another process's unit
            st["seg"], st["j"] = seg, 0
            try:
                return f(self, s, *a, **kw)
            finally:
                st["seg"] = None
        return w

    for name, f in list(vars(F._Walk).items()):
        if callable(f) and not name.startswith("__"):
            ps = list(inspect.signature(f).parameters)
            if len(ps) > 1 and ps[1] == "s":
                setattr(F._Walk, name, wrap(name, f))
    verd = F._Walk.verdict

    def verdict(self, s):  # the depth rule counts from the verdict step
        st["vdepth"] = len(s.steps) + 1
        return verd(self, s)
    F._Walk.verdict = verdict
    emit = F._Walk.emit

    def emit_logged(self, s, outcome):
        n0 = len(self.out)
        out = emit(self, s, outcome)
        if len(self.out) > n0:
            log_event("path", n0)
            st["n"] += 1
            if st["n"] % 1000 == 0:
                print(f"{time.time() - t0:7.0f}s part {k}: paths {st['n']} nodes {len(fc.nodes)} "
                      f"rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30:.2f} GB", file=log, flush=True)
        return out
    F._Walk.emit = emit_logged
    node, record, keep_late = F.Forecaster.node, F.Forecaster.record, F.Forecaster._keep_late

    def node_logged(self, d_, name, *ctx, **kw):
        n0 = len(self.nodes)
        key = node(self, d_, name, *ctx, **kw)
        log_event("node", key, self.nodes[key] if len(self.nodes) > n0 else None)
        return key

    def record_logged(self, keys, tr):
        n0 = {x: len(self.facts.get(x, ())) for x in keys}
        record(self, keys, tr)
        log_event("rec", tuple(keys), self.facts[keys[0]].blob(n0[keys[0]]) if keys else None)

    def keep_logged(self, key, prefix, row):
        n0 = len(self.facts.get(key, ()))
        keep_late(self, key, prefix, row)
        new = len(self.facts.get(key, ())) > n0
        log_event("late", key, self.late_key(key, prefix, row) if new else None,
                  self.facts[key].blob(n0) if new else None)
    F.Forecaster.node, F.Forecaster.record, F.Forecaster._keep_late = node_logged, record_logged, keep_logged

    W = F._Walk(fc, d)
    W.run()
    for i, (_, kind, payload) in enumerate(events):  # a path as it ends the walk (`_edge_after` edits it in place)
        if kind == "path":
            events[i] = (events[i][0], "path", (W.out[payload[0]], W.keys[payload[0]]))
    out = {"k": k, "events": [e for e in events if not (e[1] == "node" and e[2][1] is None)
                              and not (e[1] == "late" and e[2][1] is None)],
           "clock": st["clock"], "nseg": st["nseg"], "raise_more": fc._raise_more, "node_group": fc.node_group,
           "remitted": fc.remitted, "class_members": fc.class_members, "class_range": fc.class_range,
           "remit_classes": fc.remit_classes, "verdict_asks": getattr(fc, "verdict_asks", {}),
           "seconds": time.time() - t0, "rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30,
           "walked": st["n"]}
    with open(os.path.join(run, f"part{k}.pkl"), "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"{time.time() - t0:7.0f}s part {k}: done, {st['n']} paths, {st['nseg']} segments", file=log, flush=True)


def _fork(fc, d, procs: int, run: str, log, ks=None) -> list[dict] | None:
    """Walk in `procs` forked processes (ks: their part numbers, default 0..procs-1); their results in process order
    (ks given: written to run/part<k>.pkl only)."""
    pids = []
    sys.stdout.flush()
    sys.stderr.flush()
    for k in (range(procs) if ks is None else ks):
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                _child(fc, d, k, run, log)
                code = 0
            except BaseException as e:  # noqa: BLE001 (reported to the parent through the exit code and the log)
                import traceback
                print(f"part {k} failed: {e!r}\n{traceback.format_exc()}", file=log, flush=True)
            finally:
                os._exit(code)
        pids.append(pid)
    bad = [k for k, pid in enumerate(pids) if os.waitpid(pid, 0)[1] != 0]
    if bad:
        raise RuntimeError(f"parallel walk: process(es) {bad} failed (log: {getattr(log, 'name', log)})")
    if ks is not None:
        return None
    parts = []
    for k in range(procs):
        with open(os.path.join(run, f"part{k}.pkl"), "rb") as fh:
            parts.append(pickle.load(fh))
    return parts


def _union(parts: list[dict], field: str) -> dict:
    out: dict = {}
    for p in parts:
        for key, v in p[field].items():
            if key in out:
                assert _same(out[key], v), (field, key)
            else:
                out[key] = v
    return out


def _same(a, b) -> bool:
    import numpy as np

    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return np.array_equal(a, b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[x], b[x]) for x in a)
    return a == b


def walk(fc, d, procs: int, log=sys.stderr):
    """The dispute's paths (merged, as `Forecaster.paths` returns them), walked in `procs` processes; the
    Forecaster's nodes, facts and walk dictionaries set as the single walk leaves them."""
    import shutil

    from app.disputes.forecast import _ROW_BLOBS, Rows, merge_equivalent

    t0 = time.time()
    saved = os.environ.get("SLOPE_WALK_PARTS")  # the parts the shards walked (GitHub Actions: app/disputes/parallel.py)
    while True:  # as Forecaster.paths: walk again where a whole path shows equity the floor's prefix did not
        if saved:
            parts = load_parts(saved)
        else:
            run = tempfile.mkdtemp(prefix="walk_")
            try:
                parts = _fork(fc, d, procs, run, log)
            finally:
                shutil.rmtree(run, ignore_errors=True)
        more = set().union(*(p["raise_more"] for p in parts)) - fc._raise_open
        if not more:
            break
        if saved:
            raise RuntimeError(f"the raise is re-offered at {len(more)} floor prefixes: walk the shards again with them")
        fc._raise_open |= more
        print(f"{time.time() - t0:7.0f}s parallel walk: the raise re-offered at {len(more)} floor prefixes; "
              f"walking again", file=log, flush=True)
    assert len({p["clock"] for p in parts}) == 1 and len({p["nseg"] for p in parts}) == 1, "the tops differ"
    stream = sorted((e for p in parts for e in p["events"]), key=lambda e: e[0])
    assert len({e[0] for e in stream}) == len(stream), "a segment walked twice"
    nodes, facts, seen, pre, keys = dict(fc.nodes), {x: v.copy() for x, v in fc.facts.items()}, set(fc._late_seen), [], []
    for _, kind, x in stream:
        if kind == "node":
            if x[0] in nodes:
                assert nodes[x[0]] == x[1], x[0]
            else:
                nodes[x[0]] = x[1]
        elif kind == "rec":
            for key in x[0]:
                facts.setdefault(key, Rows()).append_blob(_ROW_BLOBS.setdefault(x[1], x[1]))
        elif kind == "late":
            if x[1] not in seen:
                seen.add(x[1])
                facts.setdefault(x[0], Rows()).append_blob(_ROW_BLOBS.setdefault(x[2], x[2]))
        else:
            pre.append(x[0])
            keys.append(x[1])
    fc.nodes, fc.facts, fc._late_seen = nodes, facts, seen
    fc.node_group.update(_union(parts, "node_group"))
    fc.remitted.update(_union(parts, "remitted"))
    fc.class_members.update(_union(parts, "class_members"))
    fc.class_range.update(_union(parts, "class_range"))
    fc.verdict_asks = {**getattr(fc, "verdict_asks", {}), **_union(parts, "verdict_asks")}
    fc.remit_classes |= set().union(*(p["remit_classes"] for p in parts))
    out = merge_equivalent(pre, keys, {x: n.branches for x, n in fc.nodes.items()})
    fc.walk_stats = {"walked": len(pre), "paths": len(out), "nodes": len(fc.nodes), "seconds": time.time() - t0,
                     "processes": [(p["k"], p["walked"], round(p["seconds"]), round(p["rss"], 2)) for p in parts]}
    print(f"{time.time() - t0:7.0f}s parallel walk: {len(pre)} paths walked, {len(out)} after the merge, "
          f"{len(fc.nodes)} nodes; processes (k, paths, s, peak GB) {fc.walk_stats['processes']}", file=log, flush=True)
    return out


def all_paths(fc, procs: int, log=sys.stderr) -> dict:
    """`Forecaster.all_paths`, a pending claim's tree walked in `procs` processes."""
    from app.disputes.forecast import PENDING

    return {d.instance_id: {"": walk(fc, d, procs, log) if d.stage == PENDING and d.borrower_role == "debtor"
                            else fc.paths(d)} for d, _ in fc.ordered()}


def load_parts(folder: str) -> list[dict]:
    """The shards' parts (part<k>.pkl under `folder`, any depth), in part order; every part present."""
    from pathlib import Path

    files = {int(p.stem[4:]): p for p in Path(folder).rglob("part*.pkl")}
    assert files and sorted(files) == list(range(len(files))), f"parts missing: have {sorted(files)}"
    parts = []
    for k in sorted(files):
        with open(files[k], "rb") as fh:
            parts.append(pickle.load(fh))
    return parts


def shard(run_id: str, job: int, jobs: int, procs: int, out: str) -> None:
    """One machine's share of the pending claim's walk: parts job*procs .. job*procs+procs-1 of jobs*procs, the
    Forecaster built as `build.judged_model` builds it."""
    from pathlib import Path

    from app.analysis.build import basis_for, run_context
    from app.disputes.forecast import PENDING, Forecaster

    ctx = run_context(run_id, Path("runs/recorded"))
    setup = ctx["setup"]
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup, basis=basis_for(ctx["feed"], setup),
                    slots=ctx["slots"], model=ctx["m"], sens=None)
    d = next(x for x, _ in fc.ordered() if x.stage == PENDING and x.borrower_role == "debtor")
    os.environ["SLOPE_WALK_SHARDS"] = str(jobs * procs)
    os.makedirs(out, exist_ok=True)
    _fork(fc, d, procs, out, sys.stderr, ks=range(job * procs, job * procs + procs))


if __name__ == "__main__":
    shard(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])

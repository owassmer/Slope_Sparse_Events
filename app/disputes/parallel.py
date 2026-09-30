"""The pending claim's dispute tree walked by several processes on a subtree queue (build setting walk_processes).

Every process walks the tree's shared top and claims units (O_EXCL files; on GitHub runners, among the units of its
machine's share): a unit is the subtree below the first state on a path at least CUT steps below the verdict, or an
emitted leaf. Only the claimer walks a unit. Watched questions: see the note below `STARTS`.
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
import zlib
from dataclasses import replace

RSS_GB = 2**30 if sys.platform == "darwin" else 2**20  # ru_maxrss: bytes on macOS, KB on Linux
CUT = int(os.environ.get("SLOPE_WALK_CUT", "4"))  # a unit starts this many steps below the verdict
STARTS = ("emit", "_end")  # walks that always start a unit

# Watched questions (forecast.py `_Watch`: q1, the appeal) that start inside a unit are walked as the single walk walks
# them. One that starts at the top is split across units: its no-event branch is walked as the single walk walks it;
# whether the question is asked depends on reads anywhere below it, so every
# process walks on as if it were asked (a speculative region: the question's node, facts, the event branch) and logs
# each event with the regions open when it happened, each read of a watch, and the watch's edge. `walk` then decides
# each region as the single walk would (a watch is read if a read under regions that all hold occurred), keeps the
# events whose regions all hold, and gives the no-event branch's paths the edge of each question asked.


def _child(fc, d, k: int, run: str, log) -> None:
    """Process k's walk (after fork): logs events and writes them to run/part<k>.pkl."""
    import app.disputes.forecast as F

    t0 = time.time()
    st = {"seg": None, "clock": 0, "nseg": 0, "vdepth": 0, "roots": {}, "seq": 0, "mine": set(), "j": 0, "n": 0,
          "regions": [], "wstack": [], "local": False}
    events: list = []
    # (key, regions) already logged: a later occurrence under the same open regions holds exactly where the logged
    # one does and comes after it, and one logged with no region open holds everywhere. The top's are kept apart
    # from this process's units', so every process logs the same top events (their count is the top's clock).
    once_top: set = set()
    once_unit: set = set()

    def logged(key, cond) -> bool:
        top = st["seg"] is None
        return any((key, c) in o for o in ((once_top,) if top else (once_top, once_unit)) for c in ((), cond))

    def fresh(key) -> bool:
        """Whether an event with this dedupe key is still to be logged here under the open regions; marks it."""
        cond = tuple(st["regions"])
        if logged(key, cond):
            return False
        (once_top if st["seg"] is None else once_unit).add((key, cond))
        return True

    job = os.environ.get("SLOPE_WALK_JOB")  # '<job>/<jobs>': a machine's units are seq mod jobs, claimed as below

    def claim(root, seq: int) -> bool:
        if job:
            j, n = map(int, job.split("/"))
            if seq % n != j:
                return False
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
        if len(steps) - st["vdepth"] >= CUT or name in STARTS:
            r = st["roots"][steps] = (st["seq"], claim(steps, st["seq"]))
            st["seq"] += 1
            return r
        return None

    def log_event(kind, *payload) -> None:
        cond = tuple(st["regions"])
        if st["seg"] is None:
            if k == 0:
                events.append(((st["clock"], 1, 0, 0), kind, payload, cond))
            st["clock"] += 1
        else:
            events.append(((st["seg"][1], 0, st["seg"][0], st["j"]), kind, payload, cond))
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
            log_event("path", n0, tuple(st["wstack"]))
            st["n"] += 1
            if st["n"] % 1000 == 0:
                print(f"{time.time() - t0:7.0f}s part {k}: paths {st['n']} nodes {len(fc.nodes)} "
                      f"rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_GB:.2f} GB", file=log, flush=True)
        return out
    F._Walk.emit = emit_logged

    # --- watched questions: speculative regions, reads and edges ---------------------------------------------------
    single_watched, single_edge = F._Walk._watched, F._Walk._edge_after

    def watched(self, s, no, w, then):
        """`_Walk._watched` with the no-event branch splittable: returns True (walk on as if asked, in a region
        named by the watch) and logs the watch's reads (`logged_reads`). Inside a unit the whole no-event branch is
        this process's, so the watch is the single walk's (no region, the edge applied here)."""
        st["local"] = st["seg"] is not None
        if st["local"]:
            return single_watched(self, s, no, w, then)
        wid = (no, s.steps)
        w.wid = wid
        self._watch.append(w)
        st["wstack"].append(wid)
        try:
            then(s.add(no, None))
        finally:
            self._watch.pop()
            st["wstack"].pop()
        st["regions"].append(wid)  # closed when the watched question's walk returns (`closing`)
        return True

    def logged_reads(f):
        """A walk read that may read a watched event: log each split watch it reads, once per set of open regions
        (for the top and for this process's units apart). A split watch counts as read, so its checks are skipped
        as the single walk skips them, exactly where its read is already logged under the open regions."""
        @functools.wraps(f)
        def g(self, *a, **kw):
            cond = tuple(st["regions"])
            for w in self._watch:
                if hasattr(w, "wid"):
                    w.read = logged(("read", w.wid), cond)
            try:
                return f(self, *a, **kw)
            finally:
                for w in self._watch:
                    if hasattr(w, "wid"):
                        if w.read and fresh(("read", w.wid)):
                            log_event("read", w.wid)
                        w.read = False
        return g

    def closing(f):
        @functools.wraps(f)
        def g(self, s, *a, **kw):
            n = len(st["regions"])
            try:
                return f(self, s, *a, **kw)
            finally:
                del st["regions"][n:]
        return g

    def edge_after(self, i0, at, edge):  # a split watch's: applied in `walk` to the paths emitted under it
        if st["local"]:
            return single_edge(self, i0, at, edge)
        log_event("edge", st["regions"][-1], at, edge)

    F._Walk._watched, F._Walk._edge_after = watched, edge_after
    for name in ("node", "_reads", "situation"):
        setattr(F._Walk, name, logged_reads(getattr(F._Walk, name)))
    F._Walk.q1, F._Walk.appeal = closing(F._Walk.q1), closing(F._Walk.appeal)
    raise_more = set()

    class _More(set):  # a floor prefix where the raise is re-offered, logged with its regions
        def add(self, x):
            log_event("raise", x)
            raise_more.add(x)
    fc._raise_more = _More()

    record = F.Forecaster.record

    def node_logged(self, d_, name, *ctx, **kw):
        key = self.key(d_, name, *ctx)
        if fresh(("node", key)):  # logged: every key is in self.nodes (the process's first creation, kept)
            n = self.new_node(d_, name, *ctx, **kw)
            self.nodes.setdefault(key, n)
            log_event("node", key, n)
        return key

    def record_logged(self, keys, tr):
        n0 = {x: len(self.facts.get(x, ())) for x in keys}
        record(self, keys, tr)
        log_event("rec", tuple(keys), self.facts[keys[0]].blob(n0[keys[0]]) if keys else None)

    def keep_logged(self, key, prefix, row):  # every process logs it; `walk` keeps the first of each late key
        lk = self.late_key(key, prefix, row)
        if fresh(("late", lk)):
            log_event("late", key, lk, F.pack_row(row))
    F.Forecaster.node, F.Forecaster.record, F.Forecaster._keep_late = node_logged, record_logged, keep_logged

    W = F._Walk(fc, d)
    W.run()
    for i, (key_, kind, payload, cond) in enumerate(events):  # a path as it ends the walk
        if kind == "path":
            events[i] = (key_, "path", (W.out[payload[0]], W.keys[payload[0]], payload[1]), cond)
    out = {"k": k, "events": events,
           "clock": st["clock"], "nseg": st["nseg"], "raise_more": raise_more, "node_group": fc.node_group,
           "remitted": fc.remitted, "class_members": fc.class_members, "class_range": fc.class_range,
           "remit_classes": fc.remit_classes, "verdict_asks": getattr(fc, "verdict_asks", {}),
           "seconds": time.time() - t0, "rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_GB,
           "walked": st["n"]}
    with open(os.path.join(run, f"part{k}.pkl"), "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"{time.time() - t0:7.0f}s part {k}: done, {st['n']} paths, {st['nseg']} segments", file=log, flush=True)


def _fork(fc, d, procs: int, run: str, log, ks=None) -> list[dict] | None:
    """Walk in `procs` forked processes (ks: their part numbers, default 0..procs-1); their results in process order
    (ks given: written to run/part<k>.pkl only)."""
    import gc

    gc.freeze()  # the shared heap built before the fork is never scanned again (and its pages stay shared)
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


def _live(parts: list[dict]) -> list:
    """The parts' events in the single walk's order, those of speculative regions the single walk does not walk
    dropped. A region (a watched question asked) holds where the watch was read by a read whose own regions all
    hold; an event holds where its regions all do."""
    stream = sorted((e for p in parts for e in p["events"]), key=lambda e: e[0])
    assert len({e[0] for e in stream}) == len(stream), "a segment walked twice"
    reads: dict = {}
    for _, kind, x, cond in stream:
        if kind == "read":
            reads.setdefault(x[0], []).append(cond)
    held: dict = {}

    def holds(r) -> bool:
        if r not in held:
            held[r] = False  # a region's reads precede it: no read's regions include it
            held[r] = any(all(holds(c) for c in cond) for cond in reads.get(r, ()))
        return held[r]

    return [e for e in stream if e[1] != "read" and all(holds(c) for c in e[3])]


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
        stream = _live(parts)
        more = {x[0] for _, kind, x, _c in stream if kind == "raise"} - fc._raise_open
        if not more:
            break
        if saved:
            raise RuntimeError(f"the raise is re-offered at {len(more)} floor prefixes: walk the shards again with them")
        fc._raise_open |= more
        print(f"{time.time() - t0:7.0f}s parallel walk: the raise re-offered at {len(more)} floor prefixes; "
              f"walking again", file=log, flush=True)
    assert len({p["clock"] for p in parts}) == 1 and len({p["nseg"] for p in parts}) == 1, "the tops differ"
    nodes, facts, seen, walked = dict(fc.nodes), {x: v.copy() for x, v in fc.facts.items()}, set(fc._late_seen), []
    under: dict = {}  # a split watch -> the paths walked under it (its no-event branch)
    for _, kind, x, _c in stream:
        if kind == "node":
            if x[0] not in nodes:  # the first live creation, as the single walk keeps it
                nodes[x[0]] = x[1]
        elif kind == "rec":
            for key in x[0]:
                facts.setdefault(key, Rows()).append_blob(_ROW_BLOBS.setdefault(x[1], x[1]))
        elif kind == "late":
            if x[1] not in seen:
                seen.add(x[1])
                facts.setdefault(x[0], Rows()).append_blob(_ROW_BLOBS.setdefault(x[2], x[2]))
        elif kind == "path":
            for wid in x[2]:
                under.setdefault(wid, []).append(len(walked))
            walked.append([x[0], x[1]])
        elif kind == "edge":  # the question asked: its edge on the no-event branch's paths (`_Walk._edge_after`)
            wid, at, edge = x
            for i in under.get(wid, ()):
                w = walked[i]
                w[0] = replace(w[0], edges=w[0].edges[:at] + (edge,) + w[0].edges[at:])
    pre, keys = [w[0] for w in walked], [w[1] for w in walked]
    fc.nodes, fc.facts, fc._late_seen = nodes, facts, seen

    def asked(field):  # entries of questions the walk asks (a region not taken asks none)
        return {x: v for x, v in _union(parts, field).items() if x in nodes}
    fc.node_group.update(asked("node_group"))
    fc.remitted.update(asked("remitted"))
    fc.class_members.update(_union(parts, "class_members"))
    fc.class_range.update(_union(parts, "class_range"))
    fc.verdict_asks = {**getattr(fc, "verdict_asks", {}), **asked("verdict_asks")}
    fc.remit_classes |= set().union(*(p["remit_classes"] for p in parts))
    out = merge_equivalent(pre, keys, {x: n.branches for x, n in fc.nodes.items()})
    fc.walk_stats = {"walked": len(pre), "paths": len(out), "nodes": len(fc.nodes), "seconds": time.time() - t0,
                     "processes": [(p["k"], p["walked"], round(p["seconds"]), round(p["rss"], 2)) for p in parts]}
    dropped = sum(p["walked"] for p in parts) - len(pre)
    print(f"{time.time() - t0:7.0f}s parallel walk: {len(pre)} paths kept ({dropped} walked under questions not "
          f"asked, dropped), {len(out)} after the merge, "
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


def _variant(ctx: dict) -> tuple:
    """The setup and the chains' sensitivities of the economic-assumption variant SLOPE_VARIANT (app/analysis/
    assumptions.py `declared`; unset or 'central': the run's own)."""
    from app.analysis.assumptions import declared
    from app.analysis.setup import setup_from_inputs

    vid = os.environ.get("SLOPE_VARIANT") or "central"
    if vid == "central":
        return ctx["setup"], None
    v = {x["id"]: x for x in declared(ctx["meta"]["snapshot_id"], ctx["inputs"], ctx["m"])}[vid]
    return setup_from_inputs(ctx["inputs"], ctx["review"], v["scenario"]), (v["sens"] or None)


def shard(run_id: str, job: int, jobs: int, procs: int, out: str) -> None:
    """One machine's share of the pending claim's walk: parts job*procs .. job*procs+procs-1 of jobs*procs, the
    Forecaster built as `build.judged_model` builds it."""
    from pathlib import Path

    from app.analysis.build import basis_for, run_context
    from app.disputes.forecast import PENDING, Forecaster

    ctx = run_context(run_id, Path("runs/recorded"))
    setup, sens = _variant(ctx)
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup, basis=basis_for(ctx["feed"], setup),
                    slots=ctx["slots"], model=ctx["m"], sens=sens)
    d = next(x for x, _ in fc.ordered() if x.stage == PENDING and x.borrower_role == "debtor")
    os.environ["SLOPE_WALK_JOB"] = f"{job}/{jobs}"  # this machine's units, claimed by its processes as they free up
    os.makedirs(out, exist_ok=True)
    _fork(fc, d, procs, out, sys.stderr, ks=range(job * procs, job * procs + procs))


def replay_block(d, feed, setup, m: dict, sens: dict, basis, paths: list) -> dict:
    """What `core.Analysis` reads of each path's event cash, for `paths`, each traced in their order on fresh draws as
    the analysis builds them (`core.event_store`):
    - central, on its own draws: the event cash on the draws the path follows (`path_mask`; elsewhere zero, where
      every central figure weighs it zero), packed, and the per-day range of its cumulative event cash less
      encumbrance over every draw (the histograms' span, `Analysis._event_range`);
    - stress, on the adverse placement's draws: its row (`core.stress_rows`)."""
    import numpy as np

    from app.analysis import operating
    from app.analysis.core import _pack, stress_rows
    from app.analysis.engine import prepare
    from app.analysis.events import BIG, Draws, EventCash, event_trace
    from app.analysis.setup import DRAWS
    from app.disputes.forecast import path_mask

    daily = setup.cash_processing == "daily"
    line = prepare(setup, operating.simulate_for(feed, setup))
    central, stress = {}, {}
    draws = Draws(DRAWS, basis=basis)
    draws.prefixes = {}
    for p in paths:
        ev = event_trace(d, p, setup, m, draws, sens).events
        cum = np.cumsum(ev.cash - ev.lock, axis=1)
        rng = (np.minimum(cum.min(axis=0), 0), np.maximum(cum.max(axis=0), 0))
        mk = path_mask(p, DRAWS)
        if mk is not None:  # new arrays: the chain's are shared read-only (copy-on-write)
            on2 = mk[:, None]

            def keep(a, off=0, on2=on2):
                return np.where(on2 if a.ndim == 2 else on2[:, 0], a, off).astype(a.dtype)
            ev = EventCash(keep(ev.cash), keep(ev.lock), keep(ev.capacity), keep(ev.petition, -1),
                           None if ev.kinds is None else {k: keep(a) for k, a in ev.kinds.items()},
                           None if ev.incurred is None else {k: keep(a, BIG) for k, a in ev.incurred.items()},
                           None if ev.proceeds is None else {k: keep(a) for k, a in ev.proceeds.items()})
        central[(p.instance_id, p.steps)] = (_pack(ev, daily), *rng)
    draws = Draws(DRAWS, stress=True, basis=basis)
    draws.prefixes = {}
    for p in paths:
        ev = event_trace(d, p, setup, m, draws, sens).events
        stress[(p.instance_id, p.steps)] = stress_rows(line, feed.available_cents, setup, [ev])[0]
    return {"central": central, "stress": stress}


def replay(run_id: str, parts: str, job: int, jobs: int, procs: int, out: str) -> None:
    """The analysis's event cash for this machine's share of the merged paths, on runners: the parts in `parts` merged
    as `slope analyze` merges them (`walk`), the paths split into jobs*procs contiguous blocks (depth-first order, so
    each process's traces resume from shared prefixes), each path traced as `core.Analysis.event_cash` traces it, on
    the analysis's draws and on the adverse placement's (`core.stress`). Written to out/ev<block>.pkl, which
    `core.event_store` reads (SLOPE_EVENT_CASH)."""
    from pathlib import Path

    from app.analysis.build import basis_for, run_context
    from app.disputes.forecast import PENDING, Forecaster

    t0 = time.time()
    ctx = run_context(run_id, Path("runs/recorded"))
    setup, sens = _variant(ctx)
    basis = basis_for(ctx["feed"], setup)
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup, basis=basis,
                    slots=ctx["slots"], model=ctx["m"], sens=sens)
    d = next(x for x, _ in fc.ordered() if x.stage == PENDING and x.borrower_role == "debtor")
    os.environ["SLOPE_WALK_PARTS"] = parts
    paths = walk(fc, d, 1)
    n, blocks = len(paths), jobs * procs
    print(f"{time.time() - t0:7.0f}s replay: {n} merged paths", file=sys.stderr, flush=True)
    os.makedirs(out, exist_ok=True)
    pids = []
    for b in range(job * procs, job * procs + procs):
        pid = os.fork()
        if pid:
            pids.append(pid)
            continue
        code = 1
        try:
            mine = paths[b * n // blocks:(b + 1) * n // blocks]
            res = replay_block(d, ctx["feed"], setup, fc.m, fc.sens, basis, mine)
            with open(os.path.join(out, f"ev{b}.pkl"), "wb") as fh:  # zlib: the arrays are mostly small integers
                fh.write(zlib.compress(pickle.dumps(res, protocol=pickle.HIGHEST_PROTOCOL), 1))
            print(f"{time.time() - t0:7.0f}s replay block {b}: {len(mine)} paths", file=sys.stderr, flush=True)
            code = 0
        except BaseException:  # noqa: BLE001 (reported to the parent through the exit code and the log)
            import traceback

            traceback.print_exc()
        finally:
            os._exit(code)
    failed = [p for p in pids if os.waitpid(p, 0)[1] != 0]
    if failed:
        raise SystemExit(f"replay: {len(failed)} processes failed")


if __name__ == "__main__":
    if sys.argv[1] == "replay":
        replay(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), sys.argv[7])
    else:
        shard(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])

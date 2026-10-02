"""The pending claim's dispute tree walked by several processes on a subtree queue (build setting walk_processes).

Every process walks the tree's shared top and claims units (O_EXCL files; on GitHub runners, among the units of its
machine's share): a unit is the subtree below the first continuation called on a state at least CUT steps below the
verdict, or an emitted leaf. Only the claimer walks a unit, whole. Every process numbers every unit in walk order
(walked or not). A process whose budget (SLOPE_WALK_MINUTES, from its start) is spent claims no more units; the
units no process walked are listed by `spill` and walked whole by a later wave (SLOPE_WALK_ROOTS: the list; the wave's
parts numbered past the first's). Each finished unit's events are appended to run/part<k>.stream as the unit ends, so
a process that dies keeps its finished units (`_restore`); a process that ends writes run/part<k>.pkl.
Each process logs what the walk adds to the Forecaster (a node, a recorded fact, a whole path's late fact, a path and
its equivalence key), keyed by its place in the single walk's order: a top event by the number of logged calls made at
the top so far (the same in every process: the top is walked identically; every process logs the top, and a consumer
takes it from one complete part), an event of a unit by the top count when the unit started, the unit's number and
the event's number in it. Replaying the top events and each owner's units in key order gives the single walk's order:
nodes in creation order (every process's copy asserted equal), facts per node in the single walk's order (late facts
once per `Forecaster.late_key`, as `_keep_late` keeps them), and the paths and keys `merge_equivalent` merges.
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
WALL_S = 60 * float(os.environ.get("SLOPE_WALK_MINUTES", "0"))  # a process claims no unit after this (0: no budget)
STARTS = ("emit", "_end")  # walks that always start a unit
FIELDS = ("node_group", "remitted", "class_members", "class_range", "verdict_asks")  # the Forecaster's dicts a walk fills
SETS = ("remit_classes", "classed")  # and its sets

# Watched questions (forecast.py `_Watch`: q1, the appeal) that start inside a unit are walked as the single walk walks
# them. One that starts at the top is split across units: its no-event branch is walked as the single walk walks it;
# whether the question is asked depends on reads anywhere below it, so every
# process walks on as if it were asked (a speculative region: the question's node, facts, the event branch) and logs
# each event with the regions open when it happened, each read of a watch, and the watch's edge. `walk` then decides
# each region as the single walk would (a watch is read if a read under regions that all hold occurred), keeps the
# events whose regions all hold, and gives the no-event branch's paths the edge of each question asked.


def _listed() -> dict | None:
    """A later wave's units (SLOPE_WALK_ROOTS: `spill`'s file), each key to its index; None: the first wave."""
    path = os.environ.get("SLOPE_WALK_ROOTS")
    if not path:
        return None
    with open(path, "rb") as fh:
        return {tuple(key): i for i, key in enumerate(pickle.load(fh))}


def _child(fc, d, k: int, run: str, log) -> None:
    """Process k's walk (after fork): logs events, appends each finished unit to run/part<k>.stream and writes the
    whole to run/part<k>.pkl."""
    import app.disputes.forecast as F

    t0 = time.time()
    top_nodes = dict(fc.nodes)  # the questions before the walk (`walk` starts from them; they are never logged)
    listed = _listed()
    selected_prefixes = None
    if os.environ.get('SLOPE_WALK_PREFIXES'):
        with open(os.environ['SLOPE_WALK_PREFIXES'], 'rb') as source:
            selected_prefixes = tuple(pickle.load(source))
    refinement = os.environ.get("SLOPE_WALK_REFINE")
    partition, partitions, depth = map(int, refinement.split("/")) if refinement else (0, 1, 0)
    if refinement and (listed is None or not 0 <= partition < partitions or depth < 1):
        raise ValueError("refinement requires selected roots, a valid partition and positive depth")
    nested = os.environ.get("SLOPE_WALK_NESTED_REFINE")
    nested_config = tuple(map(int, nested.split("/"))) if nested else None
    if nested_config and (not refinement or len(nested_config) != 3 or
                          not 0 <= nested_config[0] < nested_config[1] or nested_config[2] < 1):
        raise ValueError("nested refinement requires an outer refinement and valid index/count/depth")
    parent = None
    inner_segs, inner_done, subdivisions = [], [], {}
    nested_subdivisions = {}
    st = {"seg": None, "clock": 0, "nseg": 0, "vdepth": 0, "roots": {}, "seq": 0, "j": 0, "n": 0,
          "regions": [], "wstack": [], "local": False, "W": None}
    events: list = []
    segs: list = []  # every unit: (number, top clock at its start, unit seq); the same in every process
    done: list = []  # the units walked whole here
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

    job = os.environ.get("SLOPE_WALK_JOB")  # '<job>/<jobs>': a machine's share is every jobs-th unit (listed unit)

    def share(i: int) -> bool:
        if not job:
            return True
        j, n = map(int, job.split("/"))
        return i % n == j

    def claim(what) -> bool:  # among this machine's processes
        name = "c_" + hashlib.blake2b(repr(what).encode(), digest_size=12).hexdigest()
        try:
            os.close(os.open(os.path.join(run, name), os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            return False
        return True

    def resolve(name, s, starts: bool):
        """The unit the state belongs to, (seq, mine), or None where it is walked at the top. A unit starts at a
        continuation (a walk returning nothing: its caller reads nothing back from a unit it does not walk); the first
        wave claims it there, its share, while its budget lasts."""
        steps = s.steps
        for L in range(len(steps) + 1):
            r = st["roots"].get(steps[:L])
            if r is not None:
                return r
        if not starts or (len(steps) - st["vdepth"] < (depth if parent else CUT) and name not in STARTS):
            return None
        seq = st["seq"]
        st["seq"] += 1
        selected = selected_prefixes is None or any(
            steps[:len(prefix)] == prefix or prefix[:len(steps)] == steps for prefix in selected_prefixes)
        mine = selected and listed is None and share(seq) and not (WALL_S and time.time() - t0 > WALL_S) and claim(steps)
        r = st["roots"][steps] = (seq, mine)
        return r

    def segment(r) -> tuple[tuple, bool]:
        """A call into the unit `r` from the top: its segment key (each such call is a segment, numbered by every
        process) and whether this process walks it: the unit's claimer in the first wave; in a later wave, the
        segment listed for it (its share, claimed)."""
        key = (st["clock"], 0, st["nseg"])
        if parent is None:
            segs.append((st["nseg"], st["clock"], r[0]))
        st["nseg"] += 1
        if parent is not None:
            inner_segs.append(key)
            return parent + key, (st["nseg"] - 1) % partitions == partition
        if refinement:
            return key, key in listed
        if listed is None:
            return key, r[1]
        return key, key in listed and share(listed[key]) and claim(key)

    def log_event(kind, *payload) -> None:
        cond = tuple(st["regions"])
        if st["seg"] is None:
            if parent is None or (partition == 0 and
                                  (not nested_config or len(parent) > 3 or nested_config[0] == 0)):
                events.append(((parent or ()) + (st["clock"], 1, 0, 0), kind, payload, cond))
            st["clock"] += 1
        else:
            events.append((st["seg"] + (st["j"],), kind, payload, cond))
            st["j"] += 1

    def refined(key, f, self, s, a, kw, inner_config=None):
        nonlocal parent, once_top, once_unit, inner_segs, inner_done, partition, partitions, depth
        saved = {x: st[x] for x in ("clock", "nseg", "roots", "seq", "vdepth", "j", "local")}
        saved_once = once_top, once_unit
        saved_level = parent, inner_segs, inner_done, partition, partitions, depth
        if inner_config:
            partition, partitions, depth = inner_config
        parent, inner_segs, inner_done = key, [], []
        once_top, once_unit = once_top.copy(), set()
        st.update(clock=0, nseg=0, roots={}, seq=0, vdepth=len(s.steps), j=0)
        # The original continuation is entered once; its deeper continuations use the
        # same speculative-watch machinery as the original tree's shared prefix.
        try:
            result = f(self, s, *a, **kw)
            coverage = nested_subdivisions if inner_config else subdivisions
            coverage[key] = {"segments": tuple(inner_segs), "done": tuple(inner_done),
                             "partition": partition, "partitions": partitions, "depth": depth}
            print(f"refined {key}: partition {partition}/{partitions}, "
                  f"{len(inner_done)}/{len(inner_segs)} children", file=log, flush=True)
        finally:
            parent, inner_segs, inner_done, partition, partitions, depth = saved_level
            once_top, once_unit = saved_once
            st.update(saved)
        flush()
        return result

    def wrap(name, f):
        ann = inspect.signature(f).return_annotation
        starts = name in STARTS or ann == "None" or ann is None

        @functools.wraps(f)
        def w(self, s, *a, **kw):
            if type(self) is not F._Walk or not isinstance(s, F._S) or st["seg"] is not None:
                return f(self, s, *a, **kw)
            r = resolve(name, s, starts)
            if r is None:
                return f(self, s, *a, **kw)
            key, mine = segment(r)
            if not mine:
                return None  # another process's, or a later wave's
            if refinement and parent is None:
                return refined(key, f, self, s, a, kw)
            if nested_config and parent is not None and len(parent) == 3:
                result = refined(key, f, self, s, a, kw, nested_config)
                inner_done.append(key[len(parent):])
                return result
            st["seg"], st["j"] = key, 0
            try:
                return f(self, s, *a, **kw)
            finally:
                st["seg"] = None
                if parent is not None:
                    inner_done.append(key[len(parent):])
                else:
                    done.append(key)
                    flush()
        return w

    emit = F._Walk.emit

    def emit_logged(self, s, outcome):  # wrapped below: a leaf's path is logged inside its own segment
        n0 = len(self.out)
        out = emit(self, s, outcome)
        if len(self.out) > n0:
            log_event("path", n0, tuple(st["wstack"]))
            st["n"] += 1
            if st["n"] % 1000 == 0:
                from app.analysis.processor import PREFIX_STATS

                print(f"{time.time() - t0:7.0f}s part {k}: paths {st['n']} nodes {len(fc.nodes)} "
                      f"rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_GB:.2f} GB "
                      f"prefix hits/misses {PREFIX_STATS[0]}/{PREFIX_STATS[1]}", file=log, flush=True)
        return out
    F._Walk.emit = emit_logged

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
        local = st["seg"] is not None if nested_config else st["local"]
        if local:
            return single_edge(self, i0, at, edge)
        qcls = {k: list(self.fc._qcanon.get(k, ()) or self.fc._qcls.get(k, ())) for k in sorted(F.atoms(edge[0]))
                if self.fc._classified(k)}
        log_event("edge", st["regions"][-1], at, edge, qcls)

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
        out = record(self, keys, tr)  # per question and situation class: its key and the row kept
        for key, b in out:
            log_event("rec", (key,), b)
        return out

    def keep_logged(self, key, prefix, row):  # every process logs it; `walk` keeps the first of each late key
        blob = F.pack_row(row)
        lk = self.late_key(key, prefix, row, blob=blob)
        if fresh(("late", lk)):
            log_event("late", key, lk, blob)
    F.Forecaster.node, F.Forecaster.record, F.Forecaster._keep_late = node_logged, record_logged, keep_logged

    # --- the stream: each finished unit appended as it ends --------------------------------------------------------
    seen = {x: set() for x in (*FIELDS, *SETS)}
    at = {"events": 0, "done": 0, "segs": 0}
    stream = open(os.path.join(run, f"part{k}.stream"), "ab")
    pickle.dump({"k": k, "top_nodes": top_nodes, "job": job}, stream, protocol=pickle.HIGHEST_PROTOCOL)

    def added() -> dict:
        """What the walk added to the Forecaster's dictionaries since the last flush (a key's value never changes
        once set: `_union` asserts it across parts)."""
        out = {}
        for x in FIELDS:
            cur = getattr(fc, x, None) or {}
            out[x] = {q: v for q, v in cur.items() if q not in seen[x]}
            seen[x].update(out[x])
        for x in SETS:
            out[x] = set(getattr(fc, x, ())) - seen[x]
            seen[x] |= out[x]
        return out

    def flush() -> None:
        """The events since the last flush (each path as it ends the walk: a unit's paths are whole once it is), the
        segments numbered and walked since, and what the walk added."""
        W = st["W"]
        for i in range(at["events"], len(events)):
            key_, kind, payload, cond = events[i]
            if kind == "path":
                events[i] = (key_, "path", (W.out[payload[0]], W.keys[payload[0]], payload[1]), cond)
        lists = {"events": events, "done": done, "segs": segs}
        rec = {x: lst[at[x]:] for x, lst in lists.items()}
        rec.update(added(), clock=st["clock"], ev_range=getattr(fc, "ev_range", None), walked=st["n"],
                   seconds=time.time() - t0, subdivisions=dict(subdivisions),
                   nested_subdivisions=dict(nested_subdivisions))
        for x, lst in lists.items():
            at[x] = len(lst)
        pickle.dump(rec, stream, protocol=pickle.HIGHEST_PROTOCOL)
        stream.flush()

    W = st["W"] = F._Walk(fc, d)
    W.run()
    flush()  # the top's events after the last unit
    stream.close()
    out = {"k": k, "events": events, "top_nodes": top_nodes, "clock": st["clock"], "nseg": st["nseg"],
           "raise_more": raise_more, **{x: getattr(fc, x, None) or {} for x in FIELDS},
           **{x: set(getattr(fc, x, ())) for x in SETS},
           "seconds": time.time() - t0, "rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_GB,
           "walked": st["n"], "ev_range": getattr(fc, "ev_range", None), "segs": segs, "done": done, "job": job,
           "complete": True, "subdivisions": subdivisions, "nested_subdivisions": nested_subdivisions}
    with open(os.path.join(run, f"part{k}.pkl"), "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"{time.time() - t0:7.0f}s part {k}: done, {st['n']} paths, {st['nseg']} segments, {len(done)} walked",
          file=log, flush=True)


def _fork(fc, d, procs: int, run: str, log, ks=None) -> list[dict] | None:
    """Walk in `procs` forked processes (ks: their part numbers, default 0..procs-1); their results in process order
    (ks given: written to run/part<k>.pkl only)."""
    import gc

    gc.freeze()  # the shared heap built before the fork is never scanned again (and its pages stay shared)
    pids = []
    sys.stdout.flush()
    sys.stderr.flush()
    ks = list(range(procs) if ks is None else ks)
    for k in ks:
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
    codes = [os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) for pid in pids]
    bad = {k: c for k, c in zip(ks, codes, strict=True) if c != 0}
    if bad:  # a negative code: the signal that killed the process
        raise RuntimeError(f"parallel walk: process(es) failed (part: exit code) {bad} (log: {getattr(log, 'name', log)})")
    if ks != list(range(procs)):
        return None
    parts = []
    for k in range(procs):
        with open(os.path.join(run, f"part{k}.pkl"), "rb") as fh:
            parts.append(pickle.load(fh))
    return parts



def top_owner(parts: list[dict]) -> int:
    """The part whose top events a consumer takes: every process logs the top identically, so the lowest-numbered
    complete part's are the whole top's."""
    full = [p["k"] for p in parts if p.get("complete", True)]
    if not full:
        raise RuntimeError("no part is complete: the walk's top was never finished; walk again")
    return min(full)


def _live(parts: list[dict]) -> list:
    """The parts' events in the single walk's order (the top's from one part, `top_owner`), those of speculative
    regions the single walk does not walk dropped. A region (a watched question asked) holds where the watch was
    read by a read whose own regions all hold; an event holds where its regions all do."""
    owner = top_owner(parts)
    stream = sorted((e for p in parts for e in p["events"] if e[0][1] == 0 or p["k"] == owner), key=lambda e: e[0])
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
    """The dispute's paths (merged, as `Forecaster.paths` returns them), walked in `procs` processes, the units a
    wave leaves walked by the next (`missing_segments`); the Forecaster's nodes, facts and walk dictionaries set as
    the single walk leaves them."""
    import shutil

    from app.disputes.forecast import _ROW_BLOBS, Rows, class_entry, merge_equivalent, path_mask, qcls_best

    t0 = time.time()
    saved = os.environ.get("SLOPE_WALK_PARTS")  # the parts the shards walked (GitHub Actions: app/disputes/parallel.py)
    while True:  # as Forecaster.paths: walk again where a whole path shows equity the floor's prefix did not
        if saved:
            parts = load_parts(saved)
            missing = missing_segments(parts)
            if missing:
                raise RuntimeError(f"{len(missing)} units were never walked: list them (`spill`) and walk them again")
        else:
            run = tempfile.mkdtemp(prefix="walk_")
            try:
                parts = _fork(fc, d, procs, run, log)
                wave = 1
                while missing := missing_segments(parts):
                    listing = os.path.join(run, f"spill{wave}.pkl")
                    with open(listing, "wb") as fh:
                        pickle.dump(missing, fh)
                    print(f"{time.time() - t0:7.0f}s parallel walk: wave {wave + 1} walks {len(missing)} units left",
                          file=log, flush=True)
                    os.environ["SLOPE_WALK_ROOTS"] = listing
                    try:
                        _fork(fc, d, procs, run, log, ks=range(wave * procs, (wave + 1) * procs))
                    finally:
                        del os.environ["SLOPE_WALK_ROOTS"]
                    parts = load_parts(run)
                    wave += 1
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
            wid, at, edge, qcls = x if len(x) > 3 else (*x, {})
            for i in under.get(wid, ()):
                w = walked[i]
                have = {c[0] for c in w[0].classes}
                add = tuple(class_entry(k, cls, path_mask(w[0], fc.draws.n)) for k, entries in qcls.items()
                            if k not in have and (cls := qcls_best(entries, w[0].steps)) is not None)
                w[0] = replace(w[0], edges=w[0].edges[:at] + (edge,) + w[0].edges[at:], classes=w[0].classes + add)
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
    fc.classed |= {x for x in set().union(*(p.get("classed", ()) for p in parts)) if x in nodes}
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


def part_files(folder: str) -> dict[int, str]:
    """Each part under `folder` (any depth) by number: its part<k>.pkl, or the stream of a process that died."""
    from pathlib import Path

    files: dict = {}
    for p in Path(folder).rglob("part*.stream"):
        files[int(p.stem[4:])] = str(p)
    for p in Path(folder).rglob("part*.pkl"):
        files[int(p.stem[4:])] = str(p)
    return files


def read_part(path: str) -> dict:
    if path.endswith(".stream"):
        return _restore(path)
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _restore(path: str) -> dict:
    """A part from its stream (the process died before writing part<k>.pkl): the events of its finished units and
    what they added; the top's clock and segment count unknown (`complete` False)."""
    recs = []
    with open(path, "rb") as fh:
        while True:
            try:
                recs.append(pickle.load(fh))
            except EOFError:
                break
            except Exception:  # noqa: BLE001 (a record cut short when the process died: nothing after it is whole)
                break
    head = recs[0]
    out = {"k": head["k"], "top_nodes": head["top_nodes"], "job": head.get("job"), "events": [], "clock": None,
           "nseg": None, "segs": [], "done": [], "seconds": 0.0, "rss": 0.0, "walked": 0, "ev_range": None,
           "complete": False, **{x: {} for x in FIELDS}, **{x: set() for x in SETS}}
    out["subdivisions"] = {}
    out["nested_subdivisions"] = {}
    for r in recs[1:]:
        out["subdivisions"].update(r.get("subdivisions", {}))
        out["nested_subdivisions"].update(r.get("nested_subdivisions", {}))
        for x in ("events", "segs", "done"):
            out[x] += r[x]
        for x in FIELDS:
            out[x].update(r[x])
        for x in SETS:
            out[x] |= r[x]
        if r["ev_range"] is not None:
            out["ev_range"] = r["ev_range"]
        out["walked"], out["seconds"] = r["walked"], r["seconds"]
    out["raise_more"] = {x[0] for _, kind, x, _c in out["events"] if kind == "raise"}
    return out


def load_parts(folder: str) -> list[dict]:
    """The shards' parts (part<k>.pkl or .stream under `folder`, any depth), in part order."""
    files = part_files(folder)
    assert files, f"no parts under {folder}"
    return [read_part(files[k]) for k in sorted(files)]


def missing_segments(parts: list[dict]) -> list[tuple]:
    """The segments (calls into a unit from the top) no part walked whole: every complete part numbers them all."""
    if any(p.get("nested_subdivisions") for p in parts):
        raise RuntimeError("nested refinement requires complete child coverage assembly before adoption")
    full = [p for p in parts if p.get("complete", True)]
    if not full:
        raise RuntimeError("no part is complete: the walk's top was never finished; walk again")
    assert len({(p["clock"], p["nseg"]) for p in full}) == 1, "the tops differ"
    assert len({tuple(p["segs"]) for p in full}) == 1, "the segments differ"
    every = {(clock, 0, n) for n, clock, _seq in full[0]["segs"]}
    done = set().union(*(p["done"] for p in parts))
    refined = {}
    for p in parts:
        for key, sub in p.get("subdivisions", {}).items():
            refined.setdefault(key, []).append(sub)
    for key, subs in refined.items():
        if key in done:
            raise RuntimeError(f"segment {key} has both whole and subdivided results")
        first = subs[0]
        if any((s["segments"], s["partitions"], s["depth"]) !=
               (first["segments"], first["partitions"], first["depth"]) for s in subs):
            raise RuntimeError(f"subdivision topology differs for {key}")
        indexes = [s["partition"] for s in subs]
        children = [c for s in subs for c in s["done"]]
        if len(set(indexes)) != len(indexes) or len(set(children)) != len(children):
            raise RuntimeError(f"duplicate subdivision for {key}")
        if set(indexes) == set(range(first["partitions"])):
            if set(children) != set(first["segments"]):
                raise RuntimeError(f"subdivision coverage differs for {key}")
            done.add(key)
    return sorted(every - done)


def spill(folder: str, out: str) -> int:
    """The segments the parts under `folder` never walked, written to `out` for the next wave (SLOPE_WALK_ROOTS);
    returns their number."""
    parts = load_parts(folder)
    missing = missing_segments(parts)
    with open(out, "wb") as fh:
        pickle.dump(missing, fh)
    dead = sorted(p["k"] for p in parts if not p.get("complete", True))
    print(f"spill: {len(parts)} parts ({len(dead)} restored from streams: {dead}), {len(missing)} segments to walk "
          f"({sum(len(p['done']) for p in parts)} walked)", file=sys.stderr, flush=True)
    return len(missing)

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



def shard(run_id: str, job: int, jobs: int, procs: int, out: str, base: int = 0) -> None:
    """One machine's share of the pending claim's walk: parts base + job*procs .. + procs-1 of jobs*procs, the
    Forecaster built as `build.judged_model` builds it. SLOPE_WALK_ROOTS: a later wave, its listed segments shared
    among the jobs the same way (base past the first wave's parts)."""
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
    _fork(fc, d, procs, out, sys.stderr, ks=range(base + job * procs, base + job * procs + procs))

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
    elif sys.argv[1] == "spill":
        print(spill(sys.argv[2], sys.argv[3]))
    else:
        shard(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5],
              int(sys.argv[6]) if len(sys.argv) > 6 else 0)

"""Walk one refined task; at every merge decision (`_Walk._same_after`, `Forecaster.moves_cash`) also decide it with
every process cache empty. At the first disagreement, find which cache alone flips it, record what each side
compared, and stop.

Usage: python tools/diag/join_check.py <plan.json> <partition> <out dir>
"""
import json
import os
import pickle
import sys
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

plan = json.load(open(sys.argv[1]))
part, out = int(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
roots = out / f"roots-{part}.pkl"
roots.write_bytes(pickle.dumps([tuple(x) for x in plan["roots"]]))
os.environ.update(SLOPE_WALK_ROOTS=str(roots), SLOPE_WALK_CUT=str(plan["cut"]), SLOPE_WALK_MINUTES="0",
                  SLOPE_WALK_REFINE=f"{part}/{plan['partitions']}/{plan['depth']}", SLOPE_JEV_CACHE_ONLY="1")

import app.analysis.events as E  # noqa: E402
import app.analysis.processor as P  # noqa: E402
import app.disputes.forecast as F  # noqa: E402
from app.analysis.events import BIG, EventCash, canon, event_chain  # noqa: E402
from app.disputes import parallel  # noqa: E402

KNOWN = tuple(tuple(x) for x in json.load(open(Path(sys.argv[1]).with_name("prefix.json"))))
LOG = open(out / f"log-{part}.txt", "a", buffering=1)
STATE = {"walk": None, "checks": 0, "t0": time.time(), "busy": False}


def say(*a):
    print(*a, file=LOG, flush=True)
    print(f"[{part}]", *a, flush=True)


# --- the process caches a merge decision can read, each emptied and restored on its own --------------------------
def clear_prefix(fc, w):
    old = fc.draws.prefixes
    fc.draws.prefixes = {}
    return lambda: setattr(fc.draws, "prefixes", old)


def clear_traces(fc, w):
    old = fc._traces, fc.__dict__.get("_open_light")
    fc._traces, fc._open_light = F._DepthCache(fc.SIBLINGS), {}

    def back():
        fc._traces = old[0]
        if old[1] is None:
            fc.__dict__.pop("_open_light", None)
        else:
            fc._open_light = old[1]
    return back


def clear_masks(fc, w):
    if w is None:
        return lambda: None
    old = w._masks
    w._masks = F._DepthCache(fc.SIBLINGS)
    return lambda: setattr(w, "_masks", old)


def clear_engine(fc, w):
    dr = fc.draws
    old = (E._RUN_LRU, E._RUN_TOTAL, dr.__dict__.get("_subs"), dr.__dict__.get("_sub_bytes"),
           dict(dr.basis.runs), P._PREFIX_RUNS, P._PREFIX_BYTES)
    E._RUN_LRU, E._RUN_TOTAL = {}, [0]
    dr._subs, dr._sub_bytes = {}, 0
    dr.basis.runs.clear()
    P._PREFIX_RUNS, P._PREFIX_BYTES = OrderedDict(), 0

    def back():
        E._RUN_LRU, E._RUN_TOTAL = old[0], old[1]
        for k, v in (("_subs", old[2]), ("_sub_bytes", old[3])):
            if v is None:
                dr.__dict__.pop(k, None)
            else:
                setattr(dr, k, v)
        dr.basis.runs.clear()
        dr.basis.runs.update(old[4])
        P._PREFIX_RUNS, P._PREFIX_BYTES = old[5], old[6]
    return back


GROUPS = {"prefix_stack": clear_prefix, "trace_cache": clear_traces, "masks": clear_masks, "engine_runs": clear_engine}


def under(names, fc, w, fn):
    backs = [GROUPS[n](fc, w) for n in names]
    try:
        return fn()
    finally:
        for b in reversed(backs):
            b()


# --- what a merge decision compared -------------------------------------------------------------------------------
def same_after_parts(w, s, a, b, m):
    fc = w.fc
    rows_a, rows_b = w._rows(s.steps + (a,)), w._rows(s.steps + (b,))
    chain = lambda st, r: event_chain(w.d, canon(st), fc.setup, fc.m, fc.draws, fc.sens, rows=r)  # noqa: E731
    ca, cb = chain(s.steps + (a,), rows_a), chain(s.steps + (b,), rows_b)
    div = ca.divergence(cb)
    whole = lambda st, r: F.masked(fc.trace(w.d, st, True, rows=r, full_rows=True), w.mask_of(st))  # noqa: E731
    ta, tb = whole(s.steps + (a,), rows_a), whole(s.steps + (b,), rows_b)
    return {"rows_a": None if rows_a is None else np.flatnonzero(rows_a[-1]),
            "rows_b": None if rows_b is None else np.flatnonzero(rows_b[-1]),
            "mask": None if m is None else np.flatnonzero(m), "div": div, "div_ok": bool((div >= BIG).all()),
            "digest_a": ta.digest, "digest_b": tb.digest, "cause_a": ta.cause, "cause_b": tb.cause,
            "marks_a": ta.marks, "marks_b": tb.marks, "chain_a": ca, "chain_b": cb}


def diff(x, y, path=""):
    if isinstance(x, EventCash) and isinstance(y, EventCash):
        return [p for f in ("cash", "lock", "capacity", "petition", "kinds", "incurred", "proceeds")
                for p in diff(getattr(x, f), getattr(y, f), f"{path}.{f}")]
    if isinstance(x, np.ndarray) or isinstance(y, np.ndarray):
        ok = isinstance(x, np.ndarray) and isinstance(y, np.ndarray) and x.shape == y.shape and np.array_equal(x, y)
        if ok:
            return []
        where = ""
        if isinstance(x, np.ndarray) and isinstance(y, np.ndarray) and x.shape == y.shape:
            ne = np.argwhere(x != y)
            where = f" first differing index {ne[0].tolist()} ({x[tuple(ne[0])]} vs {y[tuple(ne[0])]}), {len(ne)} cells"
        return [f"{path}{where or ' shape/type differs'}"]
    if isinstance(x, dict) and isinstance(y, dict):
        return [p for k in set(x) | set(y) for p in diff(x.get(k, "<missing>"), y.get(k, "<missing>"), f"{path}[{k!r}]")]
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        if len(x) != len(y):
            return [f"{path} length {len(x)} vs {len(y)}"]
        return [p for i, (u, v) in enumerate(zip(x, y, strict=True)) for p in diff(u, v, f"{path}[{i}]")]
    try:
        return [] if x is y or x == y else [f"{path} {x!r:.100} vs {y!r:.100}"]
    except Exception:  # noqa: BLE001
        return [] if repr(x) == repr(y) else [f"{path} (repr differs)"]


SKIP = {"dr", "d", "s", "m", "sens", "fin", "basis", "_keys", "_cum", "_tau", "_out", "_hd", "_av", "_cv"}


def chain_diff(c1, c2):
    keys = (set(c1.__dict__) | set(c2.__dict__)) - SKIP
    return [p for k in sorted(keys) for p in diff(c1.__dict__.get(k, "<missing>"), c2.__dict__.get(k, "<missing>"), k)]


def report(kind, w, fc, args, walked, cold, extra):
    say(f"DISAGREE {kind} after {STATE['checks']} checks, {time.time() - STATE['t0']:.0f}s: walked {walked} cold {cold}")
    flips = {}
    for name in GROUPS:  # which cache, emptied alone, gives the cold answer
        flips[name] = extra["decide"](lambda n=name: [n]) == cold
    say("cache that alone gives the cold answer:", {k: v for k, v in flips.items() if v} or "none alone")
    rec = {"kind": kind, "args": args, "walked": walked, "cold": cold, "flips": flips, "checks": STATE["checks"]}
    if kind == "same_after":
        s, a, b, m = args
        pw = same_after_parts(w, s, a, b, m)
        pc = under(list(GROUPS), fc, w, lambda: same_after_parts(w, s, a, b, m))
        size = lambda v: None if v is None else len(v)  # noqa: E731
        for key in ("rows_a", "rows_b", "mask"):
            same = (pw[key] is None and pc[key] is None) or (
                pw[key] is not None and pc[key] is not None and np.array_equal(pw[key], pc[key]))
            say(f"  {key}: walked {size(pw[key])} draws, cold {size(pc[key])} draws, same: {same}")
        for key in ("div_ok", "digest_a", "digest_b"):
            say(f"  {key}: walked {pw[key]!r} cold {pc[key]!r}")
        say("  walked a==b digest:", pw["digest_a"] == pw["digest_b"], " cold a==b digest:", pc["digest_a"] == pc["digest_b"])
        for side in ("a", "b"):
            d = chain_diff(pw[f"chain_{side}"], pc[f"chain_{side}"])
            say(f"  chain {side} walked vs cold: {len(d)} differing fields")
            for line in d[:30]:
                say("     ", line)
            rec[f"chain_diff_{side}"] = d
        rec["walked_parts"] = {k: v for k, v in pw.items() if not k.startswith("chain")}
        rec["cold_parts"] = {k: v for k, v in pc.items() if not k.startswith("chain")}
    rec["steps"] = args[0].steps if kind == "same_after" else args[1]
    pickle.dump(rec, open(out / f"disagree-{part}.pkl", "wb"))
    say("STEPS", rec["steps"])
    LOG.flush()
    os._exit(3)


orig_same, orig_moves = F._Walk._same_after, F.Forecaster.moves_cash
orig_init = F._Walk.__init__


def init(self, *a, **k):
    orig_init(self, *a, **k)
    STATE["walk"] = self


def same_after(self, s, a, b, m):
    r = orig_same(self, s, a, b, m)
    if STATE["busy"]:
        return r
    STATE["busy"] = True
    try:
        fc = self.fc
        decide = lambda names: under(names(), fc, self, lambda: orig_same(self, s, a, b, m))  # noqa: E731
        # walk caches emptied (fast: engine runs are content-keyed and kept); every cache at the known decision
        cold = decide(lambda: list(GROUPS) if s.steps == KNOWN else ["prefix_stack", "trace_cache", "masks"])
        STATE["checks"] += 1
        if STATE["checks"] % 200 == 0:
            say(f"{STATE['checks']} merge checks agree, {time.time() - STATE['t0']:.0f}s")
        if cold != r or (os.environ.get("DIAG_FORCE") and STATE["checks"] == 3):
            report("same_after", self, fc, (s, a, b, m), r, cold, {"decide": decide})
    finally:
        STATE["busy"] = False
    return r


def moves_cash(self, d, steps, a, b):
    r = orig_moves(self, d, steps, a, b)
    if STATE["busy"]:
        return r
    STATE["busy"] = True
    try:
        w = STATE["walk"]
        decide = lambda names: under(names(), self, w, lambda: orig_moves(self, d, steps, a, b))  # noqa: E731
        cold = decide(lambda: ["prefix_stack", "trace_cache", "masks"])
        STATE["checks"] += 1
        if cold != r:
            report("moves_cash", w, self, (d, steps, a, b), r, cold, {"decide": decide})
    finally:
        STATE["busy"] = False
    return r


F._Walk.__init__, F._Walk._same_after, F.Forecaster.moves_cash = init, same_after, moves_cash
say(f"partition {part}/{plan['partitions']} of group {plan['job']}: depth {plan['depth']}, cut {plan['cut']}")
parallel.shard("akoustis_20240514-agent_plus_jev-20260929T052558Z", 0, 1, 1, str(out / f"parts-{part}"),
               9700000 + part)
say(f"DONE without disagreement: {STATE['checks']} checks, {time.time() - STATE['t0']:.0f}s")

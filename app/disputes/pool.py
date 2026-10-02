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
import multiprocessing
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
    from app.disputes import parallel

    t0 = time.time()
    os.makedirs(os.path.join(out, "control"), exist_ok=True)
    files = parallel.part_files(parts)  # a process that died: its stream's finished units (parallel._restore)
    for pk in sorted(files):
        p = parallel.read_part(files[pk])
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
        name = f"part{pk}.pkl"
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
    from app.disputes import parallel

    t0 = time.time()
    files = parallel.part_files(folder)
    if 0 not in files:
        raise SystemExit("control: part 0 (the top's events) is missing; walk job 0 again")
    reads, node_ev, edge_ev, raised, heads = {}, [], [], [], []
    for k in sorted(files):
        p = parallel.read_part(files[k])
        for ekey, kind, x, cond in p["events"]:
            if kind == "read":
                reads.setdefault(x[0], []).append((cond, ekey[1], k))
            elif kind == "node":
                node_ev.append((ekey, x[0], x[1], cond, k))
            elif kind == "edge":
                edge_ev.append((ekey, x, cond, k))
            elif kind == "raise":
                raised.append((x[0], cond, ekey[1], k))
        heads.append({x: v for x, v in p.items() if x != "events"})
        del p
    missing = parallel.missing_segments(heads)  # a tree missing a segment is not built
    if missing:
        raise SystemExit(f"control: {len(missing)} segments were never walked (e.g. {missing[:3]}); list them "
                         f"(python -m app.disputes.parallel spill) and walk them in another wave")
    owner = parallel.top_owner(heads)  # every part logs the top: one part's copy is taken

    def top(at: int, k: int) -> bool:
        return at == 1 and k != owner
    reads = {w: [c for c, at, k in v if not top(at, k)] for w, v in reads.items()}
    node_ev = [e[:4] for e in node_ev if not top(e[0][1], e[4])]
    edge_ev = [e[:3] for e in edge_ev if not top(e[0][1], e[3])]
    raised = [e[:2] for e in raised if not top(e[2], e[3])]
    holds = _holds(reads)
    live = lambda cond: all(holds(c) for c in cond)  # noqa: E731
    assert len({tuple(h.get("top_nodes", {})) for h in heads}) == 1, "the questions before the walk differ"
    nodes = dict(heads[0].get("top_nodes", {}))  # as `parallel.walk` starts: the Forecaster's before the walk
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
    ctl = {"nodes": nodes, "reads": reads, "raised": raised_live, "top_owner": owner,
           "node_group": asked("node_group"), "remitted": asked("remitted"),
           "class_members": parallel._union(heads, "class_members"),
           "class_range": parallel._union(heads, "class_range"),
           "remit_classes": set().union(*(h["remit_classes"] for h in heads)),
           "verdict_asks": asked("verdict_asks"),
           "classed": {x for x in set().union(*(h.get("classed", ()) for h in heads)) if x in nodes}}
    ctl["segments"] = next(h["nseg"] for h in heads if h.get("complete", True))
    ctl["parts"] = len(files)
    import numpy as np
    rngs = [h["ev_range"] for h in heads if h.get("ev_range") is not None]
    ctl["ev_range"] = (np.minimum.reduce([r[0] for r in rngs]), np.maximum.reduce([r[1] for r in rngs])) if rngs else None
    del node_ev, edge_ev, raised, live, asked, rngs
    heads = []
    holds = None
    os.makedirs(os.path.join(out, "paths"), exist_ok=True)
    os.makedirs(os.path.join(out, "walked"), exist_ok=True)
    global _PATH_CONTEXT
    _PATH_CONTEXT = (reads, nodes, edges, owner, out)
    total, missing, cost, meta = 0, set(), {}, []
    for k, part_meta, absent, count, seconds in _map_parts(_path_part, sorted(files.items())):
        f = f"part{k}.pkl"
        total += count
        missing.update(absent)
        cost[f] = (count, seconds)
        meta.extend(part_meta)
        print(f"control paths: {len(cost)}/{len(files)} parts, {total} paths", file=sys.stderr, flush=True)
    _PATH_CONTEXT = None
    merged = _merge(out, meta, {k: n.branches for k, n in nodes.items()})
    for f, n in merged.items():  # the reduction balances its blocks by each part's paths after the merge
        cost[f] = (n, cost[f][1])
    ctl["walked"], ctl["paths"], ctl["part_cost"] = total, sum(merged.values()), cost
    if missing and os.environ.get("SLOPE_POOL_ALLOW_MISSING") != "1":
        raise SystemExit(f"control: {len(missing)} questions the paths read were never logged, e.g. {sorted(missing)[:3]}")
    with open(os.path.join(out, "control.pkl"), "wb") as fh:
        pickle.dump(ctl, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"{time.time() - t0:7.0f}s control: {len(files)} parts, {total} paths walked, {ctl['paths']} after the "
          f"merge, {len(nodes)} questions "
          f"({len(ctl['classed'])} asked per class), raise re-offered at {len(raised_live)} prefixes",
          file=sys.stderr, flush=True)


def _merge(out: str, meta: list, branches: dict) -> dict[str, int]:
    """`forecast.merge_equivalent` over the whole tree, as `parallel.walk` applies it: the walked paths (out/walked;
    meta: (event key, equivalence key, part, index) of each) in the single walk's order, paths with the same key one
    path. merge_equivalent treats each key's group on its own, so each group is merged alone, its members in the
    walk's order. Writes out/paths/<part>: each merged path in the part of its group's first member, in the walk's
    order. Returns each part's count."""
    if os.environ.get("SLOPE_REMOTE_PATHS") == "1":
        from tools.path_archives import complete
        return complete(out, meta, branches)
    if os.environ.get("SLOPE_MERGE_FLEET") == "1":
        from tools.merge_fleet import run
        return run(out, meta, branches)
    meta.sort(key=lambda m: m[0])
    groups: dict = {}
    for e, k, f, i in meta:
        groups.setdefault(k, []).append((e, f, i))
    home = {k: g[0][1] for k, g in groups.items()}  # the part of each group's first member
    need: dict = {}  # members that live in another part than their group's home
    for k, g in groups.items():
        for _e, f, i in g:
            if f != home[k]:
                need.setdefault(f, set()).add(i)
    away: dict = {}
    for f, idx in need.items():
        with open(os.path.join(out, "walked", f), "rb") as fh:
            kept = pickle.load(fh)
        away.update({(f, i): kept[i] for i in idx})
    by_home: dict = {}
    for k in groups:
        by_home.setdefault(home[k], []).append(k)
    global _MERGE_CONTEXT
    _MERGE_CONTEXT = (out, groups, by_home, away, branches)
    counts = dict(_map_parts(_merge_home, sorted({m[2] for m in meta})))
    _MERGE_CONTEXT = None
    for f in counts:
        os.remove(os.path.join(out, "walked", f))
    return counts


_PATH_CONTEXT = None
_MERGE_CONTEXT = None


def _map_parts(fn, jobs):
    workers = max(1, int(os.environ.get("SLOPE_POOL_PROCESSES", "1")))
    if workers == 1:
        yield from map(fn, jobs)
    else:
        # Global read-only tables are inherited, not serialized for every part. Linux runners use fork.
        with multiprocessing.get_context("fork").Pool(workers) as workers_pool:
            yield from workers_pool.imap_unordered(fn, jobs, chunksize=1)


def _path_part(item):
    from app.analysis.setup import DRAWS
    from app.disputes import parallel
    from app.disputes.forecast import atoms, class_entry, path_mask, qcls_best

    reads, nodes, edges, owner, out = _PATH_CONTEXT
    holds = _holds(reads)
    k, file = item
    f = f"part{k}.pkl"
    walked = Path(out) / "walked" / f
    checkpoint = walked.with_suffix(".meta")
    implementation = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if os.environ.get("SLOPE_POOL_REUSE_WALKED") == "1" and checkpoint.exists() and walked.exists():
        with checkpoint.open("rb") as fh:
            saved = pickle.load(fh)
        if saved[0] == implementation:
            return saved[1]
    p = parallel.read_part(file)
    reuse = os.environ.get("SLOPE_POOL_REUSE_WALKED") == "1" and walked.exists()
    kept, meta, missing = [], [], set()
    for ekey, kind, x, cond in sorted(p["events"], key=lambda e: e[0]):
        if kind != "path" or not all(holds(c) for c in cond) or (ekey[1] == 1 and k != owner):
            continue
        meta.append((ekey, x[1], f, len(meta)))
        if reuse:
            continue
        w = x[0]
        for wid in x[2]:
            for at, edge, qcls in edges.get(wid, ()):
                have = {c[0] for c in w.classes}
                add = tuple(class_entry(key, cls, path_mask(w, DRAWS)) for key, entries in qcls.items()
                            if key not in have and (cls := qcls_best(entries, w.steps)) is not None)
                w = replace(w, edges=w.edges[:at] + (edge,) + w.edges[at:], classes=w.classes + add)
        kept.append(w)
    if reuse:
        with walked.open("rb") as fh:
            kept = pickle.load(fh)
        if len(kept) != len(meta):
            raise RuntimeError(f"saved path count differs for {f}")
    for w in kept:
        for key, _ in w.edges:
            missing.update(atom for atom in atoms(key) if atom not in nodes)
    if not reuse:
        with walked.open("wb") as fh:
            pickle.dump(kept, fh, protocol=pickle.HIGHEST_PROTOCOL)
    result = k, meta, missing, len(kept), float(p.get("seconds", 0.0))
    temporary = checkpoint.with_suffix(".tmp")
    with temporary.open("wb") as fh:
        pickle.dump((implementation, result), fh, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(checkpoint)
    return result


def _merge_home(f):
    from app.disputes.forecast import merge_equivalent

    out, groups, by_home, away, branches = _MERGE_CONTEXT
    with open(os.path.join(out, "walked", f), "rb") as fh:
        kept = pickle.load(fh)
    rows = []
    for k in by_home.get(f, ()):
        g = groups[k]
        members = [kept[i] if gf == f else away[(gf, i)] for _e, gf, i in g]
        rows += [(g[0][0], q) for q in merge_equivalent(members, [k] * len(members), branches)]
    rows.sort(key=lambda r: r[0])
    write_paths(os.path.join(out, "paths", f), [q for _e, q in rows])
    return f, len(rows)


def write_paths(path: str, paths: list) -> None:
    """A part's merged paths, each pickled on its own, with an index of their byte offsets (`<path>.idx`) so a
    reduction block reads only its range (`read_paths`)."""
    import struct

    offsets = []
    with open(path, "wb") as fh:
        for q in paths:
            offsets.append(fh.tell())
            pickle.dump(q, fh, protocol=pickle.HIGHEST_PROTOCOL)
        offsets.append(fh.tell())
    with open(path + ".idx", "wb") as fh:
        fh.write(struct.pack(f"<{len(offsets)}Q", *offsets))


def read_paths(path: str, lo: int = 0, hi: int | None = None) -> list:
    """Paths lo..hi of a part written by `write_paths` (a file without an index: one pickled list)."""
    import struct

    if not os.path.exists(path + ".idx"):
        with open(path, "rb") as fh:
            return pickle.load(fh)[lo:hi]
    with open(path + ".idx", "rb") as fh:
        raw = fh.read()
    offsets = struct.unpack(f"<{len(raw) // 8}Q", raw)
    n = len(offsets) - 1
    lo, hi = max(lo, 0), n if hi is None else min(hi, n)
    if lo >= hi:
        return []
    out = []
    with open(path, "rb") as fh:
        fh.seek(offsets[lo])
        for _ in range(hi - lo):
            out.append(pickle.load(fh))
    return out


def forecaster(run_id: str, ctl: dict, root: Path = Path("runs/recorded")):
    """The run's Forecaster as the walk built it (parallel.shard), with the merged questions and dictionaries."""
    from app.analysis.build import basis_for, run_context
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    ctx = run_context(run_id, root)
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

    rows, owner = [], ctl.get("top_owner", 0)
    for f in _parts(folder):
        k = int(os.path.basename(f)[4:-4])
        with open(f, "rb") as fh:
            rows += [r for r in pickle.load(fh) if r[0][1] == 0 or k == owner]  # the top's rows from one part
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
    t0 = time.time()
    with open(control_file, "rb") as fh:
        ctl = pickle.load(fh)
    fc = forecaster(run_id, ctl)
    fc.facts = bucket_facts(folder, ctl)
    mine = sorted(k for k in fc.nodes if bucket(k) == b and k not in fc.classed)
    result = question_states(fc, mine, check)
    os.makedirs(out, exist_ok=True)
    with gzip.open(os.path.join(out, f"states{b}.json.gz"), "wt") as fh:
        json.dump(result, fh, default=str)
    print(f"{time.time() - t0:7.0f}s facts bucket {b}: {len(mine)} questions, "
          f"{len(result['states'])} states, {len(result['errors'])} errors", file=sys.stderr, flush=True)


def question_states(fc, mine, check=False):
    from app.disputes.forecast import CLASS_TAG, Rows

    states, errors, differ, never = {}, {}, [], []
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
    return {"states": states, "errors": errors, "differ": differ, "never_live": never}


def judge(run_id: str, states: str, control_file: str, count_only: bool = False,
          *, root: Path = Path("runs/recorded")) -> None:
    """Jev's answer to every question the facts stage built (states/**/states*.json.gz), asked on this machine as
    `Forecaster.judge` asks (DisputeProfile.forecast; Jev's cache first). Writes runs/recorded/<run>/
    tree_answers[-<variant>].json (each question's distribution, and the classes live on no path: the reduction's
    input) and tree_judgments[-<variant>].json.gz (each Judgment, for the page). A question whose state did not build
    stops it. count_only: the questions by type, nothing asked."""
    import asyncio
    import collections
    import dataclasses

    from app.disputes.forecast import Judgment, answer_distribution, state_evidence

    t0 = time.time()
    got, errors, dead = {}, {}, set()
    for f in sorted(glob.glob(os.path.join(states, "**", "states*.json.gz"), recursive=True)):
        with gzip.open(f, "rt") as fh:
            x = json.load(fh)
        for k, value in x["states"].items():
            if k in got and got[k] != value:
                raise SystemExit(f"judge: conflicting question state {k!r} in {f}")
        got.update(x["states"])
        errors.update(x["errors"])
        dead |= set(x.get("never_live", ()))
    if errors:
        raise SystemExit(f"judge: {len(errors)} question states did not build, e.g. {next(iter(errors.items()))}")
    with open(control_file, "rb") as fh:
        ctl = pickle.load(fh)
    nodes = ctl["nodes"]
    expected = set(nodes) - set(ctl["classed"])
    present = set(got) | dead
    missing, extra, overlap = expected - present, present - expected, set(got) & dead
    if missing or extra or overlap:
        raise SystemExit(f"judge: incomplete or inconsistent question coverage: "
                         f"{len(missing)} missing, {len(extra)} unexpected, {len(overlap)} both live and dead; "
                         f"examples: {sorted(missing | extra | overlap)[:5]}")
    by_type = collections.Counter(nodes[k].node for k in got)
    print(f"{time.time() - t0:7.0f}s judge: {len(got)} questions, {len(dead)} classes live on no path; by type "
          f"{dict(sorted(by_type.items()))}", file=sys.stderr, flush=True)
    if count_only:
        return
    from app.agent.jev import JevAdapter
    from app.agent.jev_profiles import DisputeProfile
    from app.analysis.build import VAR

    records: list = []
    # the cap is a setting (SLOPE_JEV_CAP, dollars), never a blocker; the adapter reserves every in-flight request
    # worst case, so the questions go in batches (SLOPE_JEV_BATCH) and the reserve stays a batch's
    jev = JevAdapter(run_id=f"{run_id}-analysis", use_cache=True, spend_cap_usd=os.environ.get("SLOPE_JEV_CAP"))
    batch = int(os.environ.get("SLOPE_JEV_BATCH", "200"))
    prof = DisputeProfile(jev, lambda kind, obj: records.append({"kind": kind, **obj.model_dump(mode="json")}))

    async def one(k: str) -> Judgment:
        n, x = nodes[k], got[k]
        st, fids = x["state"], tuple(x["fids"])
        o = await prof.forecast(n.question_id, st, (n.instance_id, *fids), n.branches)
        return Judgment(key=k, instance_id=n.instance_id, node=n.node, question_id=n.question_id, event=n.event,
                        assumptions=n.assumptions, window=n.window, distribution=answer_distribution(k, n.branches, o),
                        confidence=o.confidence, finding_ids=fids, readings=x["readings"],
                        evidence=state_evidence(st),
                        observation_id=o.observation_id, path_facts=st.get("path_facts", st.get("situation")))

    async def every() -> list:
        keys, out = sorted(got), []
        for lo in range(0, len(keys), batch):
            out += await asyncio.gather(*(one(k) for k in keys[lo:lo + batch]))
            print(f"{time.time() - t0:7.0f}s judge: {len(out)}/{len(keys)} answered; Jev {jev.usage_summary()}",
                  file=sys.stderr, flush=True)
        return out

    js = asyncio.run(every())
    variant = os.environ.get("SLOPE_VARIANT", "")
    out = root / run_id
    suffix = f"-{variant}" if variant else ""
    (out / f"tree_answers{suffix}.json").write_text(json.dumps(
        {"answers": {j.key: j.distribution for j in js}, "dead": sorted(dead)}, sort_keys=True) + "\n")
    text = json.dumps([dataclasses.asdict(j) for j in js], default=str, sort_keys=True) + "\n"
    (out / f"tree_judgments{suffix}.json.gz").write_bytes(gzip.compress(text.encode(), mtime=0))
    scratch = VAR / "analysis" / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"tree_jev_records{suffix}.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in records))
    print(f"{time.time() - t0:7.0f}s judge: {len(js)} answers; Jev {jev.usage_summary()}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "split":
        split(sys.argv[2], sys.argv[3])
    elif cmd == "control":
        control(sys.argv[2], sys.argv[3])
    elif cmd == "facts":
        facts(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6],
              check=os.environ.get("SLOPE_POOL_CHECK") == "1")
    elif cmd == "judge":
        judge(sys.argv[2], sys.argv[3], sys.argv[4], count_only=os.environ.get("SLOPE_JUDGE_COUNT") == "1")
    else:
        raise SystemExit(f"unknown stage {cmd!r}")

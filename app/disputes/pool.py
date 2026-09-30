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
    ctl = {"nodes": nodes, "reads": reads, "raised": raised_live,
           "node_group": asked("node_group"), "remitted": asked("remitted"),
           "class_members": parallel._union(heads, "class_members"),
           "class_range": parallel._union(heads, "class_range"),
           "remit_classes": set().union(*(h["remit_classes"] for h in heads)),
           "verdict_asks": asked("verdict_asks"), "classed": set().union(*(h.get("classed", ()) for h in heads))}
    os.makedirs(os.path.join(out, "paths"), exist_ok=True)
    os.makedirs(os.path.join(out, "walked"), exist_ok=True)
    total, missing, cost, meta = 0, set(), {}, []
    for f in files:
        with open(f, "rb") as fh:
            p = pickle.load(fh)
        kept = []
        for ekey, kind, x, cond in sorted(p["events"], key=lambda e: e[0]):
            if kind != "path" or not live(cond):
                continue
            meta.append((ekey, x[1], os.path.basename(f), len(kept)))
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
        # each part's paths and its walk's seconds: the reduction balances its blocks by them (analysis/reduce.py)
        cost[os.path.basename(f)] = (len(kept), float(p.get("seconds", 0.0)))
        with open(os.path.join(out, "walked", os.path.basename(f)), "wb") as fh:
            pickle.dump(kept, fh, protocol=pickle.HIGHEST_PROTOCOL)
        del p
    merged = _merge(out, meta, {k: n.branches for k, n in nodes.items()})
    for f, n in merged.items():  # the reduction balances its blocks by each part's paths after the merge
        cost[f] = (n, cost[f][1])
    ctl["walked"], ctl["paths"], ctl["part_cost"] = total, sum(merged.values()), cost
    import numpy as np  # the tree's per-day range of cumulative event cash on each path's draws (_Walk.emit)
    rngs = [h["ev_range"] for h in heads if h.get("ev_range") is not None]
    ctl["ev_range"] = (np.minimum.reduce([r[0] for r in rngs]), np.maximum.reduce([r[1] for r in rngs])) if rngs \
        else None
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
    from app.disputes.forecast import merge_equivalent

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
    counts = {}
    for f in sorted({m[2] for m in meta}):
        with open(os.path.join(out, "walked", f), "rb") as fh:
            kept = pickle.load(fh)
        rows = []
        for k in by_home.get(f, ()):
            g = groups[k]
            members = [kept[i] if gf == f else away[(gf, i)] for _e, gf, i in g]
            rows += [(g[0][0], q) for q in merge_equivalent(members, [k] * len(members), branches)]
        rows.sort(key=lambda r: r[0])  # stable: a group's subgroups keep merge_equivalent's order
        with open(os.path.join(out, "paths", f), "wb") as fh:
            pickle.dump([q for _e, q in rows], fh, protocol=pickle.HIGHEST_PROTOCOL)
        counts[f] = len(rows)
    for f in counts:
        os.remove(os.path.join(out, "walked", f))
    return counts


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


def judge(run_id: str, states: str, control_file: str, count_only: bool = False) -> None:
    """Jev's answer to every question the facts stage built (states/**/states*.json.gz), asked on this machine as
    `Forecaster.judge` asks (DisputeProfile.forecast; Jev's cache first). Writes runs/recorded/<run>/
    tree_answers[-<variant>].json (each question's distribution, and the classes live on no path: the reduction's
    input) and tree_judgments[-<variant>].json.gz (each Judgment, for the page). A question whose state did not build
    stops it. count_only: the questions by type, nothing asked."""
    import asyncio
    import collections
    import dataclasses

    from app.disputes.forecast import Judgment, answer_distribution

    t0 = time.time()
    got, errors, dead = {}, {}, set()
    for f in sorted(glob.glob(os.path.join(states, "**", "states*.json.gz"), recursive=True)):
        with gzip.open(f, "rt") as fh:
            x = json.load(fh)
        got.update(x["states"])
        errors.update(x["errors"])
        dead |= set(x.get("never_live", ()))
    if errors:
        raise SystemExit(f"judge: {len(errors)} question states did not build, e.g. {next(iter(errors.items()))}")
    with open(control_file, "rb") as fh:
        nodes = pickle.load(fh)["nodes"]
    by_type = collections.Counter(nodes[k].node for k in got)
    print(f"{time.time() - t0:7.0f}s judge: {len(got)} questions, {len(dead)} classes live on no path; by type "
          f"{dict(sorted(by_type.items()))}", file=sys.stderr, flush=True)
    if count_only:
        return
    from app.agent.jev import JevAdapter
    from app.agent.jev_profiles import DisputeProfile
    from app.analysis.build import VAR

    records: list = []
    jev = JevAdapter(run_id=f"{run_id}-analysis", use_cache=True)
    prof = DisputeProfile(jev, lambda kind, obj: records.append({"kind": kind, **obj.model_dump(mode="json")}))

    async def one(k: str) -> Judgment:
        n, x = nodes[k], got[k]
        st, fids = x["state"], tuple(x["fids"])
        o = await prof.forecast(n.question_id, st, (n.instance_id, *fids), n.branches)
        return Judgment(key=k, instance_id=n.instance_id, node=n.node, question_id=n.question_id, event=n.event,
                        assumptions=n.assumptions, window=n.window, distribution=answer_distribution(k, n.branches, o),
                        confidence=o.confidence, finding_ids=fids, readings=x["readings"],
                        evidence=st["evidence"] if "evidence" in st else [
                            e for kk in ("historical_evidence", "party_assertions", "court_findings") for e in st[kk]],
                        observation_id=o.observation_id, path_facts=st.get("path_facts", st.get("situation")))

    async def every() -> list:
        return await asyncio.gather(*(one(k) for k in sorted(got)))

    js = asyncio.run(every())
    variant = os.environ.get("SLOPE_VARIANT", "")
    out = Path("runs/recorded") / run_id
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

"""Pass 1's Python side: the case's tables for the walk (`tree.TK`) and a draw's walk run (`tree.explore`)."""
from __future__ import annotations

import numpy as np

from app.exact import fields_walk as w
from app.exact import replay, tree, walk


def case_tables(fc, d, ch0) -> tree.TK:
    """The case's tables: the verdict classes (`Forecaster.verdict_classes`, over the forecaster's draws) as encoded
    steps with their reduced bands, the question nodes' situation conditions, the offering bound, the notice lag."""
    from app.exact import vocabulary

    classes = list(fc.verdict_classes(d))
    verdicts = np.array([replay.encode(ch0, ("verdict", "I0", b)) for b in classes], dtype=np.int64)
    lower, own = lower_bands(fc, d, classes)
    _, conds, appeals, stays = vocabulary.tables(fc, d)
    ec = replay.export_equity.config(ch0)
    nc = replay.export_notes.config(ch0)
    return tree.TK(listing=ch0.fin is not None and ch0.fin.listing_deadline is not None, verdicts=verdicts,
                   lower=lower, own=own, conds=conds, bound=int(ec.bound), notice=int(nc.notice), appeals=appeals,
                   stays=stays)


def lower_bands(fc, d, classes) -> tuple[np.ndarray, np.ndarray]:
    """([classes, MAX_BANDS, 3], own[classes]): per verdict class, the bands a reduction may reach, lowest first, each
    (booked, low, high) and booked as J1b books a band (its midpoint; the top band its own amount); -1 where none
    (QUESTIONS J2). A banded award: every J1b band below its own (outright or by remittitur) and its own band (a
    remittitur inside it keeps its cash if accepted, sets the judgment aside if refused: own). An exact award (the small
    claims at their amounts, J1): every J1b band wholly below it, and the part of its own J1b band below it, booked at
    that part's midpoint; it has no band of its own to stay in (own false)."""
    bands = [b for b in fc.verdict_lines(d, fc.equity_inflows(d))["bands"] if b[1] > 0]
    out = np.full((len(classes), tree.MAX_BANDS, 3), -1, dtype=np.int64)
    own = np.zeros(len(classes), dtype=np.bool_)
    for i, c in enumerate(classes):
        if not c.startswith("award:"):
            continue
        _, booked_s, lo_s, hi_s = c.split(":")
        entered = int(booked_s)
        at = next(j for j, (lo, hi, _) in enumerate(bands) if lo < entered <= hi)
        if lo_s == hi_s:  # exact: the bands wholly below, then the part of its own band below it
            reach = [b for b in bands[:at]]
            lo = bands[at][0]
            if entered - 1 > lo:
                reach.append((lo, entered - 1, (lo + entered - 1) // 2))
        else:
            reach = bands[:at + 1]
            own[i] = True
        if len(reach) > tree.MAX_BANDS:
            raise ValueError(f"{len(reach)} bands a reduction may reach: more than the walk holds ({tree.MAX_BANDS})")
        for j, (lo, hi, booked) in enumerate(reach):
            out[i, j] = booked, lo, hi
    return out, own


def explore(ch0, r: int, tk: tree.TK, budget: int = 10**9, forced=(), capacity: int = 600_000, check=None):
    """Pass 1 on draw row r: (its members (`tree.Members`, the first `count` rows), occurrences, nodes, leaves,
    deepest, the watched keys read on the draw, the final cumulative event cash's range (lo, hi) per day). forced: the
    watched keys read on some draw, asked wherever they arise (`depth`)."""
    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    wk = walk.WK(listing=tk.listing)
    sk = tree.stack_for(st)
    mem = tree.members_for(capacity)
    reads = np.zeros(4096, dtype=np.int64)
    forced = np.array(sorted(forced), dtype=np.int64)
    ev_lo, ev_hi = np.zeros(ch0.N, np.int64), np.zeros(ch0.N, np.int64)
    nq, nodes, leaves, deepest, nr = tree.explore(st, k, wk, tk, ws, sk, mem, budget, forced, reads, ev_lo, ev_hi,
                                                  np.zeros(0, np.int64) if check is None else check)
    if nr > reads.size:
        raise ValueError("more watched reads than the buffer holds")
    m = int(mem.count[0])
    kept = tree.Members(None, None, None, mem.rows[:m], mem.cls[:m], mem.ids[:m], mem.ints[:m], mem.floats[:m], m)
    return kept, nq, nodes, leaves, deepest, set(reads[:nr].tolist()), (ev_lo, ev_hi)


def depth(run_draw) -> tuple[set, int]:
    """QUESTIONS §1 Depth over the draws: `run_draw(forced) -> keys read` for each draw (all draws walked by the
    caller); the watched questions read on some draw are asked on every draw where they arise, which can expose
    further reads, so the draws are walked again until no new key is read. Returns (the keys asked, rounds)."""
    forced: set = set()
    rounds = 0
    while True:
        rounds += 1
        read = run_draw(frozenset(forced))
        if read <= forced:
            return forced, rounds
        forced |= read


def questions_on(fc_all, d, draws, forced=(), log=print, shards: int = 0, out_dir=None) -> dict:
    """Pass 1 on `draws` (indices among the 512), the case's tables from the whole population (`fc_all`: the award
    partition is the case's, never a subset's): each draw's tree size, the distinct questions as the judge reads them
    (the rendered key and situation class: `question_keys`), the watched keys read (`depth`); with `shards`, each
    draw's members written to `out_dir`, filed by question as the judge reads it (`pool.render`)."""
    import time

    from app.analysis.events import Chain
    from app.exact import case, question_keys

    fc, _ = case.on_draws(tuple(draws))
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = case_tables(fc_all, d, ch0)
    qt = question_keys.case_tables(fc_all, d, ch0, tk)
    out: dict = {"draws": {}, "questions": set(), "reads": set(), "range": None}
    parts: dict = {}
    for r, draw in enumerate(draws):
        t0 = time.perf_counter()
        mem, nq, nodes, leaves, deepest, reads, (lo, hi) = explore(ch0, r, tk, forced=forced)
        out["range"] = (lo, hi) if out["range"] is None else (np.minimum(out["range"][0], lo),
                                                                np.maximum(out["range"][1], hi))
        _, first = np.unique(mem.ids[:, 1], return_index=True)
        found = set()
        for j in first:
            row, code = mem.rows[j], mem.cls[j]
            klass = "" if row[w.QN] == w.Q_VERDICT else question_keys.situation_class(row, code, qt)
            found.update((key, klass) for key in question_keys.keys(row, qt))
        out["questions"] |= found
        out["reads"] |= reads
        if shards:  # filed by question as the judge reads it (`pool.render`): a reduce line reads only its own
            from app.exact import pool

            names = {int(i): pool.render(mem.rows[j], mem.cls[j], qt) for i, j in
                     zip(mem.ids[first, 1], first, strict=True)}
            member_names = np.array([pool.question_id(names[int(i)]) for i in mem.ids[:, 1]], dtype=np.int64)
            shard = np.array([pool.shard_of_question(names[int(i)], shards) for i in mem.ids[:, 1]], dtype=np.int64)
            for s in range(shards):
                sel = shard == s
                parts.setdefault(s, []).append((np.full(int(sel.sum()), int(draw), np.int16), mem.rows[sel],
                                                mem.cls[sel], mem.ids[sel], mem.ints[sel], mem.floats[sel],
                                                member_names[sel]))
        out["draws"][int(draw)] = {"nodes": nodes, "leaves": leaves, "deepest": deepest, "occurrences": nq,
                                   "members": mem.count, "questions": len(found),
                                   "seconds": round(time.perf_counter() - t0, 1)}
        log(f"draw {draw}: {out['draws'][int(draw)]}")
    if shards:
        from pathlib import Path

        for s, ps in parts.items():
            cols = [np.concatenate(c) for c in zip(*ps, strict=True)]
            np.savez_compressed(Path(out_dir) / f"questions-{s:03d}.pkl.npz", draw=cols[0], rows=cols[1], cls=cols[2],
                                ids=cols[3], ints=cols[4], floats=cols[5], question=cols[6])
    return out


def shard_of(ids: np.ndarray, shards: int) -> np.ndarray:
    """A question identity's shard (all its members, from every draw, go to the same reduce line)."""
    return (ids.astype(np.uint64) % np.uint64(shards)).astype(np.int64)


def main(argv=None) -> None:
    """`python -m app.exact.pass1 <first> <last> <out dir> [<shards> [<forced keys file>]]`: pass 1 on draws first..last
    (inclusive), its summary to <out dir>/pass1-<first>.json, the range of the final cumulative event cash over its
    draws (what pass 2's bins span: `pass2.bins`) to <out dir>/range-<first>.npz and, with shards, its members filed by
    question under `pool.HEAVY` (kept on GitHub)."""
    import json
    import sys
    from pathlib import Path

    from app.exact import case

    args = argv or sys.argv[1:]
    first, last, out_dir = int(args[0]), int(args[1]), Path(args[2])
    shards = int(args[3]) if len(args) > 3 else 0
    forced = set(json.loads(Path(args[4]).read_text())) if len(args) > 4 else set()
    from app.exact.pool import HEAVY

    out_dir.mkdir(parents=True, exist_ok=True)
    HEAVY.mkdir(parents=True, exist_ok=True)
    fc_all, d = case.forecaster()
    got = questions_on(fc_all, d, range(first, last + 1), forced=forced, log=lambda s: print(s, flush=True),
                       shards=shards, out_dir=HEAVY)  # the members stay on GitHub (`heavy-*`); the summary comes back
    np.savez(out_dir / f"range-{first:03d}.npz", lo=got["range"][0], hi=got["range"][1])
    (out_dir / f"pass1-{first:03d}.json").write_text(json.dumps(
        {"draws": got["draws"], "questions": sorted(got["questions"]), "reads": sorted(got["reads"]),
         "forced": sorted(forced)}))


if __name__ == "__main__":
    main()

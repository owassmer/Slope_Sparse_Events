"""The judge's input from the dated walk (design/002-exact-evaluator.md, The judge's input): each distinct question's
members (its records on the draws where it arises, `tree.take`) pooled into the rows the 14 May state reads, and its
state built by `state14` through the Forecaster (`Forecaster.state`), unchanged: the representative trajectory's joint
facts and the members' range beside each figure.

Reduce (`python -m app.exact.pool <shard> <shards> <map dir> <out>`): every map line's members of one shard, grouped by
question identity; per question its node (the walk's key, its class as the class tag, a grouped question's own
answers) and its state, written as one JSON line.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.analysis.events import BIG
from app.disputes.forecast import CLASS_TAG
from app.exact import decode, question_keys
from app.exact import fields_walk as w


def _stack(values: list):
    """Members' decoded values stacked into the one-row form the state reads: arrays concatenated; a value the same on
    every member kept as it is; else one value per member."""
    first = values[0]
    if isinstance(first, dict):
        keys = set().union(*(v.keys() for v in values))
        if any(set(v) != keys for v in values):
            raise ValueError(f"members' records differ in their fields: {sorted(keys)}")
        return {k: _stack([v[k] for v in values]) for k in first}
    if isinstance(first, np.ndarray):
        return np.concatenate(values)
    if all(v is None for v in values):
        return None
    if all(v == first for v in values):
        return first
    out = np.empty(len(values), dtype=object)
    out[:] = values
    return out


def _offerings(members: list[dict]) -> list:
    """The offering attempts on each member, padded to the most any member has with attempts that did not happen on
    it (initiated never: `state14` reads the representative's, dated before its decision)."""
    most = max(len(m["sit"]["offerings"]) for m in members)
    out = []
    for i in range(most):
        cols = [[], [], []]
        for m in members:
            got = m["sit"]["offerings"]
            a, c, ok = got[i] if i < len(got) else (np.array([BIG]), np.array([BIG]), np.array([False]))
            cols[0].append(a)
            cols[1].append(c)
            cols[2].append(ok)
        out.append(tuple(np.concatenate(x) for x in cols))
    return out


def rows_of(ints: np.ndarray, floats: np.ndarray) -> dict:
    """A question's members (record integers [M, NI] and floats [M, NF]) as one row of M trajectories."""
    members = [decode.record(ints[i], floats[i]) for i in range(len(ints))]
    offers = _offerings(members)
    for m in members:
        m["sit"]["offerings"] = []
    row = _stack(members)
    row["sit"]["offerings"] = offers
    return row


def node_for(fc, d, key: str, q: np.ndarray, klass: str) -> str:
    """The Forecaster's node for a question (its key; its class as the class tag; a grouped question with its group's
    own answers, `Forecaster.class_key`): its node key."""
    name, *ctx = key.split(":", 1)[1].split("|")
    base = fc.node(d, name, *ctx)
    if not klass:
        return base
    if q[w.QGROUP] >= 0:
        fc.grouped.add(base)
    return fc.class_key(base, klass if klass.startswith(CLASS_TAG) else CLASS_TAG + klass)


def states_of(fc, d, q: np.ndarray, klass: str, ints: np.ndarray, floats: np.ndarray, qt) -> list[tuple[str, dict]]:
    """A question's node key and state, from its members: one, or the verdict form's questions (each its own key,
    read on the same members)."""
    row = rows_of(ints, floats)
    if q[w.QN] in (w.Q_PETITION_NOTES, w.Q_HOLDERS_INV):  # the notes question's own context (`notes.record_row`)
        context = next((p[3:] for p in klass.split(".") if p.startswith("ctx")), "")
        row["note_context"] = np.full(len(ints), bytes.fromhex(context).decode(), dtype=object)
    out = []
    for key in question_keys.keys(q, qt):
        node = node_for(fc, d, key, q, klass)
        fc.facts[node] = [row]
        st, _, _ = fc.state(fc.nodes[node])
        out.append((node, st))
    return out


def load(map_dir: Path, shard: int) -> dict[str, np.ndarray]:
    """Every map line's members of one shard."""
    parts = [np.load(p) for p in sorted(Path(map_dir).rglob(f"members-{shard:03d}.npz"))]
    if not parts:
        raise FileNotFoundError(f"no members of shard {shard} under {map_dir}")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0].files}


def reduce(fc, d, qt, members: dict[str, np.ndarray], out, log=print) -> int:
    """Each question of the members (by identity): its state, one JSON line to `out`. Returns how many."""
    ids = members["ids"][:, 1]
    order = np.argsort(ids, kind="stable")
    bounds = np.flatnonzero(np.diff(ids[order])) + 1
    count = 0
    for sel in np.split(order, bounds):
        q, code = members["rows"][sel[0]], members["cls"][sel[0]]
        klass = "" if q[w.QN] == w.Q_VERDICT else question_keys.situation_class(q, code, qt)
        for key, st in states_of(fc, d, q, klass, members["ints"][sel], members["floats"][sel], qt):
            n = fc.nodes[key]
            out.write(json.dumps({"id": int(ids[sel[0]]), "key": key, "question_id": n.question_id,
                                  "branches": list(n.branches), "members": len(sel),
                                  "draws": int(np.unique(members["draw"][sel]).size), "state": st},
                                 default=str) + "\n")
            count += 1
    log(f"{count} questions")
    return count


def context():
    """The Forecaster of the case on its whole population, reading the run's evidence (the judge's states cite it),
    and the question rendering tables."""
    from app.analysis.events import Chain
    from app.exact import oracle, pass1

    fc, d = oracle._forecaster()
    fc.hydrate = oracle._run_context()[0]["hydrate"]
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = pass1.case_tables(fc, d, ch0)
    return fc, d, question_keys.case_tables(fc, d, ch0, tk)


def main(argv=None) -> None:
    """`python -m app.exact.pool <shard> <map dir> <out.jsonl>`: the states of one shard's questions."""
    import sys

    args = argv or sys.argv[1:]
    shard, map_dir, out = int(args[0]), Path(args[1]), Path(args[2])
    fc, d, qt = context()
    members = load(map_dir, shard)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        reduce(fc, d, qt, members, fh, log=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()

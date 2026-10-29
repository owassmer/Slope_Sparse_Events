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


ENFORCEABLE = "the judgment is enforceable, unstayed and unpaid"
NONPAYMENT = "the notes are due and unpaid under Indenture §7.02 on the general nonpayment"
RESPONSE_WHEN = {"post": ("the creditor levies on the company's cash that day",),
                 "ripe": ("the judgment default under the notes has ripened that day",),
                 "entry": ("the money judgment was entered that day, unpaid; execution is stayed automatically for "
                           "its first 30 days (Fed. R. Civ. P. 62(a))",)}


def assumptions(fc, name: str, ctx: list[str]) -> tuple[str, ...]:
    """What a question is asked on (each `_Walk` constructor's `assumptions`: the proposition it conditions on, set by
    code), by its node and context."""
    from app.analysis.events import settlement_terms
    from app.disputes.forecast import ASSUMED

    first = ctx[0] if ctx else ""
    if name == "settlement_accept":
        mode, count = settlement_terms(fc.m, fc.sens)
        return ("the company offers to settle for its available cash above its 30-day operating need" + (
            f", paid in {count} equal monthly installments from the settlement date" if mode == "installments" else ""),)
    if name == "judgment_response":
        return (() if first == "entry" else (ENFORCEABLE,)) + RESPONSE_WHEN.get(first, ())
    if name in ("petition_on_notes", "holders_involuntary"):
        issuer = name == "petition_on_notes"
        if first.startswith("judgment_"):
            return ("the holders accelerate the notes",) if issuer else \
                ("the notes are accelerated and unpaid", "the issuer does not file")
        if first == "nonpayment":
            return (NONPAYMENT,) if issuer else (NONPAYMENT, "the issuer does not file")
        return ASSUMED[f"{name}:{first.split('_', 1)[0]}"]
    return {"post_trial_motions": ("a money judgment is entered on the verdict",),
            "execute_pre_ruling": ("post-trial motions are pending",),
            "stay_motion": ("the creditor executes before the ruling",) if first == "I1" else
            ("the final judgment is entered",),
            "stay_approved": ("the company moves for a stay",),
            "registration_early": ("the creditor executes before finality",) if first == "I1" else
            ("the creditor enforces before finality",),
            "appeal": ("a money judgment remains outstanding",),
            "enforce_after_final": (ENFORCEABLE,),
            "post_trial_ruling": ("post-trial motions are pending",),
            "remittitur_elected": ("the court orders a new trial unless the claimant accepts the reduced amount",),
            "hearing_request": ("compliance is not regained by the deadline",),
            "holders_act_delisting": ASSUMED["holders_act_delisting"]}.get(name, ())


def node_for(fc, d, key: str, q: np.ndarray, klass: str, tk=None) -> str:
    """The Forecaster's node for a question (its key and what it is asked on; its class as the class tag; a grouped
    question with its group's own answers, `Forecaster.class_key`; a remittitur with its proposed amount and band,
    `fc.remitted`): its node key."""
    name, *ctx = key.split(":", 1)[1].split("|")
    base = fc.node(d, name, *ctx, assumptions=assumptions(fc, name, ctx))
    if q[w.QN] == w.Q_REMITTITUR:
        [i] = np.flatnonzero(tk.reduced[:, 0] == q[w.QEXTRA])[:1]
        fc.remitted[base] = tuple(int(x) for x in tk.reduced[i])  # booked, low, high
    if not klass:
        return base
    if q[w.QGROUP] >= 0:
        fc.grouped.add(base)
    key = fc.class_key(base, klass if klass.startswith(CLASS_TAG) else CLASS_TAG + klass)
    if base in fc.remitted:
        fc.remitted[key] = fc.remitted[base]
    return key


class _View:
    """An object as another, some attributes replaced."""

    def __init__(self, base, **over):
        self.__dict__.update(over)
        self._base = base

    def __getattr__(self, name):
        return getattr(self._base, name)


def states_of(fc, d, q: np.ndarray, klass: str, ints: np.ndarray, floats: np.ndarray, draws: np.ndarray, qt,
              tk=None) -> list[tuple[str, dict]]:
    """A question's node key and state, from its members (in a fixed order: by draw, then record), each read with its
    own draw's operating figures: one, or the verdict form's questions (each its own key, read on the same members).
    Every member must be one on which the question's situation holds (`Forecaster.live`)."""
    row = rows_of(ints, floats)
    if q[w.QN] in (w.Q_PETITION_NOTES, w.Q_HOLDERS_INV):  # the notes question's own context (`notes.record_row`)
        context = next((p[3:] for p in klass.split(".") if p.startswith("ctx")), "")
        row["note_context"] = np.full(len(ints), bytes.fromhex(context).decode(), dtype=object)
    real = fc.draws  # a member's position in the row reads its own draw's operating need (`state14` indexes by it)
    view = _View(real, basis=_View(real.basis, need=real.basis.need[np.asarray(draws, dtype=np.int64)]))
    out = []
    try:
        fc.draws = view
        for key in question_keys.keys(q, qt):
            node = node_for(fc, d, key, q, klass, tk)
            live = fc.live(fc.nodes[node], row)
            if not live.all():
                raise ValueError(f"{node}: {int((~live).sum())} of {len(live)} members on which the question does "
                                 "not arise")
            fc.facts[node] = [row]
            st, _, _ = fc.state(fc.nodes[node])
            out.append((node, st))
    finally:
        fc.draws = real
    return out


def load(map_dir: Path, shard: int) -> dict[str, np.ndarray]:
    """Every map line's members of one shard."""
    parts = [np.load(p) for p in sorted(Path(map_dir).rglob(f"members-{shard:03d}.npz"))]
    if not parts:
        raise FileNotFoundError(f"no members of shard {shard} under {map_dir}")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0].files}


def reduce(fc, d, qt, members: dict[str, np.ndarray], out, log=print, tk=None) -> int:
    """Each question of the members (by identity): its state, one JSON line to `out`. Returns how many. The members
    are taken by question, then draw, then record digest, so a question's state does not depend on how the map split
    them (`state14.medoid` breaks ties by order)."""
    ids = members["ids"][:, 1]
    order = np.lexsort((members["ids"][:, 0], members["draw"], ids))
    bounds = np.flatnonzero(np.diff(ids[order])) + 1
    count = 0
    for sel in np.split(order, bounds):
        q, code = members["rows"][sel[0]], members["cls"][sel[0]]
        klass = "" if q[w.QN] == w.Q_VERDICT else question_keys.situation_class(q, code, qt)
        for key, st in states_of(fc, d, q, klass, members["ints"][sel], members["floats"][sel],
                                 members["draw"][sel], qt, tk):
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
    with its verdict form (`verdict_classes`: the verdict questions and what each asks), the walk's tables and the
    question rendering tables."""
    from app.analysis.events import Chain
    from app.exact import oracle, pass1

    fc, d = oracle._forecaster()
    fc.hydrate = oracle._run_context()[0]["hydrate"]
    fc.verdict_classes(d)
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = pass1.case_tables(fc, d, ch0)
    return fc, d, question_keys.case_tables(fc, d, ch0, tk), tk


def main(argv=None) -> None:
    """`python -m app.exact.pool <shard> <map dir> <out.jsonl>`: the states of one shard's questions."""
    import sys

    args = argv or sys.argv[1:]
    shard, map_dir, out = int(args[0]), Path(args[1]), Path(args[2])
    fc, d, qt, tk = context()
    members = load(map_dir, shard)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        reduce(fc, d, qt, members, fh, log=lambda s: print(s, flush=True), tk=tk)


if __name__ == "__main__":
    main()

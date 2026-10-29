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

from app.disputes.forecast import CLASS_TAG
from app.exact import decode, question_keys
from app.exact import fields_walk as w

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
    branches = None
    if q[w.QN] == w.Q_RULING:  # a reduction is offered only where the award has a lower band (`_Walk.ruling_pending`)
        branches = ("unchanged", "reduced", "set_aside") if q[w.QNANS] == 3 else ("unchanged", "set_aside")
    base = fc.node(d, name, *ctx, assumptions=assumptions(fc, name, ctx), branches=branches)
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
              tk=None) -> list[tuple[str, dict, tuple]]:
    """A question's node key, state and the findings it cites, from its members (in a fixed order: by draw, then record), each read with its
    own draw's operating figures: one, or the verdict form's questions (each its own key, read on the same members).
    Every member must be one on which the question's situation holds (`Forecaster.live`)."""
    row = decode.rows(ints, floats)
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
            st, fids, _ = fc.state(fc.nodes[node])
            out.append((node, st, fids))
    finally:
        fc.draws = real
    return out


def render(q: np.ndarray, code: np.ndarray, qt) -> str:
    """A question as the judge reads it: its key and situation class (the verdict form: its questions' keys). Several
    of the walk's identities can render one question (the identity holds detail the question does not state): their
    members are one question's."""
    keys = question_keys.keys(q, qt)
    if q[w.QN] == w.Q_VERDICT:
        return "\t".join(sorted(keys))
    [key] = keys
    return key + "\t" + question_keys.situation_class(q, code, qt)


def _digest(rkey: str) -> bytes:
    import hashlib

    return hashlib.sha256(rkey.encode()).digest()[:8]


def shard_of_question(rkey: str, shards: int) -> int:
    return int.from_bytes(_digest(rkey), "big") % shards


def question_id(rkey: str) -> int:
    """A rendered question's 64-bit identity (its text can run to tens of thousands of characters: the verdict form)."""
    return int.from_bytes(_digest(rkey), "big", signed=True)


HEAVY = Path("var/walk-population/questions")  # the run workflow keeps `*.pkl*` there on GitHub (`heavy-*` artifacts)


def reshard(src: Path, out: Path, shards: int, qt) -> None:
    """One map line's members filed by question as rendered (`render`): `questions-<shard>.npz` holds the members of
    the questions that fall to that shard, each with its question, so a reduce line reads only its own."""
    rendered: dict[int, str] = {}
    parts: dict[int, list] = {}
    for path in sorted(Path(src).rglob("members-*.npz")):
        part = np.load(path)
        ids = part["ids"][:, 1]
        uniq, first, inverse = np.unique(ids, return_index=True, return_inverse=True)
        names = np.empty(len(uniq), dtype=object)
        for j, (i, f) in enumerate(zip(uniq, first, strict=True)):
            if int(i) not in rendered:
                rendered[int(i)] = render(part["rows"][f], part["cls"][f], qt)
            names[j] = rendered[int(i)]
        qids = np.array([question_id(x) for x in names], dtype=np.int64)[inverse]
        shard = np.array([shard_of_question(x, shards) for x in names], dtype=np.int64)[inverse]
        for s in np.unique(shard):
            sel = shard == s
            parts.setdefault(int(s), []).append({**{k: part[k][sel] for k in part.files}, "question": qids[sel]})
    Path(out).mkdir(parents=True, exist_ok=True)
    for s, ps in parts.items():
        np.savez_compressed(Path(out) / f"questions-{s:03d}.pkl.npz",
                            **{k: np.concatenate([p[k] for p in ps]) for k in ps[0]})


def load(map_dir: Path, shard: int, shards: int, qt) -> dict[str, np.ndarray]:
    """The members of the questions (as rendered: `render`) that fall to this shard, from every map line's files: each
    member once (a question, a draw and a record), with its question and the walk identity it was found under. Filed
    by question already (`reshard`, or pass 1 with `render`): only this shard's files are read."""
    filed = sorted(Path(map_dir).rglob(f"questions-{shard:03d}*.npz"))
    if filed:
        parts = [np.load(p) for p in filed]
        return _once({k: np.concatenate([p[k] for p in parts]) for k in parts[0].files})
    rendered: dict[int, str] = {}
    keep: dict[str, list] = {}
    files = sorted(Path(map_dir).rglob("members-*.npz"))
    if not files:
        raise FileNotFoundError(f"no members under {map_dir}")
    for path in files:
        part = np.load(path)
        ids = part["ids"][:, 1]
        uniq, first, inverse = np.unique(ids, return_index=True, return_inverse=True)
        mine = np.zeros(len(uniq), dtype=bool)
        names = np.empty(len(uniq), dtype=object)
        for j, (i, f) in enumerate(zip(uniq, first, strict=True)):
            i = int(i)
            if i not in rendered:
                rendered[i] = render(part["rows"][f], part["cls"][f], qt)
            names[j] = question_id(rendered[i])
            mine[j] = shard_of_question(rendered[i], shards) == shard
        sel = mine[inverse]
        if sel.any():
            for k in part.files:
                keep.setdefault(k, []).append(part[k][sel])
            keep.setdefault("question", []).append(names[inverse][sel].astype(np.int64))
    return _once({k: np.concatenate(v) for k, v in keep.items()})


def _once(out: dict) -> dict:
    """Each member once (a record reached under two of the walk's identities of one question is one situation), in
    the order the reduce takes them: by question, then draw, then record digest."""
    order = np.lexsort((out["ids"][:, 0], out["draw"], out["question"]))
    q, dr, dg = out["question"][order], out["draw"][order], out["ids"][order, 0]
    first = np.ones(len(order), dtype=bool)
    first[1:] = (q[1:] != q[:-1]) | (dr[1:] != dr[:-1]) | (dg[1:] != dg[:-1])
    return {k: v[order][first] for k, v in out.items()}


def reduce(fc, d, qt, members: dict[str, np.ndarray], out, log=print, tk=None, shared: dict | None = None) -> int:
    """Each question of the members (as rendered: `load`): its state, one JSON line to `out`: the walk identities it
    answers for (pass 2 reads its answer under each), its own parts (its question and situation) and, by its digest,
    its shared parts (the case, the standard, the record items and the evidence, the same for every question about one
    decision: kept once in `shared`). Returns how many lines. The members come by question, then draw, then record
    digest (`load`), so a question's state does not depend on how the map split them (`state14.medoid` breaks ties by
    order)."""
    from app.agent.decisions import split_state
    from app.agent.jev import canonical_sha256

    shared = {} if shared is None else shared
    names = members["question"]
    bounds = np.flatnonzero(names[1:] != names[:-1]) + 1
    count = 0
    for sel in np.split(np.arange(len(names)), bounds):
        q, code = members["rows"][sel[0]], members["cls"][sel[0]]
        klass = "" if q[w.QN] == w.Q_VERDICT else question_keys.situation_class(q, code, qt)
        ids = sorted({int(x) for x in members["ids"][sel, 1]})
        for key, st, fids in states_of(fc, d, q, klass, members["ints"][sel], members["floats"][sel],
                                       members["draw"][sel], qt, tk):
            n = fc.nodes[key]
            common, own = split_state(json.loads(json.dumps(st, default=str)))
            digest = canonical_sha256(common)
            shared.setdefault(digest, common)
            out.write(json.dumps({"ids": ids, "key": key, "question_id": n.question_id,
                                  "instance_id": n.instance_id, "findings": list(fids), "verdict": klass == "",
                                  "branches": list(n.branches), "answers": int(q[w.QNANS]), "members": len(sel),
                                  "draws": int(np.unique(members["draw"][sel]).size), "shared": digest,
                                  "order": list(st), "own": own}) + "\n")
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
    """`python -m app.exact.pool <shard> <shards> <map dir> <out.jsonl>`: the states of one shard's questions."""
    import sys

    args = argv or sys.argv[1:]
    if args[0] == "reshard":  # reshard <src> <out> <shards>
        _, _, qt, _ = context()
        reshard(Path(args[1]), Path(args[2]), int(args[3]), qt)
        return
    shard, shards, map_dir, out = int(args[0]), int(args[1]), Path(args[2]), Path(args[3])
    fc, d, qt, tk = context()
    members = load(map_dir, shard, shards, qt)
    out.parent.mkdir(parents=True, exist_ok=True)
    shared: dict = {}
    with out.open("w") as fh:
        reduce(fc, d, qt, members, fh, log=lambda s: print(s, flush=True), tk=tk, shared=shared)
    out.with_suffix(".shared.json").write_text(json.dumps(shared))


def state(line: dict, shared: dict) -> dict:
    """A question's state from its reduce line and the shared parts, its keys in the order the builder wrote them."""
    parts = {**shared[line["shared"]], **line["own"]}
    return {k: parts[k] for k in line["order"]}


if __name__ == "__main__":
    main()

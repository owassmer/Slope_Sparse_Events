"""The walk never skips a decision, checked on a walk's own emitted histories (no second walker as reference).

uv run python tests/walk_order.py ROOT        (walks the chronological walk from the saved root, then checks)
uv run python tests/walk_order.py ROOT --saved=PATH  (checks a saved walk's histories, e.g. 001-1f's pickles)

For each history, on the root's draw:
- its dated facts are the plain engine's replay of the whole history from scratch (no prefix cache, no sharing);
- the decisions pending at each point come from the question rules below (which answer opens which decision),
  written here independently of the walk's continuations; listing, delisting and distress follow from the steps;
- at every decision it asks, the 001-1e frontier (Chain.next_decisions) on the history's state before that answer,
  with every booking dated before the decision made, must report no other pending decision dated earlier (same-day
  ties in the engine's order) that is due. Due: a probe replay of the history so far plus that decision dates it
  inside the horizon, before any petition, and offers it (a settlement amount above zero, an option group);
- after its last decision, no pending decision may be due inside the horizon.
A pending decision the probe shows is not due is resolved as the walk resolves it without a question (a settlement
with no amount opens what its 'no' opens). Synthetic: no model call, no judgment.
"""
import json
import pickle
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import akoustis_20240514_fixture as fx
import numpy as np
from benchmark_chronological import root

from app.analysis.events import GROUPED, Chain, Draws, Trace, canon, event_trace, plain
from app.analysis.frontier import Cursors, Decision
from app.disputes.forecast import DisputePath, Forecaster, path_mask

RESP = "judgment_response"
# what an answer opens next (the question rules as _Walk.post/appeal/stay_post/stayed_tail/ruling_pending walk them)
OPENS = {("post_trial_ruling", "unchanged"): (Decision("settle", "I2"), Decision(RESP, "post")),
         ("post_trial_ruling", "reduced"): (Decision("judgment_default", "ruling"), Decision("settle", "I2"),
                                            Decision(RESP, "post")),
         ("settle:I2", "no"): (Decision("appeal"),),
         ("appeal", "*"): (Decision("settle", "I4"),),  # the comparison roots are stayed before the ruling
         ("settle:I4", "no"): (Decision("judgment_default", "post"),)}
# answers that end the dispute's open decisions (a set-aside, an agreed settlement, a payment, a filing)
CLOSES = {("post_trial_ruling", "set_aside"), ("settle:I2", "yes"), ("settle:I4", "yes"), (RESP, "pay")}
PROBE = {"settle": "no", RESP: "none", "post_trial_ruling": "unchanged", "appeal": "no", "judgment_default": "no",
         "cash_floor": "neither", "cash_out": "neither", "nonpayment": "due", "listing_date": "",
         "delisting_notes": "none"}


@dataclass
class State:
    pending: list = field(default_factory=list)  # legal and notes decisions open, in the walk's cursor order
    a4: str = "seek"
    listed: bool = False  # the listing decision is resolved (asked, or not due)
    suspended: bool = False
    delisting: bool = False
    floor: int = 1
    out: bool = False
    nonpayment: bool = False

    def answer(self, node, ctx, b):
        d = Decision(node, ctx)
        if d in self.pending:
            self.pending.remove(d)
        key = f"{node}:{ctx}" if node == "settle" else node
        kind = b.split(":")[0]
        if (key, kind) in CLOSES or (node == RESP and b == "file" and ctx != "ripe"):
            self.pending.clear()
            return
        if node == RESP:
            self.a4 = "seek" if b in ("none", "initiate_offering") else "closed"
        for o in OPENS.get((key, kind), OPENS.get((key, "*"), ())):
            if o not in self.pending:
                self.pending.append(o)
        if node == "listing":
            self.listed, self.suspended = True, b == "suspended"
        elif node == "delisting_notes":
            self.delisting = True
        elif node == "cash_floor":
            self.floor = max(self.floor, int(ctx) + 1)
        elif node == "cash_out":
            self.out = True
        elif node == "nonpayment":
            self.nonpayment = True

    def absent(self, d):
        """Resolved without a question: not due where its rules would ask it (the walk books no step)."""
        if d.node == "settle":
            return self.answer("settle", d.ctx, "no")
        if d.node == "listing_date":
            self.listed = True
        elif d in self.pending:
            self.pending.remove(d)
        else:
            self.answer(d.node, d.ctx, PROBE[d.node])

    def cursors(self, has_listing):
        legal, notes = [], []
        for d in self.pending:
            if d.node == RESP and (self.a4 == "closed" or (d.ctx == "ripe" and self.a4 != "seek")):
                continue
            (notes if d.node == "judgment_default" or d.ctx == "ripe" else legal).append(d)
        listing = [Decision("listing_date", "compliance")] if has_listing and not self.listed else []
        if self.suspended and not self.delisting:
            notes.append(Decision("delisting_notes", "delisted_suspension"))
        distress = [Decision("cash_floor", str(self.floor))]
        if not self.out:
            distress.append(Decision("cash_out"))
        elif not self.nonpayment:
            distress.append(Decision("nonpayment"))
        return Cursors(tuple(legal), tuple(distress), tuple(listing), tuple(notes))


def _at(a, row, n):
    a = np.asarray(a)
    return a[row] if a.ndim and len(a) == n else (a[0] if a.ndim else a)


class Checker:
    def __init__(self, fc, d, row, prefix):
        self.fc, self.d, self.row, self.prefix = fc, d, row, prefix
        self.n, self.N = fc.draws.n, fc.days
        self.draws = Draws(fc.draws.n, basis=fc.draws.basis)  # prefixes None: every replay from scratch
        self.mask = np.arange(self.n) == row
        self.listing = any(f.status != "superseded" and f.listing_deadline is not None for f in d.financing)
        self._probes = {}

    def replay(self, steps, day_only=False):
        path = DisputePath(instance_id=self.d.instance_id, steps=tuple(steps), outcome="", edges=())
        return event_trace(self.d, path, self.fc.setup, self.fc.m, self.draws, self.fc.sens, day_only=day_only,
                           light=True, rows=tuple(self.mask for _ in steps))

    def due(self, steps, d):
        """(day, due) of decision d after `steps` by a probe replay: inside the horizon, before any petition, and
        offered (a settlement amount above zero; a grouped question's option group)."""
        probe = canon(tuple(steps)) + ((d.node, d.ctx, PROBE[d.node]),)
        hit = self._probes.get(probe)
        if hit is None:
            tr = self.replay(probe, day_only=True)
            row, n = self.row, self.n
            day = int(_at(tr.day[-1], row, n))
            pet = int(_at(tr.events.petition, row, n))
            ok = day < self.N and (pet < 0 or day < pet)
            if d.node == "settle":
                ok &= int(_at(tr.settle_offer, row, n)) > 0
            if d.node in GROUPED and len(probe) - 1 in tr.groups:
                ok &= int(_at(tr.groups[len(probe) - 1], row, n)) >= 0
            hit = self._probes[probe] = (day, bool(ok))
        return hit

    def history(self, p):
        """Violations on one emitted history: [] when every decision is asked in date order and none is missing."""
        row, n, N = self.row, self.n, self.N
        tr = self.replay(p.steps)
        days = [int(_at(x, row, n)) for x in tr.day]
        pet = int(_at(tr.events.petition, row, n))
        bound = N if pet < 0 else min(N, pet)
        ch = Chain(self.d, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens)
        ch.instrument_cash()
        ch = ch.sliced(np.array([row]), self.fc.draws.sub(self.mask))
        t = Trace(ch.ev)
        st = State()
        answered = {s[:2] for s in p.steps[:self.prefix]}
        st.pending = [x for x in (Decision("post_trial_ruling"), Decision(RESP, "I1"), Decision(RESP, "ripe"),
                                  Decision("judgment_default", "I1")) if (x.node, x.ctx) not in answered]
        st.floor = 1 + max((int(c) for nd, c, _ in p.steps[:self.prefix] if nd == "cash_floor"), default=0)
        for s in p.steps[:self.prefix]:
            ch.advance(t, *s)
        bad = []
        last = -1
        for i in range(self.prefix, len(p.steps) + 1):
            if i < len(p.steps):
                node, ctx, b = p.steps[i]
                if node == "offering":  # the offering's outcome, answered with the initiation that precedes it
                    ch.advance(t, *p.steps[i])
                    continue
                at = days[i]
                me = Decision("listing_date", "compliance") if node == "listing" else Decision(node, ctx)
                quiet = b.startswith("@-1=")
                if quiet or at >= bound:  # walked, not asked on this draw: it must not be due there
                    group = tr.groups.get(i)
                    asked = at < bound and (group is None or int(_at(group, row, n)) >= 0)
                    if asked:
                        bad.append(dict(kind="unasked_step", step=list(p.steps[i]), day=at))
                    if quiet and me in st.pending:  # the response stays as it stood (no answer, no a4 change)
                        st.pending.remove(me)
                    elif not quiet:
                        st.answer(node, ctx, plain(b))
                    ch.advance(t, *p.steps[i])
                    continue
                if at < last:
                    bad.append(dict(kind="out_of_order", step=list(p.steps[i]), day=at, after=last))
            else:
                at, me = bound, None
            later = set()  # due, but its probe dates it after this decision (a prospective date moved)
            for _ in range(32):  # resolve, as the walk does, the decisions found not due
                view = ch.clone()
                view.until(np.array([at], dtype=np.int64))  # every booking dated before this decision
                f = view.next_decisions(st.cursors(self.listing))
                mine = next((c for c in f.candidates if c.decision == me), None)
                rank = (at, 9, 1 << 30) if mine is None else (at, mine.phase, mine.order)
                early = [c for c in f.candidates if c.chain != "deterministic" and c.decision != me
                         and c.decision not in later
                         and (int(c.day[0]), c.phase, c.order) < rank and int(c.day[0]) < bound]
                if not early:
                    break
                c = min(early, key=lambda c: (int(c.day[0]), c.phase, c.order))
                day, ok = self.due(p.steps[:i], c.decision)
                if ok and (day < at or (day == at and me is not None)):
                    bad.append(dict(kind="missing" if me is None else "skipped", decision=[c.decision.node,
                                    c.decision.ctx], day=day, before=None if me is None else list(p.steps[i]),
                                    at=None if me is None else at))
                elif ok:
                    later.add(c.decision)
                    continue
                st.absent(c.decision)
            if me is None:
                break
            if mine is None and me.node not in ("cash_floor", "cash_out", "nonpayment", "listing", "delisting_notes"):
                bad.append(dict(kind="not_open", step=list(p.steps[i]), day=at, open=[[x.node, x.ctx]
                                                                                       for x in st.pending]))
            st.answer(node, ctx, plain(b))
            ch.advance(t, *p.steps[i])
            last = at
        return bad


class FutureChecker:
    """§1: an emitted question must be reconstructible without later answers.

    `records` is the recording-boundary sidecar from question_history, not a
    second walk. Early records identify their probe prefix; completed records
    identify their entire emitted history. Same-day steps retain engine order.
    Cached replays are shared only for identical histories/probes on this draw.
    Unknown/unopened contexts are reported, never replaced with a guessed state.
    """

    def __init__(self, fc, d, row, records):
        from collections import defaultdict

        self.fc, self.d, self.row = fc, d, row
        self.replayer = Checker(fc, d, row, 0)
        from copy import copy

        self.replayer.draws = copy(fc.draws)  # preserve native keyed uniforms, including SubDraws
        self.replayer.draws.prefixes = None
        self.records = defaultdict(list)
        for (key, prefix, completed), blobs in records.items():
            self.records[key].append((prefix, completed, blobs))
        self.cache, self.rebuilt_cache = {}, {}
        self.checked = {}

    def snapshot(self, key, steps, index, probe, deferred):
        identity = key, steps, index, probe, deferred
        if identity not in self.rebuilt_cache:
            if len(self.rebuilt_cache) >= 1024:
                self.rebuilt_cache.pop(next(iter(self.rebuilt_cache)))
            self.rebuilt_cache[identity] = self.rebuild(key, steps, index, probe, deferred)
        return self.rebuilt_cache[identity]

    def local(self, value):
        """Select this native draw; keep typed missing values and exact cents."""
        if isinstance(value, np.ndarray):
            selected = value[self.row] if value.ndim and len(value) == self.fc.draws.n else value
            return selected.tolist() if hasattr(selected, 'tolist') else selected
        if isinstance(value, dict):
            return {k: self.local(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.local(v) for v in value]
        if isinstance(value, np.generic):
            return value.item()
        return value

    def facts(self, row):
        from app.analysis.events import BIG
        from app.disputes.forecast import pack_row, unpack_row

        # Canonicalize absence on this draw, not on its original pooled group.
        # A trigger BIG here may have been retained because another draw had it.
        one = {**row, 'day': np.where(self.replayer.mask, row['day'], BIG)}
        return self.local(unpack_row(pack_row(one)))

    def rebuild(self, key, steps, index, probe, deferred):
        from app.disputes.forecast import _Walk, as_of, group_classes
        from app.disputes.notes import NAMES, decision_row, record_row

        fc, d = self.fc, self.d
        base = key.split('|#', 1)[0]
        node = fc.nodes[base]
        before = steps[:index] + steps[index + 1:]  # every retained earlier-date event, not just traversal prefix
        verdict = next((b for n, _, b in before if n == 'verdict'), '')
        ruling = next((b for n, _, b in before if n == 'post_trial_ruling'), '')
        label = 'award' + verdict.split(':')[1] if verdict.startswith('award:') else verdict or 'claimed'
        if ruling.startswith('reduced:'):
            label = 'reduced' + ruling.split(':')[1]
        elif ruling == 'set_aside':
            label = 'set_aside'
        walk = _Walk(fc, d)
        # Contexts are declarations, not facts supplied by the replay. Rebuild
        # their amount/stage labels too: replaying an unchanged I1 key would
        # silently preserve the very future-motion dependency being tested.
        ctx = node.context.split('|') if node.context else []
        ctx = [label if c.startswith(('award', 'reduced')) or c in ('claimed', 'set_aside') else c for c in ctx]
        if node.node == 'judgment_response' and any(c in ('first', 'after_none', 'after_offer') for c in ctx):
            dated = self.replayer.replay(steps, day_only=True)
            own_day = int(_at(dated.day[index], self.row, fc.draws.n))
            responses = [(int(_at(dated.day[i], self.row, fc.draws.n)), i, plain(b))
                         for i, (n, _, b) in enumerate(steps) if i != index and n == 'judgment_response'
                         and not b.startswith('@-1=')
                         and (i < index or int(_at(dated.day[i], self.row, fc.draws.n)) < own_day)]
            answer = max(responses)[2] if responses else None
            after = 'after_offer' if answer == 'initiate_offering' else 'after_none' if answer == 'none' else 'first'
            ctx = [after if c in ('first', 'after_none', 'after_offer') else c for c in ctx]
        if node.node == 'enforce_after_final':
            appealed = any(n == 'appeal' and plain(b) == 'yes' for n, _, b in before)
            ctx = [('appealed' if appealed else 'final') if c in ('appealed', 'final') else c for c in ctx]
        unopened = None
        if ctx and ctx[0] in ('I1', 'I2', 'I4'):
            motions = next((b for n, _, b in before if n == 'post_trial_motions'), None)
            if d.stage == 'liability_pending':
                if ctx[0] == 'I1' and motions != 'yes':
                    unopened = 'I1 requires the post-trial motions answer yes'
                elif ctx[0] == 'I2' and not ruling and motions != 'no':
                    unopened = 'I2 requires the motions answer no or a post-trial ruling'
                elif ctx[0] == 'I4' and not any(n == 'appeal' for n, _, _ in before):
                    unopened = 'I4 requires the appeal decision'
        # Remove historical tags and derive them anew from the dated row.
        conditions = walk.situation_conditions(node.node)
        ctx = [c for c in ctx if c not in conditions and c not in ('motions_pending', 'stay_pending', 'stay_denied')]
        rebuilt_base = fc.node(d, node.node, *ctx, assumptions=node.assumptions, branches=node.branches)
        if node.node in NAMES:
            actor = 'holders' if node.node == 'holders_involuntary' else 'issuer'
            row, tr = decision_row(fc, d, steps, index, actor, self.replayer.mask)
            got = []
            original = fc._keep_late
            fc._keep_late = lambda k, p, r: got.append((k, r))
            try:
                record_row(fc, d, steps, rebuilt_base, index, row, tr)
            finally:
                fc._keep_late = original
        else:
            completed = fc.completed_probe(base, steps, index) if deferred else None
            replay = completed if completed is not None else steps[:index] + (probe,) + steps[index + 1:]
            at = len(replay) - 1 if completed is not None else index
            path = DisputePath(self.d.instance_id, tuple(replay), '', ())
            tr = event_trace(self.d, path, fc.setup, fc.m, self.replayer.draws, fc.sens,
                             rows=tuple(self.replayer.mask for _ in replay))
            raw = tr.questions.get(at)
            if raw is None:
                raw = dict(day=tr.day[at], cash=tr.cash[at], owed=tr.owed[at], collateral=tr.collateral[at],
                           petition=tr.events.petition, settle_offer=tr.settle_offer, stay_offer=tr.stay_offer,
                           triggers=tr.triggers, raise_offer=tr.raise_offer, sit=tr.situations.get(at),
                           marks=tr.marks, groups=tr.groups.get(at))
            row = as_of(raw)
            cls = fc.dated_class(d, rebuilt_base, replay, row) if fc._classified(base) else None
            if base in fc.grouped:
                fc.grouped.add(rebuilt_base)
                cls = group_classes(cls, row['groups'])
            got = []
            fc._split((rebuilt_base,), row, lambda k, r: got.append((k, r)), cls)
        live = [(k, r) for k, r in got if fc.live(fc.nodes[k], r)[self.row]]
        if not live:
            return dict(key=None, cls=None, facts=None, unavailable='question not live after date-local replay')
        if len(live) != 1:
            raise AssertionError('multiple classes for one question/draw')
        k, r = live[0]
        return dict(key=None if unopened else k, cls=k.split('|#', 1)[1] if '|#' in k else None,
                    facts=self.facts(r), **({'unavailable': unopened} if unopened else {}))

    def history(self, p):
        from datetime import timedelta

        from app.disputes.forecast import atoms, unpack_row

        fc, row = self.fc, self.row
        mask = path_mask(p, fc.draws.n)
        if mask is not None and not mask[row]:
            return []
        classes, inactive = {}, set()
        slot = row if mask is None else int(mask[:row].sum())
        for base, tags, codes in p.classes:
            code = 0 if codes is None else int(np.frombuffer(codes, dtype=np.int8)[slot])
            if code >= 0:
                classes[base] = fc.class_key(base, tags[code])
            else:
                inactive.add(base)
        keys = {classes.get(k, k) for edge, _ in p.edges for k in atoms(edge) if k not in inactive}
        tr = self.replayer.replay(p.steps, day_only=True)
        days = [int(_at(day, row, fc.draws.n)) for day in tr.day]
        bad = []
        for key in sorted(keys):
            matches = []
            for prefix, completed, blobs in self.records.get(key, ()):
                if completed is not None:
                    if completed != p.steps:
                        continue
                    index, probe = len(prefix), p.steps[len(prefix)]
                else:
                    if not prefix or p.steps[:len(prefix) - 1] != prefix[:-1]:
                        continue
                    index, probe = len(prefix) - 1, prefix[-1]
                    if index >= len(p.steps) or p.steps[index][:2] != probe[:2]:
                        continue
                for blob in blobs:
                    recorded = unpack_row(blob)
                    if not fc.live(fc.nodes[key], recorded)[row]:
                        continue
                    matches.append((index, probe, completed is not None, blob, recorded))
            # Some edge atoms are logical/composite prerequisites, not an asked
            # event (e.g. verdict-form arithmetic); they have no dated record.
            if not matches:
                if fc.nodes[key].question_id not in fc.no_cash and ('|#' in key or not fc._classified(key)):
                    raise AssertionError(f'No recorded occurrence for emitted question {key}')
                continue
            for index, probe, deferred, blob, recorded in matches:
                name = fc.nodes[key].node
                self.checked[name] = self.checked.get(name, 0) + 1
                day = int(recorded['day'][row])
                later = [i for i, at in enumerate(days) if at > day and i != index]
                kept = tuple(s for i, s in enumerate(p.steps) if i not in later)
                own = index - sum(i < index for i in later)
                identity = (key, kept, own, probe, deferred, blob)
                if identity not in self.cache:
                    rebuilt = self.snapshot(key, kept, own, probe, deferred)
                    original = dict(key=key, cls=key.split('|#', 1)[1] if '|#' in key else None,
                                    facts=self.facts(recorded))
                    changed = [k for k in original if original[k] != rebuilt[k]]
                    if len(self.cache) >= 1024:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[identity] = (changed, rebuilt)
                changed, rebuilt = self.cache[identity]
                if changed:
                    # Deletion witnesses, not a list of unrelated future steps:
                    # each reported removal changed the reconstructed question.
                    # Reverse traversal order preserves prerequisites longest;
                    # all later steps are nevertheless removed by the end.
                    remaining = list(range(len(p.steps)))
                    previous = self.snapshot(key, p.steps, index, probe, deferred)
                    recorded_question = dict(key=key, cls=key.split('|#', 1)[1] if '|#' in key else None,
                                             facts=self.facts(recorded))
                    dependencies = []
                    for removed in reversed(later):
                        remaining.remove(removed)
                        part = tuple(p.steps[i] for i in remaining)
                        after = self.snapshot(key, part, remaining.index(index), probe, deferred)
                        delta = [k for k in changed if previous[k] == recorded_question[k]
                                 and after[k] != recorded_question[k]]
                        if delta:
                            dependencies.append(dict(step=list(p.steps[removed]), day=days[removed],
                                                     date=str(fc.review + timedelta(days=days[removed])),
                                                     changed=delta))
                        previous = after
                    bad.append(dict(kind='future_conditioning' if dependencies else 'record_replay_mismatch',
                                    question=key, node=fc.nodes[key].node, depends_on=dependencies,
                                    date=str(fc.review + timedelta(days=day)), day=day,
                                    changed=changed, later_steps=[dict(step=list(p.steps[i]), day=days[i],
                                    date=str(fc.review + timedelta(days=days[i]))) for i in later],
                                    recorded=dict(key=key, facts=self.facts(recorded)), rebuilt=rebuilt))
        return bad


def check(fc, d, out, row, prefix):
    c = Checker(fc, d, row, prefix)
    found, kinds, decisions = [], {}, {}
    for p in out:
        mask = path_mask(p, fc.draws.n)
        assert mask[row] and mask.sum() == 1
        for v in c.history(p):
            kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
            what = " ".join(v.get("decision") or v.get("step")[:2])
            decisions[what] = decisions.get(what, 0) + 1
            found.append(dict(v, steps=[list(s) for s in p.steps[prefix:]]))
    asked = {}
    for p in out:
        for node, ctx in {(n, c) for n, c, _ in p.steps[prefix:] if n == "settle"}:
            asked[f"{node} {ctx}"] = asked.get(f"{node} {ctx}", 0) + 1
    return dict(histories=len(out), violations=len(found), kinds=kinds, by_decision=decisions, settlements_asked=asked, examples=found[:20])


def main(name, saved=None, limit=None):
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import _S

    steps, row, cls = root(name)
    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon, hydrate=lambda f: {},
                    model=fx.model(), setup=fx.setup(), basis=fx.basis())
    t0 = time.perf_counter()
    if saved:
        with Path(saved).open("rb") as source:
            fc.nodes, out, _, _ = pickle.load(source)
    else:
        w = ChronologicalWalk(fc, d)
        w.run_from(_S(steps=steps, cls=cls, a4="seek", stayed=True, early=True), np.arange(fc.draws.n) == row)
        out = w.out
        Path("var/diag/001-1h").mkdir(parents=True, exist_ok=True)
        with Path(f"var/diag/001-1h/{name}-chronological.pkl").open("wb") as target:  # for --saved rechecks
            pickle.dump((fc.nodes, out, None, None), target, protocol=pickle.HIGHEST_PROTOCOL)
    walked = time.perf_counter() - t0
    out = out[:limit] if limit else out
    print(json.dumps(dict(root=name, stage="walked", histories=len(out), walk_s=round(walked, 1))), flush=True)
    t0 = time.perf_counter()
    result = dict(root=name, row=row, walk_s=round(walked, 1), **check(fc, d, out, row, len(steps)),
                  check_s=round(time.perf_counter() - t0, 1))
    folder = Path("var/diag/001-1h")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}{'-saved' if saved else ''}.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "examples"}), flush=True)
    for e in result["examples"][:5]:
        print(json.dumps(e), flush=True)
    return result


if __name__ == "__main__":
    args = sys.argv[2:]
    main(sys.argv[1], saved=next((a.split("=", 1)[1] for a in args if a.startswith("--saved=")), None),
         limit=next((int(a.split("=")[1]) for a in args if a.startswith("--limit=")), None))

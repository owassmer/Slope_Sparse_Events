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
                view.restay()
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

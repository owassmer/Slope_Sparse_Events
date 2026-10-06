"""Experimental, unmerged walk below a saved stayed-registration prefix.

Not used by Forecaster.paths. Question construction, grouping and completed-path
booking remain _Walk's; only continuation selection uses the answer-free Chain
frontier. No histories or financial states are interned.
"""
from dataclasses import replace

import numpy as np

from app.analysis.events import GROUPED, Chain, Trace, group_branches, plain
from app.analysis.frontier import Cursors, Decision
from app.disputes.forecast import _S, _Walk


class ChronologicalWalk(_Walk):
    """Local comparison walker for the saved pending-claim registration roots."""

    def run(self):
        raise NotImplementedError("use run_from(state, support) for the saved-root comparison; production is unchanged")

    def run_from(self, state: _S, support=None):
        if not self.pend or not self.fc.equity or not state.stayed:
            raise NotImplementedError("comparison roots must be stayed pending claims with equity")
        if any(st[0] == "post_trial_ruling" for st in state.steps):
            raise NotImplementedError("seed before the ruling")
        chain = Chain(self.d, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens)
        chain.instrument_cash()
        trace = Trace(chain.ev)
        for step in state.steps:
            chain.advance(trace, *step)
        answered = {st[:2] for st in state.steps}
        pending = tuple(d for d in (Decision("post_trial_ruling"), Decision(self.resp, "I1"),
                                    Decision(self.resp, "ripe"), Decision("judgment_default", "I1"))
                        if (d.node, d.ctx) not in answered)
        floors = [int(ctx) for node, ctx, _ in state.steps if node == "cash_floor"]
        state = replace(state, k=max(floors, default=0) + 1,
                        out="done" if any(st[0] == "cash_out" for st in state.steps) else state.out,
                        np="done" if any(st[0] == "nonpayment" for st in state.steps) else state.np)
        self._continuations = []
        self._population = support  # retain the root population for subsequent path replay, as _Walk does
        mask = self.mask_of(state.steps)  # the supplied history is replayed only to seed its support
        mask = np.ones(chain.n, dtype=bool) if mask is None else mask
        self.scoped(mask, lambda: self._loop(state, chain, pending, "unresolved",
                                            np.full(chain.n, -1, dtype=np.int64), mask, np.arange(chain.n)))
        return self.out

    def first(self, s, probe, then):
        return False  # the global frontier owns scheduling, not nested insertions

    def _first_listing(self, s, probe, then):
        return False

    def _resume(self, s, add=(), outcome=None, close=False):
        return self._continuations[-1](s, add, outcome, close)

    # These are continuation exits from the unchanged question constructors.
    def post(self, s):
        # A writ already queued before the ruling can levy after it. The I1
        # response then ceases to apply; its post-ruling response is still owed.
        self._resume(s, (Decision("settle", "I2"), Decision(self.resp, "post")))

    def notes_petition(self, s, phase, then):
        self._resume(s, (Decision("judgment_default", phase), Decision("settle", "I2"),
                         Decision(self.resp, "post")))

    def tail(self, s, outcome):
        self._resume(s, outcome=outcome, close=True)

    def stay_post(self, s):
        if not s.stayed:
            raise NotImplementedError("unstayed post-ruling continuation")
        self._resume(s, (Decision("settle", "I4"),))

    def _end(self, s, outcome, then):
        self._resume(s, outcome=outcome)

    def _cursors(self, s, pending):
        legal, notes = [], []
        for d in pending:
            if d.node == self.resp and (s.a4 == "closed" or (d.ctx == "ripe" and s.a4 != "seek")):
                continue
            (notes if d.node == "judgment_default" or d.ctx == "ripe" else legal).append(d)
        listing = []
        if not any(st[0] == "listing" for st in s.steps):
            if self.fin is not None and self.fin.listing_deadline is not None:
                listing.append(Decision("listing_date", "compliance"))
        elif (p := self._pending_delisting(s)) is not None:
            notes.append(Decision("delisting_notes", p[0]))
        return Cursors(tuple(legal), tuple(Decision(n, c) for n, c, _ in self._candidates(s)),
                       tuple(listing), tuple(notes))

    def _loop(self, s, chain, pending, outcome, committed, mask, rows):
        if not mask.any():
            return
        # Use the engine's existing row slicing, preserving original draw IDs.
        # Simulating the 511 unsupported draws at a one-draw root is not a fork.
        keep = mask[rows]
        if not keep.all():
            chain = chain.sliced(np.flatnonzero(keep), self.fc.draws.sub(mask))
            rows = rows[keep]
        frontier = chain.next_decisions(self._cursors(s, pending))
        for j, candidate in enumerate(frontier.candidates):
            local_on = frontier.pick == j
            if not local_on.any():
                continue
            on = np.zeros(self.fc.draws.n, dtype=bool)
            on[rows[local_on]] = True

            def selected(candidate=candidate, on=on, local_on=local_on):
                if (candidate.day[local_on] < committed[on]).any():
                    raise RuntimeError(f"decision inserted before committed frontier: {candidate.decision}; "
                                       f"day={candidate.day[local_on].tolist()}, committed={committed[on].tolist()}, "
                                       f"steps={s.steps!r}")
                boundary = committed.copy()
                boundary[on] = candidate.day[local_on]
                before = chain.clone()
                before.until(np.where(local_on, candidate.day, -1))
                before.restay()
                if candidate.chain == "deterministic":
                    before.until(np.where(local_on, candidate.day + 1, -1))
                    before.restay()
                    return self._loop(s, before, pending, outcome, boundary, on, rows)
                d = candidate.decision
                remaining = tuple(x for x in pending if x != d)

                def child(y, add=(), result=None, close=False):
                    booked = before.clone()
                    tr = Trace(booked.ev)
                    for step in y.steps[len(s.steps):]:
                        booked.advance(tr, *step)
                    booked.until(np.where(local_on, candidate.day + 1, -1))
                    booked.restay()  # as advance/finish: re-size already chosen security after waiting bookings
                    todo = () if close else remaining
                    todo = tuple(dict.fromkeys((*todo, *add)))
                    if y.steps == s.steps and todo == pending:
                        raise RuntimeError(f"selected decision made no progress: {d}")
                    # Resolve the existing grouped answer's population here,
                    # with question construction, not in frontier discovery.
                    child_mask = self.mask_of(y.steps)
                    child_mask = on if child_mask is None else on & child_mask
                    if child_mask.any():
                        self._loop(y, booked, todo, outcome if result is None else result, boundary, child_mask, rows)

                self._continuations.append(child)
                try:
                    self._ask(s, d, outcome)
                finally:
                    self._continuations.pop()

            self.scoped(on, selected)
        rest = np.zeros(self.fc.draws.n, dtype=bool)
        rest[rows[frontier.pick == -1]] = True
        if rest.any():
            result = "motions_pending" if outcome == "unresolved" and Decision("post_trial_ruling") in pending else outcome
            self.scoped(rest, lambda: self.emit(s, result))

    def equivalence(self, s, outcome, tr, mask):
        # Do not return a history whose later booking invalidates an earlier
        # offered answer (the demonstrated floor/offering failure).
        for i, (node, ctx, answer) in enumerate(s.steps):
            if node not in GROUPED:
                continue
            q = tr.questions[i]
            live = (q["day"] < self.N) & (q["groups"] >= 0)
            if mask is not None:
                live &= mask
            for code in np.unique(q["groups"][live]):
                if plain(answer) not in group_branches(node, int(code)):
                    raise RuntimeError(f"infeasible completed answer: {node}/{ctx}/{answer}, group {code}")
        return super().equivalence(s, outcome, tr, mask)

    def _ask(self, s, d, outcome):
        again = lambda y: self._resume(y)  # noqa: E731
        if d.node == self.resp:
            return _Walk.a4_grouped(self, s, d.ctx, again, again, False)
        if d.node == "post_trial_ruling":
            return _Walk.ruling_pending(self, s)
        if d.node == "judgment_default":
            return _Walk.notes_petition(self, s, d.ctx, again)
        if d.node == "settle":
            next_ = Decision("appeal") if d.ctx == "I2" else Decision("judgment_default", "post")
            return _Walk.settle(self, s, d.ctx,
                                lambda y: self._resume(y, (next_,), "stayed" if d.ctx == "I4" else None))
        if d.node == "appeal":
            return _Walk.appeal(self, s)
        if d.node == "listing_date":
            return _Walk.listing(self, s, outcome, again, defer_delisting=True)
        if d.node == "delisting_notes":
            dc, day = self._pending_delisting(s)
            return _Walk.delisting(self, s, dc, day, outcome, again)
        if d.node in ("cash_floor", "cash_out", "nonpayment"):
            return _Walk.ask_distress(self, s, (d.node, d.ctx, "due" if d.node == "nonpayment" else "neither"),
                                      outcome, again)
        raise ValueError(f"unsupported comparison decision: {d}")

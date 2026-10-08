"""Chronological walk from review or a native pending-claim prefix.

Question construction and grouping remain _Walk's; terminal booking resumes the leaf;
state-derived chain heads compete on the answer-free Chain frontier.
Forecaster.paths uses this population walk for pending claims with equity.
"""
from dataclasses import replace

import numpy as np

from app.analysis.events import BIG, GROUPED, Chain, Trace, group_branches, plain
from app.analysis.frontier import Cursors, Decision
from app.disputes.forecast import _S, _Walk, atoms


class ChronologicalWalk(_Walk):
    """Select the earliest unresolved decision on each supported draw."""

    def __init__(self, fc, dispute, *, bounds=None):
        super().__init__(fc, dispute)
        from app.disputes.recurrence import RecurrenceBounds
        self.bounds = RecurrenceBounds.from_model(fc.m, fc.sens) if bounds is None else bounds
        if bounds is not None and 'offering_initiations' in fc.m['parameters']:
            # Explicit comparison scenarios also govern the engine's answer domains.
            fc.sens = {**fc.sens, 'offering_initiations': bounds.offering or 'unbounded'}

    def offer(self, s, occasion, then):
        if self.bounds.reached('offering', s.steps):
            return  # no further initiation branch, not a failed offering
        return super().offer(s, occasion, then)

    def run(self):
        return self.run_from(_S(cls="claimed"))

    def run_from(self, state: _S, support=None):
        if not self.pend or not self.fc.equity:
            raise NotImplementedError("requires a pending claim with equity")
        chain = Chain(self.d, self.fc.setup, self.fc.m, self.fc.draws, self.fc.sens)
        chain.capture_questions = True  # preserve before-answer facts for terminal emission, not final-state guesses
        chain.instrument_cash()
        trace = Trace(chain.ev)
        for step in state.steps:
            chain.advance(trace, *step)
        pending = frozenset()  # decisions resolved without a booked step
        floors = [int(ctx) for node, ctx, _ in state.steps if node == "cash_floor"]
        state = replace(state, k=max(floors, default=0) + 1,
                        out="done" if any(st[0] == "cash_out" for st in state.steps) else state.out,
                        np="done" if any(st[0] == "nonpayment" for st in state.steps) else state.np)
        self._continuations = []
        self._dated_records = ()
        self._dated_classes = {}
        self._question_queue = ()
        self._population = support  # retain the root population for subsequent path replay, as _Walk does
        mask = self.mask_of(state.steps)  # the supplied history is replayed only to seed its support
        mask = np.ones(chain.n, dtype=bool) if mask is None else mask
        self.scoped(mask, lambda: self._loop(state, chain, pending, self._outcome(state),
                                            np.full(chain.n, -1, dtype=np.int64), mask, np.arange(chain.n)))
        return self.out

    def first(self, s, probe, then):
        return False  # the global frontier owns scheduling, not nested insertions

    def _first_listing(self, s, probe, then):
        return False

    def _resume(self, s, add=(), outcome=None, close=False):
        return self._continuations[-1](s, add, outcome, close)

    # Constructor exits return to the scheduler, not a nested question.
    def post(self, s):
        self._resume(s)

    entry = post
    entry_settlement = post
    motions = post
    q1 = post
    stay_i1 = post
    j9_stayed = post
    j9_i1 = post
    a4_i1 = post
    ripe_i1 = post
    ripe_after_stay = post
    ruling = post
    appeal_settlement = post
    stayed_tail = post
    ripe_post = post

    def notes_petition(self, s, phase, then):
        self._resume(s)

    def settle(self, s, interval, then_no, then_yes=None):
        self._resume(s)

    def i3(self, s, pending=False, then=None):
        self._resume(s)

    def a4_post(self, s, then, i3=False):
        self._resume(s)

    def tail(self, s, outcome):
        self._resume(s, outcome=outcome, close=True)

    def stay_post(self, s):
        self._resume(s)

    def _end(self, s, outcome, then):
        self._resume(s, outcome=outcome)

    def _outcome(self, s):
        for node, _, answer in reversed(s.steps):
            b = plain(answer)
            if node == 'settle' and b == 'yes':
                return 'settled'
            if node == self.resp and b in ('pay', 'file'):
                return 'paid' if b == 'pay' else 'petition'
            if node == 'post_trial_ruling' and b == 'set_aside':
                return 'set_aside'
            if node == 'verdict':
                branches = self.fc.m['templates']['pending_money_claim']['verdict_branches']
                if b in branches and not branches[b]['judgment']:
                    return 'no_judgment'
        return 'unresolved'

    def _pending(self, s, absent):
        """Derive chain heads from answers; absent decisions have no booked step."""
        answers = {(n, c): plain(b) for n, c, b in s.steps}
        done = set(answers) | {(d.node, d.ctx) for d in absent}
        result = []

        def has(n, c=""):
            return (n, c) in done

        def add(n, c=""):
            if not has(n, c):
                result.append(Decision(n, c))

        closed = any(n == "settle" and b == "yes" for (n, _), b in answers.items())
        closed |= answers.get(("post_trial_ruling", "")) == "set_aside"
        closed |= any(n == self.resp and b in ("pay", "file") for (n, _), b in answers.items())
        verdict = answers.get(("verdict", "I0"))
        if verdict is not None:
            branches = self.fc.m["templates"]["pending_money_claim"]["verdict_branches"]
            closed |= verdict in branches and not branches[verdict]["judgment"]
        if closed:
            return ()
        if not has("settle", "I0"):
            add("settle", "I0")
        elif verdict is None:
            add("verdict", "I0")
        else:
            add(self.resp, "entry")
            if has(self.resp, "entry"):
                if not has("post_trial_motions"):
                    add("settle", "Ientry")
                add("post_trial_motions")
                motion = answers.get(("post_trial_motions", ""))
                ruled = has("post_trial_ruling")
                if motion == "yes":
                    if not has("execute_pre_ruling", "I1"):
                        add("settle", "I1")
                    add("execute_pre_ruling", "I1")
                    add("post_trial_ruling")
                    if answers.get(("execute_pre_ruling", "I1")) == "yes":
                        add("stay", "I1")
                        if has("stay", "I1"):
                            add("registration_early", "I1")
                    if has("registration_early", "I1") and not ruled:
                        add(self.resp, "I1")
                    if answers.get(("stay", "I1")) == "yes":
                        add("settle", "Istay")
                    if has(self.resp, "I1"):
                        add("settle", "Ienforce")
                if motion is not None:
                    if s.a4 == "seek":
                        add(self.resp, "ripe")
                    add("judgment_default", "I1")
                if motion == "no" or ruled:
                    if not has("appeal"):
                        add("settle", "I2")
                    add("appeal")
                    if str(answers.get(("post_trial_ruling", ""), "")).startswith("reduced"):
                        add("judgment_default", "ruling")
                    if has("appeal"):
                        if s.appealed:
                            add("settle", "Iappeal")
                        if not s.stayed:
                            add("stay", "post")
                        if s.stayed or has("stay", "post"):
                            add("settle", "I4" if s.stayed else "I3")
                            if answers.get(("stay", "I1")) != "yes":
                                add("enforce", "post")
                            add("judgment_default", "post")
                    if s.a4 != "closed":
                        add(self.resp, "post")
        return tuple(result)

    def _cursors(self, s, pending):
        legal, notes = [], []
        for d in self._pending(s, pending):
            if d.node == self.resp and (s.a4 == "closed" or (d.ctx == "ripe" and s.a4 != "seek")):
                continue
            (notes if d.node == "judgment_default" or d.ctx == "ripe" else legal).append(d)
        listing = []
        if not any(st[0] == "listing" for st in s.steps):
            if self.fin is not None and self.fin.listing_deadline is not None:
                listing.append(Decision("listing_date", "compliance"))
        elif (p := self._pending_delisting(s)) is not None:
            notes.append(Decision("delisting_notes", p[0]))
        unresolved = lambda ds: tuple(d for d in ds if d not in pending)  # noqa: E731
        return Cursors(unresolved(legal), unresolved(Decision(n, c) for n, c, _ in self._candidates(s)),
                       unresolved(listing), unresolved(notes))

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
                if candidate.chain == "deterministic":
                    before.until(np.where(local_on, candidate.day + 1, -1))
                    return self._loop(s, before, pending, outcome, boundary, on, rows)
                self.freeze_due(s, boundary, on)
                d = candidate.decision
                remaining = pending | {d}

                def child(y, add=(), result=None, close=False):
                    booked = before.clone()
                    tr = Trace(booked.ev)
                    for step in y.steps[len(s.steps):]:
                        booked.advance(tr, *step)
                    booked.until(np.where(local_on, candidate.day + 1, -1))
                    todo = remaining
                    if y.steps == s.steps and todo == pending:
                        raise RuntimeError(f"selected decision made no progress: {d}; "
                                           f"rows={np.flatnonzero(on).tolist()}, "
                                           f"day={candidate.day[local_on].tolist()}, steps={s.steps!r}")
                    # Resolve the existing grouped answer's population here,
                    # with question construction, not in frontier discovery.
                    child_mask = self.mask_of(y.steps)
                    child_mask = on if child_mask is None else on & child_mask
                    if child_mask.any():
                        records, classes = self.capture_decisions(s, y, child_mask)
                        previous = self._dated_records, self._dated_classes, self._question_queue
                        self._dated_classes = {**self._dated_classes, **classes}
                        for key in classes:
                            base_records = [r for k, _, r in records if k.split('|#', 1)[0] == key]
                            day = (np.minimum.reduce([r['day'] for r in base_records]) if base_records
                                   else np.full(self.fc.draws.n, BIG))
                            index = next((i for k, i in y.late[len(s.late):] if k == key), len(s.steps))
                            captured = tuple((k, p, r) for k, p, r in records if k.split('|#', 1)[0] == key)
                            self._question_queue += ((key, index, day, y.steps, captured, classes[key]),)
                        try:
                            self._loop(y, booked, todo, outcome if result is None else result, boundary, child_mask, rows)
                        finally:
                            self._dated_records, self._dated_classes, self._question_queue = previous

                self._continuations.append(child)
                try:
                    self._ask(s, d, outcome)
                finally:
                    self._continuations.pop()

            saved_questions = self._dated_records, self._dated_classes, self._question_queue
            try:
                self.scoped(on, selected)
            finally:
                self._dated_records, self._dated_classes, self._question_queue = saved_questions
        rest = np.zeros(self.fc.draws.n, dtype=bool)
        rest[rows[frontier.pick == -1]] = True
        if rest.any():
            result = outcome
            if result == "unresolved":
                result = ("motions_pending" if Decision("post_trial_ruling") in self._pending(s, pending)
                          else "stayed" if s.stayed else "unresolved")
            self.freeze_due(s, np.full(self.fc.draws.n, self.N), rest)
            self._leaf = chain, rows, rest
            try:
                self.scoped(rest, lambda: self.emit(s, result))
            finally:
                del self._leaf

    def freeze_due(self, state, boundary, mask):
        """Retain the reached-date record before advancing to the next answer.

        Only composite questions can still be queued here. If earlier answers
        have intervened since their origin, probe the reached prefix again; a
        terminal history is never used to rewrite a previously frozen question.
        """
        from app.disputes.notes import record

        remaining = []
        for key, index, day, prefix, captured, initial_class in self._question_queue:
            on = mask & (day <= boundary)
            if on.any():
                records = []
                keep = lambda k, p, r, records=records: records.append((k, p, r))  # noqa: E731
                if state.steps == prefix:
                    records = captured
                    cls = initial_class
                elif self.deferred_notes(key):
                    cls = record(self.fc, self.d, state.steps, key, on, keep=keep)
                else:
                    cls = self.fc.record_late(self.d, state.steps, ((key, index),), on, keep=keep).get(key)
                # A population can reach this question on only some draws.
                # Captured rows still cover the original population: retaining
                # them unmasked would freeze the other draws' facts too soon.
                self._dated_records += tuple((k, p, {**r, 'day': np.where(on, r['day'], BIG)})
                                             for k, p, r in records)
                if cls is not None:
                    prior = self._dated_classes.get(key)
                    if prior is not None:
                        cls = np.where(on, cls, prior)
                    self._dated_classes = {**self._dated_classes, key: cls}
            outstanding = np.where(on, BIG, day)
            if (outstanding < self.N).any():
                remaining.append((key, index, outstanding, prefix, captured, initial_class))
        self._question_queue = tuple(remaining)

    def capture_decisions(self, before, after, mask):
        """Capture date-local questions; queue composite questions until their date.

        A constructor can include more than one question (e.g. an offering after
        initiation). Preserve each question's own index, not the constructor's.
        Court approval needs security sized on approval day; notes petitions need
        the declaration and notice period first. Those questions wait at the
        frontier, so intervening earlier decisions are included, never later ones.
        Neutral probes project scheduled bookings from the reached prefix only.
        """
        from app.disputes.notes import record

        records = []
        keep = lambda k, p, r: records.append((k, p, r))  # noqa: E731
        pending = tuple((k, i) for k, i in after.late[len(before.late):] if not self.deferred_notes(k))
        classes = self.fc.record_late(self.d, after.steps, pending, mask, keep=keep) if pending else {}
        old = {k for edge, _ in before.edges for k in atoms(edge)}
        for k in sorted({k for edge, _ in after.edges for k in atoms(edge)} - old):
            if self.deferred_notes(k):
                classes[k] = record(self.fc, self.d, after.steps, k, mask, keep=keep)
        return records, classes

    def terminal_questions(self, s, mask, tr):
        if not hasattr(self, "_leaf"):
            return super().terminal_questions(s, mask, tr)
        for key, prefix, row in self._dated_records:
            if mask is not None:
                row = {**row, "day": np.where(mask, row["day"], BIG),
                       "petition": np.where(mask, row["petition"], 0)}
            self.fc._keep_late(key, prefix, row)
        return self._dated_classes

    def terminal_trace(self, s):
        """Finish the owned leaf copy without rebuilding the question records."""
        from app.analysis.events import _trace_rows, _widen

        if not hasattr(self, "_leaf"):  # graph materialization has histories, not live leaf chains
            return super().terminal_trace(s)
        chain, rows, mask = self._leaf
        keep = mask[rows]
        if keep.all():
            chain = chain.clone()
        else:
            chain = chain.sliced(np.flatnonzero(keep), self.fc.draws.sub(mask))
            rows = rows[keep]
        tr = chain.finish(Trace(chain.ev))
        chain.court_questions(tr, s.steps)
        if len(rows) != self.fc.draws.n:
            tr = _trace_rows(tr, lambda v, k: v if k == "events" else _widen(v, rows, self.fc.draws.n, k))
            tr.rows = rows
        return tr

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
        if self.bounds.reached(d.node, s.steps):
            if d.node == 'settle':
                return again(s.add(('settle', d.ctx, 'no'), None))
            if d.node == 'cash_floor':
                return again(s.add(('cash_floor', d.ctx, 'neither'), None, k=s.k + 1))
            if d.node == 'judgment_default':
                return again(s.add(('judgment_default', d.ctx, 'no'), None))
        if d.node == self.resp:
            return _Walk.a4_grouped(self, s, d.ctx, again, again, False)
        if d.node == "post_trial_ruling":
            return _Walk.ruling_pending(self, s)
        if d.node == "judgment_default":
            return _Walk.notes_petition(self, s, d.ctx, again)
        if d.node == "settle":
            return _Walk.settle(self, s, d.ctx, again)
        methods = {"verdict": "verdict", "post_trial_motions": "motions",
                   "execute_pre_ruling": "q1", "registration_early": "j9_i1", "appeal": "appeal"}
        if d.node in methods:
            return getattr(_Walk, methods[d.node])(self, s)
        if d.node == "stay":
            return (_Walk.stay_i1 if d.ctx == "I1" else _Walk.stay_post)(self, s)
        if d.node == "enforce":
            return _Walk.enforce(self, s, again)
        if d.node == "listing_date":
            return _Walk.listing(self, s, outcome, again, defer_delisting=True)
        if d.node == "delisting_notes":
            dc, day = self._pending_delisting(s)
            return _Walk.delisting(self, s, dc, day, outcome, again)
        if d.node in ("cash_floor", "cash_out", "nonpayment"):
            return _Walk.ask_distress(self, s, (d.node, d.ctx, "due" if d.node == "nonpayment" else "neither"),
                                      outcome, again)
        raise ValueError(f"unsupported comparison decision: {d}")

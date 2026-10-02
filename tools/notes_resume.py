"""Capture an existing walk continuation by replaying only its exact history prefix."""
from __future__ import annotations

import copy
import functools
import inspect
from contextlib import ExitStack
from dataclasses import dataclass, replace
from unittest.mock import patch

import numpy as np

from app.disputes.forecast import _S, _DepthCache, _Prefix, _Walk, as_of, group_classes


@dataclass
class Continuation:
    walk: _Walk
    state: _S
    function: object
    args: tuple
    kwargs: dict
    canonical: dict
    classes: dict
    record_at: tuple | None
    legacy: bool = False
    incoming_mask: object = None

    def run(self):
        self.walk.fc._qcanon = copy.deepcopy(self.canonical)
        self.walk.fc._qcls = copy.deepcopy(self.classes)
        self.walk.fc._rec_at = self.record_at
        if self.legacy:
            fc = self.walk.fc
            fc._traces = _DepthCache(fc.SIBLINGS)
            fc._reuse.clear()
            fc.__dict__.get('_open_light', {}).clear()
            self.walk._masks = _DepthCache(fc.SIBLINGS)
            current = self.walk.mask_of(self.state.steps)
            old = np.ones(fc.draws.n, dtype=bool) if self.incoming_mask is None else self.incoming_mask
            current = np.ones(fc.draws.n, dtype=bool) if current is None else current
            if not np.array_equal(old, current):
                raise ValueError('Corrected eligibility changes the incoming recovery population; '
                                 'resume before the affected ancestor decision')
        return self.function(self.walk, self.state, *self.args, **self.kwargs)


def capture(walk: _Walk, prefix: tuple, method: str, phase: str | None = None,
            legacy: bool = False) -> Continuation:
    """Recover flags and the actual callback; never reconstruct state from steps alone.

    This operates in a dedicated process: class methods are temporarily wrapped.
    A capture within an open speculative watch must be promoted to its owning
    decision before it can be resumed independently.
    """
    found = []
    pruning = True

    def wrap(name, function):
        @functools.wraps(function)
        def call(self, state, *args, **kwargs):
            if self is not walk or not pruning or not isinstance(state, _S):
                return function(self, state, *args, **kwargs)
            if state.steps != prefix[:len(state.steps)]:
                return None
            if name == method and state.steps == prefix and (phase is None or args[0] == phase):
                if self._watch:
                    raise ValueError('Continuation is inside a speculative watch; resume its owning decision')
                found.append(Continuation(self, copy.deepcopy(state), function, args, kwargs,
                                          copy.deepcopy(self.fc._qcanon), copy.deepcopy(self.fc._qcls),
                                          self.fc._rec_at, legacy, copy.deepcopy(self.mask_of(state.steps))))
                return None
            replay = {"offer": _original_offer, "settle": _original_settle}.get(name, function) if legacy else function
            return replay(self, state, *args, **kwargs)
        return call

    try:
        with ExitStack() as stack:
            if legacy:
                stack.enter_context(patch.object(_Walk, 'node', _original_node))
                stack.enter_context(patch.object(_Walk, 'situation', _original_situation))
                stack.enter_context(patch.object(_Walk, 'levy_first', _original_levy_first))
                prefix_of = _Prefix.of.__func__

                def historical_prefix(cls, trace, *args, **kwargs):
                    result = prefix_of(cls, trace, *args, **kwargs)
                    petition = trace.events.petition.copy()
                    if trace.rows is not None:
                        widened = np.zeros(len(result.petition), dtype=petition.dtype)
                        widened[trace.rows] = petition
                        petition = widened
                    return replace(result, petition=petition)

                stack.enter_context(patch.object(_Prefix, 'of', classmethod(historical_prefix)))
            for name, function in list(vars(_Walk).items()):
                if not callable(function) or name.startswith('__'):
                    continue
                signature = inspect.signature(function)
                parameters = list(signature.parameters)
                if len(parameters) > 1 and parameters[1] == 's' and signature.return_annotation in ('None', None):
                    stack.enter_context(patch.object(_Walk, name, wrap(name, function)))
            walk.run()
    finally:
        # Captured callbacks can reference a bound wrapper after class restoration.
        pruning = False
    if len(found) != 1:
        raise ValueError(f'Expected one exact continuation, found {len(found)}')
    return found[0]


def _original_offer(self, s, occasion, then):
    """Replay saved incoming keys only; capture wrappers use the current method once resumed."""
    if self._closes_after(s, occasion):
        return then(s.add(("offering", occasion, "no"), None))
    key = self.node("offering_closes", occasion, *(("after_failed",) if s.failed else ()), branches=("yes", "no"))
    late = s.late + ((key, len(s.steps)),)
    for branch in ("yes", "no"):
        then(s.add(("offering", occasion, branch), (key, branch), late=late, failed=s.failed or branch == "no"))


def _original_settle(self, s, interval, then_no):
    """Replay the ungrouped settlement steps in the immutable recovery manifest."""
    from app.analysis.events import settlement_terms

    probe = ("settle", interval, "no")
    tr = self._trace(s.steps + (probe,))
    if not ((tr.day[-1] < self.N) & (tr.settle_offer > 0)).any():
        return then_no(s)
    if interval == "I3":
        self._reads("unstayed")
    if self.first(s, probe, lambda y: self.settle(y, interval, then_no)):
        return
    a3 = self.node("settlement_offer", interval, s.cls, s=s, probe=probe)
    mode, count = settlement_terms(self.fc.m, self.fc.sens)
    terms = "the company offers to settle for its available cash above its 30-day operating need" + (
        f", paid in {count} equal monthly installments from the settlement date" if mode == "installments" else "")
    q4 = self.node("settlement_accept", interval, s.cls, s=s, probe=probe, assumptions=(terms,))
    self.binary(s, "settle", interval, [[(a3, "yes"), (q4, "yes")]], (a3, q4),
                lambda y: self.tail(y, "settled"), then_no)


# Historical replay only. Restored before the captured continuation executes.
def _original_node(self, name, *ctx, s: _S | None = None, probe=None, assumptions=(), branches=None, groups=None):
        """The question in its situation: the context tags given, plus the conditions its actor weighs (the model
        node's `situation`) that hold at the decision on every trajectory. groups: per draw, the option group it is
        asked of (-1: not asked): a grouped question, each draw in its group's class (`group_classes`)."""
        for w in self._watch:  # a later question that reads the watched event directly
            w.read |= name in w.nodes
        tags = self.situation(s, probe, name, ctx) if s is not None else ()
        if groups is not None:
            from app.analysis.events import group_branches

            # a grouped question: every answer it offers any group (`branches` as given: this prefix's groups'
            # answers, the path's edges); each class node carries its group's own (`Forecaster.class_key`)
            branches = group_branches(name, 3)
        k = self.fc.node(self.d, name, *ctx, *tags, assumptions=assumptions, branches=branches)
        if groups is not None:
            self.fc.grouped.add(k)
        if s is not None and probe is not None and self.fc._classified(k) and not self.deferred_notes(k) \
                and not any(e[0] == s.steps for e in self.fc._qcanon.get(k, ())):
            # QUESTIONS §1 Grouping: each draw's class as the question is asked, on the branch that books nothing (the
            # state before the decision); every branch's paths and rows read it (`Forecaster.canon_get`)
            at = probe if isinstance(probe[0], tuple) else (probe,)
            row = as_of(self.fc.row_of(self._facts(s.steps + at)))
            if groups is not None:
                cls = group_classes(self.fc.question_class(self.fc.nodes[k], row, groups >= 0), groups)
            else:
                cls = self.fc.question_class(self.fc.nodes[k], row, self.fc.live(self.fc.nodes[k], row))
            self.fc.canon_put(k, s.steps, cls)
        return k

def _original_situation(self, s: _S, probe, name: str, ctx) -> tuple[str, ...]:
        conds = list(self.fc.spec[name].get("situation", []))
        if not conds:
            return ()
        at = probe if isinstance(probe[0], tuple) else (probe,)  # one probe step, or several
        tr = self._trace(s.steps + at)
        out = self._tags(s, conds, ctx, tr, at)
        for w in self._watch:  # would the event, had it occurred, change the question's situation?
            if not w.read and tr.marks is not None and set(w.marks) & set(conds):
                cf = {**tr.marks, **{c: np.minimum(tr.marks[c], v) for c, v in w.marks.items()}}
                w.read = self._tags(s, conds, ctx, replace(tr, marks=cf), at) != out
        return out

def _original_levy_first(self, s: _S) -> bool:
        levy = s.steps + (("enforce", "post", "levy"),)
        lv = self._trace(levy + ((self.resp, "post", self.quiet),)).day[-1]
        w = self._trace(s.steps + (("settle", "I3", "no"),)).day[-1]
        both = (lv < self.N) & (w < self.N)
        return bool((both & (lv < w)).any())

"""Bounded, native-draw prefix reuse for the exhaustive replay checks.

Only engine states *before finish* are shared. Finish and court-question booking
always operate on a private clone; later answers cannot enter an earlier prefix.
The uncached checker remains available as the reference implementation.
"""
from collections import OrderedDict

from app.analysis.events import Chain, Trace, canon


class Replay:
    def __init__(self, fc, d, draws, capacity=256):
        self.fc, self.d, self.draws = fc, d, draws
        self.capacity = capacity
        self.states = OrderedDict()

    def __call__(self, steps, *, day_only=False, light=False, question=None):
        steps = canon(steps)
        # Captured question records are part of the state, not interchangeable
        # with the cheaper light replay used for dates and order checks.
        mode = not light
        depth = len(steps)
        while depth and (mode, steps[:depth]) not in self.states:
            depth -= 1
        key = mode, steps[:depth]
        if key in self.states:
            ch = self.states[key].clone()
            self.states.move_to_end(key)
        else:
            fc = self.fc
            ch = Chain(self.d, fc.setup, fc.m, self.draws, fc.sens)
            ch.capture_questions = mode and (ch.pending or ch.ordinary)
            ch.instrument_cash()
            self.states[key] = ch.clone()
        tr = Trace(ch.ev)
        for i in range(depth, len(steps)):
            ch.advance(tr, *steps[i])
            self.states[mode, steps[:i + 1]] = ch.clone()
            if len(self.states) > self.capacity:
                self.states.popitem(last=False)
        tr = ch.finish(tr, day_only, light)
        if not day_only and not light:
            # Facts reconstruction consumes one question, not every historical
            # court read. Prefix states still capture all decision boundaries.
            ch.court_questions(tr, steps, indices=None if question is None else {question})
        return tr

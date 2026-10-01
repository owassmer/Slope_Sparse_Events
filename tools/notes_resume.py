"""Capture an existing walk continuation by replaying only its exact history prefix."""
from __future__ import annotations

import copy
import functools
import inspect
from contextlib import ExitStack
from dataclasses import dataclass
from unittest.mock import patch

from app.disputes.forecast import _S, _Walk


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

    def run(self):
        self.walk.fc._qcanon = copy.deepcopy(self.canonical)
        self.walk.fc._qcls = copy.deepcopy(self.classes)
        self.walk.fc._rec_at = self.record_at
        return self.function(self.walk, self.state, *self.args, **self.kwargs)


def capture(walk: _Walk, prefix: tuple, method: str, phase: str | None = None) -> Continuation:
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
                                          self.fc._rec_at))
                return None
            return function(self, state, *args, **kwargs)
        return call

    try:
        with ExitStack() as stack:
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

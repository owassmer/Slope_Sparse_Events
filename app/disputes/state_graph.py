"""One-draw chronological DAG. Edges retain answers, never their probabilities.

Construction visits a continuation once; expansion is lazy and preserves incoming
answer histories. The plain scheduler remains available as the comparison oracle.
"""
from dataclasses import dataclass, fields, replace

import numpy as np

from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import atoms


def freeze(value):
    """Exact, hashable state (no lossy hash or floating point rounding)."""
    if isinstance(value, np.ndarray):
        return (value.dtype.str, value.shape,
                freeze(value.tolist()) if value.dtype.hasobject else value.tobytes())
    if hasattr(value, '__dataclass_fields__'):
        return tuple((f.name, freeze(getattr(value, f.name))) for f in fields(value))
    if isinstance(value, dict):
        return frozenset((freeze(k), freeze(v)) for k, v in value.items())
    if isinstance(value, (set, frozenset)):
        return frozenset(map(freeze, value))
    if isinstance(value, (tuple, list)):
        return tuple(map(freeze, value))
    return value


@dataclass(frozen=True)
class Transition:
    target: int
    steps: tuple
    answers: tuple
    late: tuple
    changes: tuple
    classes: tuple
    records: tuple = ()

    @classmethod
    def between(cls, target, before, after, fc):
        n = len(before.steps)
        return cls(target, after.steps[n:], after.edges[len(before.edges):],
                   tuple((key, i - n) for key, i in after.late[len(before.late):]),
                   tuple((f.name, getattr(after, f.name)) for f in fields(after)
                         if f.name not in ('steps', 'edges', 'late')
                         and getattr(before, f.name) != getattr(after, f.name)),
                   tuple((k, fc._qcls_get(k, after.steps), fc.canon_get(k, after.steps))
                         for edge, _ in after.edges[len(before.edges):] for k in atoms(edge)))

    def apply(self, state):
        return replace(state, steps=state.steps + self.steps, edges=state.edges + self.answers,
                       late=state.late + tuple((key, i + len(state.steps)) for key, i in self.late),
                       **dict(self.changes))


class StateGraph:
    def __init__(self):
        self.nodes = []
        self.root = None
        self.initial = None
        self.hits = 0
        self.complete = False

    def histories(self):
        """Yield (walk state, outcome); probabilities remain products of state.edges."""
        for state, outcome, _, _ in self._histories():
            yield state, outcome

    def _histories(self):
        def expand(node, state, classes, records):
            for edge in self.nodes[node]:
                if isinstance(edge, str):
                    yield state, edge, classes, records
                else:
                    added = tuple((key, state.steps + suffix, blob) for key, suffix, blob in edge.records)
                    yield from expand(edge.target, edge.apply(state), classes + edge.classes, records + added)
        if self.root is not None:
            yield from expand(self.root, self.initial, (), ())

    def history_count(self):
        counts = {}

        def count(node):
            if node not in counts:
                counts[node] = sum(1 if isinstance(e, str) else count(e.target) for e in self.nodes[node])
            return counts[node]
        return 0 if self.root is None else count(self.root)


class GraphWalk(ChronologicalWalk):
    """Build a graph, without replaying every completed history during construction.

    Use ``histories`` for streaming paths or ``materialize`` for the existing
    DisputePath/late-facts interface. No judgment calls are needed to build it.
    """
    def run_from(self, state, support=None):
        if self.fc.draws.n != 1:
            raise ValueError('state graph requires one native draw; slice draws before construction')
        self.graph = StateGraph()
        self._states = {}
        self._active = set()
        self._parents = []
        self._recordings = []
        super().run_from(state, support)
        self.graph.complete = True
        return self.graph

    def _loop(self, s, chain, pending, outcome, committed, mask, rows):
        if not mask.any():
            return
        cursors = self._cursors(s, pending)
        frontier = chain.next_decisions(cursors)
        ev = chain.ev
        key = freeze(((ev.cash, ev.lock, ev.capacity, ev.petition, ev.kinds, ev.incurred, ev.proceeds),
                      cursors, [(c.chain, c.decision, c.day) for c in frontier.candidates], committed,
                      chain.stays, chain.hearing_requested, chain.suspended, chain.delisted,
                      chain.collateral_required,
                      outcome, tuple((f.name, getattr(s, f.name)) for f in fields(s)
                                     if f.name not in ('steps', 'edges', 'late'))))
        node = self._states.get(key)
        fresh = node is None
        if fresh:
            node = len(self.graph.nodes)
            self._states[key] = node
            self.graph.nodes.append([])
        elif node in self._active:
            raise RuntimeError('state graph cycle: continuation key lacks progress')
        else:
            self.graph.hits += 1
        if self._parents:
            parent, before = self._parents[-1]
            edge = Transition.between(node, before, s, self.fc)
            if self._recordings and self._recordings[-1][0] == parent:
                edge = replace(edge, records=tuple(self._recordings[-1][1]))
            self.graph.nodes[parent].append(edge)
        else:
            self.graph.root, self.graph.initial = node, s
        if not fresh:
            return
        self._parents.append((node, s))
        self._active.add(node)
        try:
            super()._loop(s, chain, pending, outcome, committed, mask, rows)
        finally:
            self._parents.pop()
            self._active.remove(node)

    def _ask(self, s, d, outcome):
        record = self.fc.record
        captured = []
        self._recordings.append((self._parents[-1][0], captured))

        def recording(keys, tr):
            result = record(keys, tr)
            if self._recordings[-1][1] is captured:
                captured.extend((key, self.fc._rec_at[len(s.steps):], blob) for key, blob in result)
            return result

        self.fc.record = recording
        try:
            return super()._ask(s, d, outcome)
        finally:
            self.fc.record = record
            self._recordings.pop()

    def emit(self, s, outcome):
        self.graph.nodes[self._parents[-1][0]].append(outcome)

    def materialize(self):
        """Replay expanded histories for the legacy booking/classification interface."""
        for state, outcome, classes, _ in self.graph._histories():
            self.materialize_history(state, outcome, classes)
        return self.out

    def materialize_history(self, state, outcome, classes):
        self.fc._qcls, self.fc._qcanon = {}, {}
        for key, recorded, canonical in classes:
            if recorded is not None:
                self.fc._qcls[key] = [(state.steps, recorded)]
            if canonical is not None:
                self.fc._qcanon[key] = [(state.steps, canonical)]
        emit = super().emit
        self.scoped(np.ones(self.fc.draws.n, dtype=bool), lambda: emit(state, outcome))
        return self.out[-1]

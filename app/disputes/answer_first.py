"""The answer-first walk (design/002-answer-first.md): the chronological walk, expanded exactly where a history is
likely and sampled where it is not.

Every question is built as the chronological walk builds it. What changes is which children are walked:
- Above the threshold (a prefix whose probability on some draw is at least `tau`), every child is walked, as today.
- Below it, the prefix's subtree is walked `replicates` times, each time sampling: at each decision the draws take
  one child, with the probability the guide gives it among the children open to them, on one shared uniform, so
  draws offered the same children go the same way (`_sample`). A question
  that is asked only if a later question reads its event (`_Walk._watched`) is not sampled: all its children are
  walked, since whether it is asked at all depends on what lies below it.
- Each path carries `divisor`: the probability with which it was sampled (1 above the threshold) times the number of
  replicates. Its weight on a draw is its probability under the judgments divided by its divisor, an unbiased
  estimate of the probability mass it stands for (importance sampling), and that stays so under any change to a
  judgment, since the divisor does not move with them. `replicate` says which sampling it came from (-1: above the
  threshold), so the spread between replicates gives each figure's sampling error.

The guide is the judge's answers on each question's base key (before its situation class): it decides only where
sampling looks, never a path's weight, so a guide that differs from a class's own answer costs precision, not
correctness. A child the guide gives no probability is never sampled, so the guide gives every answer some.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np

from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import COMPOSITE, Dist


class Guide(Dist):
    """The guide's answer to each question key, from `answer(key, branches)`; composites by the chain rule."""

    def __init__(self, fc, answer):
        super().__init__()
        self.fc, self.answer = fc, answer

    def __missing__(self, key):
        if key.startswith(COMPOSITE):
            return super().__missing__(key)
        v = self.answer(key, self.fc.nodes[key].branches)
        self[key] = v
        return v


def stand_in(key: str, branches: tuple[str, ...], sure: float = 0.8) -> dict[str, float]:
    """A seeded stand-in judge for building and keyless runs: one answer, chosen by the question's key, gets `sure`,
    the rest share what is left. Deterministic, never zero, and not a judgment about the case."""
    if len(branches) == 1:
        return {branches[0]: 1.0}
    pick = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(branches)
    return {b: (sure if i == pick else (1 - sure) / (len(branches) - 1)) for i, b in enumerate(branches)}


class _Node:
    """The decision being asked at one point of a sampled walk: its children as the constructor gives them."""

    def __init__(self, state):
        self.state, self.children, self.take_all = state, [], False


class AnswerFirstWalk(ChronologicalWalk):
    def configured(self, **kw) -> AnswerFirstWalk:
        self.configure(**kw)
        return self

    def configure(self, *, guide, tau: float, replicates: int = 4, seed: int = 0):
        if replicates < 2:
            raise ValueError("at least two replicates: their spread is the sampling error")
        self.guide, self.tau, self.replicates, self.seed = guide, tau, replicates, seed
        self._div = 1.0              # the current subtree's sampling divisor
        self._rep = -1               # the replicate being sampled (-1: above the threshold)
        self._nodes: list[_Node] = []  # the decisions being asked, innermost last (sampled walks only)
        self._filter = None          # the draws the next `_loop` keeps (a sampled child's)
        self._asked = 0              # decisions asked while sampling: each its own uniforms
        self.stubs = 0               # prefixes below the threshold

    # --- where the threshold is crossed ---------------------------------------------------------------------------
    def mass(self, s, mask) -> np.ndarray:
        """The guide's probability of the prefix on each draw of `mask` (0 elsewhere): it decides only where to sample."""
        p = 1.0
        for key, branch in s.edges:
            p *= self.guide[key][branch]
        return np.where(mask, p, 0.0)

    def _loop(self, s, chain, pending, outcome, committed, mask, rows):
        if self._filter is not None:
            mask, self._filter = mask & self._filter, None
        if self._rep < 0 and mask.any() and (not self._watch or all(w.read for w in self._watch)) \
                and self.mass(s, mask).max() < self.tau:
            self.stubs += 1
            div = self._div
            try:
                for r in range(self.replicates):
                    self._rep, self._div = r, div * self.replicates
                    super()._loop(s, chain.clone(), pending, outcome, committed.copy(), mask, rows)
            finally:
                self._rep, self._div = -1, div
            return
        return super()._loop(s, chain, pending, outcome, committed, mask, rows)

    # --- sampling a decision's children -----------------------------------------------------------------------------
    def _ask(self, s, d, outcome):
        if self._rep < 0:
            return super()._ask(s, d, outcome)
        node = _Node(s)
        self._nodes.append(node)
        try:
            super()._ask(s, d, outcome)
        finally:
            self._nodes.pop()
        self._sample(node)

    def _watched(self, s, no, w, then):
        if self._nodes:
            self._nodes[-1].take_all = True  # whether it is asked depends on what lies below: walk every child
        return super()._watched(s, no, w, then)

    def _resume(self, s, add=(), outcome=None, close=False):
        node = self._nodes[-1] if self._rep >= 0 and self._nodes else None
        if node is None or node.take_all:
            return super()._resume(s, add, outcome, close)
        node.children.append((s, add, outcome, close))

    def _sample(self, node: _Node) -> None:
        """Each draw at the decision takes one of the children open to it, with the guide's probability among them;
        each child is walked once per distinct sampling probability among the draws that took it (so a subtree's
        divisor is one number)."""
        if not node.children:
            return
        population = self._population
        population = np.ones(self.fc.draws.n, dtype=bool) if population is None else population
        open_ = []
        for y, *_ in node.children:
            m = self.mask_of(y.steps)
            open_.append(population if m is None else population & m)
        q = np.array([np.prod([self.guide[k][b] for k, b in y.edges[len(node.state.edges):]] or [1.0])
                      for y, *_ in node.children])
        eligible = np.stack(open_)                                   # children x draws
        weights = eligible * q[:, None]
        total = weights.sum(axis=0)
        if ((total <= 0) & eligible.any(axis=0)).any():
            raise ValueError("the guide gives no probability to every child open to a draw")
        self._asked += 1
        # One uniform for the decision, shared by its draws: draws offered the same children take the same one, so a
        # sampled subtree follows one path rather than one per draw (each draw's estimate is still unbiased; the
        # replicates' spread carries the correlation this adds).
        u = np.full(self.fc.draws.n, np.random.default_rng((self.seed, self._rep, self._asked)).random())
        cum = np.cumsum(weights, axis=0)
        total = cum[-1]
        pick = (cum > (u * total)[None, :]).argmax(axis=0)
        pick = np.where(total > 0, pick, -1)
        chosen = np.zeros_like(eligible)
        chosen[pick[pick >= 0], np.flatnonzero(pick >= 0)] = True
        for i, (y, add, outcome, close) in enumerate(node.children):
            drew = chosen[i] & eligible[i]
            if not drew.any():
                continue
            prob = q[i] / np.where(total > 0, total, 1)
            for v in np.unique(prob[drew]):
                part = drew & (prob == v)
                div = self._div
                self._div, self._filter = div * float(v), part
                try:
                    super()._resume(y, add, outcome, close)
                finally:
                    self._div, self._filter = div, None

    # --- what a path carries --------------------------------------------------------------------------------------
    def emit(self, s, outcome):
        n = len(self.out)
        super().emit(s, outcome)
        if len(self.out) > n:
            self.out[-1] = replace(self.out[-1], divisor=self._div, replicate=self._rep)
            if self.keys:
                self.keys[-1] = (self.keys[-1], self._div, self._rep)  # never merge across samplings

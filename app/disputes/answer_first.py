"""The answer-first walk (design/002-answer-first.md): the chronological walk, expanded exactly where a history is
likely and sampled where it is not, so that every expected figure has a range that holds whatever the unwalked part
holds (`app/analysis/sampling.py`).

Every question is built as the chronological walk builds it. What changes is which children are walked:
- Above the threshold (a prefix whose probability on some draw is at least `tau`), every child is walked, as today.
- Below it the prefix is a stub. Its continuations are sampled in G_s independent groups of `group_size` replicates.
  G_s follows where the samples matter (Neyman allocation): it grows with the stub's share of the probability over
  all draws and with the square root of the days left to the horizon (what a continuation can still move grows with
  the days left, and so does what it costs to walk), from `min_groups` up to `groups` for a stub at the threshold
  with the whole horizon ahead. The allocation is fixed by quantities known before sampling, so it biases nothing.
- Within a group the replicates' choices are a Latin hypercube: at the k-th decision of a replicate the uniform is
  (pi(r) + V) / R for a random permutation pi of the group's R replicates and an independent V, so the group's
  replicates spread over each decision's answers evenly, while each replicate on its own still draws every decision
  with its judged probability. Groups are independent, so their spread is an honest measure of the error.
- At a sampled decision the draws take one child, with its judged probability among the children open to them, on
  one shared uniform, so draws offered the same children go the same way (`_sample`). The guide is the judgment:
  the seeded stand-in's answer on the question's base key, renormalised over the answers open to the draw, which is
  the stand-in's answer to the draw's own situation class (`app/analysis/build.stand_in_judgments`). So on each draw
  a group's weights add up to the stub's mass, which `sampling.check_masses` checks.
- Decisions within `enumerate_days` of the horizon, and any whose being asked depends on a later question
  (`_Walk._watched`), are not sampled: all their children are walked (conditioning on them removes their share of
  the noise, at little cost near the end).
- Each path carries `divisor` (the probability with which it was sampled, times G_s x R; 1 when walked exactly) and
  its stub and group, so its weight is its probability under the judgments over its divisor: an unbiased estimate
  of the mass it stands for under any judgments.
"""
from __future__ import annotations

import hashlib
import math
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

    def configure(self, *, guide, tau: float, group_size: int = 4, groups: int = 6, min_groups: int = 2,
                  enumerate_days: int = 30, seed: int = 0):
        if min_groups < 2 or groups < min_groups or group_size < 1:
            raise ValueError("at least two groups per stub (their spread is the error) and one replicate per group")
        self.guide, self.tau, self.seed = guide, tau, seed
        self.group_size, self.groups, self.min_groups, self.enumerate_days = group_size, groups, min_groups, enumerate_days
        self._div = 1.0              # the current subtree's sampling divisor
        self._rep = None             # (stub, group, replicate) being sampled; None above the threshold
        self._k = 0                  # the replicate's decisions so far: the k-th takes the k-th uniform
        self._at = None              # (boundary, draws) of the decision about to be asked (freeze_due)
        self._nodes: list[_Node] = []  # the decisions being asked, innermost last (sampled walks only)
        self._filter = None          # the draws the next `_loop` keeps (a sampled child's)
        self.stub_table: list[dict] = []  # per stub: its draws, first decision day per draw, prefix edges, groups

    # --- where the threshold is crossed ---------------------------------------------------------------------------
    def mass(self, s, mask) -> np.ndarray:
        """The guide's probability of the prefix on each draw of `mask` (0 elsewhere)."""
        p = 1.0
        for key, branch in s.edges:
            p *= self.guide[key][branch]
        return np.where(mask, p, 0.0)

    def _first_day(self, s, chain, pending, mask, rows) -> np.ndarray:
        """Per draw, the day of the stub's first decision (the horizon where none is left): every continuation's event
        cash equals this prefix's before it."""
        keep = mask[rows]
        ch, r = (chain, rows) if keep.all() else (chain.sliced(np.flatnonzero(keep), self.fc.draws.sub(mask)),
                                                  rows[keep])
        frontier = ch.next_decisions(self._cursors(s, pending))
        day = np.full(self.fc.draws.n, self.N, dtype=np.int64)
        for j, candidate in enumerate(frontier.candidates):
            on = frontier.pick == j
            day[r[on]] = np.minimum(candidate.day[on], self.N)
        return day

    def _loop(self, s, chain, pending, outcome, committed, mask, rows):
        if self._filter is not None:
            mask, self._filter = mask & self._filter, None
        if self._rep is None and mask.any() and (not self._watch or all(w.read for w in self._watch)):
            m = self.mass(s, mask)
            if m.max() < self.tau:
                day = self._first_day(s, chain, pending, mask, rows)
                left = float((self.N - day[mask]).max()) / self.N
                share = float(m.sum()) / self.fc.draws.n
                n_groups = max(self.min_groups, min(self.groups, round(self.groups * share / self.tau
                                                                       * math.sqrt(max(left, 0.0)))))
                sid = len(self.stub_table)
                self.stub_table.append({"mask": mask.copy(), "day": day, "edges": len(s.edges),
                                        "groups": n_groups, "group_size": self.group_size})
                div = self._div
                try:
                    for g in range(n_groups):
                        for r in range(self.group_size):
                            self._rep, self._k = (sid, g, r), 0
                            self._div = div * n_groups * self.group_size
                            super()._loop(s, chain.clone(), pending, outcome, committed.copy(), mask, rows)
                finally:
                    self._rep, self._div = None, div
                return
        return super()._loop(s, chain, pending, outcome, committed, mask, rows)

    # --- sampling a decision's children -----------------------------------------------------------------------------
    def freeze_due(self, state, boundary, mask):
        self._at = boundary, mask
        return super().freeze_due(state, boundary, mask)

    def _ask(self, s, d, outcome):
        if self._rep is None:
            return super()._ask(s, d, outcome)
        node = _Node(s)
        if self._at is not None and self.enumerate_days > 0:
            boundary, on = self._at
            if on.any() and int((self.N - boundary[on]).max()) <= self.enumerate_days:
                node.take_all = True  # near the horizon: every child walked, its share of the noise gone
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
        node = self._nodes[-1] if self._rep is not None and self._nodes else None
        if node is None or node.take_all:
            return super()._resume(s, add, outcome, close)
        node.children.append((s, add, outcome, close))

    def _uniform(self) -> float:
        """The replicate's next uniform: a Latin hypercube across its group at each decision index."""
        sid, g, r = self._rep
        k, self._k = self._k, self._k + 1
        perm = np.random.default_rng((self.seed, sid, g, k)).permutation(self.group_size)
        v = np.random.default_rng((self.seed, sid, g, r, k, 1)).random()
        return (perm[r] + v) / self.group_size

    def _sample(self, node: _Node) -> None:
        """Each draw at the decision takes one of the children open to it, with the guide's probability among them;
        each child is walked once per distinct sampling probability among the draws that took it (so a subtree's
        divisor is one number)."""
        if not node.children:
            return
        if node.take_all:  # whole after all (a later question reads this one): walk each captured child
            for y, add, outcome, close in node.children:
                super()._resume(y, add, outcome, close)
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
        cum = np.cumsum(weights, axis=0)
        total = cum[-1]
        if ((total <= 0) & eligible.any(axis=0)).any():
            raise ValueError("the guide gives no probability to every child open to a draw")
        # One uniform for the decision, shared by its draws: draws offered the same children go the same way, so a
        # sampled subtree follows one path rather than one per draw (each draw's estimate stays unbiased; the groups'
        # spread carries the correlation this adds).
        u = self._uniform()
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
            sid, g, r = self._rep if self._rep is not None else (-1, -1, -1)
            self.out[-1] = replace(self.out[-1], divisor=self._div, stub=sid, group=g)
            if self.keys:
                self.keys[-1] = (self.keys[-1], self._div, sid, g, r)  # never merge across samplings

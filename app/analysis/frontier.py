"""Answer-free cursors and dated candidates for a shared-state walk.

Cursors are continuation state, not forecasts: the caller supplies the unresolved
prerequisite(s) in each chain. In particular a ruling and a levy response can both
be enabled. An empty cursor means no enabled decision, not permanent completion.
No answer, probability, amount or booked history is part of a cursor.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Decision:
    node: str
    ctx: str = ""


@dataclass(frozen=True)
class Cursors:
    litigation: tuple[Decision, ...] = ()
    distress: tuple[Decision, ...] = ()
    listing: tuple[Decision, ...] = ()
    notes: tuple[Decision, ...] = ()


@dataclass(frozen=True)
class Candidate:
    chain: str
    decision: Decision
    day: np.ndarray  # BIG means no pending decision on that draw in this horizon
    phase: int
    order: int


@dataclass(frozen=True)
class Frontier:
    candidates: tuple[Candidate, ...]
    pick: np.ndarray  # candidate index per draw; -1 means none

    def for_chain(self, name: str) -> np.ndarray:
        """Earliest candidate index in this chain per draw (-1: none)."""
        return select(self.candidates, len(self.pick), name)


def select(candidates, draws, chain=None):
    from app.analysis.events import BIG

    pick = np.full(draws, -1, dtype=np.int64)
    best = np.full(draws, BIG, dtype=np.int64)
    # Explicit semantic ties: levy response before levy; levy before other
    # questions; floor before cash-out. Remaining prerequisites keep cursor order.
    for i in sorted(range(len(candidates)), key=lambda i: (candidates[i].phase, candidates[i].order)):
        c = candidates[i]
        if chain is not None and c.chain != chain:
            continue
        earlier = c.day < best
        pick[earlier], best[earlier] = i, c.day[earlier]
    pick.flags.writeable = False
    return pick


def next_decisions(chain, cursors: Cursors, support=None) -> Frontier:
    """Read dates on booked state, without advancing waiting answers or levies.

    Cash-trigger dates are prospective: recompute after the selected boundary.
    A pending levy is included as a deterministic boundary so a caller cannot
    mistake a later ripe response's prospective cash for its before-answer cash.
    Only calculation caches may be populated; no semantic state is changed.
    """
    from app.analysis.events import BIG, answers_levy

    candidates = []
    pet = np.where(chain.ev.petition < 0, BIG, chain.ev.petition)

    def add(name, decision, day, phase):
        day = np.where((day >= 0) & (day < chain.N) & (day < pet), day, BIG)
        if support is not None:
            day = np.where(support, day, BIG)
        day.flags.writeable = False
        candidates.append(Candidate(name, decision, day, phase, len(candidates)))

    for name in ('litigation', 'distress', 'listing', 'notes'):
        for decision in getattr(cursors, name):
            phase = 0 if answers_levy(decision.node, decision.ctx) else 2
            if decision.node == 'cash_out':
                phase = 3
            add(name, decision, chain.decision_day(decision.node, decision.ctx), phase)
    if chain.pending_levy is not None:
        add('deterministic', Decision('levy'), chain.pending_levy, 1)
    candidates = tuple(candidates)
    return Frontier(candidates, select(candidates, chain.n))

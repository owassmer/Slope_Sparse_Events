"""The exact evaluator's cash reads for one draw (design/002-exact-evaluator.md, port order 2): what `events.Chain`
reads of the company's cash under daily processing (`cum`, `processed`, `processing_balance`, `decision_cash`,
`cash_at`), from the compiled daily line (`app/analysis/line_state.py`) run on the event cash booked so far (`D`).

The line runs forward from a saved line state at day `a` over the days a..N (`forward`); a day's cash reads nothing
booked after it, so a state saved at day a stays valid while nothing is booked before a. From the review date it is
`Chain.processed`'s whole run. The lock the line reads is the booked lock plus every approved stay's security
(`Chain.lock_cash`): the stays' area computes it; here it is an input.
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np
from numba import njit

from app.analysis import line_state as ls
from app.analysis.events import OBLIGATIONS
from app.exact import fields as f

OBL_ROWS = np.array([f.D_KIND[c] for c in OBLIGATIONS], dtype=np.int64)  # D rows of the obligations, in order
OBL_INC = np.array([f.INCURRED[c] for c in OBLIGATIONS], dtype=np.int64)  # S fields of their incurred days

# One draw's line: its immutable tables, the line state at the review date, the operating receipts and payments,
# and the buffers a run writes (caller-owned, reused).
Line = namedtuple("Line", "tables initial ops_in ops_out post levy out obl inc lock capacity outputs draws scratch "
                          "state opening need")


def line_for(chain, r: int) -> Line:
    """Row r of the chain's draws: the daily line `Chain._daily_run` runs (`engine.run` on the same opening and
    nonpayment terms), prepared once."""
    from app.analysis.events import EventCash

    line, days = chain.basis.line, chain.N
    opening = chain.basis.opening - chain.s.exposure.cash_cents
    empty = EventCash.zeros(chain.n, days, kinds=True)
    state, tables, _, outputs, draws, scratch = ls.prepare_draw(line, opening, empty, r, chain.nonpayment_terms())
    nobl = len(OBLIGATIONS)
    return Line(tables, state.copy(), np.ascontiguousarray(line.ops.inflow[r, :days], dtype=np.int64),
                np.ascontiguousarray(-line.ops.outflow[r, :days], dtype=np.int64),
                np.zeros(days, np.int64), np.zeros(days, np.int64), np.zeros(days, np.int64),
                np.zeros((nobl, days), np.int64), np.zeros(nobl, np.int64), np.zeros(days, np.int64),
                np.zeros(days, np.int64), outputs, draws, scratch, state, int(chain.basis.opening),
                np.ascontiguousarray(chain.basis.need[r], dtype=np.int64))


@njit(cache=True)
def event_rows(S, D, lock, ln, a):
    """`processor._rows` for this draw from day a: what posts before anything is paid (operating receipts and event
    inflows less the lock's change), the levy, the operating payments less the reductions, the obligations and the
    days they were incurred."""
    for t in range(a, D.shape[1]):
        ln.post[t] = ln.ops_in[t] + D[f.D_KIND_INFLOW, t] - lock[t]
        ln.levy[t] = -D[f.D_KIND_LEVY, t]
        ln.out[t] = ln.ops_out[t] - D[f.D_KIND_REDUCTION, t]
        ln.lock[t] = lock[t]
        ln.capacity[t] = D[f.D_CAPACITY, t]
        for c in range(OBL_ROWS.size):
            ln.obl[c, t] = -D[OBL_ROWS[c], t]
    for c in range(OBL_INC.size):
        ln.inc[c] = S[OBL_INC[c]]


@njit(cache=True)
def forward(S, D, lock, ln, saved, a):
    """Run the line from `saved` (its state at day a: `ln.initial` at 0) over the days a..N on the event cash booked
    now; `ln.outputs[0]` is the end-of-day available cash (`Chain.cum`) from a on. Returns (first unpaid day, the
    §3.3 general-nonpayment day), BIG where none: `Chain.processed()[1:]`."""
    event_rows(S, D, lock, ln, a)
    cash = ls.Cash(ln.post, ln.levy, ln.out, ln.obl, ln.inc, ln.lock, ln.capacity, S[f.PETITION])
    ls.restore(ln.state, saved)
    ls.advance(ln.state, ln.tables, cash, a, D.shape[1], ln.outputs, ln.draws, ln.scratch)
    return ln.state[ls.FIRST_UNPAID], ln.state[ls.NONPAYMENT]


@njit(cache=True)
def cash_at(ln, day):
    """`Chain.cash_at`: the end-of-day cash on the day (clipped to the period), after `forward`."""
    t = min(max(day, 0), ln.outputs.shape[1] - 1)
    return ln.outputs[0, t]


@njit(cache=True)
def processing_balance(D, ln, day):
    """`Chain.processing_balance` (and `decision_cash` under daily processing): the day before's end balance plus the
    day's receipts, less the day's lock change, plus any levy already booked that day; after `forward`."""
    N = D.shape[1]
    t = min(max(day, 0), N - 1)
    prev = ln.outputs[0, t - 1] if t > 0 else ln.opening
    return prev + ln.ops_in[t] + D[f.D_KIND_INFLOW, t] - ln.lock[t] + D[f.D_KIND_LEVY, t]

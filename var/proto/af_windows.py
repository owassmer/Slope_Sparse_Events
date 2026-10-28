"""Where the window rule's cost goes: decisions walked while a two-part decision's later question waits, by the kind
of the waiting question, printed every 30 s (a line stopped at its limit still answers)."""
import collections
import signal
import sys
import time

import numpy as np

from app.analysis.population_walk import context
from app.disputes.answer_first import AnswerFirstWalk, Guide, stand_in

draws, tau, reps = int(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
t0 = time.monotonic()
loops, inside, emitted = [0], collections.Counter(), [0]
opened = collections.Counter()
last = [t0]


def report(final=False):
    total_in = sum(inside.values())
    print(f"{time.monotonic() - t0:.0f}s loops {loops[0]} inside-window {total_in} emitted {emitted[0]}"
          + (" FINAL" if final else ""), flush=True)
    for kinds, n in inside.most_common(8):
        print(f"   inside {n:7d}  waiting: {', '.join(sorted(kinds))}", flush=True)
    for k, n in opened.most_common(8):
        print(f"   opened {n:7d}  {k}", flush=True)


orig_loop, orig_emit, orig_opens = AnswerFirstWalk._loop, AnswerFirstWalk.emit, AnswerFirstWalk._opens_window


def loop(self, s, chain, pending, outcome, committed, mask, rows):
    loops[0] += 1
    waiting = frozenset(self.fc.nodes[k].node for k, _i, day, *_ in self._question_queue
                        if (day[mask] < self.N).any() and k in self.fc.nodes)
    if waiting:
        inside[waiting] += 1
    if time.monotonic() - last[0] > 30:
        last[0] = time.monotonic()
        report()
    return orig_loop(self, s, chain, pending, outcome, committed, mask, rows)


def opens(self, parent, y):
    hit = orig_opens(self, parent, y)
    if hit:
        new = [k for k, _ in y.late[len(parent.late):]]
        for k in new:
            if k in self.fc.nodes:
                opened[self.fc.nodes[k].node] += 1
    return hit


def emit(self, s, o):
    emitted[0] += 1
    return orig_emit(self, s, o)


AnswerFirstWalk._loop, AnswerFirstWalk.emit, AnswerFirstWalk._opens_window = loop, emit, opens
signal.signal(signal.SIGTERM, lambda *_: (report(True), sys.exit(0)))
fc, d = context()
fc.draws = fc.draws.sub(np.arange(fc.draws.n) < draws)
fc.answer_first = dict(guide=Guide(fc, stand_in), tau=tau, replicates=reps, seed=7)
fc.all_paths()
report(True)

"""Sanity of the answer-first walk with the stand-in judge: per draw, the head's mass plus each replicate's tail."""
import sys
import time

import numpy as np

from app.analysis.population_walk import context
from app.disputes.answer_first import Guide, stand_in
from app.disputes.forecast import Dist, expand_classes, path_mask, path_probability

import os
if os.environ.get("NO_WINDOWS"):  # the walk as it was before the window rule, for comparison
    from app.disputes.answer_first import AnswerFirstWalk
    AnswerFirstWalk._window_open = lambda self, mask: False
    AnswerFirstWalk._opens_window = lambda self, parent, y: False

draws, tau, reps = int(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
t0 = time.monotonic()
fc, d = context()
fc.draws = fc.draws.sub(np.arange(fc.draws.n) < draws)
fc.answer_first = dict(guide=Guide(fc, stand_in), tau=tau, replicates=reps, seed=int(sys.argv[4]) if len(sys.argv) > 4 else 7)
per = fc.all_paths()
t1 = time.monotonic()
paths = [p for v in per.values() for ps in v.values() for p in ps]
expanded = expand_classes(paths, set(fc.nodes), fc.draws.n)
judged = Dist({k: stand_in(k.split("|#")[0], n.branches) for k, n in fc.nodes.items()})
n = fc.draws.n
head = np.zeros(n)
tail = np.zeros((reps, n))
for p in expanded:
    m = path_mask(p, n)
    m = np.ones(n, bool) if m is None else m
    w = path_probability(p.edges, judged) / p.divisor
    if p.replicate < 0:
        head[m] += w
    else:
        tail[p.replicate, m] += w * reps
est = head + tail.mean(axis=0)
print(f"walk {t1 - t0:.0f}s draws {n} tau {tau} replicates {reps}: paths {len(paths)} expanded {len(expanded)} "
      f"head {sum(p.replicate < 0 for p in paths)} questions {len(fc.nodes)}")
print(f"head mass per draw {head.min():.4f}-{head.max():.4f}; each replicate's total per draw "
      f"{(head + tail).min():.4f}-{(head + tail).max():.4f}; estimate per draw {est.min():.4f}-{est.max():.4f}, "
      f"mean {est.mean():.5f}")
means = (head + tail).mean(axis=1)
se = means.std(ddof=1) / np.sqrt(reps)
print("replicate means (each: per-draw total averaged over draws): " + ", ".join(f"{m:.4f}" for m in means))
print(f"estimate {means.mean():.5f}, standard error {se:.5f}, (estimate - 1) / se = {(means.mean() - 1) / se:.2f}")

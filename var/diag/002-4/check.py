"""002-4 on the real case, on its own output: the analysis's time per history, its spool size, and the invariants it
states (app/analysis/core.py docstring): no daily cash value outside its final bins, and every daily cash percentile
within one bin width of the exact weighted percentile computed from the per-draw values. Expectations are sums of
per-draw values, exact by construction."""
import sys
import time
from pathlib import Path

import numpy as np

from app.analysis.build import judged_model, run_context
from app.analysis.core import QS, Analysis, Reduction
from app.analysis.stats import weighted_quantiles
from app.analysis.walk_measurement import LEAD_RUN

tau, reps = float(sys.argv[1]), int(sys.argv[2])
ctx = run_context(LEAD_RUN, Path("runs/recorded"))
setup = ctx["setup"]
days = (setup.horizon - setup.review).days
SAMPLE = np.arange(0, days, 15)
t0 = time.monotonic()
fc, model = judged_model(ctx, setup, None, [], answer_first=dict(tau=tau, replicates=reps, seed=0))
t1 = time.monotonic()
print(f"walk {t1 - t0:.0f}s: {len(model.combos)} histories, {len(model.bank_combos)} ordinary", flush=True)

kept, spool = {}, {}
add, finish = Reduction.add, Reduction.finish_histograms


def keep_add(self, i, t, ev=None, mask=None):
    m = None if mask is None or mask.all() else mask
    kept.setdefault(id(self), {})[i] = (t.cash if m is None else t.cash[m])[:, SAMPLE].copy()
    return add(self, i, t, ev, mask)


def keep_finish(self, bins):
    spool[id(self)] = self._histogram_spool.seek(0, 2)
    return finish(self, bins)


Reduction.add, Reduction.finish_histograms = keep_add, keep_finish
a = Analysis(ctx["feed"], setup, model, dispute_model=ctx["m"])
t2 = time.monotonic()
n = len(model.combos) + len(model.bank_combos)
print(f"analysis {t2 - t1:.0f}s: {(t2 - t1) / n:.3f}s per history over {n}; spool "
      f"{sum(spool.values()) / 1e6:.0f} MB ({sum(spool.values()) / n / 1e6:.2f} MB per history)", flush=True)

ok = True
for name, r, probs in (("event", a.r, model.probs()), ("ordinary", a.bank_r, model.bank_probs())):
    b, rows = r.bins["cash"], kept[id(r)]
    lo, hi = b.lo[SAMPLE], b.lo[SAMPLE] + b.n * b.width[SAMPLE]
    outside = sum(int(((v < lo) | (v >= hi)).sum()) for v in rows.values())
    vals = np.concatenate([rows[i] for i in sorted(rows)])
    w = np.concatenate([np.full(len(rows[i]), probs[i]) for i in sorted(rows)])
    exact = weighted_quantiles(vals.astype(np.float64), w / w.sum(), QS)
    binned = b.quantiles(r.counts["cash"].weighted(probs), QS)[:, SAMPLE]
    dev = np.abs(binned - exact) / b.width[SAMPLE]
    print(f"{name}: {len(rows)} histories, {vals.shape[0]} history-draws, {len(SAMPLE)} days: values outside their "
          f"bins {outside}; percentile deviation in bin widths max {dev.max():.3f} (bin width median "
          f"${np.median(b.width[SAMPLE]) / 100:,.0f})", flush=True)
    ok &= outside == 0 and dev.max() <= 1.0
print("PASS" if ok else "FAIL", flush=True)

"""Pass 2 (app/exact/tree.py evaluate) on a whole draw: every history's weight is the product of its answers'
probabilities, so the weights over the draw's tree sum to one under any judge, and each day's histogram holds that
mass; the identities pass 1 found are every question pass 2 meets (it raises on one it did not)."""
import numpy as np

from app.analysis.events import Chain
from app.exact import fields_walk as w
from app.exact import oracle, pass1, pass2


def _skewed(row):
    n = int(row[w.QNANS])
    p = np.arange(1, n + 1, dtype=np.float64)
    return p / p.sum()


def test_the_weights_of_a_draws_histories_sum_to_one():
    fc, d = oracle.context((145,))
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = pass1.case_tables(fc, d, ch0)
    mem, _, nodes, leaves, _, reads, (lo, hi) = pass1.explore(ch0, 0, tk)
    lk = pass2.constants(ch0, pass2.bins(ch0, lo, hi))
    vprob = pass2.verdict_probs(fc, d, lambda key: {"yes": 0.3, "no": 0.7})
    for judge in (pass2.uniform, _skewed):
        qhash, qprob = pass2.table(mem.rows, mem.ids[:, 1], judge)
        total, nleaves, acc = pass2.evaluate(ch0, 0, tk, lk, qhash, qprob, vprob, forced=reads)
        assert nleaves == leaves
        assert abs(total - 1.0) < 1e-9
        np.testing.assert_allclose(acc["cash"].sum(axis=1), total, rtol=1e-9)
        np.testing.assert_allclose(acc["collected"].sum(axis=1), total, rtol=1e-9)

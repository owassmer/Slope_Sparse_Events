"""Pass 2 (app/exact/tree.py evaluate) on a whole draw: every history's weight is the product of its answers'
probabilities, so the weights over the draw's tree sum to one under any judge, and each day's histogram holds that
mass; the identities pass 1 found are every question pass 2 meets (it raises on one it did not). Its terms move every
figure exactly: changing one decision's answers moves the expected figures by the change times its terms, as walking
the draw again under the changed answers finds."""
import numpy as np
import pytest

from app.analysis.events import Chain
from app.exact import fields_walk as w
from app.exact import leaf, oracle, pass1, pass2


def _skewed(row):
    n = int(row[w.QNANS])
    p = np.arange(1, n + 1, dtype=np.float64)
    return p / p.sum()


@pytest.fixture(scope="module")
def draw():
    fc, d = oracle.context((145,))
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = pass1.case_tables(fc, d, ch0)
    mem, _, nodes, leaves, _, reads, (lo, hi) = pass1.explore(ch0, 0, tk)
    lk = pass2.constants(ch0, pass2.bins(ch0, lo, hi))
    nclasses = len(fc.verdict_classes(d))
    vprob = pass2.verdict_probs(fc, d, lambda key: {"yes": 0.3, "no": 0.7})
    return fc, d, ch0, tk, mem, leaves, reads, lk, nclasses, vprob


def test_the_weights_of_a_draws_histories_sum_to_one(draw):
    fc, d, ch0, tk, mem, leaves, reads, lk, nclasses, vprob = draw
    for judge in (pass2.uniform, _skewed):
        ans = pass2.table(mem.rows, mem.ids[:, 1], judge, nclasses)
        total, nleaves, acc = pass2.evaluate(ch0, 0, tk, lk, ans, vprob, forced=reads)
        assert nleaves == leaves
        assert abs(total - 1.0) < 1e-9
        np.testing.assert_allclose(acc["cash"].sum(axis=1), total, rtol=1e-9)
        np.testing.assert_allclose(acc["collected"].sum(axis=1), total, rtol=1e-9)
        np.testing.assert_allclose(acc["low"].sum(axis=1), total, rtol=1e-9)
        assert abs(acc["floor"].sum() - total) < 1e-9
        # the expected figure vector is the figures themselves: the scalars, and each day row summed by month
        months = pass2.months_of(ch0)
        _, month_of = pass2.calendar(ch0.s, ch0.N)
        np.testing.assert_allclose(acc["root"][:leaf.NSC], acc["scalars"], rtol=1e-9, atol=1e-6)
        by_month = np.stack([acc["day"][:, month_of == m].sum(axis=1) for m in range(months)], axis=1)
        np.testing.assert_allclose(acc["root"][leaf.NSC:].reshape(leaf.NDAY, months), by_month, rtol=1e-9, atol=1e-6)
        assert not acc["repeats"].any()


def test_terms_move_the_figures_exactly(draw):
    fc, d, ch0, tk, mem, leaves, reads, lk, nclasses, vprob = draw
    base = pass2.table(mem.rows, mem.ids[:, 1], _skewed, nclasses)
    _, _, acc = pass2.evaluate(ch0, 0, tk, lk, base, vprob, forced=reads)
    T, root = acc["T"], acc["root"]
    # the questions whose answers move the figures most (each its own decision here), changed one at a time
    q_of_row = np.repeat(np.arange(len(base.keys)), base.nans)
    weight = np.zeros(len(base.keys))
    np.add.at(weight, q_of_row[:base.vbase], np.abs(T[:base.vbase]).sum(axis=1))
    for q in np.argsort(weight)[::-1][:4]:
        n, b0 = int(base.nans[q]), int(base.base[q])
        new = np.full(n, 1.0 / n) if n > 1 else np.ones(1)
        sel = base.qpair == b0
        old = base.qprob[sel][0, :n].copy()
        prob = base.qprob.copy()
        prob[sel, :n] = new
        _, _, moved = pass2.evaluate(ch0, 0, tk, lk, base._replace(qprob=prob), vprob, forced=reads)
        expect = root + (new - old) @ T[b0:b0 + n]
        np.testing.assert_allclose(moved["root"], expect, rtol=1e-7, atol=1e-3 * np.abs(root).max())
    # the verdict's classes
    v2 = np.full_like(vprob, 1.0 / len(vprob))
    _, _, moved = pass2.evaluate(ch0, 0, tk, lk, base, v2, forced=reads)
    expect = root + (v2 - vprob) @ T[base.vbase:base.vbase + len(vprob)]
    np.testing.assert_allclose(moved["root"], expect, rtol=1e-7, atol=1e-3 * np.abs(root).max())


def test_an_unknown_answer_stops_the_walk(draw):
    fc, d, ch0, tk, mem, leaves, reads, lk, nclasses, vprob = draw
    ans = pass2.table(mem.rows, mem.ids[:, 1], pass2.uniform, nclasses)
    prob = ans.qprob.copy()
    prob[:] = np.nan
    with pytest.raises(ValueError, match="unknown"):
        pass2.evaluate(ch0, 0, tk, lk, ans._replace(qprob=prob), vprob, forced=reads)

"""The post-trial ruling (QUESTIONS J2, C3): a reduction, outright or by remittitur, to any band below the award's,
each its own answer stating the band the walk books (above its low end, at most its high end); the compiled ruling's
probabilities exhaustive over those answers, the remittitur's election per band."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.disputes import state14
from app.disputes.forecast import Forecaster
from app.exact import fields_walk as w
from app.exact import pass1, tree


@pytest.fixture(scope='module')
def fixture():
    d = fx.pending()
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    bands = [b for b in fc.verdict_lines(d, fc.equity_inflows(d))['bands'] if b[1] > 0]
    classes = [f'award:{booked}:{lo}:{"top" if i == len(bands) - 1 else hi}' for i, (lo, hi, booked) in enumerate(bands)]
    return fc, d, bands, classes


def test_every_band_below_the_award_is_an_answer(fixture):
    fc, d, bands, classes = fixture
    lower, own = pass1.lower_bands(fc, d, classes)
    for i, (lo, hi, booked) in enumerate(bands):
        got = [tuple(x) for x in lower[i] if x[0] >= 0]
        assert got == [(b, low, high) for low, high, b in bands[:i + 1]]  # every band up to its own, lowest first
        below = got[:-1]
        branches = ('unchanged', *(f'reduced@{b[0]}' for b in below), *(f'remitted@{b[0]}' for b in got),
                    'set_aside')
        key = fc.node(d, 'post_trial_ruling', f'award{booked}', branches=branches)
        assert own[i]  # a banded award has a band of its own
        fc.ruling_bands[key] = (got, True)
        row = {'day': np.array([60]), 'cash': np.array([100]), 'owed': np.array([booked]),
               'petition': np.array([-1]),
               'sit': {'entry': np.array([1]), 'entered': booked, 'motions_filed': np.array([29]),
                       'events': np.array([[]] + [None], dtype=object)[:1], 'offerings': np.array([[]] + [None], dtype=object)[:1],
                       'motions_deadline': np.array([29]), 'ruling': np.array([60]),
                       'band_range': (lo, None if i == len(bands) - 1 else hi)}}
        state, _, _ = state14.build(fc, fc.nodes[key], d, [], [row], [np.array([True])])
        answers = state['question']['answers']
        assert list(answers) == list(branches)
        for b, low, high in got:
            band = state14.band_text(low, high)
            assert band in answers[f'remitted@{b}']
            assert (f'reduced@{b}' in answers) == ((b, low, high) in below)
            assert ('positive amount of at most' in band) == (low == 0)
        assert state14.band_text(got[-1][1], got[-1][2]) in answers['unchanged']  # the award's own band
        assert 'reduced_band' not in state['situation']


@pytest.mark.parametrize('nb,own', [(0, True), (1, True), (2, True), (5, True), (1, False), (3, False)])
def test_the_rulings_probabilities_and_their_derivatives(nb, own):
    """They sum to one, and each derivative is right: moving probability between two answers of one question moves
    each outcome by the difference of its derivatives (finite differences on `decision_probs` itself)."""
    rng = np.random.default_rng(nb)
    nel = nb + 1 if own else nb  # elections: one per band, the award's own too where it has one
    m, nq = nb + 3, 1 + nel
    P = np.zeros((tree.MAX_Q, tree.MAX_ANS))
    P[0, :2 + nb + nel] = rng.dirichlet(np.ones(2 + nb + nel))
    for b in range(nel):
        P[1 + b, :2] = rng.dirichlet(np.ones(2))
    ans = np.arange(m)

    def probs(P):
        out = np.zeros(tree.MAX_ANS)
        tree.decision_probs(w.DEC_RULING, m, ans, nq, P, np.zeros(1), out)
        return out[:m]

    assert abs(probs(P).sum() - 1) < 1e-12
    G = np.zeros((tree.MAX_Q, tree.MAX_ANS, tree.MAX_ANS))
    tree.decision_grads(w.DEC_RULING, m, nq, P, G)
    eps = 1e-6
    for q in range(nq):
        n = 2 + nb + nel if q == 0 else 2
        for x in range(n):
            y = (x + 1) % n
            up, down = P.copy(), P.copy()
            up[q, x] += eps
            up[q, y] -= eps
            down[q, x] -= eps
            down[q, y] += eps
            np.testing.assert_allclose((probs(up) - probs(down)) / (2 * eps), G[q, :m, x] - G[q, :m, y], atol=1e-7)


def test_an_exact_award_is_reduced_to_the_part_of_its_band_below_it(fixture):
    """An exact award (the small claims at their amounts, J1) has no band of its own: a reduction reaches every band
    wholly below it and the part of its own band below it, booked at that part's midpoint, never above the award."""
    fc, d, bands, classes = fixture
    lo, hi, _ = bands[1]
    exact = lo + (hi - lo) // 3
    lower, own = pass1.lower_bands(fc, d, [f'award:{exact}:{exact}:{exact}'])
    got = [tuple(x) for x in lower[0] if x[0] >= 0]
    assert not own[0]
    assert got[:-1] == [(b, low, high) for low, high, b in bands[:1]]
    booked, low, high = got[-1]
    assert (low, high) == (lo, exact - 1) and low < booked < exact

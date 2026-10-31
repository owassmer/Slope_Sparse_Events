from types import SimpleNamespace

import numpy as np

from app.analysis.tree_concentration import Histogram, distributions
from app.analysis.tree_pool import random_dist


def test_bins_merge_exactly(tmp_path):
    whole, left, right = Histogram(), Histogram(), Histogram()
    for i, p in enumerate((.25, .25, .125, .125, .03125, .015625)):
        mask = np.arange(512) % (i + 1) == 0
        whole.add(0, p, mask)
        (left if i % 2 else right).add(0, p, mask)
    left.save(tmp_path / 'left.npz')
    merged = Histogram.load(tmp_path / 'left.npz')
    merged.merge(right)
    assert whole.counts.keys() == merged.counts.keys()
    for key in whole.counts:
        np.testing.assert_array_equal(whole.counts[key], merged.counts[key])
        # Binary-exact probabilities also give bitwise-exact summed mass.
        np.testing.assert_array_equal(whole.mass[key], merged.mass[key])


def test_cutoffs_enclose_exact_sort_and_absolute_unreachable():
    hist = Histogram()
    rng = np.random.default_rng(71)
    probabilities = rng.dirichlet(np.ones(600)) * .95
    mask = np.ones(512, bool)
    for p in probabilities:
        hist.add(0, p, mask)
    ordered = np.sort(probabilities)[::-1].cumsum()
    for row in hist.rows():
        if row['distribution'] != 'random-17':
            continue
        target = row['share'] * (.95 if row['basis'] == 'walked' else 1)
        if target > .95:
            assert row['count_lower'] is row['count_upper'] is None
        else:
            exact = np.searchsorted(ordered, target) + 1
            assert row['count_lower'] <= exact <= row['count_upper']
        assert abs(row['walked_mass'] - .95) < 1e-14


def test_seeded_distributions():
    nodes = {'a': SimpleNamespace(branches=('yes', 'no', 'other')),
             'b': SimpleNamespace(branches=('only',))}
    dists = distributions(nodes)
    assert dists[:3] == [random_dist(nodes, s) for s in (17, 91, 307)]
    assert distributions(dict(reversed(list(nodes.items())))) == dists
    for dist in dists[3:6]:
        assert sorted(dist['a'].values()) == [(1 - .8) / 2] * 2 + [.8]
        assert dist['b'] == {'only': 1.}
    for dist in dists[6:]:
        assert max(dist['a'].values()) == .95
        assert np.isclose(sum(dist['a'].values()), 1)

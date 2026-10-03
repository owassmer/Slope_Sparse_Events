from types import SimpleNamespace

import numpy as np

from app.disputes.forecast import DisputePath, pack_mask
from tools.question_mass_check import distributions, totals


def test_partial_jobs_sum_across_all_draws_without_per_job_normalization():
    nodes = {'q': SimpleNamespace(branches=('yes', 'no'))}
    draws = 512
    masks = [np.arange(draws) % 2 == parity for parity in (0, 1)]
    paths = [DisputePath('d', (), 'done', (('q', answer),), mask=pack_mask(mask))
             for mask in masks for answer in ('yes', 'no')]
    pieces = [totals([path], nodes, draws, frozenset(), distributions(nodes), {}) for path in paths]
    assert all(not np.allclose(value['d'], 1) for _, value in pieces)
    assert sum(count for count, _ in pieces) == 4
    total = sum(value['d'] for _, value in pieces)
    np.testing.assert_allclose(total, np.ones((3, draws)), atol=1e-10, rtol=0)
    broken = sum(value['d'] for _, value in pieces[:-1])
    assert not np.allclose(broken, 1)

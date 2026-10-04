from types import SimpleNamespace

import numpy as np
import pytest

from app.disputes.forecast import DisputePath, pack_mask
from tools.question_mass_check import distributions, totals


def test_dead_option_group_uses_the_live_choice_domain_on_all_draws():
    from app.disputes.forecast import class_firsts
    nodes = {'q|#dead.g3': SimpleNamespace(branches=('pay', 'initiate_offering', 'file', 'none')),
             'q|#live.g2': SimpleNamespace(branches=('initiate_offering', 'file', 'none'))}
    dead = {'q|#dead.g3'}
    paths = [DisputePath('d', (), 'done', (('q', answer),),
                        classes=(('q', ('#dead.g3',), None),))
             for answer in nodes['q|#dead.g3'].branches]
    count, result = totals(paths, nodes, 512, dead, distributions(nodes), class_firsts(nodes, dead))
    assert count == 4
    np.testing.assert_allclose(result['d'], 1, atol=1e-10, rtol=0)


def test_unavailable_answers_have_no_probability_or_derivative_but_zero_probabilities_do():
    from app.analysis.core import EventModel
    from app.analysis.reduce import atoms_derivative, edge_prob, group_probs
    from app.disputes.forecast import Dist, composite, path_probability
    d = Dist({'q': {'file': 0.0, 'none': 1.0}, 'r': {'yes': .4, 'no': .6}})
    absent = (('q', 'pay'), ('r', 'yes'))
    assert path_probability(absent, d) == 0
    assert edge_prob('q', 'pay', d) == 0
    assert group_probs(absent, [d]).tolist() == [0]
    model = SimpleNamespace(_dist=lambda overrides: d)
    p = DisputePath('d', (), 'done', absent)
    assert EventModel._weigh(model, 'test', [(p,)], None).tolist() == [0]
    assert atoms_derivative(absent, d) == []
    assert atoms_derivative((('q', 'file'), ('r', 'yes')), d) == [('q', 'file', .4)]
    c = composite([[('q', 'pay'), ('r', 'yes')], [('q', 'file'), ('r', 'no')]])
    assert d[c]['yes'] == 0
    assert atoms_derivative(((c, 'yes'),), d) == [('q', 'file', .6)]
    assert atoms_derivative(((c, 'no'),), d) == [('q', 'file', -.6)]
    with pytest.raises(KeyError, match='unknown'):
        path_probability((('unknown', 'pay'),), d)


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

from types import SimpleNamespace

import numpy as np
import pytest

from app.disputes.forecast import DisputePath, pack_mask
from tools.question_mass_check import distributions, totals


def test_dead_option_group_preserves_its_saved_answer_domain_on_all_draws():
    from app.disputes.forecast import class_firsts, expand_classes
    nodes = {'q|#dead.g0': SimpleNamespace(branches=('file', 'none')),
             'q|#a.g2': SimpleNamespace(branches=('initiate_offering', 'file', 'none')),
             'q|#z.g0': SimpleNamespace(branches=('file', 'none'))}
    dead = {'q|#dead.g0'}
    paths = [DisputePath('d', (), 'done', (('q', answer),),
                        classes=(('q', ('#dead.g0',), None),))
             for answer in nodes['q|#dead.g0'].branches]
    first = class_firsts(nodes, dead)
    assert first['q'] == 'q|#a.g2'  # the global first class has a different domain
    expanded = expand_classes(paths, nodes, 512, dead, first)
    assert [p.edges for p in expanded] == [(('q|#z.g0', a),) for a in ('file', 'none')]
    live = {key: node for key, node in nodes.items() if key not in dead}
    count, result = totals(paths, nodes, 512, dead, distributions(live), first)
    assert count == 2
    np.testing.assert_allclose(result['d'], 1, atol=1e-10, rtol=0)


def test_missing_answers_fail_instead_of_hiding_incorrect_class_substitution():
    from app.analysis.core import EventModel
    from app.analysis.reduce import atoms_derivative, edge_prob, group_probs
    from app.disputes.forecast import Dist, composite, path_probability
    d = Dist({'q': {'file': 0.0, 'none': 1.0}, 'r': {'yes': .4, 'no': .6}})
    absent = (('q', 'pay'), ('r', 'yes'))
    model = SimpleNamespace(_dist=lambda overrides: d)
    p = DisputePath('d', (), 'done', absent)
    calls = (lambda: path_probability(absent, d), lambda: edge_prob('q', 'pay', d),
             lambda: group_probs(absent, [d]), lambda: EventModel._weigh(model, 'test', [(p,)], None),
             lambda: atoms_derivative(absent, d), lambda: d[composite([[('q', 'pay')]])])
    for call in calls:
        with pytest.raises(KeyError, match='pay'):
            call()
    assert atoms_derivative((('q', 'file'), ('r', 'yes')), d) == [('q', 'file', .4)]
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

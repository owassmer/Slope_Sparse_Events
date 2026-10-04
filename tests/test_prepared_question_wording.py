from datetime import date
from types import SimpleNamespace

import numpy as np

from app.agent.jev import build_question, registry_question
from app.analysis.events import BIG
from app.disputes.state14 import Group, Situation, question_text


def test_grouped_question_and_provider_only_offer_recorded_actions():
    entry = registry_question('forecast_judgment_response')
    node = SimpleNamespace(node='judgment_response', branches=('file', 'none'))
    text = question_text(node, entry['prompt']['instructions'],
                         {'company': 'Company', 'decision_date': '28 Oct 2024', 'occasion': 'before the levy'})
    assert text == 'What does Company do on 28 Oct 2024, before the levy: file a voluntary petition, or none of these?'
    restricted = {**entry, 'prompt': {**entry['prompt'], 'criteria': {
        b: entry['prompt']['criteria'][b] for b in node.branches}}}
    provider = build_question(restricted).model_dump(mode='json')
    assert 'state.question.text' in provider['instructions']
    assert 'pay the judgment' not in provider['instructions']
    assert set(provider['criteria']) == set(node.branches)


def test_cash_floor_keeps_trigger_but_excludes_unavailable_offering():
    entry = registry_question('forecast_financing_at_floor')
    node = SimpleNamespace(node='financing_at_floor', branches=('file', 'neither'))
    text = question_text(node, entry['prompt']['instructions'], {
        'company': 'Company', 'decision_date': '11 Oct 2024',
        'projected_cash_after_payments': '$2.59 million', 'operating_need_30_days': '$2.62 million'})
    assert 'falls below' in text and '$2.59 million' in text
    assert 'offering' not in text and 'file a voluntary petition' in text


def test_proposed_remittitur_is_not_the_pre_answer_accepted_amount():
    key = 'd:remittitur_elected|award2672665250|remit2397555350'
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None,
                         remitted={key: (2397555350, 2260000400, 2535110300)})
    s = Situation(fc, SimpleNamespace(key=key+'|#class'), None, None, [], {})
    assert s.remitted_amount() == '$23,975,553.50'


def test_known_ruling_establishes_deadline_beyond_simulation_horizon():
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None,
                         m={'rules': {'frap_4a1a': {'value': 30}}})
    d = SimpleNamespace(stage='liability_pending', financing=())
    row = {'day': np.array([155]), 'cash': np.array([100]), 'triggers': {},
           'sit': {'ruling': np.array([155])}}
    g = Group.of([row], [np.array([True])])
    s = Situation(fc, None, d, g, [], {})
    assert s.appeal_deadline() == '16 Nov 2024'
    row['sit']['ruling'][:] = BIG
    assert s.appeal_deadline() == 'not applicable'


def test_motion_security_uses_approval_balance_for_wording_and_eligibility():
    from app.disputes.state14 import eligible, security_kinds
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None, sens={}, days=200,
                         m={'parameters': {'stay_security': {'value': 'cash'}}})
    node = SimpleNamespace(node='stay_motion')
    # The motion-day balances deliberately point in the opposite direction.
    row = {'day': np.array([10, 10]), 'cash': np.array([0, 10000]),
           'collateral': np.array([0, 0]), 'stay_offer': np.array([0, 0]),
           'security_terms': {'day': np.array([40, 40]), 'cash': np.array([10000, 0]),
                              'need': np.array([1000, 1000]), 'collateral': np.array([5000, 5000]),
                              'offer': np.array([0, 0]), 'live': np.array([True, True])}}
    assert security_kinds(fc, node, row).tolist() == ['full', 'none']
    masks = eligible(fc, node, [row], [np.array([True, True])])
    assert masks[0].tolist() == [True, False]
    s = Situation(fc, node, None, Group.of([row], masks), [], {})
    assert s.available_cash() == '$0.00'
    assert s.security_offered() == 'full bond collateral of $50.00 in cash'
    assert s.collateral_required() == '$50.00'


def test_inactive_stay_and_unavailable_future_security_are_not_pending_sizing():
    from app.disputes.state14 import security_kinds
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None, sens={}, days=200,
                         m={'parameters': {'stay_security': {'value': 'cash'}}})
    node = SimpleNamespace(node='stay_motion')
    row = {'day': np.array([10, 10]), 'cash': np.array([10000, 10000]),
           'security_terms': {'day': np.array([40, BIG]), 'cash': np.array([10000, 10000]),
                              'need': np.array([1000, 1000]), 'collateral': np.array([5000, 5000]),
                              'offer': np.array([0, 0]), 'live': np.array([False, False])}}
    assert security_kinds(fc, node, row).tolist() == ['inactive', 'unavailable']
    inactive = Situation(fc, node, None, Group.of([row], [np.array([True, False])]), [], {})
    assert inactive.security_offered() == 'no security takes effect on 24 Jun 2024'
    assert inactive.collateral_required() == 'not applicable: the stay does not take effect'
    unavailable = Situation(fc, node, None, Group.of([row], [np.array([False, True])]), [], {})
    assert unavailable.security_offered() == 'security amount unavailable'
    assert unavailable.collateral_required() == 'not available'

from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from app.agent.jev import build_question, registry_question
from app.analysis.events import BIG
from app.disputes.state14 import Group, Situation, Unbuilt, question_text


def test_grouped_question_and_provider_only_offer_recorded_actions():
    entry = registry_question('forecast_judgment_response')
    node = SimpleNamespace(node='judgment_response', branches=('file', 'none'))
    text = question_text(node, entry['prompt']['instructions'],
                         {'company': 'Company', 'decision_date': '28 Oct 2024', 'occasion': 'before the levy'})
    assert text == ('What does Company do on 28 Oct 2024, before the levy: file a voluntary petition, or continue '
                    'without filing?')
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
    # the band the walk books, never its midpoint stated as the figure the Claimant would accept
    assert s.remitted_amount() == 'an amount of more than $22,600,004.00 and at most $25,351,103.00'


def test_known_ruling_establishes_deadline_beyond_simulation_horizon():
    courts = {k: {'declared_holidays': [], 'through': '2024-12-31'} for k in ('district', 'appeal')}
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None,
                         m={'rules': {'frap_4a1a': {'value': 30}}, 'case_courts': courts})
    d = SimpleNamespace(stage='liability_pending', financing=())
    row = {'day': np.array([155]), 'cash': np.array([100]), 'triggers': {},
           'sit': {'ruling': np.array([155])}}
    g = Group.of([row], [np.array([True])])
    s = Situation(fc, None, d, g, [], {})
    assert s.appeal_deadline() == '18 Nov 2024'  # 30 days from the ruling: Saturday 16 Nov, so the Monday
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
           'security_terms': {'day': np.array([40, 210]), 'cash': np.array([10000, 10000]),
                              'need': np.array([1000, 1000]), 'collateral': np.array([5000, 5000]),
                              'offer': np.array([0, 0]), 'live': np.array([False, False])}}
    assert security_kinds(fc, node, row).tolist() == ['inactive', 'unavailable']
    inactive = Situation(fc, node, None, Group.of([row], [np.array([True, False])]), [], {})
    assert inactive.security_offered() == 'no security takes effect on 24 Jun 2024'
    assert inactive.collateral_required() == 'not applicable: the stay does not take effect'
    unavailable = Situation(fc, node, None, Group.of([row], [np.array([False, True])]), [], {})
    # the court's day after the period: the offer as it will be sized then, its amount not known on the decision date
    assert unavailable.security_offered().startswith('the bond collateral if the company')
    assert 'sized on 11 Dec 2024, when the court decides' in unavailable.security_offered()
    assert unavailable.collateral_required().startswith('set on 11 Dec 2024, when the court decides')


def test_each_dated_event_is_stated_once_and_only_on_or_after_its_day():
    """The record's dated events (`record.events`: every stay motion and its answer, every levy and payment), each with
    its date, in order, and only those on or before the question's day."""
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None)
    d = SimpleNamespace(counterparty='Qorvo', financing=())
    events = [('stay_moved', 10, 0, 10), ('stay_denied', 20, 0, 10), ('stay_moved', 25, 0, 25),
              ('stay_approved', 40, 123_400, 25), ('levied', 45, 5_000_00, 45)]
    for day, want in ((19, 1), (20, 2), (40, 4), (60, 5)):
        evs = np.empty(1, dtype=object)
        evs[0] = events
        offs = np.empty(1, dtype=object)
        offs[0] = []
        row = {'day': np.array([day]), 'cash': np.array([100]), 'marks': {}, 'sit': {'events': evs, 'offerings': offs}}
        s = Situation(fc, None, d, Group.of([row], [np.array([True])]), [], {})
        got = s.historical_events()
        assert len(got) == want, (day, got)
        assert got[0] == 'on 25 May 2024, the company moved for a stay of execution'
        if want >= 2:  # each answer names the motion it answers
            assert got[1] == 'on 4 Jun 2024, the court denied the stay the company moved for on 25 May 2024'
        if want >= 4:
            assert got[3] == ('on 24 Jun 2024, the court approved the stay the company moved for on 9 Jun 2024, the '
                              'company posting $1,234.00 of cash security')
        if want == 5:
            assert got[4] == "on 29 Jun 2024, Qorvo levied $5,000.00 on the company's cash"


def test_prior_denial_does_not_describe_a_new_pending_request_as_denied():
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None)
    row = {'day': np.array([112, 225]), 'cash': np.array([100, 100]),
           'marks': {'stay_denied': np.array([109, 224])},
           'sit': {'stay_status': np.array(['pending', 'denied'], dtype=object)}}
    pending = Situation(fc, None, None, Group.of([row], [np.array([True, False])]), [], {})
    assert pending.stay_events() == ['the current stay request is awaiting a court decision']
    decided = Situation(fc, None, None, Group.of([row], [np.array([False, True])]), [], {})
    assert decided.stay_events() == ['the court denied the latest stay request']
    mixed = Situation(fc, None, None, Group.of([row], [np.array([True, True])]), [], {})
    with pytest.raises(Unbuilt, match='mixes decision-time stay statuses'):
        mixed.stay_events()

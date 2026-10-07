"""Entry, the filing deadline and finality are distinct legal occasions."""
import numpy as np
import pytest
from test_question_futures import sample

from app.analysis.events import BIG, Chain, Trace
from app.disputes.forecast import as_of
from app.disputes.state14 import Group, Situation


def entered():
    fc, d, s = sample()
    ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    tr = Trace(ch.ev)
    for step in s.steps:
        ch.advance(tr, *step)
    return fc, d, ch


@pytest.mark.parametrize('answer', ['yes', 'no'])
def test_entry_window_and_deadlines_do_not_depend_on_later_filing(answer):
    fc, d, ch = entered()
    entry = ch.E_ix.copy()
    start, end = ch.settlement_window('Ientry')
    np.testing.assert_array_equal(start, entry)
    np.testing.assert_array_equal(end, entry + 28)
    np.testing.assert_array_equal(ch.AD, entry + 30)
    ch.step('post_trial_motions', '', answer)
    row = as_of({'day': entry, 'cash': ch.cash_at(entry),
                 'sit': ch.c_situation(entry), 'triggers': ch.trigger_days()})
    np.testing.assert_array_equal(row['triggers']['appeal_deadline'], entry + 30)
    assert (row['sit']['motions_filed'] == BIG).all()
    s = Situation(fc, None, d, Group.of([row], [np.array([True])]), ['Ientry'], {})
    assert 'may be filed until' in s.post_trial_ruling()
    if answer == 'no':
        np.testing.assert_array_equal(ch.F, entry + 28)
        np.testing.assert_array_equal(ch.AD, entry + 30)
        np.testing.assert_array_equal(ch.EF, entry + 31)
    else:
        np.testing.assert_array_equal(ch.AD, ch.F + 30)
        np.testing.assert_array_equal(ch.settlement_window('I1')[0], entry + 28)
        filed = entry + 28
        row = as_of({'day': filed, 'cash': ch.cash_at(filed),
                     'sit': ch.c_situation(filed), 'triggers': ch.trigger_days()})
        assert (row['triggers']['appeal_deadline'] == BIG).all()
        s = Situation(fc, None, d, Group.of([row], [np.array([True])]), ['I1'], {})
        assert s.post_trial_ruling() == 'the post-trial motions are pending'

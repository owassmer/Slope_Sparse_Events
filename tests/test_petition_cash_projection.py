"""Native root 355, draw 404: a stay read after finish must not reverse erased ATM cash."""
import numpy as np
from test_decision_snapshots import case  # noqa: F401

from app.analysis.events import Chain, event_questions
from app.disputes.forecast import DisputePath

STEPS = (
    ('settle', 'I0', 'no'),
    ('verdict', 'I0', 'award:1695000300:1130000200:2260000400'),
    ('judgment_response', 'entry', '@2=none'),
    ('post_trial_motions', '', 'yes'),
    ('settle', 'I1', 'no'),
    ('execute_pre_ruling', 'I1', 'yes'),
    ('stay', 'I1', 'yes'),
    ('registration_early', 'I1', 'no'),
    ('judgment_response', 'ripe', '@2=initiate_offering'),
    ('offering', 'ripe', 'no'),
    ('judgment_default', 'I1', 'no'),
    ('post_trial_ruling', '', 'reduced:1065000100:1000000000:1130000200'),
    ('settle', 'I2', 'no'),
    ('appeal', '', 'no'),
    ('judgment_default', 'ruling', 'holders_file'),
    ('listing', '', 'compliant'),
    ('cash_floor', '1', '@2=initiate_offering'),
    ('offering', 'floor1', 'yes'),
    ('listing_date', 'compliance', ''),
)


def test_draw404_completed_listing_probe(case, monkeypatch):  # noqa: F811
    finish = Chain.finish
    finished = []

    def inspect(chain, *args, **kw):
        tr = finish(chain, *args, **kw)
        if chain.n == 1 and chain.ev.petition[0] == 174:
            # Review + 174 = 4 Nov 2024: this T+1 ATM receipt is stayed by the petition.
            # Keep it in the working ledger so a later repricing can reverse it once,
            # but never count it in the finished cash or proceeds.
            assert chain._atm[0, 174] == 5_438_365
            assert chain.ev.kinds['inflow'][0, 174] == 5_438_365
            assert not tr.events.kinds['inflow'][0, 174:].any()
            assert not tr.events.cash[0, 174:].any()
            assert tr.events.proceeds['atm_proceeds'][0] == chain._atm[0, :174].sum()
            np.testing.assert_array_equal(tr.events.cash, sum(tr.events.kinds.values()))
            assert (tr.events.kinds['inflow'] >= 0).all()
            finished.append(True)
        return tr

    monkeypatch.setattr(Chain, 'finish', inspect)
    fc, d = case
    mask = np.arange(fc.draws.n) == 404
    questions, _ = event_questions(
        d, DisputePath(d.instance_id, STEPS, '', ()), fc.setup, fc.m, fc.draws, fc.sens,
        indices=(-1,), day_only=True, rows=tuple(mask for _ in STEPS))
    assert questions[-1]['day'][404] == 159
    assert finished

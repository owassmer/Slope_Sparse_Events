"""Real payment paths from part1830010 / part1220001: ripe is not a levy response."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.analysis.events import event_trace, group_branches
from app.disputes.forecast import DisputePath, Forecaster

COMMON = (
    ('settle', 'I0', 'no'),
    ('verdict', 'I0', 'award:1065000100:1000000000:1130000200'),
)
MOTION = (
    ('post_trial_motions', '', 'yes'), ('settle', 'I1', 'no'),
    ('execute_pre_ruling', 'I1', 'yes'), ('stay', 'I1', 'yes'),
    ('registration_early', 'I1', 'yes'),
)
NONE = COMMON + (('judgment_response', 'entry', '@3=none'),) + MOTION + (
    ('judgment_response', 'I1', '@-1=none'), ('cash_floor', '1', '@2=neither'),
    ('cash_out', '', '@2=initiate_offering'), ('offering', 'cash_out', 'yes'),
)
OFFER = COMMON + (('judgment_response', 'entry', '@3=initiate_offering'),
                  ('offering', 'entry', 'no')) + MOTION + (
    ('judgment_response', 'I1', '@2=initiate_offering'), ('offering', 'I1', 'yes'),
    ('cash_floor', '1', '@0=neither'),
)


@pytest.mark.parametrize('prefix,rows,listing', [
    (NONE, [279, 383], 'compliant'),
    (OFFER, [7, 63, 149, 215, 225, 325, 329, 380, 393, 406], 'suspended'),
])
@pytest.mark.parametrize('ending', ['file', 'neither', 'failed_offer'])
def test_real_ripe_payment_sees_earlier_levy_with_or_without_later_listing(prefix, rows, listing, ending):
    d = fx.pending(instance_id='dispute_002')
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    steps = prefix + (('judgment_response', 'ripe', '@3=pay'),)
    tail = (('listing', '', listing), ('cash_floor', '2', '@2=initiate_offering'),
            ('offering', 'floor2', 'no' if ending == 'failed_offer' else 'yes'))
    if ending != 'failed_offer':
        tail += (('cash_floor', '3', '@0=' + ending),)
    if listing == 'suspended' and ending == 'neither':
        tail += (('delisting_notes', 'delisted_suspension', 'petition_delist'),)
    if listing == 'suspended' and ending == 'failed_offer':
        # The third saved offering path closes, then accelerates after suspension.
        tail = tail[:-1] + (('offering', 'floor2', 'yes'), ('cash_floor', '3', '@0=neither'),
                           ('delisting_notes', 'delisted_suspension', 'accelerated'),
                           ('cash_out', '', '@-1=neither'))
    traces = [event_trace(d, DisputePath(d.instance_id, ss, '', ()),
                          fc.setup, fc.m, fc.draws, fc.sens) for ss in (steps, steps + tail)]
    before, full = [tr.questions[12] for tr in traces]
    for field in ('day', 'cash', 'owed', 'groups'):
        np.testing.assert_array_equal(before[field][rows], full[field][rows])
    for row in rows:
        assert 'pay' in group_branches('judgment_response', int(full['groups'][row]))
    if prefix == NONE:
        assert (full['day'][279], full['cash'][279], full['owed'][279]) == (99, 1043021722, 326308733)

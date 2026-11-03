"""The post-trial question must describe the exact lower band its answer books."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.disputes import state14
from app.disputes.forecast import _S, Forecaster, _Walk


@pytest.fixture(scope='module')
def fixture():
    d = fx.pending()
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    bands = [b for b in fc.verdict_lines(d, fc.equity_inflows(d))['bands'] if b[1] > 0]
    return fc, d, bands


def test_reduced_prompt_matches_booked_band_including_open_top_and_lowest(fixture):
    fc, d, bands = fixture
    walk = _Walk(fc, d)
    for i, (lo, hi, booked) in enumerate(bands):
        below = bands[i - 1] if i else None
        upper = 'top' if i == len(bands) - 1 else str(hi)
        steps = _S(steps=(('verdict', '', f'award:{booked}:{lo}:{upper}'),))
        assert walk.reduced_band(steps) == below
        branches = ('unchanged', 'reduced', 'remitted', 'set_aside') if below else ('unchanged', 'set_aside')
        node = fc.nodes[fc.node(d, 'post_trial_ruling', f'award{booked}', branches=branches)]
        row = {'day': np.array([60]), 'cash': np.array([100]), 'owed': np.array([booked]),
               'petition': np.array([-1]),
               'sit': {'entry': np.array([1]), 'entered': booked, 'motions_filed': np.array([29]),
                       'motions_deadline': np.array([29]), 'ruling': np.array([60]),  # the walk's clocks
                       'band_range': (lo, None if upper == 'top' else hi)}}
        state, _, _ = state14.build(fc, node, d, [], [row], [np.array([True])])
        if below:  # the band below, (low, high] as booked: above its low end, never including it
            band = state14.band_text(below[0], below[1])
            assert state['situation']['reduced_band'] == band
            assert band in state['question']['answers']['reduced'] and band in state['question']['answers']['remitted']
            assert ('positive amount of at most' in band) == (below[0] == 0)
        else:
            assert not {'reduced', 'remitted'} & state['question']['answers'].keys()
            assert 'reduced_band' not in state['situation']
    assert fc.reduced_band(d, 0) is None


def test_a_cut_belongs_to_the_band_ending_at_that_cut(fixture):
    fc, d, bands = fixture
    for i, (_lo, hi, _booked) in enumerate(bands[:-1]):
        assert fc.reduced_band(d, hi) == (bands[i - 1] if i else None)

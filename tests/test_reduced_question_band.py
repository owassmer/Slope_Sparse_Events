"""The post-trial question must describe the exact lower band its answer books."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.disputes import state14
from app.disputes.forecast import _S, Forecaster, _Walk
from app.domain.values import usd


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
        branches = ('unchanged', 'reduced', 'set_aside') if below else ('unchanged', 'set_aside')
        node = fc.nodes[fc.node(d, 'post_trial_ruling', f'award{booked}', branches=branches)]
        row = {'day': np.array([60]), 'cash': np.array([100]), 'owed': np.array([booked]),
               'petition': np.array([-1]),
               'sit': {'entry': np.array([1]), 'entered': booked,
                       'band_range': (lo, None if upper == 'top' else hi)}}
        state, _, _ = state14.build(fc, node, d, [], [row], [np.array([True])])
        if below:
            assert state['situation']['reduced_low'] == usd(below[0])
            assert state['situation']['reduced_high'] == usd(below[1])
            assert f'from {usd(below[0])} to {usd(below[1])}' in state['question']['answers']['reduced']
        else:
            assert 'reduced' not in state['question']['answers']
            assert not {'reduced_low', 'reduced_high'} & state['situation'].keys()
    assert fc.reduced_band(d, 0) is None


def test_a_cut_belongs_to_the_band_ending_at_that_cut(fixture):
    fc, d, bands = fixture
    for i, (_lo, hi, _booked) in enumerate(bands[:-1]):
        assert fc.reduced_band(d, hi) == (bands[i - 1] if i else None)

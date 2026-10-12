"""Answer-enabled decisions must reach the frontier before same-day bookings."""
from dataclasses import replace

import numpy as np
from question_history import question_records
from test_question_futures import sample

from app.analysis.events import plain
from app.analysis.walk_checks import Checker, FutureChecker
from app.disputes.chronological import ChronologicalWalk

# walk-g1's shortest reported skipped response, native draw 0.
REAL = (
    ('settle', 'I0', 'no'),
    ('verdict', 'I0', 'award:2397555350:2260000400:2535110300'),
    ('judgment_response', 'entry', 'initiate_offering'), ('offering', 'entry', 'yes'),
    ('settle', 'Ientry', 'no'), ('post_trial_motions', '', 'no'),
    ('settle', 'I2', 'no'), ('appeal', '', 'no'), ('stay', 'post', 'yes'),
    ('enforce', 'post', 'levy'), ('judgment_response', 'post', 'none'),
    ('cash_floor', '1', 'neither'), ('cash_out', '', 'file'),
)


class Guided(ChronologicalWalk):
    """Keep a specified answer prefix, without replacing any question constructor."""

    guide = REAL

    def _loop(self, s, *args):
        actual = tuple((n, c, plain(b)) for n, c, b in s.steps)
        if actual[:len(self.guide)] != self.guide[:len(actual)]:
            return
        return super()._loop(s, *args)


def test_real_response_precedes_levy_and_both_checks_reject_a_skip():
    fc, d, _ = sample(row=None)  # retain the original population's award bands
    fc.draws = fc.draws.sub(np.arange(fc.draws.n) == 0)
    w = Guided(fc, d)
    with question_records(w) as records:
        w.run()
    assert len(w.out) == 1
    p = w.out[0]
    order = Checker(fc, d, 0, 0)
    assert order.history(p) == []
    future = FutureChecker(fc, d, 0, records)
    assert future.history(p) == []
    assert future.checked['judgment_response'] >= 2
    tr = order.replay(p.steps)
    i = next(i for i, st in enumerate(p.steps) if st[:2] == ('judgment_response', 'post'))
    assert int(tr.day[i][0]) == 39
    assert str(np.datetime64(fc.review) + np.timedelta64(39, 'D')) == '2024-06-22'
    assert int(tr.cash[i][0]) == 1_979_221_514
    assert int(tr.owed[i][0]) == 2_408_001_466
    assert int(tr.cash[i + 1][0]) == 0
    skipped = replace(p, steps=p.steps[:i] + p.steps[i + 1:])
    assert order.history(skipped) == [dict(kind='skipped', decision=['judgment_response', 'post'],
                                         day=39, before=list(p.steps[i + 1]), at=39)]

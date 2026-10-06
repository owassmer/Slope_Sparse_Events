"""Stay sizing reads collateral before posting its own lock."""
from unittest.mock import patch

import numpy as np
from test_decision_snapshots import AWARD, case  # noqa: F401

from app.analysis.events import Chain, Trace, event_questions, event_trace
from app.disputes.forecast import DisputePath, _Walk

STEPS = (('settle', 'I0', 'no'),
 ('verdict', 'I0', 'award:2397555350:2260000400:2535110300'),
 ('judgment_response', 'entry', '@2=initiate_offering'),
 ('offering', 'entry', 'yes'),
 ('post_trial_motions', '', 'yes'),
 ('settle', 'I1', 'no'),
 ('execute_pre_ruling', 'I1', 'yes'),
 ('stay', 'I1', 'no'),
 ('registration_early', 'I1', 'yes'),
 ('judgment_response', 'I1', '@-1=none'),
 ('judgment_response', 'ripe', '@2=initiate_offering'),
 ('offering', 'ripe', 'no'),
 ('judgment_default', 'I1', 'accelerated'),
 ('post_trial_ruling', '', 'reduced:1695000300:1130000200:2260000400'),
 ('settle', 'I2', '@1=no'),
 ('appeal', '', 'no'),
 ('stay', 'post', 'yes'),
 ('enforce', 'post', 'levy'),
 ('settle', 'I3', '@0=no'),
 ('listing', '', 'suspended'),
 ('cash_floor', '1', '@0=neither'),
 ('judgment_response', 'post', '@0=file'))


def test_cached_security_and_subset_question_agree(case):  # noqa: F811
    fc, d = case
    path = DisputePath(d.instance_id, STEPS, "", ())
    # Another row's pending levy used to recursively book draw42's own lock.
    event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens, rows=_Walk(fc, d)._rows(STEPS))
    mask = np.arange(fc.draws.n) == 42
    probe = STEPS[:19] + (("listing", "", "compliant"),) + STEPS[20:] + (("listing_date", "hearing_request", ""),)
    question = DisputePath(d.instance_id, probe, "", ())

    def read():
        rows, _ = event_questions(d, question, fc.setup, fc.m, fc.draws, fc.sens,
                                 indices=(-1,), day_only=True, rows=tuple(mask for _ in probe))
        return rows[-1]

    warm = read()
    fc.draws.prefixes = {}
    cold = read()
    for row in (warm, cold):
        assert row["day"][42] == 167
        assert row["marks"]["stayed"][42] == 153
        assert row["cash"][42] == 183083039
    for field in ("day", "cash", "owed", "petition"):
        np.testing.assert_array_equal(warm[field][mask], cold[field][mask])


def test_initial_sizing_still_releases_prior_stay_in_read(case):  # noqa: F811
    fc, d = case
    chain = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    tr = Trace(chain.ev)
    chain.advance(tr, *AWARD[0])
    chain.stay_security(np.full(chain.n, 10), "stay_I1", approved=True)
    prior_index = len(chain.rec[0])
    prior = chain.stays[prior_index]
    supported = prior["lock"] > 0
    assert supported.any()
    chain.advance(tr, "appeal", "", "no")
    current_index = len(chain.rec[0])
    assert current_index != prior_index
    original_seen = Chain.seen_at
    observed = []

    def releasing_read(self, bound, levy=False):
        if self._sizing_stay == current_index and not self._restaying:
            view = self.clone()
            release = prior["approval"] + 1
            view.resolve(release, supported)
            held = view.stays[prior_index]
            np.testing.assert_array_equal(held["rel"][supported], release[supported])
            assert "lock" not in view.stays[current_index]
            np.testing.assert_array_equal(view.ev.lock[np.flatnonzero(supported), release[supported]],
                                          -prior["lock"][supported])
            observed.append(True)
            return view
        return original_seen(self, bound, levy=levy)

    with patch.object(Chain, "seen_at", releasing_read):
        chain.stay_security(np.full(chain.n, 100), "stay_post", approved=True)
    assert observed
    assert chain._sizing_stay is None
    assert not chain.stays[current_index]["lock"][supported].any()
    np.testing.assert_array_equal(chain.stays[prior_index]["lock"], prior["lock"])

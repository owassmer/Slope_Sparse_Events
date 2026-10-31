"""Court snapshots precede their own result and consequent same-day distress."""
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from test_decision_snapshots import case  # noqa: F401
from test_stay_motion_history import SAVED_PREFIX

from app.analysis.events import BIG, Chain, _same_state, event_questions, event_trace
from app.disputes.forecast import DisputePath


def paths():
    steps = SAVED_PREFIX + (("stay", "post", "yes"), ("enforce", "post", "levy"),
                            ("judgment_response", "post", "@-1=none"),
                            ("settle", "I4", "@0=no"), ("listing", "", "compliant"))
    yes = DisputePath(instance_id="dispute_002", steps=steps, outcome="", edges=())
    no = replace(yes, steps=steps[:13] + (("stay", "post", "denied"),) + steps[14:])
    return yes, no


def test_supported_same_day_levy_is_after_court_answer(case):  # noqa: F811
    fc, d = case
    traces = [event_trace(d, p, fc.setup, fc.m, fc.draws, fc.sens) for p in paths()]
    yes, denied = (tr.questions[13] for tr in traces)
    assert _same_state(yes, denied)
    rows = np.array([139, 314, 362])
    np.testing.assert_array_equal(yes["day"][rows], [169, 172, 152])
    assert (yes["cash"][rows] > 0).all()
    assert not yes["sit"]["offering_pending"][rows].any()
    assert (yes["marks"]["levied"][rows] == BIG).all()
    # Denial really does permit the levy and later distress. Only the court read
    # excludes those effects; the corrected snapshot cannot erase real finances.
    np.testing.assert_array_equal(traces[1].marks["levied"][rows], denied["day"][rows])
    assert (traces[1].events.kinds["levy"][rows].sum(axis=1) < 0).all()
    earlier = traces[1].marks["levied"] < denied["day"]
    assert earlier.any()
    np.testing.assert_array_equal(denied["marks"]["levied"][earlier], traces[1].marks["levied"][earlier])


def test_selected_court_rows_match_full_and_do_not_share_mutable_cache(case):  # noqa: F811
    fc, d = case
    path = paths()[1]
    full = event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens)
    mask = np.isin(np.arange(fc.draws.n), [139, 314, 362])
    rows, _ = event_questions(d, path, fc.setup, fc.m, fc.draws, fc.sens,
                             indices=(13,), rows=tuple(mask for _ in path.steps))
    for key in ("cash", "owed", "day"):
        np.testing.assert_array_equal(rows[13][key][mask], full.questions[13][key][mask])
    expected = full.questions[13]["cash"].copy()
    full.questions[13]["cash"][:] = -123
    again = event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens)
    np.testing.assert_array_equal(again.questions[13]["cash"], expected)


def test_court_read_keeps_independent_dated_listing_and_financial_events(case):  # noqa: F811
    fc, d = case
    path = paths()[1]
    path = replace(path, steps=path.steps[:-1] + (("listing", "", "suspended"),))
    corrected = event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens)
    with patch.object(Chain, "court_questions", lambda *args: None):
        original = event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens)
    assert _same_state(vars(corrected.events), vars(original.events))
    row = corrected.questions[13]
    before = corrected.marks["delisted"] < row["day"]
    assert before.any()
    np.testing.assert_array_equal(row["marks"]["delisted"][before], corrected.marks["delisted"][before])


def test_no_levy_court_read_is_independent_of_own_lock(case):  # noqa: F811
    fc, d = case
    traces = []
    for path in paths():
        path = replace(path, steps=tuple((n, c, "none" if n == "enforce" else b)
                                         for n, c, b in path.steps))
        traces.append(event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens))
    assert not traces[0].events.kinds["levy"].any()
    assert not traces[1].events.kinds["levy"].any()
    assert traces[0].events.lock.any()
    assert not traces[1].events.lock.any()
    assert _same_state(traces[0].questions[13], traces[1].questions[13])

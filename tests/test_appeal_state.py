"""Appeal wording and question classes use the same before-answer dates."""
from datetime import date
from types import SimpleNamespace

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.analysis.events import BIG
from app.disputes.forecast import _S, Forecaster, _Walk, situation_class, unpack_row
from app.disputes.state14 import Group, Situation, Unbuilt, appeal_state, assumed_events, when


def row(days, deadlines=None, filed=None):
    n = len(days)
    return {"day": np.array(days), "cash": np.full(n, 100), "owed": np.full(n, 100),
            "petition": np.full(n, -1),
            "triggers": {"appeal_deadline": np.array(deadlines if deadlines is not None else [142] * n)},
            "marks": {"appealed": np.array(filed if filed is not None else [BIG] * n)},
            "sit": {"standing": np.full(n, "unpaid"), "ruling": np.full(n, 94)}}


def situation(r, tags=("final",)):
    fc = SimpleNamespace(review=date(2024, 5, 14), instrument=lambda: None)
    return Situation(fc, None, None, Group.of([r], [r["day"] < BIG]), list(tags), {})


@pytest.mark.parametrize("day,passed", [(112, False), (142, False), (143, True)])
def test_deadline_is_not_expired_on_or_before_its_date(day, passed):
    s = situation(row([day]))
    deadline = when(s.review, 142)
    phrase = f"the deadline to appeal {'passed on' if passed else 'is'} {deadline}"
    assert s.appeal_events() == [phrase]
    assert phrase in s.interval()
    assert phrase in s.judgment_status()
    assert "time to appeal has expired" not in s.interval()
    assert "time to appeal is running" not in s.judgment_status()


def test_routing_tags_cannot_establish_filing_or_expiry():
    assert assumed_events(["final", "appealed"], {}, "Qorvo") == []
    s = situation(row([112], [BIG], [120]), tags=("appealed",))
    assert s.appeal_events() == []
    assert s.interval() == "after the post-trial ruling"
    assert "expired" not in s.judgment_status()


def test_only_an_appeal_filed_by_the_decision_is_stated():
    r = row([112, 112], filed=[110, 120])
    np.testing.assert_array_equal(appeal_state(r), [4, 1])
    assert situation_class(r)[0] != situation_class(r)[1]
    s = situation(row([112], filed=[110]))
    assert s.appeal_events()[1] == f"the company filed an appeal on {when(s.review, 110)}"


def test_mixed_deadline_statuses_split_without_changing_an_unchanged_scenario():
    r = row([112, 143])
    classes = situation_class(r)
    assert classes[0] != classes[1]
    assert classes[0] == situation_class(row([112]))[0]
    changed = row([112, 140])
    assert situation_class(changed)[0] == classes[0]
    assert situation_class(changed)[0] == situation_class(changed)[1]
    with pytest.raises(Unbuilt, match="mixes decision-time appeal statuses"):
        situation(r).appeal_events()


def test_appeal_answers_keep_the_quiet_question_status():
    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    w = _Walk(fc, d)
    s = _S(steps=(("settle", "I0", "no"), ("verdict", "I0", "award:2397555350:2260000400:2535110300"),
                  ("post_trial_motions", "", "yes"), ("post_trial_ruling", "", "unchanged")),
           cls="award2397555350")
    key = w.node("appeal", s.cls, s=s, probe=("appeal", "", "no"))
    assert fc.canon_get(key, s.steps) is not None
    records = []
    for answer in ("yes", "no"):
        tr = w._facts(s.steps + (("appeal", "", answer),))
        if answer == "yes":
            assert (tr.marks["appealed"] < BIG).any()
        kept = fc.record((key,), tr)
        records.append({k: unpack_row(blob) for k, blob in kept})
    assert records[0].keys() == records[1].keys()
    assert records[0]
    for key, yes in records[0].items():
        no = records[1][key]
        live = yes["day"] < fc.days
        np.testing.assert_array_equal(yes["appeal_state"][live], no["appeal_state"][live])
        assert (yes["appeal_state"][live] < 3).all()
        assert not any("filed an appeal" in text for text in situation(yes).appeal_events())

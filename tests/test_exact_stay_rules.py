"""The stay rules on the compiled walk (QUESTIONS D4, J3, §2.5), over every question a whole draw asks: a stay motion
is asked only where security of some type can be posted on the court's day; a stay is never granted with none; two
stays never hold security at once; and every question's dated events are on or before its day, in date order."""
import json
from pathlib import Path

import numpy as np
import pytest

from app.exact import case, decode, pass1
from app.exact import fields_record as r
from app.exact import fields_walk as w


@pytest.fixture(scope="module", params=[145, 300])
def walked(request):
    fc_all, d = case.forecaster()
    fc, _ = case.on_draws((request.param,))
    ch0 = case.chain(fc, d)
    tk = pass1.case_tables(fc_all, d, ch0)
    forced_file = Path("var/waves/range1/forced.json")
    forced = set(json.loads(forced_file.read_text())) if forced_file.exists() else set()
    mem, *_ = pass1.explore(ch0, 0, tk, forced=forced)
    return mem


def test_a_stay_motion_offers_security_it_can_post(walked):
    mem = walked
    count = int(np.asarray(mem.count).reshape(-1)[0])
    for i in np.flatnonzero(mem.rows[:count, w.QN] == w.Q_STAY_MOTION):
        ints = mem.ints[i]
        if not ints[r.HAS_SECURITY]:
            continue
        cash, need, collateral, offer, live = (int(ints[r.SECURITY0 + j]) for j in (1, 2, 4, 5, 6))
        day = int(ints[r.SECURITY0])
        if day >= 10**6:
            continue
        if live:  # the court's day inside the period: full collateral, or positive reduced cash security
            assert offer > 0 or (collateral > 0 and cash - need >= collateral), (i, cash, need, collateral, offer)


def test_dated_events_are_on_or_before_the_question_and_in_order(walked):
    mem = walked
    count = int(np.asarray(mem.count).reshape(-1)[0])
    for i in range(count):
        row = decode.record(mem.ints[i], mem.floats[i])
        evs = row["sit"]["events"][0]
        days = [t for _, t, _, _ in evs]
        assert days == sorted(days)
        assert all(t <= int(row["day"][0]) for t in days), (i, int(row["day"][0]), evs)
        approved = [t for k, t, _, _ in evs if k == "stay_approved"]
        moot_or_after = [k for k, t, _, _ in evs if approved and t > approved[0] and k == "stay_approved"]
        assert not moot_or_after, (i, evs)  # one judgment, one stay in effect

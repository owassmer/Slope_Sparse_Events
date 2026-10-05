"""Outstanding writs follow event dates, including release before collection."""
from types import MethodType

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.analysis.events import BIG, Chain, Trace, canon
from app.disputes.forecast import Forecaster


@pytest.fixture
def chain():
    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    c = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    c.capture_questions = False
    c.instrument_cash()
    t = Trace(c.ev)
    steps = (("verdict", "I0", "award:2397555350:2260000400:2535110300"),
             ("post_trial_motions", "", "yes"), ("post_trial_ruling", "", "unchanged"))
    for step in canon(steps):
        c.advance(t, *step)
    return c


def test_pending_writs_preserve_each_date_and_draw():
    c = Chain.__new__(Chain)
    c.n, c.pending_levy, c._pending_writs, c.waiting = 2, None, [], []
    fired = []
    c.upto = MethodType(lambda self, *a, **k: False, c)
    c.levy = MethodType(lambda self, day, **k: fired.append(day.copy()), c)
    c.queue_levy(np.array([20, 40]))
    c.queue_levy(np.array([10, 30]))
    c.until(np.array([25, 35]))
    assert [int(a[0]) for a in fired if a[0] < BIG] == [10, 20]
    assert [int(a[1]) for a in fired if a[1] < BIG] == [30]
    np.testing.assert_array_equal(c.pending_levy, [BIG, 40])
    c.until(np.array([50, 50]))
    assert c.pending_levy is None


@pytest.mark.parametrize("release_offset", [-1, 0, 1])
def test_release_preempts_only_writs_on_or_after_release(chain, release_offset):
    c = chain
    day = np.full(c.n, 140, dtype=np.int64)
    baseline = c.clone()
    baseline.queue_levy(day)
    baseline.until(day + 1)
    valid = baseline.ev.kinds["levy"].sum(axis=1) < 0
    assert valid.any()
    c.queue_levy(day)
    c.resolve(day + release_offset, np.ones(c.n, dtype=bool))
    c.until(day + 1)
    if release_offset <= 0:
        assert not c.ev.kinds["levy"].any()
        assert (c.marks["levied"] == BIG).all()
    else:
        np.testing.assert_array_equal(c.ev.kinds["levy"], baseline.ev.kinds["levy"])


def test_pending_only_cash_view_is_independent(chain):
    c = chain
    day = np.full(c.n, 140, dtype=np.int64)
    c.queue_levy(day)
    assert not c.waiting
    v = c.seen_at(day + 1, levy=True)
    assert v is not c and v.ev.kinds["levy"].any()
    assert not c.ev.kinds["levy"].any()
    np.testing.assert_array_equal(c.pending_levy, day)
    assert v.pending_levy is None


def test_pending_queue_divergence_is_dated_and_order_independent(chain):
    c = chain
    c.queue_levy(np.full(c.n, 140))
    c.queue_levy(np.full(c.n, 160))
    other = c.clone()
    other._pending_writs.reverse()
    assert (c.divergence(other) == BIG).all()
    other._pending_writs[0][0] = 165
    other._refresh_pending_levy()
    divergence = c.divergence(other)
    assert divergence[0] == 160
    assert (divergence[1:] == BIG).all()
    assert c._pending_writs[1][0] == 160  # clone owns its queue arrays


def test_earlier_structural_step_does_not_book_future_writ(chain):
    c = chain
    c.queue_levy(np.full(c.n, 180))
    c.advance(Trace(c.ev), "listing", "", "compliant")
    assert not c.ev.kinds["levy"].any()
    np.testing.assert_array_equal(c.pending_levy, np.full(c.n, 180))


def test_absent_structural_probe_does_not_preempt_later_release(chain):
    c = chain
    c.queue_levy(np.full(c.n, 140))
    assert (c.marks["notes_due"] == BIG).all()
    c.advance(Trace(c.ev), "notes_due_date", "issuer", "no")
    assert not c.ev.kinds["levy"].any()
    assert c.pending_levy is not None
    c.resolve(np.full(c.n, 132), np.ones(c.n, dtype=bool))
    c.until(np.full(c.n, 141))
    assert not c.ev.kinds["levy"].any()


def test_merge_comparison_ignores_walk_history(chain):
    """A chain served from a state walked on more draws carries the other draws' levies at zero amount and a stay
    memo key built from them; neither books anything, so the merge comparison must not see them (group 4's listing
    merge split on exactly these, so its tasks walked different pieces)."""
    c = chain
    c.queue_levy(np.full(c.n, 140))
    c.until(np.full(c.n, 141))
    other = c.clone()
    other.takes.append((np.full(c.n, 120), np.zeros(c.n, dtype=np.int64)))
    other.writs.insert(0, (np.full(c.n, 120), np.zeros(c.n, dtype=np.int64)))
    other.takes.reverse()
    other._stay_owed = ("a memo key from another history",)
    assert (c.divergence(other) == BIG).all()
    real = c.clone()  # a levy that takes cash is still a difference, from no later than its day
    amount = np.zeros(c.n, dtype=np.int64)
    amount[:3] = 1
    real.takes.append((np.full(c.n, 150), amount))
    assert (c.divergence(real)[:3] <= 150).all()


def test_merge_check_builds_its_chains_from_the_path(monkeypatch):
    """The merge check's two chains are built from the path alone, never resumed from the prefix cache (whose
    states, cut from more draws, can carry other draws' bookkeeping), so the answer cannot depend on walk history.
    Groups 1 and 15 split the same listing branch differently across tasks while the comparison read cached states."""
    import app.analysis.events as E
    from app.disputes import forecast as F

    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    fc.draws.prefixes = {}
    w = F._Walk(fc, d)
    s = F._S(steps=(("verdict", "I0", "award:2397555350:2260000400:2535110300"), ("post_trial_motions", "", "yes"),
                    ("post_trial_ruling", "", "unchanged")))
    seen = []
    real = E.event_chain
    monkeypatch.setattr(E, "event_chain", lambda *a, **k: (seen.append(a[4].prefixes), real(*a, **k))[1])
    w._same_after(s, ("appeal", "", "no"), ("appeal", "", "yes"), w.mask_of(s.steps))
    assert seen[:2] == [None, None]
    assert fc.draws.prefixes is not None  # the walk's cache is restored

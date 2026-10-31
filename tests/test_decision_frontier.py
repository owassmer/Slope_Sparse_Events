"""Answer-free frontier against the saved dated-order failures."""
import pickle

import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from test_dated_answer_domains import SAVED
from test_ripe_after_levy import NONE, OFFER

from app.analysis.events import BIG, Chain, Draws, Trace, event_trace
from app.analysis.frontier import Cursors, Decision
from app.disputes.forecast import DisputePath


def seed(steps):
    chain = Chain(fx.pending(instance_id='dispute_002'), fx.setup(), fx.model(), Draws(512, basis=fx.basis()))
    chain.instrument_cash()
    trace = Trace(chain.ev)
    for step in steps:
        chain.advance(trace, *step)
    return chain


def cursors():
    # The independent enabled prerequisites at these saved roots. No tentative
    # answer (not even 'quiet' or 'unchanged') is required to discover dates.
    return Cursors(
        litigation=(Decision('post_trial_ruling'), Decision('judgment_response', 'I1')),
        distress=(Decision('cash_floor', '1'), Decision('cash_out')),
        listing=(Decision('listing_date', 'compliance'),),
        notes=(Decision('judgment_response', 'ripe'),),
    )


def dates(frontier, node, ctx=''):
    return next(c.day for c in frontier.candidates if c.decision == Decision(node, ctx))


def chosen(frontier, row):
    return frontier.candidates[frontier.pick[row]].decision


@pytest.mark.parametrize('prefix,row', [(SAVED[:8], 145), (NONE[:8], 279), (OFFER[:9], 7)])
def test_frontier_never_books_an_answer_or_changes_semantic_state(monkeypatch, prefix, row):
    chain = seed(prefix)
    # Forbid booking on cold reads too, on *any* clone, not just this object.
    # Once calculation caches are warm, require byte-identical complete state.
    def forbidden(*args, **kwargs):
        pytest.fail('date discovery tried to book or replay')
    for name in ('step', 'advance', 'until', 'book', 'pay', 'petition', 'flush_levy', 'clone', 'mark', 'stay_security'):
        monkeypatch.setattr(Chain, name, forbidden)
    chain.next_decisions(cursors())
    before = pickle.dumps(chain)
    frontier = chain.next_decisions(cursors())
    assert pickle.dumps(chain) == before
    assert frontier.pick[row] >= 0
    for candidate in frontier.candidates:
        assert not candidate.day.flags.writeable
    assert not frontier.pick.flags.writeable


def test_already_answered_waiting_transition_remains_a_frontier_boundary():
    chain = seed(SAVED[:8] + (('judgment_response', 'ripe', 'initiate_offering'),
                             ('offering', 'ripe', 'yes')))
    support = np.arange(chain.n) == 145
    cur = Cursors(listing=(Decision('listing_date', 'compliance'),))
    frontier = chain.next_decisions(cur, support)
    assert chosen(frontier, 145) == Decision('waiting', '8')
    assert dates(frontier, 'waiting', '8')[145] == 96
    before = pickle.dumps(chain)
    chain.next_decisions(cur, support)
    assert pickle.dumps(chain) == before
    chain.until(np.where(support, 97, -1))
    frontier = chain.next_decisions(cur, support)
    assert dates(frontier, 'waiting', '8')[145] == BIG


def test_day96_response_precedes_prospective_floor():
    chain = seed(SAVED[:8])
    frontier = chain.next_decisions(cursors())
    assert dates(frontier, 'judgment_response', 'ripe')[145] == 96
    assert chosen(frontier, 145) == Decision('judgment_response', 'ripe')
    assert dates(frontier, 'cash_floor', '1')[145] > 96
    # The known full path's floor is day 155; its earlier offering cannot be
    # assumed in this prefix. It remains an unresolved response, not a booking.
    assert not chain.offerings
    saved = event_trace(chain.d, DisputePath(chain.iid, SAVED, '', ()), chain.s, chain.m, chain.dr)
    assert saved.questions[15]['day'][145] == 96
    assert saved.questions[9]['day'][145] == 155


def test_day74_ruling_precedes_day77_floor():
    chain = seed(NONE[:8])
    frontier = chain.next_decisions(cursors())
    assert dates(frontier, 'post_trial_ruling')[279] == 74
    assert chosen(frontier, 279) == Decision('post_trial_ruling')
    assert chain.cls_amount is None  # no presumed unchanged or set-aside answer
    # The old replay books the pending day-77 levy to discover its day-77
    # floor. Discovery must not book that levy past the unresolved day-74 ruling.
    assert dates(frontier, 'levy')[279] == 77
    # Booked cash only. The queued day-77 levy precedes the stay's day-112 approval, so the stay is sized after it
    # and locks nothing (117 was the stale pre-levy lock, kept while queuing the levy booked no cash).
    assert dates(frontier, 'cash_floor', '1')[279] == 179
    steps = NONE[:8] + (('cash_floor', '1', 'neither'),)
    replay = event_trace(chain.d, DisputePath(chain.iid, steps, '', ()), chain.s, chain.m, chain.dr)
    assert replay.day[-1][279] == 77
    aside = steps[:-1] + (('post_trial_ruling', '', 'set_aside'), steps[-1])
    replay = event_trace(chain.d, DisputePath(chain.iid, aside, '', ()), chain.s, chain.m, chain.dr)
    assert replay.day[-1][279] == BIG


@pytest.mark.parametrize('prefix,rows', [(NONE[:8], [279, 383]),
                                       (OFFER[:9], [7, 63, 149, 215, 225, 325, 329, 380, 393, 406])])
def test_pending_levy_is_a_boundary_before_ripe_payment(prefix, rows):
    chain = seed(prefix)
    frontier = chain.next_decisions(cursors())
    levy = dates(frontier, 'levy')
    ripe = dates(frontier, 'judgment_response', 'ripe')
    assert np.all(levy[rows] < ripe[rows])
    # Isolating the notes cursor still cannot jump the deterministic levy.
    notes = chain.next_decisions(Cursors(notes=(Decision('judgment_response', 'ripe'),)))
    assert all(chosen(notes, row) == Decision('levy') for row in rows)


def test_per_draw_ties_support_and_unavailable_can_reenable(monkeypatch):
    chain = seed(())
    chain.pending_levy = np.full(chain.n, BIG)
    chain.pending_levy[:3] = [90, 90, 90]
    day = {'judgment_response': np.full(chain.n, BIG), 'cash_floor': np.full(chain.n, BIG),
           'cash_out': np.full(chain.n, BIG)}
    day['judgment_response'][:3] = [90, 91, 89]
    day['cash_floor'][3:6] = [80, 100, 90]
    day['cash_out'][3:6] = [100, 80, 90]
    monkeypatch.setattr(chain, 'decision_day', lambda node, ctx: day[node])
    cur = Cursors(litigation=(Decision('judgment_response', 'post'),),
                  distress=(Decision('cash_out'), Decision('cash_floor', '1')))
    f = chain.next_decisions(cur)
    assert [chosen(f, i).node for i in range(6)] == [
        'judgment_response', 'levy', 'judgment_response', 'cash_floor', 'cash_out', 'cash_floor']
    assert f.pick[6] == -1
    assert (f.for_chain('listing') == -1).all()
    day['cash_floor'][6] = 95
    support = np.arange(chain.n) != 5
    chain.ev.petition[4] = 80
    f = chain.next_decisions(cur, support)
    assert f.pick[4] == f.pick[5] == -1
    assert chosen(f, 6) == Decision('cash_floor', '1')


@pytest.mark.parametrize('node,ctx,answers', [
    ('settle', 'I2', ('yes', 'no')),
    ('post_trial_ruling', '', ('unchanged', 'set_aside')),
    ('stay', 'post', ('yes', 'no', 'denied')),
    ('court_order', 'stay_post', ('',)),
    ('court_order', 'registration_post', ('',)),
    ('enforce', 'post', ('levy', 'none')),
    ('appeal', '', ('yes', 'no')),
    ('listing_date', 'compliance', ('',)),
    ('judgment_default', 'I1', ('no', 'accelerated', 'holders_file')),
    ('post_trial_motions', '', ('yes', 'no')),
])
def test_date_matches_existing_booking_for_every_answer(node, ctx, answers):
    chain = seed(NONE[:8])
    day = chain.decision_day(node, ctx)
    for answer in answers:
        booked = chain.clone().step(node, ctx, answer)
        np.testing.assert_array_equal(day, booked)

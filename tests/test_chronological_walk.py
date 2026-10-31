"""Chronological frontier ordering, answer domains and probability composition."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from benchmark_chronological import check
from test_dated_answer_domains import SAVED
from test_ripe_after_levy import NONE

from app.analysis.events import Chain, Draws, event_trace, plain
from app.analysis.frontier import Decision
from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import _S, DisputePath, Forecaster


def walker():
    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    return ChronologicalWalk(fc, d)


@pytest.mark.parametrize("prefix,row,cls,expected", [
    (SAVED[:8], 145, "award6752641200", Decision("judgment_response", "ripe")),
    (NONE[:8], 279, "award1065000100", Decision("post_trial_ruling")),
])
def test_saved_root_selects_the_earliest_chain(monkeypatch, prefix, row, cls, expected):
    w = walker()
    asked = []
    monkeypatch.setattr(w, "_ask", lambda s, d, outcome: asked.append(d))
    w.run_from(_S(steps=prefix, cls=cls, a4="seek", stayed=True, early=True), np.arange(512) == row)
    assert asked == [expected]
    assert not w.out  # stopping at the question is not a completed history


def test_mixed_draws_are_partitioned_by_their_earliest_decision(monkeypatch):
    w = walker()
    asked = {}

    def ask(s, d, outcome):
        asked[d] = np.flatnonzero(w.mask_of(s.steps)).tolist()

    monkeypatch.setattr(w, "_ask", ask)
    next_decisions = Chain.next_decisions
    sizes = []

    def discover(chain, cursors, support=None):
        sizes.append(chain.n)
        np.testing.assert_array_equal(chain.dr.u("identity"), w.fc.draws.u("identity")[[0, 6, 7, 8]])
        return next_decisions(chain, cursors, support)

    monkeypatch.setattr(Chain, "next_decisions", discover)
    support = np.isin(np.arange(512), [0, 6, 7, 8])
    w.run_from(_S(steps=NONE[:8], cls="award1065000100", a4="seek", stayed=True, early=True), support)
    assert sizes == [4]
    assert asked == {Decision("post_trial_ruling"): [8], Decision("judgment_response", "I1"): [7],
                     Decision("settle", "Istay"): [6], Decision("judgment_response", "ripe"): [0]}


def test_pre_ruling_response_books_earlier_offering_before_offering_answers(monkeypatch):
    # Continuation 0005, share 3/8: the ripe offering uses the capacity
    # before I1. The response probe used to skip all waiting decisions.
    prefix = (
        ('settle', 'I0', 'no'),
        ('verdict', 'I0', 'award:6752641200:2810220200:top'),
        ('judgment_response', 'entry', '@2=initiate_offering'),
        ('offering', 'entry', 'no'), ('settle', 'Ientry', 'no'),
        ('post_trial_motions', '', 'yes'), ('settle', 'I1', 'no'),
        ('execute_pre_ruling', 'I1', 'yes'), ('stay', 'I1', 'no'),
        ('registration_early', 'I1', 'yes'),
        ('judgment_response', 'ripe', '@2=initiate_offering'),
        ('offering', 'ripe', 'yes'), ('judgment_default', 'I1', 'holders_file'),
    )
    w = walker()
    probe = prefix + (('judgment_response', 'I1', 'none'),)
    tr = event_trace(w.d, DisputePath(w.d.instance_id, probe, '', ()),
                     w.fc.setup, w.fc.m, w.fc.draws, w.fc.sens)
    row = 0
    assert tr.questions[13]['groups'][row] == 0
    assert tr.questions[13]['sit']['ledger'][row] == 0
    assert w.walk_groups(probe)[row] == 0
    offered = []
    monkeypatch.setattr(w, 'take', lambda s, step, *args, **kwargs: s.add(step, None, **kwargs))
    monkeypatch.setattr(w, 'tail', lambda s, outcome: offered.append(s.steps[-1][-1]))
    monkeypatch.setattr(w, 'offer', lambda s, phase, then: offered.append('unexpected offering'))
    w._population = np.arange(512) == row
    state = _S(steps=prefix, cls='award6752641200', a4='seek', early=True, resp='offer')
    w.a4_grouped(state, 'I1', lambda s: offered.append(s.steps[-1][-1]), None, False)
    assert {plain(answer) for answer in offered} == {'file', 'none'}


def test_no_progress_guard_reports_the_selected_draw_and_history(monkeypatch):
    from app.analysis.frontier import Cursors

    w = walker()
    monkeypatch.setattr(w, "_cursors", lambda s, pending: Cursors(distress=(Decision("cash_out"),)))
    monkeypatch.setattr(w, "_ask", lambda s, d, outcome: w._resume(s))
    with pytest.raises(RuntimeError, match="selected decision made no progress") as caught:
        w.run_from(_S(steps=NONE[:8], cls="award1065000100", a4="seek", stayed=True, early=True),
                   np.arange(512) == 63)
    assert "Decision(node='cash_out', ctx='')" in str(caught.value)
    assert "rows=[63]" in str(caught.value)
    assert f"steps={NONE[:8]!r}" in str(caught.value)


def test_ruling_enables_response_to_an_existing_post_ruling_writ(monkeypatch):
    w = walker()
    asked = []

    def ask(s, d, outcome):
        asked.append(d)
        if d.node == "post_trial_ruling":
            w.post(s.add((d.node, d.ctx, "unchanged"), None))
        elif d == Decision("settle", "I2"):
            w._resume(s.add((d.node, d.ctx, "no"), None), (Decision("appeal"),))
        elif d.node == "appeal":
            w.stay_post(s.add((d.node, d.ctx, "no"), None))
        else:
            assert d == Decision("judgment_response", "post")

    monkeypatch.setattr(w, "_ask", ask)
    w.run_from(_S(steps=NONE[:8], cls="award1065000100", a4="seek", stayed=True, early=True),
               np.arange(512) == 279)
    assert asked == [Decision("post_trial_ruling"), Decision("settle", "I2"), Decision("appeal"),
                     Decision("judgment_response", "post")]


@pytest.mark.parametrize("prefix,row,cls", [(SAVED[:8], 145, "award6752641200"),
                                           (NONE[:8], 383, "award1065000100")], ids=["ripe96", "ruling71"])
def test_saved_root_domains_and_synthetic_mass(prefix, row, cls):
    w = walker()
    w.run_from(_S(steps=prefix, cls=cls, a4="seek", stayed=True, early=True), np.arange(512) == row)
    result, _ = check(w, row, len(prefix))
    assert result["paths"] > 0
    assert result["infeasible"] == 0, result["examples"]
    for mass in result["masses"].values():
        assert mass == pytest.approx(1, abs=1e-12, rel=0)
    if row != 145:
        return
    # Failed earlier offerings lock ATM sales during pricing/close. Sizing the
    # day-161 offering before booking those day-104 and day-138 decisions loses
    # their released share capacity and understates its day-166 cash receipt.
    wanted = {("judgment_default", "I1", "no"), ("listing", "", "suspended"),
              ("offering", "ripe", "no"), ("offering", "floor1", "no"),
              ("offering", "cash_out", "no"), ("offering", "I1", "yes"),
              ("delisting_notes", "delisted_suspension", "accelerated")}
    p = next(p for p in w.out if wanted <= {(n, c, plain(b)) for n, c, b in p.steps})
    draws = Draws(512, basis=fx.basis())
    tr = event_trace(w.d, p, w.fc.setup, w.fc.m, draws, w.fc.sens)
    i = next(i for i, st in enumerate(p.steps) if st[:2] == ("judgment_response", "I1"))
    assert tr.questions[i]["day"][145] == 161
    assert tr.questions[i]["sit"]["ledger"][145] == 43_329_542
    assert tr.events.cash[145, 166] == 382_249_451


def test_floor_after_an_offering_that_closes_before_the_stay_approval():
    """other5 (draw 63): the ripe-response offering closes before the stay's day-117 approval. Its proceeds are
    in the balance the stay's security is sized on, so the next floor is dated on the re-sized lock (day 133,
    before the holders' petition on 159), in the question's replay as in the chronological walk's chain."""
    from benchmark_chronological import root

    prefix, row, cls = root("other5")
    steps = prefix + (("post_trial_ruling", "", "unchanged"), ("settle", "I2", "no"), ("appeal", "", "no"),
                      ("judgment_response", "ripe", "@2=initiate_offering"), ("offering", "ripe", "yes"),
                      ("judgment_default", "I1", "holders_file"))
    assert prefix[2] == ("judgment_response", "entry", "@3=initiate_offering")
    w = walker()
    w._population = np.arange(512) == row
    probe = steps + (("cash_floor", "1", "neither"),)
    assert w.walk_groups(probe)[row] >= 0
    tr = w._raw(probe, True)
    assert (int(tr.day[-1][row]), int(tr.petition[row])) == (133, 159)


def test_stay_resizes_when_a_waiting_decision_or_levy_changes_its_balance():
    """none279 (draw 279): the stay approved on day 112 is sized on the balance after the day-77 levy and the waiting
    offerings dated before it. Queuing them books nothing, so the cash version alone did not re-size the stay: on a
    one-draw slice the stay kept its pre-levy security, and resuming the walk's cached whole-draw prefix then dated
    the stay-window settlement (settle I4) never, with no offer, so the walk dropped a $2.1M question. The stay's
    state on the draw must not depend on which other draws share the chain."""
    from app.analysis.events import Trace, canon

    prefix, row, _ = root_none279()
    history = canon(prefix + (("post_trial_ruling", "", "unchanged"), ("appeal", "", "no"),
                              ("judgment_response", "post", "@2=none"), ("cash_floor", "1", "@2=initiate_offering"),
                              ("offering", "floor1", "yes"), ("judgment_response", "ripe", "@3=initiate_offering")))
    w = walker()
    fc = w.fc
    whole = Chain(w.d, fc.setup, fc.m, fc.draws, fc.sens)
    whole.instrument_cash()
    one = whole.sliced(np.array([row]), fc.draws.sub(np.arange(512) == row))
    tw, to = Trace(whole.ev), Trace(one.ev)
    for step in history:
        whole.advance(tw, *step)
        one.advance(to, *step)
        assert (int(whole.stayed_from[row]), int(whole.lock_amount[row])) == \
            (int(one.stayed_from[0]), int(one.lock_amount[0])), step
    # resume the whole-draw state on the draw alone, as the prefix cache does, and finish the walked history
    resumed = whole.sliced(np.array([row]), fc.draws.sub(np.arange(512) == row))
    tr = Trace(resumed.ev)
    for step in (("offering", "ripe", "yes"), ("judgment_default", "I1", "holders_file"), ("settle", "I4", "no")):
        resumed.advance(tr, *step)
    assert (int(resumed.rec[0][-1][0]), int(resumed.settle_offer[0])) == (112, 210_454_192)


def root_none279():
    from benchmark_chronological import root

    return root("none279")


def test_walk_order_check_finds_the_skipped_stay_settlement():
    """The check judges a history by its own dated facts: before the stay fix the chronological walk emitted this
    none279 history without the day-112 stay-window settlement, which the check reports as missing; asking it
    (either answer) leaves nothing pending and due."""
    from types import SimpleNamespace

    from walk_order import Checker

    prefix, row, _ = root_none279()
    w = walker()
    check = Checker(w.fc, w.d, row, len(prefix))
    h = prefix + (("post_trial_ruling", "", "unchanged"), ("settle", "I2", "no"),
                  ("appeal", "", "no"), ("judgment_response", "post", "@2=none"),
                  ("cash_floor", "1", "@2=initiate_offering"), ("offering", "floor1", "yes"),
                  ("judgment_response", "ripe", "@3=initiate_offering"), ("offering", "ripe", "yes"),
                  ("judgment_default", "I1", "holders_file"))
    assert check.history(SimpleNamespace(steps=h)) == [
        dict(kind="missing", decision=["settle", "I4"], day=112, before=None, at=None)]
    for answer in ("@1=no", "@1=yes"):
        assert check.history(SimpleNamespace(steps=h + (("settle", "I4", answer),))) == []

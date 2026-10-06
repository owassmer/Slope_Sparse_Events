"""The experimental scheduler is separate from the production walk."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from benchmark_chronological import check
from test_dated_answer_domains import SAVED
from test_ripe_after_levy import NONE

from app.analysis.events import Chain, Draws, event_trace, plain
from app.analysis.frontier import Decision
from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import _S, Forecaster


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
                     Decision("cash_floor", "1"): [6], Decision("judgment_response", "ripe"): [0]}


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

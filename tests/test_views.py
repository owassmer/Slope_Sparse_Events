"""The two views: bank data versus bank data plus the researched record. Both carry the same distress decisions and
common borrower inputs on the same operating draws; research facts enter the augmented view only. No network:
judgments are stubs."""

import json

import numpy as np
import pytest
from akoustis_fixture import REVIEW, SETUP, SNAP, basis, judgment

from app.analysis.core import Analysis, EventModel
from app.analysis.events import BANK
from app.disputes.forecast import Forecaster, Judgment, bank_state
from app.finance.bank import load_feed

BORROWER = "Akoustis Technologies, Inc."


@pytest.fixture(scope="module")
def base():
    return basis()


def forecaster(base, disputes=()):
    return Forecaster(list(disputes), {}, borrower=BORROWER, review=REVIEW, horizon=SETUP.horizon,
                      hydrate=lambda f: {}, setup=SETUP, basis=base[1])


def stub(nodes, rng) -> dict[str, Judgment]:
    out = {}
    for n in nodes.values():
        w = rng.dirichlet(np.ones(len(n.branches)))
        out[n.key] = Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                              event=n.event, assumptions=n.assumptions, window=n.window,
                              distribution=dict(zip(n.branches, w.tolist(), strict=True)))
    return out


def bank_model(base, rng, disputes=()) -> tuple[Forecaster, EventModel]:
    fc = forecaster(base, disputes)
    paths = fc.bank_paths()
    return fc, EventModel({}, {}, {}, [], bank_paths=paths, bank_judgments=stub(fc.bank_nodes, rng))


def test_the_bank_view_decides_at_the_cash_floor_and_its_probabilities_sum_to_one(base):
    rng = np.random.default_rng(11)
    fc, m = bank_model(base, rng)
    assert {n.node for n in fc.bank_nodes.values()} >= {"petition_cash_floor"}
    assert all(n.context.split("|")[0] == BANK for n in fc.bank_nodes.values())
    for _ in range(5):
        m = EventModel({}, {}, {}, [], bank_paths=m.bank_paths, bank_judgments=stub(fc.bank_nodes, rng))
        assert m.bank_probs().sum() == pytest.approx(1.0, abs=1e-12)
    # every operating draw falls below its 30-day need inside the period on the bank data alone
    t = fc.bank_trace((("cash_floor", "", "no"),)).day[-1]
    assert (t < (SETUP.horizon - REVIEW).days).all()


def test_with_no_dispute_the_augmented_view_is_the_bank_view(base):
    _, m = bank_model(base, np.random.default_rng(5))
    a = Analysis(load_feed(SNAP), SETUP, m)
    v = a.views()
    assert v["bank_only"] == v["event_adjusted"]
    assert 0 < v["bank_only"]["metrics"]["petition_p"] <= 1
    steps = a.attribution()
    assert steps[0]["metrics"] == steps[2]["metrics"]


def test_both_views_share_the_operating_draws(base):
    _, m = bank_model(base, np.random.default_rng(2))
    a = Analysis(load_feed(SNAP), SETUP, m)
    assert a.bank_r.limit is a.r.limit is a.line.limit  # one line, one set of draws
    i = next(i for i, c in enumerate(m.bank_combos) if c[0].outcome == "operating" and not any(
        s[2] == "yes" for s in c[0].steps))
    # no instrument and no petition: the operating path is the operating flows alone
    assert np.array_equal(a.bank_r.per_day["cash"][i], a.bank.cash.sum(axis=0))


def test_the_bank_questions_carry_bank_facts_only(base):
    fc, _ = bank_model(base, np.random.default_rng(1), disputes=[judgment()])
    for n in fc.bank_nodes.values():
        st = bank_state(fc, n)
        assert set(st["case"]) == {"as_of", "borrower"}
        assert set(st["path_facts"]) == {"decision_date", "cash_balance_at_decision",
                                         "operating_need_30_days_at_decision"}
        assert not st["evidence"] and not st["record_items"] and not st["readings"] and not st["standard"]
        text = json.dumps(st).lower()
        for word in ("judgment", "qorvo", "notes", "nasdaq", "listing", "default", "indenture"):
            assert word not in text, (n.key, word)

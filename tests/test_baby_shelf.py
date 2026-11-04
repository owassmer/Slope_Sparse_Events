"""I.B.6 is gross-dollar capacity, not an authorized-share haircut."""
from copy import deepcopy
from datetime import date

import akoustis_20240514_fixture as fx
import numpy as np

from app.analysis.baby_shelf import annual, capacity, offering_usage, year_start
from app.analysis.events import Chain, Draws


def chain(price=44):
    basis = fx.basis()
    draws = Draws(basis.cash.shape[0], basis=basis)
    draws = draws.sub(np.arange(draws.n) == 0)
    ch = Chain(fx.pending(), fx.setup(), deepcopy(fx.model()), draws)
    # Exact hand-price paths: no model answer can set these prices or dates.
    ch.share_price[:] = price
    ch._reprice = lambda: None
    ch._atm_rebook()
    return ch


def ix(ch, day):
    return ch.ix(date.fromisoformat(day))


def test_ownership_excludes_unissued_options_and_units():
    p = fx.model()["parameters"]["baby_shelf"]
    options = sum((153000, 84000, 90000, 87467, 177214, 281766, 40000, 201707))
    units = sum((39982, 72299, 42768, 65722, 24151))
    assert options == 1115154 and units == 244922
    assert p["affiliate_shares"] == 4101085 - options - units == 2741009
    assert p["nonaffiliate_shares"] == 98669282 - p["affiliate_shares"] == 95928273
    assert p["annual_update"] == "2024-09-06"


def test_transition_and_whole_offering_test_not_a_haircut():
    ch = chain(9)
    update, _, _ = annual(ch)
    assert ch.offering_available(update - 1).all()
    terms = ch.offering_terms(update)
    assert (terms["gross"] > terms["shelf_capacity"]).all()
    assert not ch.offering_available(update).any()
    # The terms aren't silently reduced to fit; no shares or cash are booked.
    assert not ch.initiate(ch.per_draw(update), "too_large").any()
    assert not ch._offers
    assert capacity(ch, update - 1)[0] > 100_000_000_000


def test_75m_boundary_and_path_specific_eligibility():
    ch = chain()
    update, shares, _ = annual(ch)
    ch.share_price[:] = 7_500_000_000 / shares + 0.00001
    ch._eq_v += 1
    ch._atm_rebook()
    assert capacity(ch, update)[0] > 100_000_000_000
    ch.share_price[:] = 7_500_000_000 / shares - 0.00001
    ch._eq_v += 1
    ch._atm_rebook()
    assert capacity(ch, update)[0] < 2_500_000_000


def test_preupdate_sales_never_debit_baby_shelf_and_later_price_sets_new_takedown():
    ch = chain()
    update, shares, _ = annual(ch)
    sale, _, q, _ = ch._atm_schedule()
    before = ch.atm_shares_to_date(update - 1)[0]
    assert before > 0
    expected = shares * 44 // 3 - int((sale == update).sum()) * q * 44
    assert capacity(ch, update)[0] == expected
    # Later new takedown uses its current price, but ATM's original offering
    # ceiling does not grow with each execution.
    t = ix(ch, "2024-09-09")
    ch.share_price[:, t:] = 45
    ch._eq_v += 1
    ch._atm_rebook()
    gross = sum(q * (44 if s < t else 45) for s in sale if update <= s <= t)
    assert capacity(ch, t)[0] == shares * 45 // 3 - gross


def test_atm_partial_last_lot_is_gross_limited_and_cash_settles_net():
    ch = chain(10)
    # A small explicit ownership sensitivity makes exhaustion observable within
    # this short horizon; this is arithmetic, not the case ownership record.
    ch.m["parameters"]["baby_shelf"]["nonaffiliate_shares"] = 1_000_001
    ch._eq_v += 1
    ch._atm_rebook()
    update, shares, _ = annual(ch)
    sale, settle, q, _ = ch._atm_schedule()
    quantities = np.diff(ch._atm_csold[0], prepend=0)
    post = sale >= update
    assert quantities[post].sum() == shares // 3
    assert (quantities[post] == 26907).sum() == 1
    assert (quantities[post] == 0).any()
    for j in np.flatnonzero(post):
        if 0 <= settle[j] < ch.N:
            assert ch._atm[0, settle[j]] == quantities[j] * 10 * 9700 // 10000
    assert capacity(ch, ch.N - 1)[0] == 6  # residual cents cannot buy a share
    assert ch.atm_shares_to_date(ch.N - 1)[0] == quantities.sum()
    assert ch.ledger_left(ch.N - 1)[0] >= 0


def test_failed_and_pending_offerings_are_not_completed_sales():
    ch = chain()
    update, _, _ = annual(ch)
    assert ch.initiate(ch.per_draw(update), "first").all()
    gross = ch._offers[0]["terms"]["gross"][0]
    assert gross == 1_150_000_000
    assert offering_usage(ch, update)[0] == 0
    assert offering_usage(ch, update, reserve=True)[0] == 0  # today's ATM trade precedes initiation
    assert offering_usage(ch, update + 1, reserve=True)[0] == gross
    ch.offering_outcome("first", False)
    close = int(ch._offers[0]["close"][0])
    assert offering_usage(ch, close, reserve=True)[0] == 0
    assert offering_usage(ch, close)[0] == 0
    assert not ch._offers[0]["closed"].any()


def test_success_debits_gross_at_actual_close_not_reservation_or_net():
    ch = chain()
    update, _, _ = annual(ch)
    ch.initiate(ch.per_draw(update), "first")
    ch.offering_outcome("first", True)
    o = ch._offers[0]
    close = int(o["close"][0])
    assert offering_usage(ch, close - 1)[0] == 0
    assert offering_usage(ch, close)[0] == o["terms"]["gross"][0] == 1_150_000_000
    assert o["net"][0] == 1_040_000_000
    assert offering_usage(ch, close, reserve=True)[0] == 1_150_000_000  # not doubled
    # The reduced authorized-share terms still exceed remaining dollar room.
    assert ch.offering_terms(close)["gross"][0] > capacity(ch, close)[0]
    assert not ch.offering_available(close).any()


def test_concurrent_atm_cannot_spend_an_offerings_room_or_erase_prior_trades():
    ch = chain()
    ch.m["parameters"].pop("offering_lockup")  # expose the independent dollar constraint
    update, _, value = annual(ch)
    before = ch.atm_shares_to_date(update).copy()
    ch.initiate(ch.per_draw(update), "first")
    assert np.array_equal(ch.atm_shares_to_date(update), before)
    ch.offering_outcome("first", True)
    post_shares = ch.atm_shares_to_date(ch.N - 1) - ch.atm_shares_to_date(update - 1)
    assert (post_shares * 44 + 1_150_000_000 <= value // 3).all()
    assert (capacity(ch, ch.N - 1) < 44).all()
    assert ch._atm_sold[0, -1] == 0


def test_question_explains_the_unavailable_whole_takedown():
    from types import SimpleNamespace

    from app.disputes.state14 import Situation

    ch = chain(9)
    update, _, _ = annual(ch)
    values = dict(day=update, listing="listed", offering_pending=False,
                  ledger=ch.ledger_left(update)[0], offering_terms=ch.offering_terms(update))
    group = SimpleNamespace(rep=(0, 0), rows=[{"petition": [-1]}],
                            at_rep=lambda key, **kw: values[key])
    s = Situation(SimpleNamespace(review=ch.s.review), None, fx.pending(), group, [], {})
    text = s.offering_unavailable_reason()
    assert "Form S-3 I.B.6" in text and "gross capacity" in text and "cannot be offered" in text


def test_twelve_calendar_months_excludes_expired_sales():
    ch = chain()
    update, _, _ = annual(ch)
    ch.initiate(ch.per_draw(update), "first")
    ch.offering_outcome("first", True)
    close = int(ch._offers[0]["close"][0])
    from datetime import timedelta
    closed_on = ch.s.review + timedelta(days=close + 1)
    anniversary = ch.ix(closed_on.replace(year=closed_on.year + 1))
    assert year_start(ch, ch.per_draw(anniversary))[0] == close
    assert offering_usage(ch, anniversary - 1)[0] == 1_150_000_000
    assert offering_usage(ch, anniversary)[0] == 0

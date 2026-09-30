"""Quoted amounts, unknown amounts, and statutory arithmetic retain their meaning."""
from copy import deepcopy
from datetime import date, timedelta
from itertools import product
from types import SimpleNamespace

import akoustis_20240514_fixture as pending_fx
import akoustis_fixture as entered_fx
import pytest

from app import _native
from app.analysis import events
from app.disputes.rules import load_model


def test_every_merits_outcome_preserves_component_election_and_interest(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    dispute, model = entered_fx.judgment(), load_model()
    keys = ("liability", "damages", "remittitur", "patent", "trebling", "fees", "interest")
    choices = (("stands", "granted"), ("stands", "remit", "new_trial"), ("accept", "reject"),
               ("stands", "granted"), ("stands", "granted"), ("stands", "granted"), ("stands", "granted"))
    for answers in product(*choices):
        outcome = dict(zip(keys, answers, strict=True))
        expected = events.ruling_amounts(dispute, outcome, model)
        actual = _native.ruling_amounts(dispute, outcome, model)
        assert list(actual.items()) == list(expected.items()), outcome


def test_pending_verdict_amounts_and_sensitivities_match_quoted_record(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    dispute, model = pending_fx.pending(), pending_fx.model()
    for branch in (*events.pending_template(model)["verdict_branches"], "award:123456789:0:class"):
        for sensitivity in (None, {}, {"claimant_enhancements": True}, {"claimant_enhancements": 0}):
            expected = events.verdict_basis(dispute, model, branch, sensitivity)
            assert _native.verdict_basis(dispute, model, branch, sensitivity) == expected
    assert _native.entered_cents(dispute) == events.entered_cents(dispute)


def test_unquantified_verdict_stops_instead_of_becoming_zero(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    dispute, model = pending_fx.pending(), deepcopy(pending_fx.model())
    components = tuple(c.model_copy(update={"amount_cents": None}) for c in dispute.components)
    dispute = dispute.model_copy(update={"components": components})
    for parameter in model["parameters"].values():
        parameter.pop("bound", None)
    branch = next(k for k, v in events.pending_template(model)["verdict_branches"].items()
                  if v["judgment"] and v["amount"] == "components")
    with pytest.raises(events.UnknownAmount) as reference:
        events.verdict_basis(dispute, model, branch)
    with pytest.raises(events.UnknownAmount) as native:
        _native.verdict_basis(dispute, model, branch)
    assert str(native.value) == str(reference.value)


def test_settlement_modes_and_explicit_zero_parameters_remain_distinct(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    model = pending_fx.model()
    for sens in (None, {}, {"settlement_monthly": True}, {"settlement_payment": "lump_sum"},
                 {"settlement_payment": "installments"}):
        assert _native.settlement_terms(model, sens) == events.settlement_terms(model, sens)
    for sens in (False, True, 0, 0.0, "lump_sum"):
        assert _native.parameter_value(model, "settlement_payment", sens) == events.pval(model, "settlement_payment", sens)


@pytest.mark.parametrize("days", [1, 30, 365])
def test_scalar_interest_divides_the_exact_integer_product_before_rounding(monkeypatch, days):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    model = deepcopy(load_model())
    model["rules"][model["rules"]["nc_24_5_b"]["rate_rule"]]["value"] = 6151
    dispute = SimpleNamespace(commenced=date(2023, 1, 1), judgment_date=date(2023, 1, 1) + timedelta(days=days))
    principal = 427155806170096947
    assert _native.prejudgment_interest_cents(dispute, principal, model) == (
        events.prejudgment_interest_cents(dispute, principal, model))

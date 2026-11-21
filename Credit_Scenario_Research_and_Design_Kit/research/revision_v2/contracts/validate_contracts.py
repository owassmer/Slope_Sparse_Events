"""Validate prototype contract shapes and a small set of economic data invariants.

Requires jsonschema>=4.26,<5. This checks design artifacts, not lending accuracy.
"""
import copy
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "build-contracts.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def stamp(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for val in obj.values():
            yield from walk(val)
    elif isinstance(obj, list):
        for val in obj:
            yield from walk(val)


def validate(packet):
    VALIDATOR.validate(packet)
    context = packet["decision_context"]
    as_of = stamp(context["as_of"])
    known_facts = {x["fact_id"] for x in packet["source_fact_references"]}
    assumptions = {}
    for ob in walk(packet):
        if "source_fact_ids" in ob:
            assert set(ob["source_fact_ids"]) <= known_facts, "Unknown source-fact reference"
        if set(("unit", "status", "value", "lower", "upper")) <= set(ob):
            if ob.get("provenance", {}).get("basis") == "operator_assumption":
                aid = ob["provenance"]["assumption_id"]
                signature = (ob["unit"], ob["status"], ob["value"], ob["lower"], ob["upper"])
                assert aid not in assumptions or assumptions[aid] == signature, "Conflicting values for one assumption ID"
                assumptions[aid] = signature
            if ob["status"] == "range":
                convert = Decimal if ob["unit"].startswith("decimal_") else lambda x: x
                assert convert(ob["lower"]) <= convert(ob["upper"]), "Inverted range"
            if ob["unit"] == "decimal_fraction":
                for key in ("value", "lower", "upper"):
                    if ob[key] is not None:
                        assert Decimal("0") <= Decimal(ob[key]) <= Decimal("1"), "Fraction outside 0..1"

    for obs in packet["historical_observations"]:
        assert stamp(obs["available_at"]) <= as_of, "Future evidence in historical run"
        assert obs["period_end"] <= as_of.date().isoformat(), "Future financial observation"
    assert packet["loan_terms"]["borrower_id"] == context["borrower_id"], "Borrower mismatch"
    for key in ("facility_limit", "principal_outstanding", "eligible_receivables_amount", "reported_undrawn_availability"):
        assert packet["loan_terms"][key]["unit"] == "USD_cents", "Money field has wrong unit"
    for key in ("annual_spread", "reported_all_in_annual_rate"):
        assert packet["loan_terms"][key]["unit"] == "decimal_rate", "Rate field has wrong unit"
    assert packet["loan_terms"]["advance_rate"]["unit"] == "decimal_fraction"
    assert packet["loan_terms"]["maturity"]["unit"] == "date"

    effects = {x["effect_id"] for x in packet["effects"]}
    scenarios = {x["scenario_id"] for x in packet["scenarios"]}
    for scenario in packet["scenarios"]:
        assert scenario["decision_id"] == context["decision_id"], "Decision mismatch"
        assert scenario["baseline_id"] == context["baseline_id"], "Baseline mismatch"
        assert set(scenario["effect_ids"]) <= effects, "Unknown effect"
        assert scenario["probability"]["unit"] == "decimal_fraction"
    export = packet["loan_cashflow_export"]
    assert export["decision_id"] == context["decision_id"]
    assert export["loan_id"] == packet["loan_terms"]["loan_id"]
    assert export["borrower_id"] == context["borrower_id"]
    assert export["baseline_id"] == context["baseline_id"]
    if export["scenario_id"] is not None:
        assert export["scenario_id"] in scenarios
    if export["cashflow_type"] == "expected":
        assert all(x["scenario_id"] in scenarios for x in export["scenario_weights"])
        assert sum(Decimal(x["weight"]) for x in export["scenario_weights"]) == Decimal("1"), "Weights must sum to 1"
    if export["cashflow_type"] in ("conditional", "expected") and export["rows"]:
        assert context["baseline_status"] in ("complete", "explicit_assumption_model"), "No numerical curve from missing baseline"
        assert context["baseline_id"] is not None, "Missing baseline version"
    assert packet["decision_result"]["decision_id"] == context["decision_id"]
    if packet["decision_result"]["policy_basis"] == "none":
        assert packet["decision_result"]["preferred_action_id"] is None, "No preferred lending action without an explicit policy"
    for threshold in packet["decision_result"].get("threshold_results", []):
        inputs = {x["name"]: x["value"]["value"] for x in threshold["inputs"]}
        if threshold["threshold_id"] == "T_H2_incremental_free_cash":
            assert 0 <= inputs["A_post_june_hvl_paid_cents"] <= inputs["HVL_H2_bucket_at_June30"]
            assert 0 <= inputs["A_post_june_supplier_paid_cents"] <= inputs["supplier_H2_bucket_at_June30"]
            assert inputs["A_policy_cash_reserve_cents"] >= 0
            expected = max(0, inputs["HVL_H2_bucket_at_June30"] + inputs["supplier_H2_bucket_at_June30"] - inputs["A_post_june_hvl_paid_cents"] - inputs["A_post_june_supplier_paid_cents"] + inputs["A_policy_cash_reserve_cents"] - inputs["A_opening_cash_for_threshold_cents"])
            assert threshold["result"]["value"] == expected, "Wrong conditional settlement threshold"
        elif threshold["threshold_id"].startswith("T_account_credits_"):
            expected = Decimal(inputs["applicable_repayment_requirement"]) / Decimal(inputs["remittance_share"])
            assert threshold["result"]["value"] == expected, "Wrong account-credit threshold"


def must_reject(packet, label):
    try:
        validate(packet)
    except Exception:
        return label
    raise AssertionError(f"Bad example accepted: {label}")


if __name__ == "__main__":
    Draft202012Validator.check_schema(SCHEMA)
    packet = json.loads((ROOT / "economic-effects.json").read_text())
    validate(packet)
    validate(json.loads((ROOT / "barfresh-economic-effects.json").read_text()))
    registry = {x["fact_id"]: x for x in json.loads((ROOT.parent / "data" / "facts_synergy.json").read_text())["facts"]}
    for item in packet["source_fact_references"]:
        assert item["fact_id"] in registry
        assert registry[item["fact_id"]]["source_id"] == item["document_id"]
    proposals = json.loads((ROOT / "proposal-fixtures.json").read_text())
    proposal_schema = json.loads((ROOT / "proposal-fixtures.schema.json").read_text())
    Draft202012Validator.check_schema(proposal_schema)
    Draft202012Validator(proposal_schema).validate(proposals)
    for offer in proposals["offers"]:
        assert sum(x["borrower_payment_cents"] for x in offer["payment_rows"]) == offer["total_repayment_cents"]
        assert offer["advance_cents"] + offer["fixed_total_fee_cents"] == offer["total_repayment_cents"]
        assert [x["month_index"] for x in offer["payment_rows"]] == list(range(1, offer["term_months"] + 1))
    def topup(window_daily, recognized_manual, outstanding_total):
        return min(outstanding_total, max(0, 12543000 - window_daily - recognized_manual))
    assert topup(4181000, 0, 16724000) == 8362000  # 50% first window, 10% second: cumulative60% is insufficient.
    assert topup(0, 0, 5000000) == 5000000  # No collection beyond outstanding total.
    assert topup(4181000, 8362000, 8362000) == 0  # Recognized manual credit counted once.
    assert topup(0, 0, 0) == 0  # Full payoff ends obligations.
    rejected = []

    bad = copy.deepcopy(packet)
    bad["historical_observations"][0]["value"]["value"] = 38300000.5
    rejected.append(must_reject(bad, "fractional-cent money"))

    bad = copy.deepcopy(packet)
    bad["loan_terms"]["merchant_repayment_rules"]["receipts_fraction"]["value"] = 0.25
    rejected.append(must_reject(bad, "floating-point rate instead of decimal string"))

    bad = copy.deepcopy(packet)
    bad["decision_context"]["requested_amount"]["value"] = 0
    rejected.append(must_reject(bad, "unknown amount silently changed to zero"))

    bad = copy.deepcopy(packet)
    bad["loan_cashflow_export"]["cashflow_type"] = "expected"
    rejected.append(must_reject(bad, "expected curve without scenario weights"))

    bad = copy.deepcopy(packet)
    bad["historical_observations"][0]["available_at"] = "2025-03-27T20:10:21Z"
    rejected.append(must_reject(bad, "future evidence at August 2024 cutoff"))

    bad = copy.deepcopy(packet)
    bad["loan_terms"]["merchant_repayment_rules"]["receipts_fraction"]["value"] = "1.10"
    rejected.append(must_reject(bad, "remittance fraction outside zero to one"))

    bad = copy.deepcopy(packet)
    bad["loan_cashflow_export"]["status"] = "complete"
    bad["decision_context"]["baseline_status"] = "missing"
    bad["loan_cashflow_export"]["rows"] = [{"date": "2024-11-01", "disbursement_cents": 0, "principal_collected_cents": 1, "interest_collected_cents": 0, "fees_collected_cents": 0, "net_recovery_cents": 0, "explicit_cost_cents": 0, "principal_outstanding_cents": 0, "calculation_id": "fabricated"}]
    rejected.append(must_reject(bad, "numerical collection curve without a baseline"))

    bad = copy.deepcopy(packet)
    bad["decision_result"]["threshold_results"][0]["result"]["value"] = 60000000
    rejected.append(must_reject(bad, "incorrect settlement threshold calculation"))

    report = {"artifact_status": "contract_shape_checks_only_not_model_validation",
              "schema_draft": "2020-12", "schema_valid": True, "example_packet_valid": True,
              "secondary_barfresh_packet_valid": True, "conditional_threshold_arithmetic_checked": True,
              "synergy_fact_ids_resolved_in_registry": True, "proposal_fixtures_valid_and_conserve_payments": True,
              "merchant_separate_window_cap_and_recognized_manual_credit_examples_checked": True,
              "negative_checks_passed": rejected,
              "not_checked": ["Independent truth verification beyond the supplied source/facts registry", "General loan cash-flow engine (not implemented)", "Actual borrower creditworthiness", "Any real model or agent inference"]}
    (ROOT / "validation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

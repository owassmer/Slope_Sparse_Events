"""Generate prototype JSON Schema and an evidence-only Barfresh research packet.

This file generates design artifacts. It is not a running credit model.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def obj(properties, required=None, description=None):
    out = {"type": "object", "properties": properties,
           "required": list(properties) if required is None else required,
           "additionalProperties": False}
    if description:
        out["description"] = description
    return out


def arr(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def enum(*values):
    return {"enum": list(values)}


def ref(name):
    return {"$ref": f"#/$defs/{name}"}


S = {"type": "string", "minLength": 1}
DATE = {"type": "string", "format": "date"}
DT = {"type": "string", "format": "date-time"}
NS = {"type": ["string", "null"]}
DEC = {"type": "string", "pattern": r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"}
I = {"type": "integer"}
B = {"type": "boolean"}

defs = {}
defs["Provenance"] = obj({
    "basis": enum("documented_evidence", "operator_assumption", "model_derived", "unknown"),
    "source_fact_ids": arr(S), "assumption_id": NS, "derivation": NS,
    "note": S,
})
defs["Provenance"]["allOf"] = [
    {"if": {"properties": {"basis": {"const": "documented_evidence"}}},
     "then": {"properties": {"source_fact_ids": {"minItems": 1}, "assumption_id": {"type": "null"}}}},
    {"if": {"properties": {"basis": {"const": "operator_assumption"}}},
     "then": {"properties": {"assumption_id": S}}},
    {"if": {"properties": {"basis": {"const": "model_derived"}}},
     "then": {"properties": {"derivation": S}}},
]

defs["EvidenceValue"] = obj({
    "status": enum("exact", "range", "unknown"),
    "unit": enum("USD_cents", "decimal_rate", "decimal_fraction", "days", "date"),
    "value": {"type": ["integer", "string", "null"]},
    "lower": {"type": ["integer", "string", "null"]},
    "upper": {"type": ["integer", "string", "null"]},
    "observed_on": {"anyOf": [DATE, {"type": "null"}]},
    "provenance": ref("Provenance"),
}, description="USD amounts are integer cents. Decimal rates/fractions are strings for Decimal parsing. Unknown is not zero; exact means the source provides a number, not that the source is infallible.")
defs["EvidenceValue"]["allOf"] = [
    {"if": {"properties": {"status": {"const": "exact"}}},
     "then": {"properties": {"value": {"not": {"type": "null"}}, "lower": {"type": "null"}, "upper": {"type": "null"}}}},
    {"if": {"properties": {"status": {"const": "range"}}},
     "then": {"properties": {"value": {"type": "null"}, "lower": {"not": {"type": "null"}}, "upper": {"not": {"type": "null"}}}}},
    {"if": {"properties": {"status": {"const": "unknown"}}},
     "then": {"properties": {x: {"type": "null"} for x in ("value", "lower", "upper")}}},
    {"if": {"properties": {"unit": {"enum": ["USD_cents", "days"]}}},
     "then": {"properties": {x: {"type": ["integer", "null"]} for x in ("value", "lower", "upper")}}},
    {"if": {"properties": {"unit": {"enum": ["decimal_rate", "decimal_fraction"]}}},
     "then": {"properties": {x: {"anyOf": [DEC, {"type": "null"}]} for x in ("value", "lower", "upper")}}},
    {"if": {"properties": {"unit": {"const": "date"}}},
     "then": {"properties": {x: {"anyOf": [DATE, {"type": "null"}]} for x in ("value", "lower", "upper")}}},
]

defs["DecisionContext"] = obj({
    "decision_id": S, "mission_id": S, "borrower_id": S, "borrower_name": S,
    "as_of": DT, "decision_timezone": S,
    "decision_type": enum("new_draw", "exposure_increase", "existing_loan_review"),
    "transaction_basis": enum("documented_transaction", "analytical_candidate"),
    "slope_relationship": enum("documented", "not_established"),
    "stakeholder_scope": enum("loan_asset_holder", "platform_net", "warehouse_equity"),
    "currency": {"const": "USD"}, "requested_amount": ref("EvidenceValue"),
    "requested_term_months": {"type": ["integer", "null"], "minimum": 1},
    "use_of_proceeds": S, "baseline_id": NS,
    "baseline_status": enum("complete", "historical_observations_only", "missing"),
    "available_actions": arr(enum("requested_offer", "smaller_feasible_purchase", "alternative_term", "conditional_offer", "decline_new_exposure")),
    "missing_inputs": arr(S), "note": S,
})

defs["Observation"] = obj({"observation_id": S, "metric": S,
                            "value": ref("EvidenceValue"), "period_start": {"anyOf": [DATE, {"type": "null"}]},
                            "period_end": DATE, "available_at": DT,
                            "is_current_bank_observation": B})

defs["PaymentRow"] = obj({"due_date": DATE, "principal_cents": I,
                           "interest_cents": I, "fee_cents": I,
                           "source_fact_ids": arr(S), "assumption_id": NS})
defs["FeeTerm"] = obj({"name": S, "rate": ref("EvidenceValue"),
                        "basis": S, "frequency": enum("one_time", "annual", "monthly", "daily", "unknown")})
defs["LoanTerms"] = obj({
    "loan_id": S, "borrower_id": S,
    "terms_basis": enum("executed_contract", "public_disclosure", "operator_candidate"),
    "product_type": enum("receivables_revolver", "fixed_installment_draw", "net_terms", "other"),
    "facility_limit": ref("EvidenceValue"), "principal_outstanding": ref("EvidenceValue"),
    "interest_index": enum("US_prime", "fixed", "unknown"),
    "annual_spread": ref("EvidenceValue"), "reported_all_in_annual_rate": ref("EvidenceValue"),
    "advance_rate": ref("EvidenceValue"), "eligible_receivables_amount": ref("EvidenceValue"),
    "reported_undrawn_availability": ref("EvidenceValue"),
    "maturity": ref("EvidenceValue"), "automatic_renewal_reported": B,
    "fees": arr(ref("FeeTerm")),
    "schedule_status": enum("documented", "generated_from_complete_terms", "unknown"),
    "contractual_schedule": arr(ref("PaymentRow")),
    "payment_allocation_rule": enum("contract_documented", "operator_assumption", "unknown"),
    "collateral_summary": S, "missing_terms": arr(S), "source_fact_ids": arr(S, 1),
})
defs["LoanTerms"]["allOf"] = [
    {"if": {"properties": {"schedule_status": {"const": "unknown"}}},
     "then": {"properties": {"contractual_schedule": {"maxItems": 0}}}},
    {"if": {"properties": {"schedule_status": {"enum": ["documented", "generated_from_complete_terms"]}}},
     "then": {"properties": {"contractual_schedule": {"minItems": 1}}}},
]

defs["Parameter"] = obj({"name": S, "role": enum("source_anchor", "model_input", "sensitivity_input"),
                          "value": ref("EvidenceValue")})
defs["Effect"] = obj({
    "effect_id": S, "event_id": S, "borrower_id": S,
    "mechanism": enum("existing_liability_payment_timing", "expense_funding_change", "receipt_timing_change", "receipt_impairment", "cash_restriction", "collateral_posting", "reimbursement", "financing_capacity_change", "inventory_conversion"),
    "finding": S, "target_stream_ids": arr(S), "source_fact_ids": arr(S, 1),
    "parameters": arr(ref("Parameter")),
    "baseline_reflection": enum("already_in_historical_balance", "already_in_historical_cashflow", "partly_reflected", "not_reflected", "unknown"),
    "proposed_operation": enum("reclassify_timing", "replace_future_forecast", "shift_cash_receipts", "reduce_cash_receipts", "transfer_cash_pool", "modify_available_funding", "convert_inventory_to_receipts"),
    "application_status": enum("ready_after_baseline_match", "requires_parameters", "research_only"),
    "activation_condition": S, "cash_direction": enum("outflow", "inflow", "timing_only", "mixed", "unknown"),
    "double_count_guard": S, "missing_inputs": arr(S),
})

defs["Scenario"] = obj({
    "scenario_id": S, "decision_id": S, "baseline_id": NS,
    "label": S, "conditions": arr(S, 1), "effect_ids": arr(S),
    "parameter_overrides": arr(ref("Parameter")),
    "probability": ref("EvidenceValue"),
    "payment_behavior": enum("contractual_if_feasible_with_documented_allocation", "operator_assumption", "unknown"),
    "forecast_status": enum("ready", "requires_inputs", "not_run"),
    "is_realized_outcome": B,
})

defs["ActionResult"] = obj({"action_id": S, "status": enum("feasible", "infeasible", "conditional", "not_computable"),
                             "amount": ref("EvidenceValue"), "term_months": {"type": ["integer", "null"]},
                             "npv": ref("EvidenceValue"), "binding_conditions": arr(S)})
defs["DecisionResult"] = obj({
    "decision_id": S, "result_status": enum("recommendation", "conditional_recommendation", "decision_boundary", "insufficient_inputs", "incomplete_review"),
    "preferred_action_id": NS, "action_results": arr(ref("ActionResult")),
    "policy_basis": enum("provided_policy", "operator_demo_policy", "none"),
    "policy_id": NS, "required_conditions": arr(S), "decisive_missing_inputs": arr(S),
    "summary": S,
})

defs["CollectionRow"] = obj({"date": DATE, "disbursement_cents": I,
                              "principal_collected_cents": I, "interest_collected_cents": I,
                              "fees_collected_cents": I, "net_recovery_cents": I,
                              "explicit_cost_cents": I, "principal_outstanding_cents": I,
                              "calculation_id": S})
defs["ScenarioWeight"] = obj({"scenario_id": S, "weight": DEC, "basis": enum("provided_empirical_model", "operator_scenario_view"), "source_id": S})
defs["LoanCashflowExport"] = obj({
    "export_id": S, "loan_id": S, "borrower_id": S, "decision_id": S,
    "as_of": DT, "currency": {"const": "USD"},
    "cashflow_type": enum("contractual", "conditional", "expected", "realized"),
    "status": enum("complete", "partial", "not_computable", "not_run"),
    "scenario_id": NS, "baseline_id": NS,
    "scenario_weights": arr(ref("ScenarioWeight")),
    "rows": arr(ref("CollectionRow")),
    "terminal_unresolved_principal": ref("EvidenceValue"),
    "missing_inputs": arr(S), "stakeholder_scope": enum("loan_asset_holder", "platform_net", "warehouse_equity"),
})
defs["LoanCashflowExport"]["allOf"] = [
    {"if": {"properties": {"cashflow_type": {"const": "expected"}}},
     "then": {"properties": {"scenario_weights": {"minItems": 1}}}},
    {"if": {"properties": {"cashflow_type": {"const": "conditional"}}},
     "then": {"properties": {"scenario_id": S, "scenario_weights": {"maxItems": 0}}}},
    {"if": {"properties": {"status": {"const": "not_computable"}}},
     "then": {"properties": {"rows": {"maxItems": 0}, "missing_inputs": {"minItems": 1}}}},
]

defs["SourceFactReference"] = obj({"fact_id": S, "document_id": S, "section": S, "note": S})
defs["CasePacket"] = obj({
    "schema_version": {"const": "0.1.0"}, "case_id": S,
    "artifact_status": {"const": "prototype_design_not_running_model"},
    "source_fact_references": arr(ref("SourceFactReference")),
    "decision_context": ref("DecisionContext"),
    "historical_observations": arr(ref("Observation")),
    "loan_terms": ref("LoanTerms"), "effects": arr(ref("Effect")),
    "scenarios": arr(ref("Scenario")), "decision_result": ref("DecisionResult"),
    "loan_cashflow_export": ref("LoanCashflowExport"),
})

schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
          "$id": "https://example.invalid/slope-demo/build-contracts-0.1.0.schema.json",
          "title": "Prototype external-event loan scenario contracts",
          "description": "Design contract only; example.invalid is an identifier, not a service. Application validation must check evidence cutoffs, units by field, cross-references, Decimal bounds, weight totals, source facts, and financial conservation.",
          "$ref": "#/$defs/CasePacket", "$defs": defs}


def value(unit, number=None, observed=None, facts=None, note="Not publicly established.", basis=None, derivation=None):
    return {"status": "unknown" if number is None else "exact", "unit": unit,
            "value": number, "lower": None, "upper": None, "observed_on": observed,
            "provenance": {"basis": basis or ("documented_evidence" if facts else "unknown"),
                           "source_fact_ids": facts or [], "assumption_id": None,
                           "derivation": derivation, "note": note}}


def p(name, unit, number=None, observed=None, facts=None, note="Not publicly established.", role="source_anchor"):
    return {"name": name, "role": role, "value": value(unit, number, observed, facts, note)}


F = {
    "q2_bs": "barfresh:brfh_2024q2_10q:balance_sheet",
    "q2_lit": "barfresh:brfh_2024q2_10q:note4_litigation",
    "q2_cash": "barfresh:brfh_2024q2_10q:note9_inventory_build",
    "q3_bs": "barfresh:brfh_2024q3_10q:balance_sheet",
    "q3_lit": "barfresh:brfh_2024q3_10q:note4_litigation",
    "q3_debt": "barfresh:brfh_2024q3_10q:note5_line_of_credit",
    "q3_cap": "barfresh:brfh_2024q3_10q:note8_capacity",
    "q3_cf": "barfresh:brfh_2024q3_10q:cash_flow_statement",
}
ASOF = "2024-10-25T23:59:59-04:00"
DID = "brfh_2024-10-25_external_event_review"
BID = "barfresh_food_group_inc"
LID = "barfresh_receivables_facility_2024"

context = {
    "decision_id": DID, "mission_id": "barfresh_20241025", "borrower_id": BID, "borrower_name": "Barfresh Food Group Inc.",
    "as_of": ASOF, "decision_timezone": "America/New_York",
    "decision_type": "new_draw", "transaction_basis": "analytical_candidate",
    "slope_relationship": "not_established", "stakeholder_scope": "loan_asset_holder", "currency": "USD",
    "requested_amount": value("USD_cents", note="No actual October 25 loan request is public; choose an analysis amount only as a separately recorded operator assumption."),
    "requested_term_months": None,
    "use_of_proceeds": "Analyze funding inventory and receivables conversion while replacement manufacturing capacity ramps. The actual receivables facility is the financing anchor; this is not a documented Slope application.",
    "baseline_id": None, "baseline_status": "historical_observations_only",
    "available_actions": ["requested_offer", "smaller_feasible_purchase", "alternative_term", "conditional_offer", "decline_new_exposure"],
    "missing_inputs": ["Current unrestricted bank balance on decision date", "Invoice-level receivables aging, eligibility and payment dates", "Dated operating cash forecast and inventory purchase plan", "Requested amount and offer/payment terms", "Current competing debt/payment obligations and payment allocation"],
    "note": "June 30 and September 30 are archived financial observation dates; neither is an October 25 bank-feed observation. No modeled baseline or exact collections is fabricated.",
}
obs = []
for date, available, fid, rows in [
    ("2024-06-30", "2024-08-14T20:10:46Z", F["q2_bs"], [("cash_and_cash_equivalents", 38300000), ("trade_receivables_net", 67100000), ("inventory_net", 153400000), ("disputed_supplier_payable", 49900000)]),
    ("2024-09-30", "2024-10-24T20:10:34Z", F["q3_bs"], [("cash_and_cash_equivalents", 40100000), ("trade_receivables_net", 166300000), ("inventory_net", 77000000), ("disputed_supplier_payable", 49900000)]),
]:
    for metric, amount in rows:
        obs.append({"observation_id": f"{BID}:{date}:{metric}", "metric": metric,
                    "value": value("USD_cents", amount, date, [fid], "Reported balance at period end, not current bank/invoice data."),
                    "period_start": None, "period_end": date, "available_at": available, "is_current_bank_observation": False})
for metric, amount in [("line_of_credit_cash_borrowings", 93000000), ("line_of_credit_cash_repayments", 84700000)]:
    obs.append({"observation_id": f"{BID}:2024-09-30:{metric}", "metric": metric,
                "value": value("USD_cents", amount, "2024-09-30", [F["q3_cf"]], "Reported nine-month cash-flow total; facility originated in August. Individual draw/repayment dates are not disclosed."),
                "period_start": "2024-01-01", "period_end": "2024-09-30", "available_at": "2024-10-24T20:10:34Z", "is_current_bank_observation": False})

debtfacts = [F["q3_debt"]]
terms = {
    "loan_id": LID, "borrower_id": BID, "terms_basis": "public_disclosure", "product_type": "receivables_revolver",
    "facility_limit": value("USD_cents", 150000000, "2024-09-30", debtfacts, "Facility commitment; not automatically drawable cash."),
    "principal_outstanding": value("USD_cents", 10000000, "2024-09-30", debtfacts, "Gross amount inferred from $86,000 carrying amount net of $14,000 deferred financing cost; reconcile separately to cash-flow totals.", "model_derived", "8600000 + 1400000 = 10000000 cents"),
    "interest_index": "US_prime", "annual_spread": value("decimal_rate", "0.012", "2024-09-30", debtfacts, "Reported annual spread over prime."),
    "reported_all_in_annual_rate": value("decimal_rate", "0.092", "2024-09-30", debtfacts, "Reported rate at September 30 only; not automatically the October 25 rate."),
    "advance_rate": value("decimal_fraction", "0.90", "2024-09-30", debtfacts, "Up to 90% of eligible customer account balances."),
    "eligible_receivables_amount": value("USD_cents", note="Net reported receivables are not an invoice eligibility certificate."),
    "reported_undrawn_availability": value("USD_cents", 140000000, "2024-09-30", debtfacts, "Reported as available on September 30; not a verified borrowing base at October 25."),
    "maturity": value("date", "2025-09-05", "2024-09-30", debtfacts, "Reported termination date with automatic renewal absent notice."),
    "automatic_renewal_reported": True,
    "fees": [{"name": "collateral_fee", "rate": value("decimal_rate", "0.0015", "2024-09-30", debtfacts, "0.15% reported; fee base and frequency are not specified in this disclosure."), "basis": "unknown", "frequency": "unknown"}],
    "schedule_status": "unknown", "contractual_schedule": [], "payment_allocation_rule": "unknown",
    "collateral_summary": "Accounts receivable and inventory per public disclosure; full priority and control terms not supplied.",
    "missing_terms": ["Executed agreement and payment/sweep conventions", "Current rate reset and contractual day count", "Collateral fee basis/frequency", "Invoice-level eligibility and current draws", "Cash-flow-to-gross-principal reconciliation"],
    "source_fact_ids": debtfacts,
}

effects = [
    {
        "effect_id": "E1_disputed_payable_timing", "event_id": "schreiber_dispute", "borrower_id": BID,
        "mechanism": "existing_liability_payment_timing",
        "finding": "$499,000 owed to the former manufacturer is withheld and remains a separately reported disputed payable. The litigation record does not establish an immediate payment date.",
        "target_stream_ids": ["pending:baseline:disputed_supplier_payable"], "source_fact_ids": [F["q3_bs"], F["q3_lit"]],
        "parameters": [p("reported_disputed_balance", "USD_cents", 49900000, "2024-09-30", [F["q3_bs"]], "Existing balance, not an incremental cash outflow."), p("conditional_payment_date", "date", role="model_input"), p("conditional_payment_amount", "USD_cents", role="model_input")],
        "baseline_reflection": "already_in_historical_balance", "proposed_operation": "reclassify_timing",
        "application_status": "requires_parameters", "activation_condition": "A sourced payment obligation or separately declared settlement sensitivity supplies amount and date.",
        "cash_direction": "outflow", "double_count_guard": "Match and replace existing payable timing; do not add the liability twice or charge a new expense.",
        "missing_inputs": ["Payment timing and amount under an operative settlement or supported scenario", "Existing baseline payable cash schedule"],
    },
    {
        "effect_id": "E2_legal_cost_funding", "event_id": "schreiber_dispute", "borrower_id": BID,
        "mechanism": "expense_funding_change",
        "finding": "Company reports nonrecourse litigation financing entered in May 2024, expected to fund pursuit of its complaint. This changes who funds future case costs; it does not establish a recovery amount or unrestricted cash advance.",
        "target_stream_ids": ["pending:baseline:future_schreiber_legal_cash_spend"], "source_fact_ids": [F["q2_lit"], F["q3_lit"]],
        "parameters": [p("covered_future_legal_cash_spend", "USD_cents", role="model_input"), p("remaining_unfunded_case_cost", "USD_cents", role="model_input")],
        "baseline_reflection": "partly_reflected", "proposed_operation": "replace_future_forecast",
        "application_status": "requires_parameters", "activation_condition": "Match the baseline case-cost forecast and establish financing coverage; historical costs before May remain historical.",
        "cash_direction": "mixed", "double_count_guard": "Do not extrapolate pre-funding case expense then charge it again; do not eliminate unrelated legal expenses or book lawsuit proceeds.",
        "missing_inputs": ["Baseline future case-cost forecast", "Coverage limits and cash-payment mechanics of litigation financing"],
    },
    {
        "effect_id": "E3_capacity_receipt_timing", "event_id": "schreiber_dispute", "borrower_id": BID,
        "mechanism": "receipt_timing_change",
        "finding": "The supplier dispute constrained bottle supply; company expects additional contracted capacity to start in Q4 2024. Expected capacity is not completed production or collected sales.",
        "target_stream_ids": ["pending:baseline:affected_bottle_sales_receipts"], "source_fact_ids": [F["q3_lit"], F["q3_cap"]],
        "parameters": [p("affected_receipts", "USD_cents", role="model_input"), p("ramp_delay_days", "days", role="sensitivity_input"), p("affected_sales_share", "decimal_fraction", role="model_input")],
        "baseline_reflection": "partly_reflected", "proposed_operation": "shift_cash_receipts",
        "application_status": "requires_parameters", "activation_condition": "A funded purchase plan and receipt forecast identify which cash flows depend on replacement manufacturing and their expected dates.",
        "cash_direction": "timing_only", "double_count_guard": "Move delayed receipts rather than deleting them and adding new receipts; no blanket haircut from the supplier's historical production share.",
        "missing_inputs": ["Affected product/channel sales and customer payment terms", "Actual production milestones", "Baseline receipt schedule"],
    },
    {
        "effect_id": "E4_inventory_conversion", "event_id": "schreiber_dispute", "borrower_id": BID,
        "mechanism": "inventory_conversion",
        "finding": "H1 cash use included $320,000 inventory build. Inventory was $1.534m at June 30 and $770k at September 30; these are dated stock balances and cannot directly establish current cash receipts.",
        "target_stream_ids": ["pending:baseline:inventory_purchase_and_conversion"], "source_fact_ids": [F["q2_cash"], F["q2_bs"], F["q3_bs"]],
        "parameters": [p("historical_h1_inventory_build_cash", "USD_cents", 32000000, "2024-06-30", [F["q2_cash"]], "Already included in historical operating cash flow."), p("inventory_at_last_reported_date", "USD_cents", 77000000, "2024-09-30", [F["q3_bs"]], "September balance, not October stock count."), p("current_saleable_inventory", "USD_cents", role="model_input"), p("inventory_conversion_days", "days", role="sensitivity_input")],
        "baseline_reflection": "already_in_historical_cashflow", "proposed_operation": "convert_inventory_to_receipts",
        "application_status": "requires_parameters", "activation_condition": "Identify saleable units, their cost, expected sales, customer collection timing, and incremental inventory to be financed.",
        "cash_direction": "mixed", "double_count_guard": "Do not re-spend the historical $320,000 or treat inventory decrease as identical cash receipts.",
        "missing_inputs": ["Current inventory composition and saleability", "Purchase plan, sales margins and collection timing"],
    },
]

scenarios = []
for sid, label, conditions in [
    ("S1_documented_ramp_conditions", "Replacement capacity comes online within the supported operating plan", ["Borrower-specific operating inputs must establish actual ramp and collection dates.", "No litigation recovery is assumed as a repayment source."]),
    ("S2_delayed_ramp_sensitivity", "Capacity-dependent receipts arrive later", ["Operator must specify an explicit delay sensitivity; no delay number is silently inferred from legal severity.", "Costs and financed purchase size must be updated consistently with the delayed operating plan."]),
]:
    scenarios.append({"scenario_id": sid, "decision_id": DID, "baseline_id": None, "label": label,
                      "conditions": conditions, "effect_ids": [x["effect_id"] for x in effects],
                      "parameter_overrides": [], "probability": value("decimal_fraction", note="No calibrated or operator-supplied scenario weight. Jev confidence cannot supply it."),
                      "payment_behavior": "unknown", "forecast_status": "requires_inputs", "is_realized_outcome": False})

packet = {
    "schema_version": "0.1.0", "case_id": "barfresh_schreiber_2024", "artifact_status": "prototype_design_not_running_model",
    "source_fact_references": [{"fact_id": fid, "document_id": fid.split(":")[1], "section": fid.split(":")[2], "note": "Source-section reference resolved to the named archived SEC document in ../data/sources.json; section identifies the inspected financial table or note."} for fid in F.values()],
    "decision_context": context, "historical_observations": obs, "loan_terms": terms, "effects": effects, "scenarios": scenarios,
    "decision_result": {"decision_id": DID, "result_status": "insufficient_inputs", "preferred_action_id": None, "action_results": [], "policy_basis": "none", "policy_id": None, "required_conditions": [], "decisive_missing_inputs": context["missing_inputs"], "summary": "Research packet identifies model adjustments and known financing terms. It does not yet establish a current baseline or an actual requested draw, so no lending verdict, payment curve, or exact safe limit has been calculated."},
    "loan_cashflow_export": {"export_id": "brfh_placeholder_export", "loan_id": LID, "borrower_id": BID, "decision_id": DID, "as_of": ASOF, "currency": "USD", "cashflow_type": "conditional", "status": "not_computable", "scenario_id": "S1_documented_ramp_conditions", "baseline_id": None, "scenario_weights": [], "rows": [], "terminal_unresolved_principal": value("USD_cents", note="No current-dated exposure and collection simulation has run."), "missing_inputs": context["missing_inputs"], "stakeholder_scope": "loan_asset_holder"},
}

(ROOT / "build-contracts.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
(ROOT / "barfresh-economic-effects.json").write_text(json.dumps(packet, indent=2) + "\n")
print("Wrote schema and evidence-only example packet.")

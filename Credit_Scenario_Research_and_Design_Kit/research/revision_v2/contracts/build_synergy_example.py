"""Extend the prototype contracts and generate the main Synergy threshold example.

Explicit sensitivities are model inputs, never fabricated borrower history.
"""
import json
from decimal import Decimal
import build_contracts as base

ROOT = base.ROOT
defs = base.defs
obj, arr, enum, ref = base.obj, base.arr, base.enum, base.ref
S, I, NS, DATE = base.S, base.I, base.NS, base.DATE
value, p = base.value, base.p

defs["DecisionContext"]["properties"]["baseline_status"]["enum"].append("explicit_assumption_model")
defs["LoanTerms"]["properties"]["product_type"]["enum"].append("revenue_share_merchant_loan")
defs["LoanTerms"]["properties"]["schedule_status"]["enum"].append("documented_rules_with_unknown_dates")
defs["MinimumCheckpoint"] = obj({"months_from_funding": {"type": "integer", "minimum": 1},
                                 "minimum_cumulative_fraction": ref("EvidenceValue"),
                                 "minimum_cumulative_repayment": ref("EvidenceValue"),
                                 "window_start_month": {"type": "integer", "minimum": 1},
                                 "window_minimum_fraction": ref("EvidenceValue"),
                                 "cap_at_outstanding_total_repayment": {"const": True},
                                 "manual_payment_credit_rule": S})
defs["MerchantRepaymentRules"] = obj({
    "advance": ref("EvidenceValue"), "total_repayment": ref("EvidenceValue"),
    "fixed_financing_cost": ref("EvidenceValue"), "receipts_fraction": ref("EvidenceValue"),
    "receipt_base_definition": S, "funding_date": ref("EvidenceValue"),
    "term_months": {"type": "integer", "minimum": 1},
    "minimum_checkpoints": arr(ref("MinimumCheckpoint"), 1),
    "remittance_calendar": S, "prepayment_treatment": S,
})
defs["LoanTerms"]["properties"]["merchant_repayment_rules"] = ref("MerchantRepaymentRules")
defs["Effect"]["properties"]["mechanism"]["enum"] += ["historical_non_cash_normalization", "resolved_obligation_exclusion"]
defs["Effect"]["properties"]["proposed_operation"]["enum"] += ["normalize_historical_metric", "exclude_already_paid_obligation"]
defs["Effect"]["properties"]["cash_direction"]["enum"].append("none")
defs["ThresholdResult"] = obj({
    "threshold_id": S, "label": S,
    "scope": enum("cumulative_period_necessary_condition", "contractual_channel_receipts_threshold", "historical_metric_normalization"),
    "formula": S, "inputs": arr(ref("Parameter"), 1), "result": ref("EvidenceValue"),
    "interpretation": S, "limitations": arr(S),
})
defs["DecisionResult"]["properties"]["threshold_results"] = arr(ref("ThresholdResult"))
defs["Scenario"]["properties"]["analysis_scope"] = enum("economic_thresholds", "loan_cashflow")
defs["CasePacket"]["properties"]["operator_assumptions"] = arr(ref("Parameter"))
defs["SourceFactReference"]["description"] = "A source-fact reference. Main Synergy IDs resolve in ../data/facts_synergy.json; Barfresh IDs resolve to the named source document and inspected section in ../data/sources.json. "
base.schema["description"] += " Explicit-assumption models may compute conditional thresholds and curves when every nonobserved input is labeled. A current observed baseline is not required to show an honestly labeled sensitivity."

F = {
    "hvl": "synergy_hvl_h2_2024_payment", "supplier": "synergy_supplier_march_h2_2024_payment",
    "gain": "synergy_hvl_2023_noncash_gain", "lodc": "synergy_lodc_paid_may_2024",
    "advance": "synergy_webbank_advance", "total": "synergy_webbank_total_repayment",
    "share": "synergy_webbank_receipts_fraction", "six": "synergy_webbank_6m_min_fraction",
    "twelve": "synergy_webbank_12m_min_fraction", "second_window": "synergy_webbank_second_window_min_fraction", "term": "synergy_webbank_term_months",
    "cashaug": "synergy_cash_20240812_approx", "cashjune": "synergy_cash_20240630_unrestricted",
    "restricted": "synergy_cash_20240630_restricted",
}
DID = "synergy_2024-08-13_external_event_review"
BID = "synergy_chc_corp"
LID = "synergy_webbank_shopify_2024-05-01"
ASOF = "2024-08-13T23:59:59-04:00"
BASELINE = "synergy_explicit_threshold_inputs_v1"


def assumption(name, unit, amount, note):
    item = p(name, unit, amount, role="sensitivity_input")
    item["value"]["provenance"].update({"basis": "operator_assumption", "assumption_id": name, "note": note})
    return item


def derived(unit, amount, facts, formula, note):
    return value(unit, amount, None, facts, note, "model_derived", formula)


operator = [
    assumption("A_post_june_hvl_paid_cents", "USD_cents", 0, "Worked sensitivity only: assume no HVL payment between July 1 and August 13. Actual payments in that interval are unknown. Replace with ledger evidence."),
    assumption("A_post_june_supplier_paid_cents", "USD_cents", 0, "Worked sensitivity only: assume no payment on the March supplier settlement after June 30 through decision date. Actual payments are unknown."),
    assumption("A_policy_cash_reserve_cents", "USD_cents", 20000000, "Illustrative $200,000 liquidity reserve, not Slope policy or a reported contractual requirement."),
    assumption("A_opening_cash_for_threshold_cents", "USD_cents", 200000000, "Use management's approximately $2 million August 12 cash as a rounded sensitivity input. Not verified unrestricted bank cash at August 13; vary it or supply bank evidence."),
]
context = {
    "decision_id": DID, "mission_id": "synergy_20240813", "borrower_id": BID, "borrower_name": "Synergy CHC Corp.", "as_of": ASOF,
    "decision_timezone": "America/New_York", "decision_type": "new_draw", "transaction_basis": "analytical_candidate",
    "slope_relationship": "not_established", "stakeholder_scope": "loan_asset_holder", "currency": "USD",
    "requested_amount": value("USD_cents", note="A proposed incremental financing amount is an operator choice, not a disclosed Slope application."),
    "requested_term_months": None,
    "use_of_proceeds": "Evaluate incremental inventory or working-capital financing alongside an actual merchant loan and dated supplier-settlement obligations.",
    "baseline_id": BASELINE, "baseline_status": "explicit_assumption_model",
    "available_actions": ["requested_offer", "smaller_feasible_purchase", "alternative_term", "conditional_offer", "decline_new_exposure"],
    "missing_inputs": ["Settlement payments actually made July 1 through August 13", "Updated unrestricted cash and other debt-service calendar", "Daily credits and remittances for the specific Shopify account", "Actual May merchant-loan effective funding date", "Proposed incremental amount, pricing, term and funded operating plan"],
    "note": "The example computes useful conditional thresholds from real obligations plus named assumptions. It does not fabricate historical bank transactions, channel receipts, a complete borrower forecast, or a Slope relationship.",
}

obs = []
for metric, amount, date, fid, note in [
    ("unrestricted_cash", 8729300, "2024-06-30", F["cashjune"], "Reported June 30 unrestricted cash; do not treat as August opening balance."),
    ("restricted_cash", 10000000, "2024-06-30", F["restricted"], "Restricted credit-card collateral; not automatically available for loan payment."),
    ("management_approximate_cash", 200000000, "2024-08-12", F["cashaug"], "Management reports approximately $2 million, not exact verified unrestricted cash."),
]:
    obs.append({"observation_id": f"{BID}:{date}:{metric}", "metric": metric,
                "value": value("USD_cents", amount, date, [fid], note), "period_start": None, "period_end": date,
                "available_at": ASOF, "is_current_bank_observation": False})

uf = lambda note, unit="USD_cents": value(unit, note=note)
loan_sources = [F[x] for x in ("advance", "total", "share", "six", "twelve", "second_window", "term")]
terms = {
    "loan_id": LID, "borrower_id": BID, "terms_basis": "executed_contract", "product_type": "revenue_share_merchant_loan",
    "facility_limit": uf("This is a single merchant loan, not a reusable line limit."),
    "principal_outstanding": uf("Current outstanding principal cannot be reconstructed without actual credits/remittances and allocation."),
    "interest_index": "unknown", "annual_spread": uf("Fixed financing charge, not a prime spread.", "decimal_rate"),
    "reported_all_in_annual_rate": uf("Do not infer an APR without dated cash flows and an explicit calculation convention.", "decimal_rate"),
    "advance_rate": uf("Not a receivables borrowing-base advance rate.", "decimal_fraction"),
    "eligible_receivables_amount": uf("No invoice borrowing base for this merchant loan."),
    "reported_undrawn_availability": uf("No undrawn revolving commitment stated for this loan."),
    "maturity": uf("18 months from effective funding; no calendar date inferred merely from agreement date.", "date"),
    "automatic_renewal_reported": False, "fees": [], "schedule_status": "documented_rules_with_unknown_dates",
    "contractual_schedule": [], "payment_allocation_rule": "unknown",
    "collateral_summary": "Broad personal-property security under section 5. Priority requires lien/intercreditor review; no assumed first lien.",
    "missing_terms": ["Effective funding date", "Actual receipts and remittances for this account", "Application of receipts among overlapping merchant advances and payment components"],
    "source_fact_ids": loan_sources,
    "merchant_repayment_rules": {
        "advance": value("USD_cents", 37000000, "2024-05-01", [F["advance"]], "Contractual advance, not remaining principal at August 13."),
        "total_repayment": value("USD_cents", 41810000, "2024-05-01", [F["total"]], "Contractual total repayment including fixed cost."),
        "fixed_financing_cost": derived("USD_cents", 4810000, [F["advance"], F["total"]], "41810000 - 37000000 = 4810000", "Fixed borrowing cost; do not invent a pro-rata early-repayment rebate."),
        "receipts_fraction": value("decimal_fraction", "0.25", "2024-05-01", [F["share"]], "Daily specified Shopify Account Credits, not consolidated company sales."),
        "receipt_base_definition": "Specified Shopify Account Credits under the agreement. Gross-sales definition includes sales despite returns, refunds or cancellations. Preserve actual account mapping; do not combine two 25% advances automatically.",
        "funding_date": uf("Effective funding date not assumed equal to contract date.", "date"), "term_months": 18,
        "minimum_checkpoints": [],
        "remittance_calendar": "Amounts for nonbusiness days remitted next business day under the agreement.",
        "prepayment_treatment": "Voluntary prepayment permitted; no assumed proportional refund of the fixed fee.",
    },
}
for months, fraction, amount, fid in [(6, "0.30", 12543000, F["six"]), (12, "0.60", 25086000, F["twelve"]), (18, "1.00", 41810000, F["term"])]:
    terms["merchant_repayment_rules"]["minimum_checkpoints"].append({
        "months_from_funding": months,
        "minimum_cumulative_fraction": value("decimal_fraction", fraction, "2024-05-01", [fid], "Cumulative fraction of total repayment, not fraction of original principal."),
        "window_start_month": 1 if months == 6 else (7 if months == 12 else 13),
        "window_minimum_fraction": value("decimal_fraction", "0.30", "2024-05-01", [F["six"] if months == 6 else F["second_window"]], "Separate window minimum; prior-window excess does not automatically satisfy the second window.") if months < 18 else uf("At maturity collect remaining total, not a separately fixed fraction.", "decimal_fraction"),
        "cap_at_outstanding_total_repayment": True,
        "manual_payment_credit_rule": "Recognize actual valid manual-payment credit once according to contract/servicing allocation; reverse disputed payments; never collect beyond remaining total. Full payoff ends further obligations.",
        "minimum_cumulative_repayment": derived("USD_cents", amount, [fid, F["total"]], f"41810000 * {fraction} = {amount}", "Relative deadline from effective funding, not an invented calendar due date."),
    })

effects = []
for eid, fid, amount, borrowerlabel, payment_assumption in [
    ("E_HVL_settlement_calendar", F["hvl"], 200000000, "HVL / Atrium", operator[0]),
    ("E_unnamed_supplier_calendar", F["supplier"], 60000000, "March 2024 settlement supplier (labelled \"VitBest\" in the June 30 notes-payable table; linking that label to this settlement is a balance-match inference, and the full legal entity is not in the August information set)", operator[1]),
]:
    effects.append({"effect_id": eid, "event_id": "hvl_maine_litigation" if "HVL" in eid else "march_supplier_settlement", "borrower_id": BID,
                    "mechanism": "existing_liability_payment_timing",
                    "finding": f"{borrowerlabel}: June 30 remaining 2024 contractual payment bucket. Amount is already part of settlement debt; exact installment dates and payments since June 30 require a bridge.",
                    "target_stream_ids": [f"{BASELINE}:{eid}:existing_debt_cash"], "source_fact_ids": [fid],
                    "parameters": [p("remaining_2024_at_june30", "USD_cents", amount, "2024-06-30", [fid], "Period bucket spans July 1 to December 31; not proof the entire amount remains unpaid August 13."),
                                   {"name": "payments_since_june30_observed", "role": "model_input", "value": uf("Unknown actual July 1 through decision-date payments.")},
                                   payment_assumption,
                                   p("exact_installment_date", "date", role="model_input")],
                    "baseline_reflection": "already_in_historical_balance", "proposed_operation": "reclassify_timing",
                    "application_status": "requires_parameters", "activation_condition": "Compute remaining_at_decision = June30 remaining2024 bucket minus actual subsequent payments; for sensitivities use explicit payment assumptions and timing bands.",
                    "cash_direction": "outflow", "double_count_guard": "Map the existing debt once; do not add it again as new leverage. Updated cash already reflects whatever intervening payments actually occurred.",
                    "missing_inputs": ["Observed intervening payments", "Exact installment dates or operator-declared timing scenario"]})
effects += [
    {"effect_id": "E_normalize_2023_gain", "event_id": "hvl_maine_litigation", "borrower_id": BID,
     "mechanism": "historical_non_cash_normalization", "finding": "The December 2023 settlement reduced cost of sales through a noncash accounting gain. Remove it from historical recurring earnings only when those 2023 metrics feed the baseline.",
     "target_stream_ids": ["historical_FY2023_normalized_operating_profit"], "source_fact_ids": [F["gain"]],
     "parameters": [p("noncash_gain_to_remove_from_2023", "USD_cents", 223598600, "2023-12-31", [F["gain"]], "A historical earnings normalization, not a cash payment or a reduction of H1 2024 profit.")],
     "baseline_reflection": "already_in_historical_balance", "proposed_operation": "normalize_historical_metric",
     "application_status": "ready_after_baseline_match", "activation_condition": "Baseline uses FY2023 profit including this gain.", "cash_direction": "none",
     "double_count_guard": "Do not subtract from current cash or from H1 2024 profit, where the 2023 gain is absent.", "missing_inputs": []},
    {"effect_id": "E_LODC_no_future_charge", "event_id": "lodc_texas_litigation", "borrower_id": BID,
     "mechanism": "resolved_obligation_exclusion", "finding": "Distinct L.O.D.C. settlement was paid in full in May 2024. It does not create a new future settlement cash outflow at August 13.",
     "target_stream_ids": ["future_LODC_settlement_cash"], "source_fact_ids": [F["lodc"]],
     "parameters": [p("remaining_settlement_cash_due", "USD_cents", 0, "2024-08-13", [F["lodc"]], "Zero follows disclosed full payment; historical settlement amount is not publicly provided.")],
     "baseline_reflection": "already_in_historical_cashflow", "proposed_operation": "exclude_already_paid_obligation",
     "application_status": "ready_after_baseline_match", "activation_condition": "A naive headline or stale forecast would otherwise add another L.O.D.C. settlement payment.", "cash_direction": "none",
     "double_count_guard": "Do not substitute the claimed damages amount for settlement paid or merge this matter with the March supplier loan.", "missing_inputs": []},
]

thresholds = [{
    "threshold_id": "T_H2_incremental_free_cash", "label": "Remaining-H2 free cash required under explicit assumptions",
    "scope": "cumulative_period_necessary_condition",
    "formula": "required_net_cash = max(0, 200000000 + 60000000 - P_HVL - P_supplier + reserve - opening_cash)",
    "inputs": [p("HVL_H2_bucket_at_June30", "USD_cents", 200000000, "2024-06-30", [F["hvl"]], "Subtract actual or explicitly assumed intervening payments."), p("supplier_H2_bucket_at_June30", "USD_cents", 60000000, "2024-06-30", [F["supplier"]], "Subtract actual or explicitly assumed intervening payments.")] + operator,
    "result": derived("USD_cents", 80000000, [F["hvl"], F["supplier"], F["cashaug"]], "max(0,200000000+60000000-0-0+20000000-200000000)=80000000", "Conditional $800,000 threshold using the named zero-intervening-payment and $200k reserve assumptions, not an observed liquidity shortfall."),
    "interpretation": "Over the remaining period, the business needs $800k of net cash after operating spending and other existing debt service, or additional committed external capital, under these assumptions. Every $1 already paid on these settlements reduces the remaining requirement by $1 when paired with the correct updated cash observation.",
    "limitations": ["The aggregate-period condition is necessary, not sufficient: a payment can fall due before receipts arrive.", "No exact installment dates, prior-payment bridge, or actual daily bank forecast are fabricated.", "This is not a new-loan approval amount; new-loan cash disbursement, repayments and the funded operating plan must be added consistently.", "The opening amount is management's approximate cash adopted as an operator sensitivity input, not verified unrestricted cash."],
}]
for months, minrepay, factid in [(6, 12543000, F["six"]), (12, 12543000, F["second_window"]), (18, 41810000, F["term"])]:
    credits = int(Decimal(minrepay) / Decimal("0.25"))
    thresholds.append({
        "threshold_id": f"T_account_credits_{months}m", "label": ("Account credits needed in months 1–6" if months == 6 else "Account credits needed separately in months 7–12" if months == 12 else "Lifetime account credits needed for full repayment"),
        "scope": "contractual_channel_receipts_threshold", "formula": f"cumulative_account_credits_required = {minrepay} / 0.25 = {credits} cents",
        "inputs": [{"name": "applicable_repayment_requirement", "role": "source_anchor", "value": derived("USD_cents", minrepay, [factid, F["total"]], f"Contract checkpoint: {minrepay} cents", "First/second six-month window minimum or lifetime total as labeled; no manual payments and full stated requirement still outstanding in this threshold.")}, p("remittance_share", "decimal_fraction", "0.25", "2024-05-01", [F["share"]], "Specific Shopify account credits only.")],
        "result": derived("USD_cents", credits, [factid, F["total"], F["share"]], f"{minrepay}/0.25={credits}", "A receipt threshold, not an assertion about actual company channel sales or actual remittances."),
        "interpretation": "Within the labeled period, percentage remittances alone must produce the applicable amount if no manual payment is credited and enough total remains outstanding. Months 7–12 are a separate window: 60% cumulative by month 12 alone does not demonstrate compliance. Apply the outstanding-total cap and valid manual credits in the dated engine.",
        "limitations": ["Relative months begin at effective funding, whose exact date must be supplied.", "Do not apply the percentage to consolidated sales or sum overlapping advances without account mapping.", "50% paid in the first window and 10% in the second meets 60% cumulative but leaves a second-window minimum shortfall; do not mark it compliant.", "This does not establish historical compliance or default."],
    })

scenario = {"scenario_id": "S_explicit_settlement_threshold", "decision_id": DID, "baseline_id": BASELINE,
            "analysis_scope": "economic_thresholds", "label": "Conditional H2 settlement-liquidity threshold", "conditions": ["No intervening settlement payments assumed solely for this worked sensitivity.", "Opening cash and reserve are the explicitly identified rounded/operator inputs.", "Operating cash available after other debt service is a variable to compare with the computed threshold."],
            "effect_ids": [x["effect_id"] for x in effects], "parameter_overrides": operator,
            "probability": uf("No scenario weight is required for a conditional threshold; none is invented.", "decimal_fraction"),
            "payment_behavior": "operator_assumption", "forecast_status": "ready", "is_realized_outcome": False}

source_refs = []
merchant_keys = {"advance", "total", "share", "six", "twelve", "second_window", "term"}
for key, fid in F.items():
    source_refs.append({"fact_id": fid,
                        "document_id": "synergy_merchant_agreement_20240501" if key in merchant_keys else "synergy_s1a_20240813",
                        "section": "See exact source passage in ../data/facts_synergy.json",
                        "note": "Resolved stable fact ID in ../data/facts_synergy.json; document hash and public availability are in ../data/sources.json. The main facts registry uses USD while this contract intentionally converts dollar values to integer cents."})

fact_registry = {x["fact_id"]: x for x in json.loads((ROOT.parent / "data" / "facts_synergy.json").read_text())["facts"]}
for item in source_refs:
    resolved = fact_registry[item["fact_id"]]
    item["document_id"] = resolved["source_id"]
    item["section"] = resolved["locator"]["section"]

packet = {"schema_version": "0.1.0", "case_id": "synergy_chc_2024", "artifact_status": "prototype_design_not_running_model",
          "source_fact_references": source_refs, "operator_assumptions": operator,
          "decision_context": context, "historical_observations": obs, "loan_terms": terms,
          "effects": effects, "scenarios": [scenario],
          "decision_result": {"decision_id": DID, "result_status": "decision_boundary", "preferred_action_id": None,
                              "action_results": [], "policy_basis": "operator_demo_policy", "policy_id": "conditional_reserve_threshold_example",
                              "required_conditions": ["Compare supported remaining-period free cash with the threshold; choose actual or explicit scenario payment dates to test interim liquidity.", "Add a proposed new loan's net cash schedule and changed purchase plan before recommending an amount or term."],
                              "decisive_missing_inputs": context["missing_inputs"],
                              "summary": "Useful outputs are $800k conditional remaining-period free-cash requirement under the named assumptions and contract-derived merchant-channel receipt thresholds of $501,720 in each separate six-month window and $1,672,400 over the full term, before manual-payment credits and outstanding-total caps. They do not claim a historical shortfall, a calibrated default probability, or an actual Slope lending decision.",
                              "threshold_results": thresholds},
          "loan_cashflow_export": {"export_id": "synergy_dated_export_awaiting_scenario_ledger", "loan_id": LID, "borrower_id": BID, "decision_id": DID, "as_of": ASOF, "currency": "USD",
                                   "cashflow_type": "conditional", "status": "not_computable", "scenario_id": scenario["scenario_id"], "baseline_id": BASELINE, "scenario_weights": [], "rows": [],
                                   "terminal_unresolved_principal": uf("No dated loan collection ledger has been computed; thresholds above are useful independently."),
                                   "missing_inputs": ["Actual or explicitly assumed funding date, channel receipt path, settlement dates and payment allocation"], "stakeholder_scope": "loan_asset_holder"}}

(ROOT / "build-contracts.schema.json").write_text(json.dumps(base.schema, indent=2) + "\n")
(ROOT / "economic-effects.json").write_text(json.dumps(packet, indent=2) + "\n")
(ROOT / "synergy-decision-context.json").write_text(json.dumps(context, indent=2) + "\n")
print("Wrote Synergy main packet and decision context; Barfresh remains separately named.")

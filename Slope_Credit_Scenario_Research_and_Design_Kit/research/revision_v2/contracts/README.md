# Prototype financial contracts and examples

These files define the implementation contracts and provide useful conditional calculations. They are not a running agent application, a calibrated credit model, or actual Slope offers.

## Main example and secondary case

- `economic-effects.json`: **Synergy CHC**, case `synergy_chc_2024`, mission `synergy_20240813`, decision cutoff August 13, 2024. Contains real settlement and merchant-loan facts, named operator assumptions, four economic-effect mappings, and calculated decision thresholds.
- `synergy-decision-context.json`: standalone decision context for the same mission.
- `proposal-fixtures.json`: three explicitly analytical incremental financing options: $100k/6 months/6% fixed total fee, $250k/6 months/6%, and $250k/3 months/3%. Monthly payment indices avoid inventing actual funding or due dates. These are not Slope pricing, historical applications, or modifications of the existing $370k WebBank loan.
- `barfresh-economic-effects.json`: separately named secondary case `barfresh_schreiber_2024`, mission `barfresh_20241025`. Historical observations and research effects only; it is not the lead model.
- `build-contracts.schema.json`: JSON Schema Draft 2020-12 reusable contracts including evidence values, decision context, loan terms, merchant repayment rules, economic effects, scenarios, threshold results, decisions, and cash-flow exports.
- `proposal-fixtures.schema.json`: schema for the analytical proposals.
- `validation-report.json`: actual artifact validation results, including conditional arithmetic and malformed-example checks.

## Useful output without fictitious history

The main packet computes a conditional threshold, rather than ending with a generic missing-input message:

`required_remaining_period_net_cash = max(0, $2m HVL + $600k supplier - intervening_payments + reserve - opening_cash)`

The $2.6m is the contractual remaining-2024 schedule measured **June 30**, not proof that all of it remains unpaid on August 13. Actual July–August payments are unknown and must be bridged. With named example assumptions of no intervening payments, a $200k reserve, and $2m starting cash, the remaining-period net-cash requirement is **$800k**. This is a conditional threshold, not an observed shortfall. Management described August 12 cash as approximately $2m; it is not a verified unrestricted bank balance. The example adopts it only as a rounded sensitivity input.

Net cash in this threshold means cash after operating needs and other existing debt service, excluding the two settlement payments separately modeled. The condition is necessary over the aggregate period, not sufficient at every date: obligations can come due before receipts arrive. Exact installment dates remain unknown. New-loan proceeds, payments, fees, and the changed funded operating plan must enter together before selecting an offer. A six-month proposal beginning in August crosses into 2025: include the disclosed 2025 settlement buckets and an explicit within-year timing assumption. The H2-only threshold cannot establish feasibility for the entire loan term.

The merchant contract also provides actionable receipt thresholds. A 25% remittance rate requires $501,720 of specified Account Credits for $125,430 of remittances. This applies separately to the first six-month window and the second six-month window, before valid manual credits and the outstanding-total cap. Full $418,100 repayment through percentage remittances alone requires $1,672,400 of lifetime Account Credits. These are not consolidated company sales forecasts.

**Do not replace the second six-month requirement with a cumulative-60% test.** Section 4.1.1 requires an additional 30% during months 7–12; section 4.2.1(b) resets the daily-payment subtraction to that window. Paying 50% in the first window and 10% in the second reaches 60% cumulatively but leaves a second-window shortfall. Apply valid manual credits once, reverse reversed payments, cap collection at remaining total, and stop after full payoff. Calendar months begin at actual effective funding, not automatically the agreement date.

## Source and assumption separation

Main Synergy fact IDs resolve in `../data/facts_synergy.json`; source IDs, hashes, dates and locators are in `../data/sources.json`. The generator loads the registry and resolves each referenced fact. Registry USD amounts are deliberately converted to integer cents in the contract examples. Merchant-agreement public availability is June 28, 2024; the S-1/A is August 13, 2024. Date-only availability is conservatively admitted at end of its known publication date, not represented as an exact SEC acceptance time.

Neither example uses pending source IDs. Synergy references resolve to the unified facts registry. Barfresh references resolve to named, inspected source sections in the same archived source catalog; they are section anchors rather than standalone entries in the Synergy facts registry.

Money uses integer USD cents. Rates/fractions use decimal strings parsed with `Decimal`. `EvidenceValue.status` distinguishes exact reported numbers, ranges, and unknowns; `provenance.basis` separately distinguishes documented evidence, operator assumptions, derived values, and absence of evidence. Approximately reported source amounts must retain that qualification in their note and registry precision; exact numeric representation does not imply exact underlying measurement.

The four main effects are:

1. Place the existing HVL debt into a payment calendar, less subsequent payments; do not add the debt twice.
2. Do the same for the separate March supplier settlement. The supplier's later-disclosed identity is excluded from the August information set.
3. Remove the $2,235,986 noncash gain only from FY2023 metrics that contain it; do not charge it against cash or H1 2024 profit.
4. Exclude a new future L.O.D.C. payment because the distinct matter was paid in full in May; do not substitute alleged damages for settlement cash.

## Permitted assumption models

A complete bank-data baseline is valuable but is not a prerequisite to an explicitly labeled scenario exercise. `baseline_status=explicit_assumption_model` permits conditional modeling when every nonobserved numerical input, date convention, allocation behavior and policy choice has an assumption ID. This allows practical offer comparisons and break-even surfaces without inventing historical transactions.

Keep reported data unchanged when testing assumptions. An unknown actual prior payment remains unknown even if an example assumes zero. A probability-weighted expected curve needs documented scenario weights summing to one. Jev confidence is not a weight source. A conditional curve needs a named scenario and coherent ledger but no probability.

The main packet deliberately has no exact dated collection rows yet: effective funding, account credits, intervening remittances and settlement dates are not established. Its decision output is nevertheless useful: the conditional liquidity threshold, merchant receipt boundaries and three explicit financing-payment fixtures. The application should let an operator supply facts or assumptions and calculate the next layer.

## Reproduce and validate

From this directory, after installing `jsonschema>=4.26,<5` into your environment:

    python build_synergy_example.py
    python build_proposal_fixtures.py
    python validate_contracts.py

`build_synergy_example.py` imports the base generator, creates the separately named Barfresh example, then writes the final expanded schema and Synergy main packet. Do not run the base generator alone as the final step: it creates the earlier base schema before Synergy extensions. Paths are relative to the script location and source registry; no workspace-specific absolute paths are required.

Validation covers schema shape, unit conventions, references, date cutoffs, consistent assumption IDs, threshold arithmetic, proposal payment conservation, and several deliberately invalid cases. It does not validate the eventual full financial engine, actual borrower creditworthiness, legal conclusions, or live model/agent behavior. The application must additionally enforce cash conservation, payment allocation, complete source cutoffs, and the separate merchant minimum-payment windows.

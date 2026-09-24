# Offline financial reference

This small standard-library Python calculator makes the numerical spine of the design reviewable. It does not create a borrower forecast, infer private transaction data or decide whether a loan should be approved. All arithmetic uses `Decimal`; no API, model, package installation or network connection is required. Python 3.10 or later.

Run from this directory:

```sh
python calculator.py
python -m unittest -v test_calculator.py
```

`calculation_outputs.json` contains source-linked contractual calculations, the unresolved cash-budget identity, and a clearly separate timing test fixture. Monetary amounts are strings rounded to cents for display.

## Contract-derived thresholds: Synergy CHC

The May 1, 2024 WebBank merchant agreement advances **$370,000** and requires total payments of **$418,100**, including a fixed **$48,100** cost. That cost is 13% of the advance; **13% is not an APR**.

| Checkpoint | Cumulative payments required | Qualifying Shopify Account Credits needed if paid entirely through the 25% remittance |
|---|---:|---:|
| 6 months | $125,430 | $501,720 |
| 12 months | $250,860 | $1,003,440 |
| 18 months | $418,100 | $1,672,400 |

These are **necessary cumulative thresholds, not a complete compliance test**. Sections 4.1.1 and 4.2.1(b) separately require a 30% minimum ($125,430) in the first six-month window and another 30% in months 7–12. Each window needs $501,720 of qualifying credits if met entirely through the 25% remittance. Excess first-window payments do not automatically satisfy the second-window requirement.

For example, paying 50% ($209,050) in months 1–6 and 10% ($41,810) in months 7–12 reaches 60% cumulatively, yet leaves a **$83,620 second-window top-up**. The included counterexample test prevents a false pass based only on cumulative payments.

`window_top_up` uses daily payments within the applicable window, explicitly confirmed manual credit for that window, and total lender receipts to date. It caps the top-up at the remaining total payment obligation. Manual payments must have been accepted and credited; reversed payments and an assumed carry of prior-window manual credit do not count. Earlier unresolved obligations are not erased. `cumulative_shortfall_only` is deliberately named to prevent using it as a complete checkpoint test. No threshold calculation by itself establishes an observed default.

“Shopify Account Credits” follows the contract definition. It is not company-wide revenue, brand-segment revenue or net customer cash after refunds. The contract uses gross sales without deducting returns/refunds/cancellations; settlement timing also matters. Thresholds assume the corresponding remittances have reached the lender by the checkpoint. Calendar months start at effective funding, not automatically on the exhibit's May 1 date. No effective funding date is invented.

The separate May 22 advance is excluded because applying two 25% rates to the same receipts without store/account mapping could double-count collections. The calculator does not allocate payments between principal and fee, infer APR, add late charges, or assume a fee rebate on prepayment.

Source ID: `synergy_merchant_agreement_20240501` — [executed agreement](https://www.sec.gov/Archives/edgar/data/1562733/000121390024056991/ea020832401ex10-32_synergy.htm), cover, definitions and payment provisions.

## Conditional capacity: the missing timeline must stay visible

The August 13, 2024 filing reports approximately **$2m cash on August 12**; this does not verify that all of it was unrestricted or available. The unavailable portion is therefore a required input, left unknown. Its June 30 settlement schedules show **$2m HVL/Atrium plus $600,000 for a second supplier remaining in H2 2024**. The second supplier's later identification as Vitabest is outside this cutoff. The amount already paid during July 1–August 12 is unknown in these inputs.

The valid identity is:

```text
Year-end cash residual before a new facility =
    approximately $2m opening cash
  - unavailable portion of that opening cash
  + customer cash collected after August 12
  + committed financing drawn after August 12
  + other confirmed cash inflows after August 12
  - ($2.6m June 30 H2 settlement schedule - payments made July 1–August 12)
  - other operating cash outflows after August 12
  - other debt service after August 12
  - operator-required cash reserve
```

Therefore the conditional break-even threshold is:

```text
Prior H2 settlement payments + future customer cash + committed financing
+ other confirmed inflows
>= approximately $600,000 + unavailable opening cash
 + other operating outflows + other debt service + reserve
```

**The $600,000 difference is not an observed August 13 funding gap.** The code leaves every missing input as `None` and returns no residual until all are supplied. No public-data claim is made for the filled values used only in the unit test.

The identity schedules existing liabilities; it does not add settlement debt to the balance sheet again. Other operating outflows must exclude the separately scheduled settlements and debt service. Customer collections must be measured before separately modeled debt remittances. Exclude the hypothetical new loan from committed financing. Do not treat the 2025 refinancing as available cash in August 2024.

A positive year-end residual is only a horizon-level necessary check: dated cash flows and reserve requirements are needed to establish whether cash runs out earlier and to size or price a new facility. Neither EBITDA nor an undisclosed annualized burn rate substitutes for those inputs.

Source ID: `synergy_s1a_20240813` — [August 13 filing](https://www.sec.gov/Archives/edgar/data/1562733/000121390024068424/ea0208324-04.htm), liquidity discussion and Note 11. June 30 unrestricted cash of $87,293 is not carried forward as the August 12 cash balance.

## Timing mechanics only

A separate, explicitly artificial arithmetic fixture returns $100,000 principal at day 90 versus day 120. At an operator-assumed 12% effective annual discount rate, ACT/365:

- Both paths return the same $100,000 principal; principal loss is zero.
- The delayed receipt has lower present value and 30 extra exposure days.
- The extra principal exposure is $3m dollar-days.

This is an engine invariant, not a Synergy repayment forecast, not a proposed synthetic borrower, and not an asserted contractual fee allocation. The exposure function accepts principal-only payments and must not be applied to mixed principal/fee remittances without an explicit allocation policy. For actual merchant payments, the calculator provides the total-payment thresholds above without inventing that allocation.

Tests cover exact cumulative floors, the 50%/10% window counterexample, confirmed manual credits, the remaining-obligation cap, one-cent boundaries, unknown cash availability, missing information, avoiding double-counted pre-cutoff settlement payments, conserved principal under delay, reduced PV at positive discount rates, and invalid numeric inputs.

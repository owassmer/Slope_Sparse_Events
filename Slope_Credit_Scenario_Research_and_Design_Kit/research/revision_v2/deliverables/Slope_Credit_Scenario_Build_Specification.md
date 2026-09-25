# Slope credit-event scenario module — definitive build specification

Prepared 24 September 2026. Revision 2 replaced the Trinity/A10 implementation direction. **Revision 3 (25 September 2026):** the lead case is ChromaDex at 19 August 2024, whose disputes are live on the decision date; Synergy becomes the secondary case. A live dispute is compiled onto a generic, host-owned post-judgment dispute model: Jev reads each cited passage atomically, code keeps every cash path the evidence permits, and Slope's product, pricing, policy and the connected-bank baseline are reconstructed as closely as public information allows.

## 1. The product to build

**Given a proposed working-capital advance and an unusual external event, determine which financing structures remain supportable and how the event changes the loan's projected collections.**

The borrower’s financial profile supplies the repayment capacity. The loan’s dated cash flows are the main output. The agent researches evidence that ordinary transaction-based underwriting may miss; Jev makes focused judgments within that investigation; deterministic financial code translates the resulting economic effects into financing choices.

The demonstration should end with an amount, schedule and explicit funding conditions—or a concrete reason no offered structure works. It should also show the cash the asset holder receives under each modeled scenario. A litigation summary or monitoring recommendation is insufficient.

This is a useful connection between Russell’s two topics: unusual borrower events create scenarios for individual loan collections; those collections feed portfolio liquidity and capital planning. It is one coherent extension of the credit workflow, not a full capital-markets platform.

### Where it fits at Slope

Use **exception review before a new discretionary advance or increase in exposure**. Slope publicly describes higher-risk, larger and unusual applications entering human review, supported by agents and traceable evidence. It also describes a reconstructed cash-flow understanding from bank data. Our module consumes that baseline rather than recreating bank connectivity or ordinary underwriting. [S1–S2]

The recommendation addresses controls Slope actually describes: business limit, maximum order/draw amount, available terms and pricing. Public product terms vary by program. Slope’s Amazon program describes fixed repayment schedules with 2–12-month draw terms; its invoice Pay Later materials describe different terms. Select one product configuration; never blend them. [S3–S5]

For this demonstration, **reconstruct Slope's actual product as closely as public information allows**: bill-pay financing, where Slope pays the supplier and the business repays on net terms or in monthly installments over 60 or 90 days, priced by risk tier from connected bank data. The reconstructed price card, credit policy and the synthetic connected-bank data are each labelled once, at their source, and are otherwise used plainly. The case is reconstructed as if the borrower applied to Slope on the decision date; there is no claim that it did. A borrower's actual existing financing (a bank revolver, a merchant loan) stays a separate existing obligation with its own contract logic.

The first financial viewpoint is the **loan asset holder**. Lead Bank origination, Slope servicing and external capital support do not mean every borrower payment is Slope corporate revenue. Platform fees, asset ownership and warehouse cash flows require separate accounting scopes. [S4]

## 2. Case selection: ChromaDex first, Synergy second

**Why the lead changed.** A lawsuit carries the most lender-relevant signal while it is unresolved: bank data shows nothing about a pending ruling, an appeal bond or whether a judgment debtor will pay, and those are branching, forward-looking questions. By 13 August 2024 Synergy's dispute had already become ordinary settlement debt, which turns the investigation into document archaeology. The lead case must therefore have a dispute that is live on the decision date, a federal docket with public filings before that date, public financials, a Slope-shaped business and a known later outcome.

**Lead: ChromaDex Corporation, using information available by the end of 19 August 2024.** A consumer supplement brand (Tru Niagen) that buys finished goods from contract manufacturers and sells online, on Amazon and in retail; about $84 million of 2023 net sales, $27.9 million of cash at 30 June 2024, an undrawn $10.0 million Western Alliance Bank revolver with liquidity covenants. Two disputes with Elysium Health, a former customer, move money in opposite directions on the decision date: in Delaware the court has granted Elysium its attorney fees, against ChromaDex, Inc. (the operating subsidiary) and the Trustees of Dartmouth College, and the amount (about $9.8 million with interest, by ChromaDex's estimate, reported as the Company's contingent liability) awaits a ruling that ChromaDex intends to appeal; in California a 13 August 2024 judgment orders Elysium to pay ChromaDex $2.5 million, with no payment date. Most resolution events (the fee judgment, the appeal bond, Elysium's appeal) fall inside the 180-day horizon over which a 90-day financing is assessed; the December settlement payment lands just after a 90-day term. Evidence: the 10-K, both 2024 10-Qs, the Q2 results release, the revolver amendment and nine federal court filings (CourtListener RECAP copies of PACER documents). The date is 19 August because on 20 August the Delaware court issued an order on the fee-amount dispute; by early September that branch was largely decided.

**Secondary: Synergy CHC at 13 August 2024** (below): a resolved dispute whose obligations hide inside aggregate debt; its recorded runs stay as the hidden-obligation example.

**Barfresh is deferred:** its live dispute is in California state court with no docket we could access, so the dispute cannot be modelled from court records.

**Synergy CHC (secondary), using information available by the end of 13 August 2024.** It is a real consumer-products business with 25 employees, approximately $42.8 million of 2023 sales and an actual $370,000 merchant advance. We acquired a court opinion, financial disclosures, its executed merchant-loan contract, later repayment disclosures and a later credit agreement that expressly financed the settlement obligations. [C1–C6]

The dispute is older than the underwriting snapshot: supplier litigation began in 2022, the main settlement occurred in December 2023, and the relevant payment obligations and financing decisions run through 2024–2025. Describe that chronology accurately. This is much more recent and economically applicable than the decade-old public-company cases, while remaining a historical demonstration with observable financing outcomes.

| Candidate | Fit and evidence | Role |
|---|---|---|
| **ChromaDex** | Consumer supplement brand buying from contract manufacturers; live fee award and judgment receivable on the decision date; two federal dockets with filed orders; public financials; public outcome | Lead: live disputes on the dispute model → weighted paths with rule-derived cash → loan collections |
| **Synergy CHC** | Small employee base, actual sub-$400,000 merchant advance, executed repayment terms, lawsuit settlement schedule, later settlement-financing contract | Secondary: resolved dispute → hidden settlement obligations → loan capacity |
| **Barfresh** | Roughly ten employees, small beverage supplier, production dispute, real receivables facility; live case in state court with no public docket | Deferred |
| Ya Ya Creations | Strong public evidence of Shopify/Stripe disruption and restored receipts; key financial declarations sealed, insufficient pre-outcome loan terms | Optional receipt-delay mechanism test, not the quantitative lead |
| Kin Social Tonics | Recent financial disclosure and vendor finance; severe pre-existing distress, several paid or non-filed disputes | Optional negative control: do not attribute an ordinary financial rejection to a new lawsuit signal |
| True Made Foods | Recent litigation/bankruptcy but inadequate contemporaneous public financial baseline | Exclude from first build |
| Trinity/A10 | Rich contracts but old and poor borrower fit | Retire as headline cases; do not reuse their decision labels |

Synergy is already highly leveraged. That does not disqualify a real financing analysis, but it rules out an invented “healthy company suddenly becomes unlendable” story. The goal is a defensible change in payment forecasts, supported exposure or funding conditions. An unchanged final approval stance is acceptable if the model correctly explains why.

### Exact lead mission

> As of 19 August 2024, assess Slope financing of a USD 2.0 million contract-manufacturer invoice for ChromaDex Corporation. ChromaDex's public filings describe long-running litigation with Elysium Health, a former customer, in several federal courts.

The request is reconstructed (Slope bill-pay financing of a real borrower's supplier invoice). The mission names the borrower and the counterparty but no expected finding.

### Secondary mission (Synergy)

> As of 13 August 2024, assess a proposed incremental working-capital draw for Synergy CHC. Reconcile existing merchant financing and supplier-settlement obligations. Compare the requested structure with permitted alternatives, identify the binding cash requirements and export conditional loan collections.

There is no claim Synergy applied to Slope. The proposed request is a **scenario input attached to a real borrower**, not a fabricated historical loan. The actual May merchant advance provides real contract mechanics and existing obligations. Its historical funding decision cannot be reconstructed from later August information.

## 3. What the evidence actually establishes

### ChromaDex: two live disputes, money moving both ways (as of 19 August 2024)

- **Delaware fee award (D. Del. 1:18-cv-01434).** ChromaDex's patents were held invalid (2021), affirmed on appeal (February 2023), and certiorari was denied (October 2023). On 25 March 2024 the court granted Elysium's motion for attorney fees and costs (Dkt. 399) against ChromaDex, Inc. and the Trustees of Dartmouth College; ChromaDex Corporation reports it as its own contingent liability. The amount was fully briefed by 13 June 2024 and awaits a ruling. ChromaDex states it intends to appeal, treats the loss as reasonably possible (not accrued), and estimates the amount sought, with post-judgment interest, at about $9.8 million. An appeal normally requires a bond or other security to stay execution, which ties up cash or credit capacity.
- **California judgment (C.D. Cal. 8:16-cv-02277).** A 2021 jury found Elysium owed about $3.0 million for unpaid ingredient purchases and ChromaDex owed smaller amounts on counterclaims. A 2022 settlement, enforced over Elysium's objection and affirmed by the Second Circuit in 2023, led to a 13 August 2024 judgment (Dkt. 618): Elysium shall pay ChromaDex $2,500,000; no post-trial motions or appeals on the jury claims; the court keeps jurisdiction to enforce for 120 days. The judgment sets no payment date, and Elysium has contested this settlement before.
- **Existing lender.** Western Alliance Bank revolver of up to $10.0 million, maturing 12 November 2025, undrawn at the December 2023 amendment, with covenants on cash kept at the lender, the quick ratio and minimum liquidity.
- **Baseline (30 June 2024).** Cash $27.9 million; trade receivables $7.8 million (including $3.5 million from a related party); inventory $11.5 million; payables $8.1 million; accrued expenses $8.6 million; Q2 net sales $22.7 million at a 60.2% gross margin; operating cash flow about breakeven for the half year.

**Later outcomes, hidden until the decision is locked:** on 20 August 2024 the Delaware court ruled on the fee-amount dispute (Dkt. 415); ChromaDex moved for its own fees in California on 3 September 2024; the fee judgment was fixed on 28 October 2024 (about $9.2 million plus interest); ChromaDex appealed and secured an appeal bond (December 2024), with a related letter of credit; Elysium appealed the California judgment on 11 September 2024 despite its terms, and the parties settled in December 2024 for $2.65 million paid in two instalments (27 December 2024 and by 31 March 2025); the fee appeal was still undecided in August 2026. These sources are outcome-only for the mission and never reach the investigation.

### Synergy: three obligations that must remain separate

| Item | Evidence at the August cutoff | Modeling treatment |
|---|---|---|
| HVL/Atrium lawsuit settlement | December 2023 settlement; June 2024 remaining schedule includes $2m in 2024, $2m in 2025 and $802,445 in 2026 | Existing liability; enrich its cash-payment calendar. Do not add a new liability or invent exact installment dates. |
| March 2024 supplier settlement | $600,000 scheduled for the remainder of 2024 in June financial notes | Separate existing obligation. The settlement note names only "a supplier"; the June 30 notes-payable table labels the same $2,920,824 balance "VitBest". The agent may report that label and link it to this settlement as a cited inference from the matching balance. The full legal entity, the named settlement agreement and all facts from the 2025 credit agreement are outcome-only. |
| L.O.D.C. litigation | Disclosed as fully paid in May 2024 | No new future settlement outflow. The claimed amount is not the undisclosed settlement payment. |
| 2023 settlement accounting gain | $2,235,986 reduced reported cost of sales | Remove from normalized 2023 earning power if that year informs the forecast. It is not cash received and is not deducted again from 2024 profit. |

Source: C1; court context C2. The supplier's identity at the August cutoff is limited to the table label and the balance-matched inference above. Evidence ingestion keeps the admissible label intact; it does not redact decision-date text to match a later identification. The acquired court document is a recommended decision on a motion to dismiss, not a merits liability finding. Allegations are not findings that Synergy or its supplier committed the alleged conduct.

The two June schedules imply **$2.6m of H2 2024 obligations measured at June 30**. They do not prove all $2.6m remained unpaid on August 13. Management separately disclosed approximately $2m of cash on August 12, without establishing that the full amount was unrestricted and available. Payments between June 30 and August 12 must be reconciled before combining these observations.

This date bridge is part of the product: an agent should identify a bank/debt-statement reconciliation as decision-relevant, not silently treat differently dated figures as a current cash forecast.

### Actual merchant contract

The May 1 agreement provides $370,000 with $418,100 total repayment, a $48,100 fixed borrowing cost and remittance of 25% of the specified Shopify Account Credits. The first six-month period requires 30% of total repayment; months seven through twelve require an additional 30%; full payment is due by eighteen months from the actual funding effective date. The second-period collection rule must retain its own payment bucket rather than using only a cumulative 60% test. [C3]

| Contract checkpoint | Cumulative repayment floor | Cumulative eligible Account Credits needed if funded entirely by 25% remittances |
|---|---:|---:|
| Six months | $125,430 | $501,720 |
| Twelve months | $250,860 | $1,003,440 |
| Eighteen months | $418,100 | $1,672,400 |

These are necessary cumulative thresholds, not the complete payment algorithm or observed sales. A borrower that pays 50% in the first six months and 10% in the next six reaches 60% cumulatively but still falls short of the second period’s 30% requirement. Track period-specific credited payments, manual payments and the remaining-obligation cap. Other borrower payments can satisfy an otherwise unmet floor. Use the defined gross eligible Account Credits: refunds, returns and cancellations do not reduce the daily-payment sales base. The agent must not use 25% of consolidated revenue, add overlapping merchant remittance percentages without account mapping, assume the agreement date equals funding date, or assume a proportional rebate of the fixed fee on early payoff.

For the August replay, the loan has already seasoned. The table above is a contract-unit test, **not a fresh eighteen-month schedule beginning in August**. Forecasting the remaining obligation requires remittances to date and current outstanding contractual amounts. Accounting carrying value can differ from the remaining contractual repayment amount because of deferred fees or original-issue discount.

### Later outcomes, hidden until the decision is locked

The later disclosures report $2m paid on the HVL obligation in 2024 and its discounted payoff in 2025. A May 2025 credit agreement expressly assigns a delayed-draw facility to repayment of the Atrium and Vitabest settlements. The May merchant loan is reported with a zero balance by June 2025. [C4–C6]

This validates the importance of settlement cash obligations and their financing. It does not establish every merchant payment was punctual or prove the August model would predict the refinancing. Conflicting reported December merchant-loan balances remain flagged; do not infer a precise monthly repayment history from them.

### Barfresh transfer case

Use an October 25, 2024 review with September 30 observations. The company disclosed a real $1.5m receivables facility, approximately $1.4m available at September 30, $401,000 cash and a $499,000 disputed supplier payable already in liabilities. Reported GAAP working capital was $872,000; management’s $1.371m figure excluded that payable. [B1]

The agent must distinguish financed litigation expense from operating disruption, planned manufacturing capacity from achieved output, and inventory on hand from collected customer cash. Borrowing availability is a dated sourced value, not an invented zero or automatically current. The executed receivables and litigation-funding agreements were not acquired; unknown fee mechanics, priority and funding conditions remain explicit.

The later facility payoff coincided with an equity raise. Do not attribute it solely to lawsuit resolution or operating performance. [B2–B3] This case proves the engine can handle an operating mechanism, rather than only a settlement-payment schedule.

## 4. The demonstration experience

The operator opens a **financing decision**, not a lawsuit dashboard. **Show the right answer, not the machinery.** The front page states one decision with confidence: the recommended offer, one or two conditions phrased as actions, and the dated cash flows. It shows no lists of unknowns, disagreement logs, Jev confidence values or reviewer objections. An unknown that matters is never hidden and never set to zero. It becomes a condition phrased as an action, for example "Fund $150k over 6 months, conditional on Knight's written consent" or "Request the July bank statement; if at least $X of the settlement was paid, the full $250k is feasible." The investigation record is a "How we know" drill-down.

Front page, in order:
- **Decision:** the recommended offer and its conditions, each phrased as an action with the evidence that would satisfy it.
- **Cash flows:** the loan's dated collections beside the borrower's month-by-month cash, before and after the event.
- **Key drivers:** two or three, one line each, with a click-through to the source quote.
- **How we know** (collapsed): the investigation record, including Jev checks, inventory accounting, reconciliations and the independent review verdict.

The detail below serves the drill-down and the decision's calculations:

1. **Decision header:** real borrower, as-of date, proposed amount/use, term, price and existing exposure. Show whether each input is sourced, operator-supplied or unresolved.
2. **Baseline:** dated opening cash, operating cash budget, existing debt service and proposed contractual payments. Show existing missing inputs before research begins.
3. **Investigation (in "How we know"):** the recorded agent activity that identifies the material event, connects entities and obligations, has Jev screen retrieved evidence and check narrow semantic claims, and chooses the next source based on its impact on the decision.
4. **Economic changes:** a short list such as “assign existing settlement debt to H2 payment periods,” “remove noncash historical gain,” and “exclude already-paid legal matter.” Each expands to evidence and calculations.
5. **Offer comparison:** requested draw and alternative amount/term structures, with cash-buffer breaches, conditional unpaid amounts, timing and economics. A specific condition identifies what must be established before a named offer can be funded.
6. **Loan cash flows:** contractual payments, baseline modeled collections and event-conditioned collections on the same dates. Conditional curves are not labeled expected unless weights are supplied.
7. **Capital view:** date-by-date collection changes, additional principal outstanding and extra funding required relative to baseline. With no actual portfolio tape, display this loan’s marginal contribution rather than a fictitious portfolio.
8. **Outcome reveal:** after locking the analysis, show subsequent payment/refinancing evidence in a separate panel: the decision made at the review date against what actually happened.

**The independent reviewer is a quality gate, not a panel.** It reads the locked packet before anything is shown. A material problem it finds is fixed in one repair pass, and the operator sees the corrected result, with at most an "independently checked" mark. If a material problem remains after that pass, the run is INCOMPLETE_REVIEW and the page says the result is not ready rather than showing a hedged decision. The reviewer's findings are kept in the "How we know" record.

The leading result should read like: “The requested schedule breaches the selected cash buffer under the early settlement scenario. This alternative clears the modeled payment constraints if the documented payment calendar and opening cash reconcile.” The completed engine fills the actual amount, dates, binding condition and calculations. Do not hard-code a favorable or adverse result into the UI.

Slope underwrites on **connected bank data**. The demonstration supplies a synthetic connected-bank feed for the borrower up to the decision date, anchored to the public financial statements and labelled synthetic once, at its source; an editable assumptions panel remains available. A fuller borrower cash-flow object can later replace assumptions without changing the product.

## 5. From evidence to a modeled adjustment

Every accepted `EconomicEffect` needs: entity and affected activity/obligation; mechanism; amount or supported range; effective period; source IDs and locators; causal explanation; baseline inclusion status; linked effects; and the specific loan metric or action it can change.

| Mechanism | Financial translation | Essential control |
|---|---|---|
| Settlement payment | Existing liability assigned to dated cash outflows | Subtract paid amounts and previously modeled debt service; count once |
| Noncash gain | Normalize a historical earnings input | No invented cash outflow; only adjust periods that contain the gain |
| Receipt delay | Move identified receipts to later dates | Preserve total collectible amount unless separate impairment is established |
| Operating interruption | Change affected sales, costs and collection dates | Affected product/channel share must be supplied; capacity is not sales |
| Restricted funds | Transfer cash from available to restricted pool; model release | Restricted is not automatically lost |
| Expense funding/reimbursement | Reduce or fund covered expenses on supported dates | Check beneficiary, covered scope and availability; do not invent unrestricted cash |
| Collateral/funding constraint | Apply documented limit or condition to available financing | Nominal commitment, reported availability and liquidation recovery are distinct |
| Resolved obligation | Remove an obsolete prospective charge | Do not refund historical cash into today’s opening balance |

The lawsuit itself does not supply a generic default-probability increment. It supplies evidence about an economic mechanism. Jev’s confidence never becomes a borrower default probability or a loan-loss assumption: default probability comes from the labelled risk tier. Jev does not forecast a live dispute's outcome: its readings establish facts that close paths or mark where the record points, and every remaining path is tested against Slope's policy, unweighted.

### A useful worked threshold without fictional accounts

Let `P` be settlement cash already paid after June 30 and before the current cash observation; `R` the required operating cash buffer; and `F` net future cash available after operations and other existing debt service, before the two settlements and new-loan service. Using the disclosed approximate $2m opening cash as a scenario anchor:

`Required F before new-loan service = $2.6m − P + R − $2m`

For the explicit assumptions that the full reported approximate $2m is available, `P = $0` and `R = $200,000`, the requirement is **$800,000**. Any unavailable portion of the reported cash increases that threshold dollar for dollar. This is a conditional cumulative threshold, not Synergy’s proven financing shortfall. If $400,000 was already paid in the intervening period, the threshold becomes $400,000. Neither assumed payment history is asserted as fact.

The dated test is stricter: cash must remain above the reserve at each payment date, not merely by year end. Model early and late placement of the unresolved payment dates within their supported windows, and allow intermediate timing. Report which dates bind. Endpoints are stress cases, not a mathematical proof of feasibility for every possible schedule.

For proposed fixed payments `D[t]` and pre-new-loan free cash `F[t]`, feasibility requires at each date:

`opening_cash + cumulative_F[t] − cumulative_settlements[t] − cumulative_D[t] >= reserve[t]`

This is an affordability test under stated assumptions. It is not proof of willingness to pay or a calibrated default model. If the advance purchases inventory, its cash-generation plan changes `F[t]`; the financed purchase cannot be removed while keeping its sales.

## 6. Financial engine specification

### Representation and dates

Use Python 3.12, Pydantic models, integer cents and Decimal rates. Store currency, unit, entity, observation date, source availability and effective period separately. Unknown is a typed state with reason and next evidence requirement, never numeric zero. Approximate values remain approximate even if an operator selects a rounded scenario anchor.

Required domain objects: `DecisionContext`, `EvidenceValue`, `BorrowerCashBase`, `CashStream`, `LoanTerms`, `EconomicEffect`, `Scenario`, `ActionCandidate`, `CollectionRecord`, `ScenarioResult` and `DecisionResult`. Included JSON Schema and examples are initial build contracts; cross-record reconciliation is enforced in code.

Support two schedule adapters in V1:

- **Actual Synergy merchant contract:** percentage of defined account credits, calendar-month milestones, funding-date anchor, separate six-month minimum-payment windows and catch-up obligations, business-day remittance handling, fixed total repayment. Do not derive interest/principal allocation or lien priority from an undocumented convention.
- **Proposed fixed-installment draw:** import an explicit schedule or generate from a declared pricing convention. For the demo, a stated fixed total fee can be allocated into equal payments with a last-payment rounding adjustment. That is a declared demonstration offer, not inferred Slope pricing. Contractual APR, nominal interest and fixed-fee percentage are different quantities.

Model relevant existing debts, not just the attractive merchant example. Until current debt-service schedules are supplied, use bounded aggregate service inputs and expose their scope. Current Shopify receipts, remittances, funding dates and settlement installments are material missing public inputs; they are identified parameters, not a reason to fabricate a full history.

### Cash and collections

Maintain a daily event ledger for known dates, displaying weekly or monthly summaries. Payment dates with only monthly/annual evidence stay interval-valued until a scenario explicitly selects dates. Horizon covers the proposed term and a declared tail; the proposed offer menu is supplied, never taken from another Slope product by accident.

`cash_before_debt = prior_closing_cash + receipts + available_financing − operating_outflows − event_outflows`

`payment_capacity = max(0, cash_before_debt − required_reserve)`

Allocate competing obligations using documented rights and a separately declared payment-behavior assumption. Ordinary payment order is not the same as liquidation security priority. Unknown allocation yields bounds. A full conditional collection path requires an explicit rule such as payment when feasible and a defined later catch-up rule.

Negative cash before debt is a financing deficit, not an implicit overdraft. A capacity breach is not automatically a legal default, bankruptcy or final loss. Forecasting reduced collection does not amend the existing contract.

Direct-to-vendor funding settles a financed payable; it is not also cash in the borrower’s bank. Bank disbursement and subsequent inventory purchase form another supported route. Each dollar follows exactly one route.

### Candidate actions and choice

Compare a small explicit menu: requested structure; one or two smaller economically feasible purchases; a supported longer/otherwise timed schedule; decline new exposure; and a conditional offer tied to a named resolvable fact. Amount and term grids are operator inputs. Do not use binary search for “maximum safe loan” unless monotonicity is established—minimum orders and incremental margins can break it.

The default demonstration policy requires the configured cash buffer and supplied economic hurdle across selected stress scenarios. Return the feasible set and the preferred candidate under a declared objective, for example largest useful financed purchase satisfying the constraints. If policy inputs are absent, return a financing boundary rather than attributing a unique decision to Slope.

A conditional offer must specify the exact fact, acceptance criterion, amount/schedule if satisfied and alternative if not. “Monitor litigation” cannot be the primary decision output. Conversely, service outages return `INCOMPLETE_REVIEW`, never an automatic borrower decline.

### Concrete proposal fixtures

Include three **analytical offer fixtures**, explicitly labeled as proposed comparison inputs rather than actual applications or Slope prices:

| Fixture | Advance | Payments | Assumed total fixed fee | Regular monthly payment | Final payment |
|---|---:|---:|---:|---:|---:|
| Smaller six-month draw | $100,000 | 6 | 6% / $6,000 | $17,666.67 | $17,666.65 |
| Requested six-month draw | $250,000 | 6 | 6% / $15,000 | $44,166.67 | $44,166.65 |
| Shorter draw | $250,000 | 3 | 3% / $7,500 | $85,833.33 | $85,833.34 |

These fixtures remain as reference arithmetic for the Synergy case; the lead case's offers come from the reconstructed Slope menu and price card. Default funding to the next business day after the decision date (Slope pays the supplier at once); the operator can change it. Show each structure's APR equivalent beside its fee, and apply Slope's proration of fees on early repayment. Show the minimum residual payment capacity required for each fixture, then apply the event calendar and action-dependent financed purchase plan. No fixture receives an automatic approval. A six-month offer extending into 2025 must include the relevant 2025 settlement timing uncertainty; stopping the legal cash obligations at December would overstate capacity.

### Loan economics and portfolio output

Keep three series separate: contractual amounts owed, conditional cash collected and probability-weighted expected collections. V1 requires the first two. Expected collections use explicit weights that sum to one and carry their basis: a labelled risk tier from bank data (probability of default). A live dispute's paths are not weighted: each is a conditional series, and the offer is sized against all of them. The conditional series always sits beside the expected one; retain coherent joint scenarios when aggregating loans.

`asset_cash_flow[t] = principal + interest + retained_fees + net_recoveries − disbursements − explicit_asset_costs`

`NPV = sum(asset_cash_flow[t] / (1 + required_return) ** (days_from_decision / 365))`

Principal collections are return of capital, not revenue. Do not average scenario IRRs; omit IRR where no unique meaningful solution exists. Do not subtract a funding cost twice through both the cash ledger and discount hurdle.

Display unpaid contractual amounts, outstanding principal where allocable, delayed capital return, outstanding principal dollar-days, minimum borrower liquidity, cash shortfall timing, NPV change, and unresolved exposure at the horizon. Recovery needs an explicit supported source, timing, costs and competing claims; unknown recovery is not zero or 100%.

Export date-level loan vectors: scenario, action, borrower, loan, currency, disbursement, principal/interest/fee collections when known, total collections, outstanding exposure, unresolved amounts and assumptions. Portfolio aggregation sums compatible date vectors and shares the borrower’s cash pool across its loans. Do not spend the same dollar twice.

**Capital-markets scope for V1:** show the loan’s marginal change in collections and funding needs. Defer a warehouse engine until actual eligibility, advance rates, reserves, concentration tests, sweeps and paydown rules are supplied. A publicly announced facility size is not drawable availability or a complete capital structure.

## 7. Agent and Jev foundation

### Runtime decision

Use the official Claude Agent SDK with native Claude subscription authentication for the primary investigation. Use Codex with saved ChatGPT sign-in for independent review. There is no required Anthropic/OpenAI API key and no automatic paid-provider fallback. Official documentation was checked for the subscription paths; account limits still apply. Jev is a separate TypeSafe service and can incur its own usage charges. [R1–R4, J1]

This is a local personal demonstration. Public sharing uses recorded runs with credentials removed. A shared live backend is a separate deployment and account decision.

| Configuration | Decision and reason |
|---|---|
| Primary model | `claude-opus-5-5`, high effort, subject to local account availability; record actual returned model ID |
| Claude Agent SDK | Start from source-verified `0.2.159`; lock exact resolved dependencies after local authentication/tool smoke test |
| Reviewer | Codex subscription session, fresh context, available model recorded; no fictional exact Codex version pin |
| Alternate reviewer | Fresh Claude context only if selected explicitly; do not silently switch after a reviewer failure |
| Jev | `jev-1.13.0` via direct `typesafe-sdk==0.7.1`; preserve complete response and model metadata |
| PydanticAI agent loop | Omit; native subscription runtimes serve this requirement directly |
| Data/domain validation | Keep Pydantic and JSON Schema independently of agent framework |
| Tool integration | Small custom MCP server; no general shell/filesystem/browser tools for historical investigation |
| Initial budgets | 120 agent turns (host-counted and enforced), 400 physical Jev attempts including SDK retries (spend cap $0.10 per run; host sweep has its own budget), 900 seconds for the investigation, plus repeated-no-progress stop; ceilings, not targets. Raised from 80 turns, 250 attempts and 600 s so the inventory can be covered by cited findings rather than exempted |
| Extra retry wrapper | Omit initially because layered retries can multiply calls; bounded provider retries within wall-clock budget |
| Viewer | FastAPI, Jinja2, HTMX, Pico.css; local-only by default |
| Storage/search | SQLite with FTS5; no vector database needed for this corpus |
| Packaging | `uv` with lockfile; Python 3.12; CLI first, viewer second |

These are verified design pins, not a claim of installed or tested integration. The first implementation gate is a local subscription-authenticated structured-output and custom-tool smoke test. Do not use Claude `--bare`; it prevents the intended subscription credential loading. Detect API-key/provider overrides without logging secrets.

### Actual adaptive workflow

The agent receives the mission and baseline, then decides which gap could change the financing choice. Its loop is: select a material uncertainty and record it as a decision dependency; search eligible evidence for it; read the strongest evidence and conflicts; propose atomic, sourced findings; reconcile contradictions; propose economic effects; run the financial sensitivity; choose the next high-impact dependency; submit a reviewable packet.

Design rationale for the semantic layer is in `Jev_Pivot.md` (builder context; never an agent prompt). The division of labour is: **the agent thinks broadly, Jev judges narrowly, code computes exactly, the host controls everything, and the reviewer sees the causal chain.**

Tools (exact MCP allowlist in `contracts/agent_config.json`): `get_mission`, `read_baseline_profile`, `read_loan_terms`, `record_dependency`, `search_evidence`, `read_evidence`, `judge`, `propose_finding`, `resolve_finding`, `propose_effect`, `run_sensitivity`, `request_missing_fact`, `submit_packet`. Missing-fact requests are recorded internally and send no message. Handlers bind run/case/cutoff on the server; tool arguments are object IDs, queries and agent-authored propositions, never paths, snapshot names, cutoffs or raw Jev state. Acquisition occurs outside a historical run; later sources cannot become available through an unrestricted browser.

Only whitelisted operational mission fields reach the agent. Case-specific expected findings, counterparty identities established only by later documents (such as a legal-entity name from a 2025 agreement) and evaluation labels remain evaluator-only in `case_eval_private.json`; neither the investigator nor the blind reviewer receives that file. Do not hand the agent a numbered list of answer-bearing documents or a scripted sequence of research steps or answers. Host-triggered semantic checks at fixed boundaries (below) are a control mechanism, not an answer script: the agent still chooses what to investigate, where to search and what to conclude. It should demonstrate that it can discover the connection between a court dispute, supplier debt and repayment requirements. The financial sensitivity tool helps prioritize payment dates and receipt channels over immaterial legal detail.

### Jev as the semantic judgment layer

Jev is a typed, narrow semantic sensor placed where retrieved text becomes a finding, a finding becomes an economic effect, or evidence conflicts. Its value is preventing category errors before they become financial inputs: an allegation treated as a liability, a paid matter treated as a future outflow, a noncash gain treated as cash, the wrong entity, a disputed demand treated as an agreed payment. Jev also **reads a live dispute's passages for a scenario compiler**. The host owns a versioned post-judgment model (`contracts/dispute_model.json`): stages (liability decided with the amount open, judgment entered, appeal pending with a secured stay, unpaid with no secured stay), generic transitions, and factors in the style of HYPO/CATO (appeal intent, amount finality, the payer's conduct and liquidity, settlement signals, an appeal waiver scoped to the obligation). The agent groups the accepted findings about one obligation, names it and the counterparty, and quotes the amount and any judgment date (both checked against the quotes); it does not judge who pays, the amount's status or the stage, and never designs paths. Jev reads each cited passage against that one obligation, never forecasting: who pays whom (Choice), the amount's status, sought, estimated, fixed or paid (Choice), whether it includes post-judgment interest (Noul), which procedural events it establishes (Noul each), which factors it bears on (Noul each) and a graded factor's level (Score). Code places the stage from the most advanced established event, aggregates each factor by its own rule (most advanced for procedure; latest by date for conduct, finances and settlement, keeping same-date conflicts; a waiver only for the obligation it covers), closes only the paths an established fact rules out (silence and stated intentions close nothing), sets every date and amount from cited rules (Fed. R. App. P. 4, Fed. R. Civ. P. 62 and 69, 28 U.S.C. § 1961, a labelled supersedeas multiple and collateral share, a code-owned ruling window placed early and late), and keeps every other path: none is weighted or pruned, and Slope's policy is tested on all of them. Paths the established factors support are marked 'the record points here' with the decisive quote (ordinal, never a probability). Conditions to restore a declined amount are computed across every tested path. The full lifecycle (pleadings to trial) is the roadmap.

The agent requests a **judgment profile** with object IDs; the host resolves the immutable objects, builds the smallest necessary state, selects the versioned questions, makes the call, logs the physical request once and returns typed semantic observations. Independent questions on the same state go in one request.

| Profile | Trigger | Questions (primitive) |
|---|---|---|
| `candidate_screen` | Host, on every `search_evidence` result (one request per query–passage pair) | `gap_relevance`, `usable_evidence`, `premise_conflict`, `instruction_like_text` (Noul) |
| `claim_interpretation` | Agent, after reading a passage and stating one atomic claim | `entity_scope`, `claim_posture`, and whichever of `obligation_status`, `cash_access`, `activity_status`, `offset_status` the claim concerns (Choice) |
| `finding_check` | Host, on every `propose_finding` | `finding_support`, `finding_atomicity`, `context_sufficiency`, `economic_role` (Choice) |
| `statement_relation` | Agent, when two findings or passages may conflict; host, on each accepted finding against the most recent accepted finding under the same dependency; host, when an effect may overlap a baseline item | `statement_relation`, `baseline_overlap` (Choice; overlap is a warning only) |
| `snapshot_sweep` | Host, once per snapshot before any run (cached, own budget): every section, then every paragraph and table row of flagged sections | `matter_inventory` (Noul), `matter_kind` (Choice) |
| `statement_support` | Host, on every effect's model consequence and on the packet conclusion | `claims_supported` (Choice) |
| `inventory_coverage` | Host, at submission, on every paragraph or table row an accepted finding cites | `coverage_supported` (Choice) |

**Jev judges claims; it does not search for what is missing.** A Jev question is used only when (1) there is one claim to judge, made by the agent or the sweep, (2) a short passage contains the answer, so Jev needs no dates or outside facts, and (3) code decides what each answer does. Enumeration and completeness belong to the agent and to the independent reviewer.

Jev's advantages are cost, independence from the agent's framing and stable typed answers, so the host also uses it at scale and at the points where fluent reasoning is riskiest:

- **Reading list and cited-paragraph gate (atomic units).** Every admissible section is screened once for specific legal matters, settlements, debt agreements, covenants, cash restrictions and matter-related accounting items; each flagged section is then split into atomic units (one per paragraph, one per table row carrying its header or introducing line) and each unit is screened on its own. The flagged units form a reading list for the agent, not a checklist. At submission the host checks every paragraph or table row that accepted findings cite: the findings citing it must state each payment, obligation, restriction, covenant, default term and earnings item it describes (for example a settlement gain in the paragraph that describes the settlement loan). A clear gap has no free-text override: the agent adds the finding or escalates it, naming the failed observation, to the independent reviewer's checklist. Flagged units that no accepted finding cites also go to that checklist. Jev judges the agent's claims one atomic unit at a time; finding what the agent never read is the reviewer's job (step 6), with one repair pass. This replaces prompt rules about coverage.
- **Category-error guard.** Every finding that supports an effect gets host-run posture and status checks, with the status question chosen by the effect's mechanism. Code-owned rules require, for example, a required (not claimed or disputed) obligation for a settlement payment effect and a reported-satisfied obligation for a resolved one; a mismatch or ambiguous answer blocks the effect. Because this guards the demanded-versus-paid critical error, disagreement is escalated, not overridden: the effect becomes disputed, creates no cash stream, and a disputed cash-moving effect makes the run INCOMPLETE_REVIEW.
- **Statement support.** Each effect's model consequence and the conclusion are checked against the cited findings and engine results; unsupported claims must be revised. A disagreement is allowed only as a reply to the specific failed observation, on the unchanged text, with a reason.
- **Overrides answer failed checks.** A reasoned disagreement always cites the observation it disagrees with, after that check has failed. It remains available for the finding-quality checks (support, atomicity, context) and statement support, where Jev reviews the agent's wording; the entity gate has none; coverage gaps and category-guard failures escalate instead.
- **Reliance attribution.** When a finding cites a screened passage, that passage's screen observations are recorded as used, so evaluation can show whether screening helped.

Screening labels and orders evidence (direct evidence, conflict, context-only); it never hides a candidate, so a Jev false negative cannot bury the passage that answers the question. Routing thresholds live in code and are checked against labelled cases in `evals/`; no confidence cutoff converts a classification into a fact or a financial input.

Acceptance is asymmetric: Jev observation → agent accepts, reconciles, or overrides with a stated reason → host structural validation (atomicity, verbatim source spans) → effect proposal → deterministic engine validation. Every semantic observation carries a `downstream_disposition` (used in finding, caused a further read, redirected research, flagged conflict, challenged the agent's draft, overridden with reason, unused), which forms the Jev contribution ledger used in evaluation.

Code, not Jev, does arithmetic, date comparisons, eligibility checks and payment allocation. The main agent remains responsible for integrating evidence and explaining the economic mechanism. Preserve unknown and conflicting states.

### Investigation graph

The investigation is recorded as one append-only graph that the packet, reviewer, viewer and evaluator all consume: decision dependencies → searches and evidence candidates → Jev call records and semantic observations → atomic findings (exact source spans verified verbatim against the snapshot, plus observation references) → reconciliation tasks → economic effect proposals (mechanism, target stream, parameter requirements, baseline treatment) → sensitivity results. Runtime effects cite findings and source spans, never the builder's facts registry. Traceability means structured observable actions, never model reasoning tokens.

### Isolation and state

Set `tools=[]`, exact `allowed_tools`, intended custom MCP server, `strict_mcp_config=True`, `setting_sources=[]` and `permission_mode="dontAsk"`; use an empty dedicated working directory. Validate these against the pinned SDK at smoke test. Server handlers provide the effective security boundary for case access.

Use a separate generated SQLite evidence database per snapshot for V1. Outcomes, answer keys, original unrestricted archives and evaluation reports are excluded from agent and reviewer tool access. A reviewer receives an isolated admissible bundle; read-only filesystem settings alone do not guarantee it cannot read unrelated files.

Scope findings to `(run_id, version, finding_id)`. Lock source hashes, findings, calculations, assumptions and decision together in immutable result packets. A locked memo pointing to mutable facts is insufficient. Cache literal passage interpretation separately from application at a date; include model/question/schema versions and relevant state hashes in cache keys.

## 8. Evidence acquisition and historical controls

The package includes original primary documents for Synergy and Barfresh, extracted text where available, source hashes, structured case facts and decision-relative source membership. Source availability is distinct from the underlying event date. The loan agreement’s signature date does not by itself establish when it became public.

Use intact sections and tables, retaining headings, units, column labels, entity scope and footnotes. No universal 1,200-character chunking rule. Quotes need exact locators; computed facts cite inputs and formula versions rather than fabricated quotations.

The source catalog records relative paths so the kit can move. Verify hashes on ingestion. Unknown publication dates are quarantined or conservatively dated to a verified public disclosure; never substitute an event date. Date-only records remain date-only and become eligible under the declared end-of-day snapshot convention.

Historical evidence control cannot erase a model’s prior knowledge of later outcomes. Evaluate whether the answer is supported by admissible evidence, not whether a famous historical case proves prospective prediction accuracy.

### Gaps with concrete treatment

| Missing public input | V1 treatment | Input that resolves it |
|---|---|---|
| Synergy settlement installments within annual buckets | Timing scenarios and decision sensitivity | Executed payment schedule/amendments |
| Payments between June 30 and August cash observation | Explicit paid-since-observation variable; no automatic full $2.6m remaining | Bank transactions and current creditor statements |
| Current merchant remaining repayment and funded date | Parameterized seasoned-loan state; origination contract used for unit tests | Funding confirmation and remittance ledger |
| Shopify channel/account mapping | Eligible-receipts range; no consolidated-revenue substitution | Store/account receipts and each loan’s account scope |
| Other current debt service and liens | Aggregate range or reconciled debt schedule; no assumed first priority | Current debt statements, lien/waiver documents |
| Proposed purchase economics and offer price | Explicit operator inputs linked to the amount financed | Real request, invoice, margin/turn assumptions and product menu |
| Barfresh executed financing/funding contracts | Bounded scenario; exclude unsupported fee or priority claims | Facility and litigation-funding agreements |
| Warehouse terms and actual portfolio tape | Loan-level marginal funding export only | Authorized facility terms and real loan tape |

These gaps define sensitivity controls and precise borrower-document requests. They do not stop the public evidence demo from showing real obligations, actual contract math and financing boundaries.

## 9. Implementation modules and sequence

Suggested application structure:

```text
app/
  domain/             # Pydantic objects and invariants
  evidence/           # source catalog, parsing, FTS, snapshot builder
  agent/              # Claude runner, tool server, Jev adapter, reviewer
  finance/            # schedules, borrower ledger, collections, valuation
  decisions/          # candidate actions, policy, sensitivity
  exports/            # dated loan vectors and immutable packets
  web/                # FastAPI routes and Jinja/HTMX views
cases/
  chromadex_20240819/ # lead case: admissible manifest and scenario inputs
  synergy_20240813/   # secondary case
  barfresh_20241025/  # deferred
outcomes/             # separate, excluded from investigation tools
evals/                # gold premises, contradictions, isolation tests
```

| Build step | Deliverable | Exit condition |
|---|---|---|
| 1. Runtime smoke test | Native subscription auth, one custom tool and schema output; one budgeted Jev call | Exact models/runtime recorded; no metered Anthropic/OpenAI fallback |
| 2. Evidence ingestion | Source hash verification, structured tables, FTS, two dated snapshots | Future source IDs inaccessible; original table context retrievable |
| 3. Financial core | Actual merchant rules, proposed fixed schedule, settlement calendar and cash ledger | Reference thresholds match; no double counts or fake opening balances |
| 4a. Semantic layer | Investigation graph and run store, Jev adapter (Choice and Noul) with host-owned judgment profiles, question registry v3, labelled semantic eval cases | Jev state is built only from admissible snapshot objects; every observation is traceable to a versioned question and physical call; Jev output cannot create a cash stream; labelled cases run live |
| 4b. Investigation | Scoped MCP tools, adaptive runner, recorded Synergy run, thin read-only investigation viewer | From only the admissible dated snapshot and locked baseline, the agent identifies decision-relevant gaps; Jev systematically screens retrieved evidence and checks narrow semantic claims; every accepted finding is atomic and traceable to source spans and semantic observations; every proposed economic effect names its mechanism, target, parameter requirements and baseline treatment; dates and arithmetic remain deterministic; pivotal unresolved facts remain typed unknowns; the full investigation is observable; and no answer-bearing document order or evaluator information reaches the run |
| 4c. Jev at scale | Snapshot sweep reading list of atomic units, cited-paragraph gate, category-error guard on effects, statement-support checks, automatic relation checks, screen reliance attribution | No packet can be submitted with a cited paragraph or table row whose payment, obligation, restriction, covenant, default term or earnings items its findings do not state, an unsupported consequence or conclusion, an effect whose supporting findings fail its category rule, or an open reconciliation, unless each exception is resolved or escalated; an escalated cash-moving effect leaves the run INCOMPLETE_REVIEW, and coverage escalations and flagged units no finding cites go to the independent reviewer's checklist; the coverage prompt rule is removed; a recorded Synergy run shows these checks at work |
| 5a. Lead case evidence | ChromaDex sources registered (mission `chromadex_20240819`), dated snapshot with federal court filings and SEC filings, locked run inputs, lead mission, reframed spec and builder context | Outcome sources and post-decision facts are absent from the snapshot; every baseline observation cites verbatim admissible text |
| 5b. Decision model and cash flows | Synthetic connected-bank baseline and reconstructed Slope menu, price card and policy (each labelled once); live disputes instantiated onto the generic post-judgment dispute model (Jev reads each cited passage: who pays, the amount's status, procedural events, factors; code places the stage, closes only paths an established fact rules out, sets dates and amounts from rules, and keeps every other path, unweighted); bank-only vs event-adjusted scenario pairs (only effect IDs differ); payment capacity with an explicit allocation rule; lender economics (fee, APR equivalent, yield, NPV at cost of funds, capital tied up, expected loss); affordability coverage; security and conditions; conditional and expected collections; marginal funding export; recorded ChromaDex run | Before/after has identical common inputs; each structure's collections and economics are shown per branch; the result explains the binding constraint and the recommended structure |
| 6. Review gate and decision-first workbench | Independent review of the locked graph packet as a quality gate, starting from the packet's reviewer checklist (escalations and flagged units no accepted finding cites) (issues point to nodes or edges; one repair pass; objections are not shown on the front page), decision-first workbench (decision with conditions as actions, dated loan collections beside borrower monthly cash before/after, 2–3 drivers with source click-through, "How we know" drill-down), outcome reveal after lock | Front page shows one decision, its conditions as actions and dated cash flows, with no unknown lists or disagreement logs; reviewer can trace claims and a material issue is repaired or the run is INCOMPLETE_REVIEW; outcome reveal cannot contaminate the run |
| 7. Transfer/evaluation | Same engine on Synergy (secondary); agent-with-Jev vs agent-alone runs measured on critical semantic errors, finding and effect quality, pivotal-gap discovery, research efficiency, the Jev contribution ledger, agent/Jev disagreement, cost and decision consistency | Differences reported honestly, including failures and cases where Jev adds no value |

The first demonstrable milestone was the Synergy financial core plus an agent-produced evidence adjustment; the lead demonstration is ChromaDex. Do not spend the first build phase on a polished legal dashboard or a warehouse model. Conversely, do not substitute a canned report for the adaptive investigation Russell explicitly mentioned.

## 10. Acceptance criteria and evaluation

Financial acceptance tests protect economics:

1. Zero external adjustments reproduce the baseline under identical common inputs.
2. The settlement liability is counted once; a paid amount cannot be deducted again.
3. June schedules and August cash cannot be combined without an explicit bridge or assumption.
4. Historical noncash normalization cannot become current cash expense.
5. Merchant Account Credits are distinct from consolidated revenue; overlapping sweeps require account mapping.
6. Seasoned-loan balances and milestone clocks do not restart at the analysis date.
7. Delayed but fully recovered principal is conserved, increases outstanding dollar-days and reduces PV at a positive hurdle absent compensating charges.
8. Direct vendor funding and borrower bank cash are not double counted.
9. A smaller or declined advance changes the funded purchase and related receipts.
10. Unknown timing, recovery, priority and scenario weights do not become zero, one or a silent default.
11. Existing contractual schedules remain unchanged by collection forecasts.
12. Portfolio aggregation reconciles to loan ledgers and shares each borrower’s available cash once.

Agent acceptance includes identifying the three separate Synergy matters; respecting the August-cutoff boundary on the second supplier's identity (reporting the "VitBest" table label and its balance-matched link to the March settlement is acceptable when cited as an inference, while the full legal entity or any 2025 agreement fact is future leakage); distinguishing agreement from allegation, recognizing the noncash gain and date mismatch, surfacing material missing terms, and not importing 2025 refinancing as August committed cash. Barfresh tests planned capacity, expense funding and disputed payable treatment.

Evaluate agent-alone and agent-plus-Jev using the same corpus, mission, primary model, research/financial tools and budgets. The agent-alone arm omits the `judge` tool, the screening fields on search results, the automatic finding check and the Jev registry; it retains the same retrieval and financial calculation capabilities. Final-decision agreement is insufficient on its own: also measure critical semantic errors (wrong entity, allegation treated as liability, paid treated as future payment, noncash treated as cash), supported-finding and effect quality, pivotal-gap discovery, research efficiency, which Jev observations changed research or corrected a finding (the contribution ledger), how often and how justifiably the agent overrode Jev, and cost. Jev may leave the recommendation unchanged on a borrower while still improving evidence discipline; that is a legitimate result. Record evidence support, economic-effect accuracy, double-count prevention, decision consistency, research efficiency, latency and Jev usage. Keep blind reviewer context and retain failures. Run multiple seeds/sessions, but do not claim a statistically meaningful production improvement from two borrowers.

Critical errors—wrong entity, future leakage, fabricated payment date, demanded amount treated as paid, a Jev judgment presented as a probability, cash overstated or duplicate obligation—block a publishable demo result even if a blended score is high.

The included schema checks and reference calculations validate design contracts and selected arithmetic only. They are not evidence of a built agent, a tested underwriting engine or a calibrated default model.

## 11. What would make this compelling to Russell

The central demonstration is a traceable chain: **a sourced legal fact changes a specific financial input; the changed input alters a loan’s cash-flow path or feasible offer; the resulting cash-flow vector changes capital needs.**

ChromaDex shows live disputes placed on a generic dispute model, whose paths carry dated cash consequences inside the loan's life. Synergy shows hidden settlement obligations from a resolved dispute. Jev reads the disputes' passages atomically and guards the category errors in both. The output is an instrument Russell could recognize in a credit or portfolio workflow, while the design keeps the public-data demonstration honest about what requires connected borrower data.

## Sources

Primary case originals and hashes are in the companion kit. URLs below are authoritative references; retrieval date is 24 September 2026.

- **S1:** Slope, [Inside Slope’s Agents Platform for Credit Risk & Customer Operations](https://slopepay.com/blog/agents-platform).
- **S2:** Slope, [From Transaction Labels to Customer Understanding](https://slopepay.com/blog/context-aware-ai-bank-transaction-labels).
- **S3:** Slope, [Borrowing limits and terms](https://help.slopepay.com/en/articles/4357633).
- **S4:** Slope, [Amazon seller line of credit](https://slopepay.com/blog/slope-amazon-sellers).
- **S5:** Slope, [Slope 101 / Pay Later](https://help.slopepay.com/en/articles/1972225).
- **C1:** Synergy, [13 August 2024 S-1/A](https://www.sec.gov/Archives/edgar/data/1562733/000121390024068424/ea0208324-04.htm), legal proceedings and financial notes.
- **C2:** U.S. District Court, [27 February 2023 opinion, Synergy v. HVL](https://www.govinfo.gov/content/pkg/USCOURTS-med-2_22-cv-00301/pdf/USCOURTS-med-2_22-cv-00301-0.pdf).
- **C3:** Synergy/WebBank, [May 2024 merchant-loan agreement](https://www.sec.gov/Archives/edgar/data/1562733/000121390024056991/ea020832401ex10-32_synergy.htm).
- **C4:** Synergy, [FY2024 annual report](https://www.sec.gov/Archives/edgar/data/1562733/000121390025026254/ea0235758-10k_synergy.htm).
- **C5:** Synergy, [May 2025 credit agreement](https://www.sec.gov/Archives/edgar/data/1562733/000121390025050984/ea024464201ex10-1_synergy.htm), recitals and settlement-funding provisions.
- **C6:** Synergy, [June 2025 quarterly report](https://www.sec.gov/Archives/edgar/data/1562733/000121390025076060/ea0252562-10q_synergy.htm).
- **B1:** Barfresh, [September 2024 quarterly report](https://www.sec.gov/Archives/edgar/data/1487197/000149315224042368/form10-q.htm), filed 24 October 2024.
- **B2:** Barfresh, [FY2024 annual report](https://www.sec.gov/Archives/edgar/data/1487197/000164117225000931/form10-k.htm).
- **B3:** Barfresh, Q1 2025 report filed 1 May 2025; original and URL in source catalog.
- **R1:** Anthropic, [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).
- **R2:** Anthropic, [Agent SDK Python reference](https://code.claude.com/docs/en/agent-sdk/python).
- **R3:** OpenAI, [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
- **R4:** Runtime version and authentication source links are recorded in `contracts/agent_config.json`.
- **J1:** TypeSafe model/SDK sources and question configuration are recorded in `contracts/agent_config.json` and `contracts/question_registry.json`.

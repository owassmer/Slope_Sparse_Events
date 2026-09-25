# Slope project: context and alignment for the coding agent

## Purpose of this document

This document gives you the intent behind the build: what Russell described to Owen, why the problem matters, what Owen wants to demonstrate, and how to make implementation choices that serve that purpose.

Read it alongside `Slope_Credit_Scenario_Build_Specification.md` and `Slope_Credit_Scenario_Research_and_Design_Kit.zip`. The specification defines the implementation. The kit supplies evidence, schemas, configuration, examples and reference calculations. This document explains the surrounding product and collaboration context.

The call account below comes from Owen’s description of his conversation with Russell. The product framing and design choices are the synthesis Owen approved for the demonstration. Keep that distinction when presenting the work: Russell supplied the problem and priorities; this implementation is Owen’s proposed solution.

## 1. What Russell described

Owen spoke with Russell at Slope about work involving credit decisions and financial modeling. Three parts of that conversation belong together.

### Sparse external information in credit decisions

Russell described an initiative to improve credit decisions using information beyond what can be obtained from banking transactions. His particular interest was information that is sparse: events or circumstances that affect relatively few businesses and therefore do not make sense as ordinary features in a full-population model.

A lawsuit affecting a business was his example. The useful information may live in public filings, court documents, company disclosures and surrounding reporting. Understanding it requires research and interpretation: identifying what happened, which entity or activity is affected, whether an obligation exists, when it matters and what economic consequences follow.

Russell described a secondary research flow using agents to locate this qualitative information and categorize or compute it into a form useful for modeling and lending decisions. He specifically mentioned **Jev** and an **agent workflow**. Both are central to what Owen wants to build.

For the demonstration, the relevant business is the prospective borrower. Its lawsuit matters through its ability to generate, access and allocate cash over the period when the loan must be repaid.

### Capital-markets scenario modeling

Russell also described capital-markets modeling. He compared it to FP&A while emphasizing more complex scenario modeling. The relevant idea is understanding how financial results change under different events and assumptions.

Owen sees the external-event initiative as a concrete way to create those scenarios. A researched lawsuit can change a payment calendar, expected customer receipts, access to funds or operating capacity. Those changes can then be propagated into loan cash flows and capital requirements.

### Assessing a loan portfolio

Russell asked Owen how he would assess a loan portfolio. In that discussion, Russell emphasized the cash flows of the loans themselves as a core component.

That emphasis determines the primary unit of this build. The borrower’s financial position explains repayment capacity. The modeled asset is the loan: money advanced, money collected, the timing of collections, outstanding exposure and the capital tied up along the way.

### What Russell asked Owen to do

Russell asked Owen to come back with his thoughts on solutions. Owen wants to respond with a working demonstration that makes the proposed solution tangible and gives Russell something concrete to evaluate.

The intended result is a useful follow-up conversation about how this could fit Slope’s work, supported by a convincing implementation of the reasoning and financial mechanics.

## 2. The approved synthesis

**Build a module that researches an unusual external event, translates the evidence into financial scenario adjustments, and shows how those adjustments change a financing decision and the loan’s cash flows.**

The complete chain is:

1. Begin with a real business, a specific financing decision and a baseline assessment.
2. Investigate a material external event using an agent and Jev.
3. Establish the economic effects supported by the evidence.
4. Apply those effects to the borrower’s cash available for repayment.
5. Calculate conditional collections under specific loan structures.
6. Compare the financing choices and identify the binding conditions.
7. Show the resulting change in capital needs and cash available for future lending.

This connects Russell’s credit-research problem to his scenario-modeling and portfolio-cash-flow interests. The first implementation completes that chain for an individual borrower and exports results that a portfolio model can consume.

## 3. Start from Slope’s decision

The product is organized around a review **before a new discretionary advance or increase in exposure**. Its operator is a credit reviewer deciding what financing to offer given the borrower’s current position and the external event.

Slope’s public workflow research supports using a specialist module within a broader underwriting process. The module can receive an existing financial baseline, bank-derived cash-flow understanding, relevant debt obligations and an available product menu. That interface is important: the work should be straightforward to connect to a lender’s existing assessment.

The decision has practical controls: how much to advance, over what term, on what payment schedule, at what supplied price, and subject to which funding conditions. The output should speak in those terms.

Useful results include a supportable amount, a better-fitting schedule, a condition that makes a particular offer feasible, or a quantified explanation that the tested structures cannot be supported. Each result should show the effect on projected lender receipts.

The use of proceeds also matters. An inventory advance can create sales and cash receipts. Comparing different advance sizes therefore requires the associated purchase and operating plans to change with them. This is part of understanding the actual financing decision.

Use a concrete test when choosing an implementation detail: **How does this help the reviewer decide what to fund, and understand the cash that comes back?**

## 4. What the financial model means in plain language

There are three connected levels.

| Level | Question it answers | Relevant outputs |
|---|---|---|
| Borrower | What cash will the business have available, and when? | Receipts, operating spending, settlement payments, other debt service and available cash |
| Loan | What does the borrower owe, and what cash is collected under each scenario? | Payment schedule, collections, shortfalls, outstanding exposure and return of capital |
| Capital and portfolio | How does that collection pattern affect the lender’s ability to fund its book? | Collection changes by date, additional funding needs and capital tied up longer |

The borrower’s balance sheet, income statement and cash-flow statement are useful inputs. A dated repayment-capacity model is the essential connection to the loan.

A legal event becomes financially useful through an identifiable mechanism. A settlement creates cash obligations on particular dates. A manufacturing interruption changes deliveries and customer receipts. Restricted funds change cash availability. Expense funding changes who pays particular costs and when.

Each effect should carry enough explanation for a reviewer to understand why the input changed. Deterministic code then applies the input consistently across the scenarios and offer alternatives.

Timing deserves as much attention as amount. A loan that ultimately repays in full can still return capital later, require additional funding and produce different economics. The interface should make that visible.

Keep contractual payments, scenario-conditioned collections and probability-weighted expected collections distinct. Scenario weights, where used, need an explicit basis. Jev’s response confidence describes its judgment, not a borrower’s probability of repayment.

## 5. The before-and-after comparison

Owen wants the effect of the research to be directly observable.

The baseline and event-adjusted views use the same decision date, common financial assumptions and financing request. The enriched view applies the specific information obtained through the investigation. The reviewer can see which inputs changed and which loan outcomes changed as a result.

Sometimes the valuable adjustment is the timing of an obligation already recorded in the accounts. Sometimes it is recognizing that a historical accounting item has a different cash implication. Sometimes it is establishing that a potential future charge has already been satisfied. The financial treatment should reflect the actual mechanism.

The measured impact can be a different feasible amount, schedule, funding condition, projected collection date or liquidity requirement. The demonstration should make the economic significance clear even when the overall lending stance stays the same.

Later events belong in a separate outcome view. First establish what the system could conclude from the evidence available at the decision date. Then reveal what happened and evaluate which mechanisms the analysis captured.

## 6. Real cases and their roles

Owen wants the demonstration grounded in actual businesses, actual legal events, public evidence and observable financing outcomes. The research package supplies that foundation.

### ChromaDex: lead demonstration

The lead decision date is **19 August 2024**. ChromaDex is a consumer supplement brand that buys finished goods from contract manufacturers. On that date its disputes with Elysium Health, a former customer, are live and move money both ways: a Delaware fee award whose amount awaits a ruling (about $9.8 million sought, and ChromaDex intends to appeal, which needs a bond), and a California judgment ordering Elysium to pay ChromaDex $2.5 million with no payment date. These are the forward-looking, branching questions that bank data cannot answer. The request is reconstructed as Slope bill-pay financing of a contract-manufacturer invoice, and the resolution events fall inside that financing's life.

### Synergy CHC: secondary demonstration

The secondary decision date is **13 August 2024**. Synergy is a consumer-products business with an actual merchant-financing agreement and disclosed supplier-settlement obligations.

Its strength is the connection between the evidence and the cash model. The research links a supplier lawsuit to settlement debt, establishes payment requirements, supplies actual merchant-loan mechanics and provides later evidence of financing directed toward the settlement obligations.

The agent’s job is to reconstruct the relevant commitments and their treatment, then apply them to the incremental financing analysis. The specification and kit contain the detailed chronology, amounts and contract rules.

The historical merchant loan is an existing obligation and a real contractual reference. The proposed fixed-installment offers are explicitly modeled financing alternatives for the demonstration. Preserve those identities throughout the application.

The public evidence supports meaningful thresholds and scenario analysis. Inputs such as exact current bank cash, intervening payments, store-level receipts and private payment calendars are handled through identified parameters. The user should be able to supply an assumption or actual data and see the consequences immediately.

### Barfresh: deferred

Barfresh (25 October 2024) is deferred: its live dispute is in state court with no docket we could access. The original rationale follows.

The second decision date was **25 October 2024**. Barfresh is a small beverage business with a supplier dispute, production considerations and a real receivables facility.

This case tests a different path from evidence to repayment: manufacturing and inventory affect deliveries, receipts and the cash-conversion cycle. Its purpose is to establish that the same product can handle operating effects as well as settlement payments.

The application should reuse the core evidence, economic-effect, scenario and decision interfaces across the two businesses. Case-specific contracts and facts supply the differences.

## 7. How agents and Jev contribute

The agent should perform a real investigation. It receives a mission and baseline, identifies a material gap, chooses evidence to inspect, interprets the findings, tests their financial relevance and decides what to investigate next.

Jev supplies focused judgments at useful points in that process. Examples include whether a passage concerns the borrower, describes an allegation or completed event, establishes a payment obligation, supports an offset, or identifies an operating restriction. It also helps evaluate whether a research step addresses the selected gap.

The agent integrates those judgments with the other evidence and the financial model. Deterministic code handles arithmetic, schedules, cash allocation and comparisons. This division gives each component a clear role.

Research priority should follow decision sensitivity. If resolving a payment date would change the feasible offer, that is a valuable next step. The financial model should help the agent identify such dependencies.

Make the workflow observable enough to evaluate: what the agent sought, what Jev judged, what evidence was accepted, what model input changed and what happened to the financing choice. This should support useful review and debugging without overwhelming the operator’s primary decision view.

## 8. Owen’s broader approach to this work

Owen approaches these projects through **domain formalization, operational decision systems and agent evaluation**.

Domain formalization means learning the actual work well enough to define its objects, relationships, rules, uncertainties and choices. Operational decision systems connect that understanding to actions a real operator can take. Agent evaluation establishes whether the system performs that work effectively and where it needs improvement.

This project should demonstrate all three. The domain work is understanding lending and the economic consequences of external events. The operational decision is a financing choice. The evaluation examines whether the agent’s research and judgments lead to supported adjustments and useful financial outputs.

Owen brings financial-mathematics training, financial-sector experience, credit-model and policy tooling, and experience building agentic applications. Relevant prior work includes credit decision workspaces with pricing, limits and scenario comparisons, as well as autonomous credit-policy research. He wants to apply that background to a specific problem Russell recognizes.

The broader professional goal is to demonstrate that Owen can understand an ambiguous business problem, acquire the necessary domain knowledge, make sound product choices and build an operationally useful solution. The quality of the demo depends on those connections being visible.

## 9. How to work with Owen

Owen values decisive, well-reasoned execution. Take ownership of routine implementation choices within the approved direction and explain the choices that materially affect behavior.

Use research and the supplied evidence to resolve questions you can answer. When information is genuinely unavailable, identify the specific parameter, determine how it affects the decision and implement the relevant sensitivity or input. Useful uncertainty is expressed as a range, threshold, condition or concrete evidence requirement.

Communicate in plain language. Owen is comfortable with technical implementation, but product explanations should remain intuitive to someone with a basic understanding of lending and lawsuits. State what changes, why it matters and what the operator can do with it.

Lead progress updates with outcomes and material findings. Explain remaining issues in terms of the behavior they affect. Complete the authorized work and make the result reviewable before raising a decision that requires Owen’s input.

Use judgment about scope. Favor a complete, convincing demonstration of the core workflow. Add engineering depth where it improves financial correctness, agent performance, evaluation or usability.

## 10. Runtime and resource preferences

Owen has full Claude and OpenAI/Codex subscription access and strongly prefers using those subscriptions for the main reasoning and coding workflows.

The selected design uses the official Claude Agent SDK with native subscription authentication for investigation and Codex with ChatGPT sign-in for independent review. Jev uses the separate TypeSafe service. The specification and configuration files contain the selected versions, tool controls and smoke-test requirements.

Begin with a local runtime that can complete a real investigation and save a reproducible result. Recorded runs support reliable demonstrations. Make run mode, model usage and any separately billed usage clear.

The research and design phase has produced the specification, acquired sources, structured examples, schemas and tested reference arithmetic. The coding phase must implement and exercise the live agent workflow, financial engine and decision interface. Report that progress accurately as each capability becomes operational.

## 11. The demonstration Owen wants to show Russell

The presentation should follow the lender’s reasoning:

1. Here is the business, the financing request and the baseline repayment picture.
2. Here is the external event the agent investigates.
3. Here is the relevant evidence and how the agent and Jev interpret it.
4. Here are the specific financial adjustments that follow.
5. Here is the effect on the requested loan and alternative offers.
6. Here is how collections and funding needs change over time.
7. Here is the subsequent public outcome, revealed after the historical analysis.

The most compelling moment is when Russell can inspect a finding, follow it into a financial assumption, and see the resulting change in a loan decision or cash-flow path. The second case should demonstrate that the same approach handles a different economic mechanism.

The interface should make the decision and financial impact immediately legible, with supporting evidence available where it helps the reviewer assess the result.

## 12. What success means

The build succeeds when a reviewer can answer all of these questions from the working demonstration:

- What decision is being made, for which borrower, at what time?
- What did the external research establish that matters to that decision?
- What contribution did the agent make, and where did Jev help?
- Which financial inputs changed, and why?
- What happens to the loan’s collections under the resulting scenarios?
- Which financing structures are supportable under the stated inputs and policy?
- What condition or missing fact could change the choice?
- How does the collection pattern affect capital needs?
- What does the later evidence tell us about the analysis?

Evaluation should address economic correctness, evidence interpretation, useful research choices, consistency of recommendations, and the contribution of Jev. Provenance serves those judgments by making the reasoning inspectable and the calculations reproducible.

## 13. Using this context during implementation

Use this brief to guide product judgment; use the specification and kit for detailed contracts, case facts and implementation requirements. Preserve the connection from domain evidence to operational decision as you work through the build sequence.

This is **builder context**. It includes the intended case mechanisms and demonstration narrative. Keep it separate from the historical investigator’s runtime inputs and the blind reviewer’s evidence bundle. Those components receive the scoped mission and admissible evidence defined by the application, so their performance can be evaluated meaningfully.

When a technical choice requires judgment, prioritize the choice that makes the completed decision workflow more useful, financially sound and understandable to Russell and Slope.

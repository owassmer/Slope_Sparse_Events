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

**Thesis: domain-informed probabilistic judgments turn unusual qualitative evidence into a useful, measurable financial signal.**

**Build a module that researches an unusual external event, turns the evidence into probabilistic financial scenarios, and shows how they change the distribution of a loan's dated cash flows.**

The complete chain is:

1. Begin with a real business, supplied financing terms and its connected-bank baseline.
2. Investigate a material external event using an agent and Jev.
3. Establish what the evidence says about the present state, one judgment target at a time.
4. Ask Jev for conditional probabilities of the specific future developments that move cash.
5. Compose those probabilities into event paths and simulate the borrower's cash against realistic operating variability.
6. Carry the result into the loan: collections, collection timing, outstanding exposure, discounted cash flows and capital tied up.
7. Show which uncertainty matters most to those outcomes.

This connects Russell's credit-research problem to his scenario-modeling and portfolio-cash-flow interests. The module ends at the financial analysis, which a lending or capital-markets model can consume.

## 3. Where the module stops

The financing terms are inputs: the amount, schedule, fee and funding date. The module does not recommend, size, tier or condition the loan. A lending decision brings in risk appetite, pricing objectives, portfolio constraints and other credit signals that the sparse-event research does not establish; attaching one would make those assumptions look like conclusions of the research. Slope's broader underwriting consumes the analysis and decides.

The use of proceeds matters. An inventory advance can create sales and receipts; a different amount changes the purchase. Controls on the financed amount keep that visible.

Use a concrete test when choosing an implementation detail: **Does this help Russell see what the researched evidence does to the loan's cash flows, and which uncertainty matters?** Russell should be able to move naturally from what the research learned, to what might happen and how likely it is, to what that does to cash and to the loan, to which uncertainty matters most. Work that does not strengthen that sequence is deferred.

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

Keep contractual payments, path-conditioned collections and probability-weighted collections distinct. The weights are Jev's conditional probabilities of specific future events, composed along each path by code, labelled once as model judgment. Jev's confidence statistic is separate and never used as a probability.

## 5. The before-and-after comparison

Owen wants the effect of the research to be directly observable.

The baseline and event-adjusted views use the same decision date, common financial assumptions, supplied financing and operating draws. The enriched view applies the specific information obtained through the investigation. The reviewer can see which inputs changed and which loan outcomes changed as a result.

Sometimes the valuable adjustment is the timing of an obligation already recorded in the accounts. Sometimes it is recognizing that a historical accounting item has a different cash implication. Sometimes it is establishing that a potential future charge has already been satisfied. The financial treatment should reflect the actual mechanism.

The measured impact is the change in the distribution: expected and downside cash, collection timing, uncollected balance, discounted lender cash flows and capital tied up. Both views use identical operating draws, so the difference is the research's effect. If the loan's collections do not change, that is a legitimate result, shown plainly.

Later events belong in a separate outcome view. First establish what the system could conclude from the evidence available at the decision date. Then reveal what happened and evaluate which mechanisms the analysis captured.

## 6. Real cases and their roles

Owen wants the demonstration grounded in actual businesses, actual legal events, public evidence and observable financing outcomes. The research package supplies that foundation.

### ChromaDex: lead demonstration

The lead decision date is **19 August 2024**. ChromaDex is a consumer supplement brand that buys finished goods from contract manufacturers. On that date its disputes with Elysium Health, a former customer, are live and move money both ways: a Delaware fee award whose amount awaits a ruling (about $9.8 million sought, and ChromaDex intends to appeal, which needs a bond), and a California judgment ordering Elysium to pay ChromaDex $2.5 million with no payment date. These are the forward-looking, branching questions that bank data cannot answer. The financing is reconstructed as Slope bill-pay financing of a contract-manufacturer invoice ($2.0M, three monthly installments, 3.7% fee), analysed over a 180-day horizon in which most resolution events fall.

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

The agent performs a real investigation. It receives a mission and baseline, identifies material gaps, chooses evidence to inspect, records atomic sourced findings and groups the evidence about each live dispute. Its prose never pre-answers a Jev question.

Jev makes the judgments, in two stages:

- **Present-state interpretation:** what a passage establishes about one target (who pays whom, whether an amount is sought or fixed, which procedural events have happened, what a party states it intends).
- **Conditional forecasting:** the probability of one defined future development under stated assumptions and a code-set window (for example, "assuming the amount is fixed and no settlement intervenes, will the company appeal within 30 days?"). Present-state readings are evidence for the forecast, never its probability: "intends to appeal" is not "will appeal".

**Atomic means one judgment target, not minimal context.** Each question gets enough surrounding evidence to reason well: the section around the passage, dates, parties, procedural position and related passages.

Deterministic code sets every date, window and amount, composes the conditional probabilities along each path, simulates cash and computes the loan's outcomes. No hand-written coefficients stand between Jev's judgments and the numbers. A low probability never deletes a feasible adverse path: stress is a separate view.

## 8. Owen’s broader approach to this work

Owen approaches these projects through **domain formalization, operational decision systems and agent evaluation**.

Domain formalization means learning the actual work well enough to define its objects, relationships, rules, uncertainties and choices. Operational decision systems connect that understanding to actions a real operator can take. Agent evaluation establishes whether the system performs that work effectively and where it needs improvement.

This project should demonstrate all three. The domain work is understanding lending and the economic consequences of external events. The operational output is the loan's cash-flow analysis that a financing decision consumes. The evaluation examines whether the agent’s research and judgments lead to supported adjustments and useful financial outputs.

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

One analysis page carries the causal chain **evidence → Jev judgment → financial mechanism → financial impact**:

1. The business, the supplied financing and what the research learned.
2. The future developments that could move cash, and how likely Jev judges each, given the evidence.
3. What those developments do to the borrower's cash, against ordinary operating variability.
4. What that does to the loan: dated collections, timing, exposure, discounted cash flows and capital tied up.
5. Which judgments matter most, at 0%, Jev's value and 100%, with a drill-down from each to its source passage.

The most compelling moment is when Russell follows a real passage into Jev's probabilistic judgment, sees it move the loan's cash-flow distribution, and changes it himself. There are no approval recommendations, provenance banners or verification displays on the page; the investigation record is a secondary link.

## 12. What success means

The build succeeds when Russell can answer these questions from the working demonstration:

- What did the research learn about the borrower's live disputes, and from which passages?
- Which future developments could move cash, and how likely are they given that evidence?
- How do they change the borrower's cash, against ordinary operating variability?
- How do they change the loan's collections, timing, exposure, discounted cash flows and capital tied up?
- Which judgment matters most to the lender's outcome?

## 13. Using this context during implementation

Use this brief to guide product judgment; use the specification and kit for detailed contracts, case facts and implementation requirements. Preserve the connection from domain evidence to operational decision as you work through the build sequence.

This is **builder context**. It includes the intended case mechanisms and demonstration narrative. Keep it separate from the historical investigator’s runtime inputs and the blind reviewer’s evidence bundle. Those components receive the scoped mission and admissible evidence defined by the application, so their performance can be evaluated meaningfully.

When a technical choice requires judgment, prioritize the choice that makes the completed analysis more useful, financially sound and understandable to Russell and Slope.

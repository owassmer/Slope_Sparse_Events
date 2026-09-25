# Jev Should Become Slope Sparse Events’ Semantic Judgment Plane, Not a Sidecar

## Executive judgment

The repository is in a strong place to open Module Four. The first three implementation steps have established the right hard boundaries: a scoped and auditable runtime, historically isolated evidence retrieval, and deterministic financial mechanics. The central design problem now is **not** “where can we call Jev?” It is:

> **How should semantic judgment flow through the system so the agent, Jev, and deterministic engine each do the kind of work they are best at—and so a credit reviewer can see exactly how evidence changed the financing decision?**

The answer is to make Module Four a **semantic judgment layer** between evidence retrieval and the financial engine.

The desired architecture is:

```text
                      ┌──────────────────────────┐
                      │       CREDIT REVIEWER    │
                      │  understands + decides   │
                      └────────────┬─────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────┐
│                     AGENT / RESEARCH DIRECTOR                │
│                                                              │
│ Frames decision-relevant unknowns                            │
│ Searches intelligently                                      │
│ Proposes atomic findings                                     │
│ Reconciles contradictions                                    │
│ Connects supported facts to economic mechanisms              │
│ Chooses what to investigate next                             │
└───────────────┬─────────────────────────────┬────────────────┘
                │                             │
                ▼                             ▼
┌──────────────────────────┐       ┌───────────────────────────┐
│ JEV / SEMANTIC JUDGMENT  │       │ HOST / CONTROL PLANE      │
│                          │       │                           │
│ Is this relevant?        │       │ Snapshot isolation        │
│ Who does it apply to?    │       │ Provenance + hashes       │
│ Alleged or completed?    │       │ Question selection        │
│ Required or disputed?    │       │ Minimal state building    │
│ Does evidence support it?│       │ Cache + budgets           │
│ Is context sufficient?   │       │ Versioning + event log    │
│ Do statements conflict?  │       │ Security boundaries       │
└───────────────┬──────────┘       └─────────────┬─────────────┘
                │                                │
                └──────────────┬─────────────────┘
                               ▼
                 ┌─────────────────────────────┐
                 │ DETERMINISTIC FINANCE ENGINE│
                 │                             │
                 │ Dates                       │
                 │ Dollars                     │
                 │ Contract schedules          │
                 │ Double-count protection     │
                 │ Cash conservation           │
                 │ Scenario sensitivities      │
                 │ Offer feasibility           │
                 │ Loan cash flows             │
                 │ Capital requirement         │
                 └─────────────────────────────┘
```

That separation is unusually well aligned with both the repository’s existing design and Slope’s own published architecture. The repo explicitly says the deliverable is **a financing decision plus the loan’s dated cash flows, not a litigation summary**; it assigns interpretation to the agent/Jev and arithmetic to deterministic code. fileciteturn17file0L2-L2 Slope’s own 2026 description of its internal agents platform similarly says its credit-review agent prepares the work while humans make the lending decision, that agents are read-only observers, that execution is traceable, and that the harness—not the model—controls tools, context, guardrails, and observability. citeturn12search0

The most important recommendation is therefore:

> **Do not implement Module Four as “Claude occasionally calls `judge_evidence` when it feels useful.”**
>
> Implement it as a structured investigation graph in which Jev systematically sits at the semantic boundaries where retrieved text becomes a finding, a finding becomes an economic effect, or conflicting evidence forces further research.

This gets materially more value from Jev **without giving Jev more authority than it should have**.

That distinction matters. “Use Jev more” should **not** mean making it determine dollar adjustments, litigation probabilities, default probabilities, payment dates, or final credit actions. TypeSafe explicitly positions Jev as a System One model for focused judgments and recommends splitting complex reasoning into atomic questions whose outputs are composed in code. Its current documentation warns that Jev 1.13 is weak at mathematical precision, date comparisons, long irrelevant state, multi-hop indirection, and generation. citeturn13view0turn13view2

The best Module Four is therefore one in which:

**Claude thinks broadly. Jev judges narrowly. Code computes exactly. The host controls everything. The reviewer can see the causal chain.**

## What the repository already establishes—and where the current Jev design falls short

### The first three modules created the right substrate

The repository’s builder instructions are clear about the product: this is a Russell/Slope proof of concept for a credit reviewer considering incremental working-capital exposure. The unusual lawsuit or external event is valuable only insofar as it changes borrower cash capacity, loan collections, feasible financing structures, or capital impact. The repo also explicitly defines several unacceptable failure modes: wrong entity, future-information leakage, fabricated payment dates, treating demanded amounts as paid, overstating cash, double-counting obligations, converting unknowns to zero, and using Jev confidence as a probability. fileciteturn17file0L2-L2

That is exactly the right philosophy.

The current `main` repository contains implemented `app/agent`, `app/evidence`, `app/domain`, and `app/finance` layers, while the later decision/export/web layers from the intended layout have not yet been built. fileciteturn2file0L2-L2 This is useful because Module Four can still establish the data model that later decision and UI modules consume rather than having to retrofit semantic traceability after decisions have already been implemented.

The merged work also gives Module Four unusually good foundations:

**Runtime and isolation.** The live smoke test established subscription-authenticated Claude execution, a scoped custom tool path, structured output, and a real Jev call. The PR record candidly notes that the exact Jev build-identity discrepancy, direct TypeSafe route, and final reviewer isolation were not fully resolved in Step One. fileciteturn14file0L5-L5

**Evidence.** `EvidenceStore` now exposes a read-only, snapshot-bound search/read interface. The model cannot pick an arbitrary database path or historical cutoff, out-of-snapshot IDs receive a non-revealing unavailable error, FTS search is bounded, and full sections/tables can be read after retrieval. fileciteturn23file0L2-L2 The independent Step Two review caught and repaired material parser and metadata-leakage issues before passing, which is exactly the sort of discipline Module Four should retain. fileciteturn15file0L5-L5

**Finance.** The financial core already enforces critical invariants that should never be delegated to an LLM: dated opening cash, unknown-vs-zero handling, recurring-stream coverage, unique obligation handling, non-cash exclusion, funding routes, reserve breaches, and deterministic cash arithmetic. fileciteturn24file0L2-L2 The Step Three review also surfaced and repaired four material errors—including missing Account Credits silently becoming zero and duplicate obligations—and specifically left the operating-receipts/Account-Credits linkage and pre-debt payment-capacity allocation for the next financial integration step. fileciteturn16file0L48-L49

This means the hard part of Module Four should **not** be another round of financial logic. It should establish the bridge from sparse evidence to validated economic effects.

### The current question registry is thoughtful but too “sidecar-shaped”

The existing `question_registry.json` already has good judgment boundaries. It distinguishes entity scope, claim posture, proposed-finding support, cash restrictions, activity restrictions, obligation status, offset support, next-source routing, and candidate relevance. It also contains excellent global rules: atomic claims, no remembered facts, source authority separated from claim posture, no automatic conversion of confidence to facts or credit decisions, and code ownership of dates and arithmetic. fileciteturn9file0L2-L2

The problem is not that those questions are bad.

The problem is that **the architecture around them makes Jev look like an optional tool the agent visits rather than a first-class semantic layer**.

The current contract describes `judge_evidence` as an analysis tool alongside `record_finding`, `record_gap`, `propose_patch`, `run_scenarios`, and `compare_offers`; the agent is allowed to choose when to call it. fileciteturn13file0L2-L2 That is flexible, but it makes several weaknesses likely:

1. **Jev usage can become opportunistic.** Two otherwise identical agent runs can use it on very different claims, which weakens both product consistency and the later agent-plus-Jev versus agent-only experiment.

2. **The most valuable place for Jev is earlier than the current emphasis suggests.** Jev can improve the step between FTS retrieval and the agent reading evidence—not merely validate a proposition after the agent has already found and understood it.

3. **All current registry questions are categorical Choice judgments.** Choice is appropriate for many of them, but TypeSafe also exposes Noul specifically for absolute yes/no judgments where the returned probability is itself useful. citeturn13view0 The retrieval-triage problem is almost tailor-made for it.

4. **The current design lacks explicit semantic quality gates on the agent itself.** There is no strong, first-class concept of “is this finding atomic?”, “is enough context present to interpret this passage?”, or “are these two apparent contradictions really about the same entity/period/obligation?”

5. **The evidence-to-decision causal chain is not yet a data model.** We have sources, Jev judgments, findings, gaps, patches, and future scenarios, but the UI and evaluator will be much stronger if these become edges in one immutable graph.

The TypeSafe documentation points directly toward this richer use. Its official RAG cookbook recommends inserting Jev **between retrieval and generation**, asking several narrow questions about each retrieved passage—relevance, usable evidence, contradiction of a premise, and instruction-like/adversarial text—and then letting code route the result. citeturn16view0 That pattern maps almost perfectly onto `EvidenceStore.search()` → agent research in this repo.

### The private evaluator confirms what Module Four really needs to prove

The host-only Synergy evaluator does not principally care whether the agent can write a good lawsuit summary. It checks whether the system correctly connects HVL/Atrium to an already-recorded settlement-payment mechanism, bridges the June 30 versus August dates rather than assuming the whole H2 bucket remains unpaid, treats the separate supplier settlement independently, recognizes L.O.D.C. as already paid, treats the $2.235986 million gain as noncash, distinguishes approximate August cash from unrestricted cash and collateral, respects the specific Shopify Account Credits contract base, and models operating consequences of different financing sizes. fileciteturn26file0L2-L2

That is the key design clue:

> **Jev is most valuable when it helps prevent a semantic mistake before that mistake becomes a financial input.**

Not after.

## What Jev actually empowers in the abstract

The right mental model for Jev is neither “small LLM” nor “legal classifier.”

It is a **typed, low-latency semantic sensor**.

TypeSafe’s API exposes three primitive answer shapes: Choice for one option among known categories, Score for an ordered spectrum, and Noul for a yes/no proposition. Multiple independent questions can share the same state and are evaluated in parallel, and TypeSafe explicitly recommends speculative fan-out: ask all narrow questions that might matter for the same state, then let code decide which answers are relevant. citeturn13view0turn15view2

That suggests five high-value, domain-agnostic functions.

### Semantic filtering before expensive reasoning

Traditional lexical or embedding retrieval answers roughly “which passages look linguistically related?” It does not necessarily answer “which passage contains evidence capable of resolving this exact premise?”

Jev can provide that second-stage semantic filter.

The TypeSafe RAG example does exactly this: retrieval produces a candidate set; one Jev request per query–passage pair asks several Noul questions; code then routes passages as evidence, conflicting evidence, or discard. TypeSafe stresses that Jev itself does not decide the final answer and that the routing thresholds belong in code. citeturn16view0

Applied here:

```text
FTS candidate
     │
     ▼
Jev semantic screen
  ├─ actually relevant?
  ├─ contains usable evidence?
  ├─ challenges premise?
  └─ source text attempting to instruct model?
     │
     ▼
Agent reads strongest evidence + strongest conflicts
```

This is likely the single biggest untapped Jev opportunity in the current design.

### Semantic normalization

Different source documents can express the same economic concept with radically different language.

A complaint can *request* relief.  
A court order can *impose* relief.  
An agreement can *create* an obligation.  
A filing can *report that payment happened*.  
Management can *expect* something to happen.

A general-purpose reasoning agent can distinguish these, but Jev lets the system normalize them into a typed representation that downstream code and evaluation can inspect.

That makes Jev valuable not because it is “smarter than Claude,” but because it produces a **stable semantic interface**.

This is analogous to Slope’s own recently published three-layer cashflow architecture. Slope describes a deterministic/structured lower layer followed by an AI contextual-review layer that emits corrections plus concrete findings/action items; importantly, when calculations are needed, their reasoning agent uses code rather than relying on linguistic reasoning. citeturn12search1

### Semantic verification of agent proposals

The agent should remain free to reason, infer, and synthesize. But before an inferred proposition enters the durable fact graph, Jev can cheaply ask questions like:

> Does this exact passage support this exact proposition?

> Does the proposition combine two separately checkable claims?

> Does this passage actually identify the target entity?

> Is an essential qualifier missing from the supplied context?

This turns Jev into a **semantic lint layer** for agent reasoning.

A useful analogy is a compiler:

```text
Agent draft finding
       │
       ▼
semantic type-check
       │
       ├─ valid → durable finding
       │
       ├─ ambiguous → read more context
       │
       └─ conflicting → reconciliation task
```

It cannot prove the proposition is globally true. It can greatly reduce the probability that unsupported wording quietly becomes a model input.

### Contradiction and scope detection

Sparse-event underwriting is full of apparent contradictions that are not really contradictions:

- one statement concerns a subsidiary, another the parent;
- one is June 30, one August 12;
- one describes a requested freeze, another an imposed restriction;
- one describes an outstanding claim, another a later satisfied obligation;
- one concerns manufacturing capacity, another actual realized sales.

Jev is well suited to identify the **semantic relation** between narrow statements, while code owns chronology and amounts.

That distinction is important because TypeSafe warns that Jev 1.13 should not be trusted for date ordering and recommends putting date arithmetic and comparisons in code. citeturn15view0

### Ambiguity sensing

Choice outputs include their full option distribution and a derived confidence value; TypeSafe describes a flatter distribution as a sign that no option is clearly dominant and recommends calibrating any behavior thresholds on the application’s own data. citeturn15view1

For Slope Sparse Events, this uncertainty signal is useful—but in a very specific way.

It should mean:

> “This semantic interpretation is ambiguous; perhaps retrieve more context, reconcile another source, or surface a human question.”

It must **not** mean:

> “There is a 72% chance the lawsuit causes a payment.”

And it must never mean:

> “Apply a 72% cash-flow haircut.”

The repo already prohibits that conversion. fileciteturn11file0L2-L2

A powerful later routing heuristic is therefore:

```text
research priority
    ≈ deterministic decision sensitivity
      × semantic ambiguity
      × feasible resolvability
```

Here, **decision sensitivity comes from the financial engine**, semantic ambiguity can come from Jev, and feasible resolvability comes from the known evidence/action set. The expression is a queue heuristic, not a lending score and not a probability model.

### More Jev does not mean every Jev primitive

I would **not** force Score into Module Four merely to demonstrate the full TypeSafe API.

Score is best when there is a genuinely ordered semantic spectrum. The key sparse-event judgments here are mostly binary or categorical. TypeSafe itself says to select the primitive whose output shape matches the decision, and Jev 1.13’s documentation specifically cautions against using Score interpolation for numeric magnitude. citeturn13view0turn15view0

The intelligent expansion is therefore:

- **Choice** for categorical semantic state;
- **Noul** for absolute filters and proposition tests;
- **Score** only when a later, well-labeled ordered judgment genuinely emerges.

That is “getting more out of Jev” by using it more correctly, not ornamentally.

## The Module Four architecture I recommend

### Make the investigation itself a typed graph

Module Four should establish a durable graph:

```text
Mission
  │
  ├── DecisionDependency / Gap
  │       │
  │       ├── Search
  │       │     └── EvidenceCandidate
  │       │              │
  │       │              └── Jev semantic screen
  │       │
  │       ├── SourceRead
  │       │     └── Jev interpretation
  │       │
  │       └── AtomicFinding
  │               │
  │               ├── semantic observations
  │               ├── source spans
  │               └── reconciliation links
  │
  └── EconomicEffectProposal
          │
          ├── target stream
          ├── mechanism
          ├── parameter requirements
          ├── baseline overlap
          ├── linked effects
          └── unresolved parameters
                  │
                  ▼
             Finance engine
```

This graph should be the same thing consumed by the final packet, independent reviewer, UI, and evaluator. Slope’s own internal platform philosophy is notably similar: its thread captures messages, tool calls, cost metadata, active versions, and other execution events in one source of truth used for debugging and quality control. citeturn12search0

The key new domain models should be conceptually:

| Model | Purpose |
|---|---|
| `DecisionDependency` | One unknown premise tied to a specific financial quantity or action that might change. |
| `EvidenceCandidate` | One snapshot-admissible retrieved passage/table associated with a dependency. |
| `JevCallRecord` | One physical provider request: version, state hash, source hashes, usage, raw response. |
| `SemanticObservation` | One answer within that call, tied to a versioned question. |
| `AtomicFinding` | One agent-authored proposition with exact evidence and semantic-observation references. |
| `ReconciliationTask` | An explicit unresolved relationship among findings. |
| `EconomicEffectProposal` | The bridge from accepted finding to target financial stream and required parameters. |
| `InvestigationEvent` | The append-only event that powers UI/debugging: search, read, judgment, finding, effect, calculation, decision impact. |

That last object is important: do **not** wait until the final UI module and then attempt to reconstruct an investigation trace from logs.

Record the trace correctly now.

### Change `judge_evidence` from “pick questions” to “request a judgment profile”

The agent should not normally supply arbitrary question IDs and handcrafted Jev state.

Instead, its call should look conceptually like:

```json
{
  "profile": "claim_interpretation",
  "finding_id": "finding_017",
  "evidence_id": "synergy_s1a_20240813#s...."
}
```

or:

```json
{
  "profile": "candidate_screen",
  "gap_id": "gap_settlement_bridge",
  "candidate_ids": ["...", "...", "..."]
}
```

The **host** should then:

1. resolve the referenced immutable objects;
2. construct the smallest semantically necessary state;
3. select the correct versioned questions;
4. call Jev;
5. log the physical call once;
6. materialize separate semantic observations;
7. return a concise typed result.

That change has four benefits.

First, it prevents the primary agent from accidentally stuffing irrelevant context into Jev. TypeSafe explicitly warns that unrelated state degrades Jev accuracy and recommends retrieving/filtering first. citeturn15view0

Second, it makes Jev usage reproducible across agent runs.

Third, it makes agent-plus-Jev versus agent-only evaluation much cleaner.

Fourth, it lets question engineering evolve without changing the agent’s conceptual tool interface.

### Use several purpose-specific judgment profiles

I recommend four profiles.

**`candidate_screen`** runs over each FTS candidate and is where the biggest new Jev usage occurs.

**`claim_interpretation`** runs after the agent reads a passage and proposes one atomic claim.

**`finding_check`** validates a proposed durable finding before acceptance.

**`statement_relation`** handles evidence reconciliation.

This is better than a single giant semantic request because TypeSafe’s own guidance says to minimize irrelevant state and make each judgment a quick, focused System One decision. citeturn13view0turn15view0

Within one profile, however, **fan out aggressively**: if five independent questions use the same passage and claim state, send all five in the same request rather than making the coding-agent-style mistake of one request per question. TypeSafe explicitly recommends that pattern. citeturn15view2

### Let the host pick the next *semantic operation*; let the agent pick the research strategy

There is an important boundary here.

I would **demote `next_source` from being a centerpiece of the Jev demo**.

In this fixed historical corpus, “court order vs executed agreement vs financial statement” is often something the agent can route easily from the known source catalog, and it risks making Jev look like a glorified switch statement.

The more impressive and genuinely useful demonstration is:

> “The search returned eight passages. Jev identified two as direct evidence, one as contradicting the premise, and five as context-only. The agent then read the direct evidence and conflict, proposed a narrower finding, and the finance layer discovered that one missing bridge amount remains decision-pivotal.”

That is a coherent collaboration among retrieval, Jev, Claude, and deterministic code.

### Make every Jev answer earn its existence

Add a `downstream_disposition` to semantic observations:

```text
used_in_finding
caused_more_context_read
caused_research_redirect
flagged_conflict
challenged_agent_draft
overridden_by_agent_with_reason
unused
```

This creates what I would call a **Jev contribution ledger**.

By Module Seven, you can answer Russell’s implicit question—“what did Jev actually add?”—with evidence:

- prevented an allegation from becoming a liability;
- filtered irrelevant passages;
- caught a scope mismatch;
- triggered a second read;
- disagreed with an agent proposal;
- did nothing material on this case.

That is vastly more credible than showing an aggregate count of Jev calls.

### Keep authority asymmetric

The acceptance flow should be:

```text
Jev judgment
     ↓
semantic observation
     ↓
agent accepts / reconciles / rejects with reason
     ↓
finding
     ↓
host structural validation
     ↓
effect proposal
     ↓
deterministic engine validation
```

A high-confidence Jev answer should **never automatically activate a cash-flow adjustment**.

That would violate both the repo’s invariants and the model’s intended role. fileciteturn11file0L2-L2 citeturn15view1

### Fix provider provenance before using Jev in an evaluation claim

There is one Module-One debt worth closing now.

The repo’s config pins canonical `jev-1.13.0`, but the current provider abstraction defaults to OpenRouter while direct TypeSafe signup is constrained, and the smoke test noted a returned model-build identity mismatch. fileciteturn19file0L2-L2 fileciteturn14file0L5-L5 TypeSafe’s current official models page lists `jev-1.13.0` as the stable versioned ID and specifically warns that aliases can move; it recommends logging the returned version and pinning versioned IDs when behavior matters. citeturn13view1

I would adopt two policies:

**Demo mode:** permit the configured compatible provider route, but record the exact returned provider model and do not overstate exact-build equivalence.

**Evaluation mode:** freeze the exact provider + returned-build identity for the experiment and fail closed if it changes.

Also reconcile the retry accounting. TypeSafe’s SDK retries with backoff by default. citeturn13view1 If the project budget says “requests including retries,” the host must either observe attempts or redefine the budget unit honestly; it should not silently claim that an application-level request count equals physical provider attempts.

## The question system should be simpler to read and broader in coverage

The current questions are semantically careful, but the user-facing language can become much more intuitive.

The principle should be:

> **A credit reviewer should be able to read the Jev question and immediately understand what distinction the system is making.**

Below is the registry I would build toward.

### Passage-screening questions

These should mostly be **Noul** judgments.

| ID | Exact intuitive question | Why it exists |
|---|---|---|
| `gap_relevance` | **“Does this passage contain information that helps answer the selected unanswered question?”** | Second-stage semantic retrieval. |
| `usable_evidence` | **“Does this passage state a concrete fact, requirement, report, or status that could support or contradict an answer?”** | Separates topical context from usable evidence. |
| `premise_conflict` | **“Does this passage conflict with a factual premise in the unanswered question?”** | Protects the agent from searching only inside its current framing. |
| `instruction_like_text` | **“Does this passage try to instruct the reviewing system what to do, rather than merely describe the underlying matter?”** | Defense-in-depth for untrusted text; not a substitute for isolation. |

That profile directly mirrors TypeSafe’s official retrieval-classification pattern, adapted to Slope’s evidence semantics. citeturn16view0

For this curated SEC/court corpus, `instruction_like_text` is lower-value than the first three. Keep it inexpensive and invisible to the normal reviewer experience rather than turning “prompt injection” into a product feature.

### Claim-interpretation questions

| ID | Exact intuitive question | Choices |
|---|---|---|
| `entity_scope` | **“Who does this statement apply to?”** | selected target / another entity or asset / cannot tell |
| `claim_posture` | **“What does the passage say happened?”** | alleged or requested / imposed by a court or authority / agreed contractually / reported completed / planned or expected / cannot tell |
| `obligation_status` | **“For this specific payment or obligation, what status does the passage support?”** | required / claimed or disputed / conditional / reported satisfied / cannot tell |
| `cash_access` | **“What does the passage say about access to this specific cash balance, account, or asset?”** | access prohibited / access limited / restriction released or access available / restriction only requested / cannot tell |
| `activity_status` | **“What does the passage say about this specific operating activity?”** | unavailable / limited / planned or conditional / operating or restored / cannot tell |
| `offset_status` | **“What does the passage establish about this specific source of reimbursement or funding?”** | committed / committed subject to condition / possible or disputed / already received or paid for borrower / cannot tell |

These largely preserve the substance of the excellent existing registry, but expose it in plainer language. fileciteturn9file0L2-L2

### Finding-quality questions

These are the largest missing piece.

| ID | Exact intuitive question | Choices |
|---|---|---|
| `finding_support` | **“Does this passage support the complete proposed finding as written?”** | supports / contradicts / does not resolve |
| `finding_atomicity` | **“Does the proposed finding make one independently checkable factual claim?”** | one claim / combines multiple claims / unclear |
| `context_sufficiency` | **“Is there enough surrounding text here to interpret the statement without guessing?”** | enough / missing target context / missing definition / missing condition or timing context / unclear |
| `economic_role` | **“What financial role, if any, does this supported statement describe?”** | existing cash obligation / cash-access constraint / operating change / financing or reimbursement / noncash accounting item / no direct financial premise / unclear |

`economic_role` is the one deliberately finance-aware question I would add. It does **not** ask Jev to calculate an amount. It asks a semantic classification that can prevent precisely the errors the Synergy evaluator is designed to catch: a noncash gain becoming cash, a disputed claim becoming debt service, or an offset being treated as unrestricted liquidity. The private evaluator makes those distinctions explicitly important. fileciteturn26file0L2-L2

### Reconciliation questions

| ID | Exact intuitive question | Choices |
|---|---|---|
| `statement_relation` | **“How do these two statements relate to the same proposed fact?”** | agree / conflict / concern different entities, assets, periods, or obligations / cannot tell |
| `baseline_overlap` | **“Do this proposed economic effect and this baseline item describe the same underlying cash obligation or event?”** | same / distinct / cannot tell |

`baseline_overlap` should be a **warning signal only**. Deterministic IDs and host validation retain final authority over double counting. Jev 1.13’s own documentation says structural invariants belong in code. citeturn15view0

### What the Synergy hero flow should look like

The lead demo should visibly produce several moments where the components complement rather than duplicate one another.

**HVL/Atrium.** The agent searches the dispute and settlement. Jev helps establish that the relevant passage concerns the right entity/obligation and reports an existing contractual payment mechanism rather than a new hypothetical liability. The finance layer knows the H2 2024 payment bucket is $2 million, but the system preserves the missing July-to-August payment bridge instead of treating the entire June 30 bucket as unpaid on August 13. That date/amount discipline is already required by the design contracts. fileciteturn18file0L2-L2

**March supplier settlement.** The same architecture identifies a distinct $600,000 H2 payment bucket and keeps it separate from HVL. Again, any intervening payments remain unknown rather than silently zero. fileciteturn18file0L2-L2

**Court-language trap.** A passage describing requested relief should visibly become **“allegation/request—not an imposed payment obligation.”** This is an ideal Jev demonstration because it is exactly a fast semantic distinction, and it prevents a potentially catastrophic downstream error.

**L.O.D.C.** A source reports the separate settlement as paid in May 2024. Jev’s obligation-status judgment should classify that specific obligation as reported satisfied; the agent can then propose “no new future cash outflow for this already-paid matter.” The private evaluator expressly checks for this. fileciteturn26file0L2-L2

**The $2.235986 million gain.** Jev can classify its economic role as a noncash accounting item; code therefore never inserts it into a borrower cash stream. The contract design already says it may be removed from relevant normalized FY2023 metrics but not charged against cash or removed again from H1 2024. fileciteturn18file0L2-L2

**Approximate August cash.** The agent finds management’s approximately $2 million August 12 cash statement. The host preserves the approximation and does not silently equate it with verified unrestricted bank cash. fileciteturn20file0L2-L2

**WebBank/Shopify.** Jev correctly recognizes contractual requirements in the executed agreement; the deterministic engine applies the 25% Account Credits logic and the two separate six-month minimum-payment windows. The actual effective funding date and actual Account Credits/remittances remain missing inputs, so no one invents a calendar schedule. fileciteturn21file0L2-L2

That is a compelling demo because the viewer sees that Jev did not “predict lawsuit risk.” It repeatedly prevented **category errors** at the exact points where category errors would distort finance.

## The frontend should be a credit workbench, not a chat transcript or lawsuit dashboard

The frontend hierarchy should reflect the actual job:

> **What should I fund, why, and what cash comes back?**

The repo already says that is the product. fileciteturn17file0L2-L2

I would bring a **thin investigation viewer forward into Module Four** rather than waiting until the later review/UI module. Module Six can still own the polished final credit-decision experience. Module Four needs enough interface to make the semantic pipeline observable and debuggable now.

That is also consistent with Slope’s own platform philosophy: structured UI artifacts, traceable execution, and an agent that prepares evidence and analysis for human review rather than taking lending actions itself. citeturn12search0

### Primary screen hierarchy

Conceptually:

```text
┌────────────────────────────────────────────────────────────────────┐
│ Synergy CHC Corp.      Review date Aug 13, 2024      Investigating │
│ Incremental working-capital financing                              │
└────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────┐ ┌────────────────────────────────────┐
│ CURRENT DECISION STATE       │ │ PIVOTAL UNRESOLVED INPUT           │
│ Not yet determined           │ │ Post-June settlement payments      │
│ 3 structures to compare      │ │ Could change available cash        │
└──────────────────────────────┘ └────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ WHAT CHANGED FROM THE BASELINE                                     │
│                                                                    │
│ Existing HVL settlement payments       Existing obligation         │
│ Separate supplier settlement           Existing obligation         │
│ L.O.D.C. settlement                    Reported satisfied           │
│ 2023 settlement gain                   Noncash; no cash impact      │
│ Shopify/WebBank loan                   Account-credit-linked debt   │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ BORROWER CASH CAPACITY                                             │
│ [dated chart — baseline / event-adjusted / selected structure]      │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ LOAN CASH FLOWS                                                    │
│ [contractual vs conditional collections — deliberately separate]   │
└────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── Investigation ──────────────────────────┐
│ Why are we checking July–August settlement payments?               │
│   Search → evidence → Jev interpretation → finding → model effect  │
└────────────────────────────────────────────────────────────────────┘
```

The investigation should be **secondary but one click away**, because the principal user is deciding a financing action, not auditing a model execution log.

### Make Jev visible through meaning, not model mechanics

A normal credit reviewer should see:

> **Jev interpretation: Contractual obligation**

> “The passage describes a payment obligation agreed by the parties.”

or:

> **Jev interpretation: Allegation only**

> “The passage describes requested relief, not an imposed payment requirement.”

They should **not** be confronted first with:

> `claim_posture = allegation_or_request, p=.937, confidence=.884`

The raw distribution, model ID, question version, request hash, cache state, and cost should be under an expandable “Judgment details” panel.

This is not hiding uncertainty. It is putting the information at the right cognitive layer.

Microsoft Research’s human-AI interaction guidance specifically recommends making clear what the AI can and cannot do, exposing contextually relevant information, supporting efficient correction, scoping the system when uncertain, and making clear why the system behaved as it did. citeturn17search5

### Do not turn Jev confidence into a traffic-light risk score

There should be no big red/amber/green “Jev confidence” indicator next to a borrower.

The distinction to communicate is:

```text
Source fact          → what the document states
Jev interpretation   → how a narrow semantic question was classified
Agent finding        → the proposition accepted for analysis
Assumption           → an operator-supplied value, not a sourced fact
Engine result        → deterministic consequence under those inputs
```

Those should have different visual tokens and text labels.

Color may reinforce the state, but it should never be the only distinction. WCAG 2.2 explicitly requires that color not be the sole means of conveying information or status. citeturn17search2turn17search8

### Every effect card should answer “so what?”

A finding card should not end at:

> “The court document is an allegation.”

It should connect through to:

> **Model consequence:** No cash payment added from this passage.

Likewise:

> “L.O.D.C. is reported satisfied.”  
> **Model consequence:** No future L.O.D.C. settlement stream.

> “HVL has $2.0m in the H2 schedule as of June 30.”  
> **Model consequence:** Potential existing-debt cash outflow; remaining Aug–Dec amount unresolved until post-June payments are bridged.

> “$2.235986m is a noncash settlement gain.”  
> **Model consequence:** May alter normalized historical operating metrics; never enters cash ledger.

This makes the semantic layer legible to Russell.

### Operator correction should be first-class

For any material proposition, the reviewer should be able to choose among a very small set of actions:

**Accept for analysis**  
**Keep unresolved**  
**Provide fact or assumption**

An override should require a concise note and become another immutable investigation event.

This is better than an unrestricted “edit AI answer” textbox because it preserves provenance and makes the evaluation legible.

### Do not expose private chain-of-thought

“Traceability” should mean structured observable actions:

- question being investigated;
- query made;
- evidence returned;
- Jev question and result;
- finding recorded;
- effect proposed;
- calculation changed;
- decision impact.

It should not mean rendering internal model reasoning tokens.

That is also closer to Slope’s own described execution-log approach, which records tool calls, configuration versions, cost, and other observable execution events. citeturn12search0

## The remaining modules should be re-planned around this graph

### Module Five should become the decision engine, not just “more scenario math”

Module Five should consume the immutable findings/effects graph from Module Four.

Its core abstraction should be a **matched scenario pair**:

```text
same base profile
same opening observation
same policy
same requested use
same common assumptions
             │
             ├── baseline scenario
             │
             └── event-adjusted scenario
                    only explicit effect IDs differ
```

That makes “before versus after” auditable rather than merely visually comparable.

The module must also finish the two material integration items explicitly deferred by the Step Three review: link the Account Credits that drive the merchant sweep to the same economic receipts model used by the borrower ledger, and calculate payment capacity **before** debt service under an explicit allocation rule. fileciteturn16file0L48-L49

I would require each scenario result to expose distinct series for:

```text
Borrower operating cash
Existing contractual debt service
Existing conditional merchant collections
New-loan contractual payments
Conditional new-loan collections, if any
Fees
Reserve
Available payment capacity
Closing cash
```

Do not conflate them into one “cashflow.”

A candidate financing action must also modify the operating plan when appropriate. The private Synergy evaluator explicitly says that declining or shrinking inventory funding can change future receipts; therefore “smaller loan = mechanically safer” is not a valid general rule. fileciteturn26file0L2-L2

The action comparison should therefore support causal action-conditioned streams:

```text
$250k purchase
    → more inventory
    → corresponding modeled receipts/costs
    → financing payment schedule

$100k purchase
    → smaller inventory plan
    → different receipts/costs
    → smaller financing payment schedule
```

This is one of the most important insights in the whole project.

A conditional offer should not say “approve subject to monitoring.” It should say:

> **Missing fact:** amount of HVL settlement payments made July 1–August 12.  
> **Acceptable evidence:** bank statement or creditor statement covering that interval.  
> **If remaining obligation ≤ X:** structure A remains feasible.  
> **If remaining obligation > X:** structure B or no new exposure.  

That turns uncertainty into an actionable lending workflow.

### Module Six should review the graph, not just the memo

The independent reviewer should receive an immutable packet containing:

```text
sources
  → semantic observations
    → findings
      → effects
        → assumptions
          → scenario calculations
            → compared actions
              → proposed decision
```

A review issue should point to a node or edge:

- unsupported finding;
- wrong entity scope;
- incorrect posture;
- missing contradictory source;
- duplicate economic effect;
- baseline-overlap error;
- unjustified parameter;
- calculation mismatch;
- decision inconsistent with scenario results.

That is far more useful than “the memo seems wrong.”

The current config already envisions an immutable final packet, blind reviewer isolation, one repair cycle, and an `UNDETERMINED` outcome when a pivotal input remains unresolved rather than treating infrastructure failure or uncertainty as a decline. fileciteturn12file0L2-L2 fileciteturn13file0L2-L2

Module Six should also finish the polished interface:

```text
Decision
↓
Binding constraint / pivotal missing fact
↓
Offer comparison
↓
Borrower cash
↓
Loan collections
↓
Capital / marginal funding need
↓
Economic changes from baseline
↓
Investigation trace
↓
Independent review
↓
Outcome reveal after lock
```

The outcome reveal must remain physically inaccessible until the candidate packet is locked; this is not just an interface convention. The repo’s evaluator file explicitly says it is host-only and must never be mounted or serialized to either the investigator or blind reviewer. fileciteturn26file0L2-L2

### Module Seven should evaluate Jev’s contribution, not merely whether final answers differ

The existing agent-plus-Jev versus agent-only design is good: the same main model, evidence, finance tools, reviewer treatment, and comparable budgets should be preserved, while the Jev tool and registry are removed from the control arm. fileciteturn12file0L2-L2

But final lending decision agreement is an insufficient metric.

The comparison should measure at least:

| Dimension | What it reveals |
|---|---|
| Critical semantic errors | Wrong entity, allegation→liability, paid→future payment, noncash→cash. |
| Supported-finding quality | Are durable findings actually supported by admissible evidence? |
| Effect quality | Correct mechanism, stream, baseline treatment, parameter requirements. |
| Pivotal-gap discovery | Did the agent identify the unknown that can actually change the action? |
| Research efficiency | Evidence reads, agent turns, redundant searches, time. |
| Jev intervention utility | Which judgments changed research, corrected a finding, or flagged conflict? |
| Agent/Jev disagreement | How often did Claude override Jev, and was that override justified? |
| Cost | Jev requests, tokens, latency, provider cost. |
| Decision consistency | Does the final recommendation follow the modeled cash results? |

The **Jev contribution ledger** proposed above is what makes this evaluation possible.

It also protects the project against a perfectly legitimate outcome:

> Jev may not change the final financing recommendation on a given borrower, while still materially improving evidence discipline, reducing semantic errors, or shortening research.

That is an honest and valuable result.

With only Synergy and Barfresh, the demo should not claim statistical superiority. It should show mechanism-level evidence of contribution and transfer. The Barfresh evaluator is deliberately different: it tests production/inventory effects, replacement capacity versus completed production, litigation-funding semantics, and avoiding duplicate payables/facility effects. fileciteturn26file0L2-L2 That is a good transfer test for whether the architecture is genuinely sparse-event general rather than Synergy-specific.

### Concrete handoff for the coding agent

I would give the implementation agent the following target—not as an invitation to rebuild everything, but as the minimum architecture that makes the later modules coherent.

| Area | Recommended change |
|---|---|
| `app/domain/` | Add investigation models: dependency, candidate, semantic observation, atomic finding, reconciliation task, effect proposal, event. |
| `app/agent/jev.py` | Support `Choice` **and `Noul`;** split physical call records from per-question answers; harden provider/model provenance; preserve raw response once per request. |
| `app/agent/jev_profiles.py` | New host-owned mapping from judgment profile → questions + minimal-state builder. |
| `question_registry.json` | Version to a new registry with passage-screen, interpretation, finding-quality, and reconciliation questions. |
| `app/agent/tools.py` | Scoped MCP handlers resolve object IDs against the run; agent never supplies filesystem path, snapshot path, cutoff, or arbitrary Jev state. |
| `app/agent/investigation.py` | Main adaptive research loop and progress accounting. |
| `app/evidence/` | Keep existing FTS; add the semantic-screen adapter above search results rather than replacing retrieval. |
| `app/finance/` | Expose effect validation and existing limited sensitivity hooks; do not move arithmetic into Module Four. |
| `app/web/` | Add a **thin** investigation/debug viewer now; keep full decision workbench for Module Six. |
| `evals/` | Add hand-labeled Jev semantic boundary cases from both borrowers plus small synthetic edge cases. |
| tests | Protect isolation, exact state construction, question-version/cache behavior, source→finding provenance, and “Jev never determines arithmetic/date ordering.” |

The semantic eval file is especially important. TypeSafe explicitly says confidence thresholds are use-case dependent and should be tested on the system’s own data. citeturn15view1 Before any automatic confidence gate exists, the repository should contain labeled examples for entity mismatch, ambiguous entity, allegation versus agreement, disputed demand versus required payment, satisfied obligation, noncash item, combined claim, missing qualification, and same-versus-distinct obligation.

I would revise the practical Module Four exit condition to:

> **From only the admissible dated snapshot and locked baseline, the agent identifies decision-relevant gaps; Jev systematically screens retrieved evidence and checks narrow semantic claims; every accepted finding is atomic and traceable to source spans and semantic observations; every proposed economic effect names its mechanism, target, parameter requirements, and baseline treatment; dates and arithmetic remain deterministic; pivotal unresolved facts remain typed unknowns; the full investigation is observable; and no answer-bearing document order or evaluator information reaches the run.**

That is a substantially stronger milestone than “Jev is callable.”

It also creates the demo Russell actually needs to see:

> **An unusual external event enters as messy text. The system progressively turns it into clean, auditable economic facts. Those facts change explicit cash streams. Those streams change loan collections and feasible structures. The reviewer sees exactly where AI judgment was used, where deterministic math took over, what remains unknown, and why the proposed financing action follows.**

That is the architecture in which Jev is maximally utilized **and correctly bounded**—and it is much closer to Slope’s own emerging philosophy of structured context, specialized reasoning, deterministic analysis, traceability, and human decision authority than a generic “agent with an AI judge” demo. citeturn12search0turn12search1
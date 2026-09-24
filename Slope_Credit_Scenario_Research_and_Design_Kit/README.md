# Slope credit-event scenario research and design kit

Start with `research/revision_v2/deliverables/Slope_Credit_Scenario_Build_Specification.md`.

This kit completes the research and implementation design for a Slope-style external-event credit review. It is not a built lending application or a live Jev evaluation. The lead case is Synergy CHC at 13 August 2024; the transfer case is Barfresh at 25 October 2024. Both are real businesses with acquired primary evidence and documented financing.

## Contents

| Path | Purpose |
|---|---|
| `research/revision_v2/deliverables/` | Definitive product, financial and technical specification |
| `research/revision_v2/data/sources.json` | Canonical primary-source catalog with hashes, relative paths, dates and mission eligibility |
| `research/revision_v2/data/facts_synergy.json` | Research reference and evaluator facts; **do not preload the entire file into the investigating agent** |
| `research/revision_v2/data/outcome_checks_synergy.json` | Later evidence and conflicts; excluded from historical investigation |
| `research/revision_v2/contracts/` | JSON Schema, economic effects, Jev questions, runtime configuration, proposal fixtures and validation scripts |
| `research/revision_v2/contracts/case_eval_private.json` | Expected findings for the offline evaluator only; exclude from investigator and blind reviewer |
| `research/revision_v2/reference/` | Offline Decimal calculations and financial-invariant checks; no model or network required |
| `research/recent_cases/synergy_chc/` | Acquired original Synergy sources and supporting text/filing metadata |
| `research/barfresh_case/` | Acquired original Barfresh sources and supporting text/filing metadata |
| `PACKAGE_MANIFEST.json` | Packaged file hashes and sizes |

Preserve this directory layout when unpacking. Source-catalog paths are relative to the kit root. Raw source documents and evaluator files are for the builder; generate a dated, case-scoped evidence database before giving the agent tools. The entire ZIP is not an agent prompt or an admissible historical workspace.

## Decisions already made

- Primary product: compare a requested new discretionary working-capital draw with specific amount/term alternatives, then export conditional loan collections.
- Primary case: Synergy's supplier lawsuit settlement obligations and actual merchant financing. Barfresh tests production and cash-conversion effects.
- Primary agent: official Claude Agent SDK with native subscription authentication. Reviewer: Codex with ChatGPT sign-in. No required metered Anthropic/OpenAI API path.
- Jev: focused evidence and research-routing decisions through the direct TypeSafe SDK; its own usage can be separately billed.
- Financial model: dated borrower cash capacity feeds contractual and conditional loan payments. No invented PD adjustment from a legal severity label or Jev confidence.
- Storage/viewer: SQLite FTS5, Python/Pydantic, FastAPI, Jinja2 and HTMX; local execution and recorded replay.
- Portfolio scope: export the loan's marginal collection and funding changes; a full warehouse model waits for actual facility terms.

## Verification

The supplied reference calculations can run using only Python's standard library:

```sh
cd research/revision_v2/reference
python calculator.py
python -m unittest -v test_calculator.py
```

The JSON Schema checks additionally need the dependency stated in `contracts/README.md`. Run them from that directory according to its instructions. These checks cover design-contract structure and selected economic invariants. They do not validate a completed financial application, agent behavior or model availability.

The first application build gate is a local authentication, custom-tool and structured-output smoke test. Model IDs and package versions are source-verified configuration choices; no live inference was performed in preparing this kit.

## Important case-specific implementation details

The Synergy merchant agreement has separate minimum-payment windows. A cumulative 60% receipt by month twelve alone does not establish compliance with the second six-month payment requirement. Defined eligible Shopify Account Credits are not consolidated revenue or net deposits after refunds.

The $2.6m settlement schedule is measured at June 30, while approximately $2m cash is reported at August 12. Reconcile intervening payments and cash availability; do not automatically claim a $600,000 current funding deficit. Historical liabilities, paid settlements and noncash gains must not be counted twice.

The three proposed fixed-payment fixtures are analytical inputs, not historical Slope applications or actual Slope pricing. Missing borrower cash flows stay explicit; the initial public-data demonstration can show conditional financing thresholds while the completed application accepts real bank and debt data.

Later outcomes remain separate until the decision packet is locked. The primary-source catalog's mission membership supersedes any earlier working label that calls a document an “outcome” without a decision date.

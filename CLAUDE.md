# Slope Sparse Events — builder instructions

## What this is
A proof-of-concept demo for Russell (Slope). Thesis: **domain-informed probabilistic judgments turn unusual qualitative evidence into a useful, measurable financial signal.** An agent (Claude Agent SDK) investigates an unusual external event (a lawsuit). Jev (TypeSafe) reads the evidence and supplies conditional probabilities for the future events that move cash. Deterministic code composes those probabilities into event paths, simulates the borrower's cash against a bootstrapped operating distribution, and carries the result into the loan's dated cash flows. Lead case: ChromaDex at 19 Aug 2024 (live disputes with Elysium Health: a fee award being fixed and a judgment receivable). Secondary case: Synergy CHC at 13 Aug 2024 (its financial tests remain). Barfresh is deferred.

The demo reconstructs Slope as closely as public information allows: Slope's bill-pay product with supplied terms ($2.0M, three monthly installments, 3.7% fee) and synthetic connected-bank data, labelled once at its source and otherwise used plainly (this is a proof of concept for Russell, not a hedged research note).

The deliverable is **probabilistic scenario and sensitivity analysis of the lender's dated cash flows** for supplied terms: collections, collection timing, outstanding exposure, discounted cash flows and capital tied up, bank-only versus event-adjusted, and which judgment matters most. Borrower cash is the mechanism. The product does **not** recommend, size, tier or condition a loan; Slope's broader underwriting would consume the analysis. A litigation summary is not the deliverable either.

## Authoritative documents (read before implementing any step)
1. `Slope_Coding_Agent_Context_and_Alignment.md`: intent, where the module stops, what success means.
2. `Slope_Credit_Scenario_Build_Specification.md`: the implementation spec (revision 4 governs). §9 lists the build steps and exit conditions, §10 the acceptance criteria.
3. `Slope_Credit_Scenario_Research_and_Design_Kit/`: evidence, schemas, config, reference math. All source-catalog paths are relative to this directory, so never move or rename files inside it.
   - `research/revision_v2/contracts/agent_config.json`: runtime pins, MCP tool allowlist, budgets, mission whitelist, execution states
   - `research/revision_v2/contracts/question_registry.json`: Jev questions (present-state interpretation and conditional forecast profiles)
   - `research/revision_v2/contracts/dispute_model.json`: the post-judgment event model (forecast nodes, windows, cash rules)
   - `research/revision_v2/contracts/economic-effects.json`, `barfresh-economic-effects.json`, `proposal-fixtures.json`, `build-contracts.schema.json`
   - `research/revision_v2/data/sources.json` (catalog, hashes, mission membership) and `facts_synergy.json`
   - `research/revision_v2/reference/calculator.py`: reference arithmetic the finance core must reproduce
4. `Jev_Pivot.md`: early design rationale for the semantic layer. The spec wins where they differ.

**Isolation (non-negotiable):** `case_eval_private.json`, `outcome_checks_synergy.json`, the facts registry, and any source whose `mission_membership` is `outcome` must never reach the investigating agent, Jev, or the blind reviewer. They reach the agent only through scoped MCP handlers built from the dated snapshot DB. This document, the alignment doc and `Jev_Pivot.md` are builder context and must not be injected into agent or Jev prompts. Forecast prompts forbid remembered facts about the parties or later events.

## Critical errors (they block a demo result)
Wrong entity. Future-information leakage. A fabricated payment date. A demanded amount treated as paid. Overstated cash. A duplicated obligation. Unknown values silently becoming 0 or 1 (an explicit 0%/100% sensitivity is fine). An interpretation reading (an intention, a Score level) substituted for a future-event probability. A Jev probability presented as an observed frequency (it is labelled model judgment). A low or zero probability used to remove a feasible stress path.

## Stack
Python 3.12 (`uv`), Pydantic, integer cents plus `Decimal` rates, NumPy for the simulation, SQLite FTS5, FastAPI + Jinja2 with small JavaScript/SVG charts, `claude-agent-sdk==0.2.159` (subscription auth, never `--bare`, never an API key), `typesafe-sdk==0.7.1` (`TYPESAFE_API_KEY`, host-side only).

Layout: `app/{domain,evidence,agent,disputes,analysis,finance,decisions,web}`, `cases/`, `outcomes/`, `evals/`, `tests/`. Generated DBs, caches and simulation arrays go in `var/` (gitignored). Recorded demo runs go in `runs/recorded/`, with the analysis as sidecar artifacts (`analysis.json`, `collections.csv`, `cashflows.csv`).

Commands: `uv sync`, `uv run slope ...` (`slope analyze --run <id>`), `uv run pytest`, `uv run ruff check .`

## Delivery workflow
1. **Implement in an isolated worktree** on a branch off the relevant PR head or the latest `main`.
2. **Commit and push, then open or update the PR**, stating the step's exit condition from spec §9 and how it was met.
3. **One focused, independent review** of probability composition and financial correctness, posted as a PR comment. Demonstrated defects get fixed and the affected checks rerun.
4. **Merge-ready.** Once review passes, apply the `merge-ready` label and summarize in the PR.
5. **Owen merges.** Agents never merge PRs or push to `main`.

## Build posture
This is a concept demonstration for Russell. **Do not build provenance machinery, verification campaigns, review workflows, banners or hedging captions.** Existing isolation, source links and spans, accepted findings, caching, replay and the deterministic financial guards are enough; add a check only where it prevents a demonstrated financial or semantic failure. Tests protect financial correctness, probability composition and isolation, nothing more. This does not permit careless implementation: the economics, the probability arithmetic and the evidence-to-impact chain must be right, and the demo must be coherent and intuitive.

## Product judgment
Ask: *does this help Russell see what the researched evidence does to the loan's cash flows, and which uncertainty matters?* Russell should move naturally from what the research learned, to what might happen and how likely it is, to what that does to cash and to the loan, to which uncertainty matters most. The primary experience is one analysis page carrying **evidence → Jev judgment → financial mechanism → financial impact**; the investigation record is a secondary link.

The agent investigates broadly and groups evidence; its prose never pre-answers a Jev question. Jev makes the judgments. **Atomic means one judgment target, not minimal context**: questions are hydrated from the evidence store with surrounding context. Present-state interpretation ("intends to appeal") and conditional forecasting ("will appeal within 30 days, assuming the amount is fixed") are separate judgments; a reading is evidence for a forecast, never its probability. Code owns every date, window, amount and the composition of probabilities; no hand-written coefficients stand between Jev's judgments and the numbers. Keep contractual, path-conditioned and probability-weighted series distinct. Probability-weighted paths drive the distribution; every structurally feasible path stays available for the separate stress view. Unknown is a typed state, never zero. A Jev answer never sets an amount or a date.

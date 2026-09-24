# Slope Sparse Events — builder instructions

## What this is
A proof-of-concept demo for Russell (Slope). A credit reviewer is deciding on a new discretionary working-capital draw. An agent (Claude Agent SDK) investigates an unusual external event (a lawsuit), with Jev (TypeSafe) making focused judgments along the way. Deterministic code turns the sourced economic effects into borrower cash capacity, loan collections, a feasible offer set, and the capital impact. Lead case: Synergy CHC at 13 Aug 2024. Transfer case: Barfresh at 25 Oct 2024.

The deliverable is a **financing decision plus the loan's dated cash flows**. A litigation summary is not the deliverable.

## Authoritative documents (read before implementing any step)
1. `Slope_Coding_Agent_Context_and_Alignment.md`: intent, the decision this serves, what success means.
2. `Slope_Credit_Scenario_Build_Specification.md`: the implementation spec. §9 lists the 7 build steps and their exit conditions, §10 the acceptance criteria.
3. `Slope_Credit_Scenario_Research_and_Design_Kit/`: evidence, schemas, config, reference math. All source-catalog paths are relative to this directory, so never move or rename files inside it.
   - `research/revision_v2/contracts/agent_config.json`: runtime pins, MCP tool allowlist, budgets, mission whitelist, execution states
   - `research/revision_v2/contracts/question_registry.json`: Jev questions
   - `research/revision_v2/contracts/economic-effects.json`, `barfresh-economic-effects.json`, `proposal-fixtures.json`, `build-contracts.schema.json`
   - `research/revision_v2/data/sources.json` (catalog, hashes, mission membership) and `facts_synergy.json`
   - `research/revision_v2/reference/calculator.py`: reference arithmetic the finance core must reproduce

**Isolation (non-negotiable):** `case_eval_private.json`, `outcome_checks_synergy.json`, the facts registry, and any source whose `mission_membership` is `outcome` must never reach the investigating agent or the blind reviewer. They reach the agent only through scoped MCP handlers built from the dated snapshot DB. This document and the alignment doc are builder context, and they must not be injected into agent prompts either.

## Critical errors (they block a demo result)
Wrong entity. Future-information leakage. A fabricated payment date. A demanded amount treated as paid. Invented Slope policy. Overstated cash. A duplicated obligation. Unknown values silently becoming 0 or 1. Jev confidence used as a probability.

## Stack
Python 3.12 (`uv`), Pydantic, integer cents plus `Decimal` rates, SQLite FTS5, FastAPI + Jinja2 + HTMX + Pico.css, `claude-agent-sdk==0.2.159` (subscription auth, never `--bare`, never an API key), `typesafe-sdk==0.7.1` (`TYPESAFE_API_KEY`, host-side only), and the Codex CLI (ChatGPT sign-in) for the app's independent reviewer.

Layout (spec §9): `app/{domain,evidence,agent,finance,decisions,exports,web}`, `cases/`, `outcomes/`, `evals/`, `tests/`. Generated DBs and run scratch go in `var/` (gitignored). Recorded demo runs go in `runs/recorded/`.

Commands: `uv sync`, `uv run slope ...`, `uv run pytest`, `uv run ruff check .`

## Delivery workflow (every build step)
1. **Implement in a worktree** on branch `step-N-<slug>` off the latest `main`. One step per branch and PR.
2. **Commit and push, then open a PR** with `gh pr create`, using the PR template. State the step's exit condition from spec §9 and how it was met.
3. **Independent, intent-grounded review.** A fresh-context reviewer that did not write the code reads the three authoritative sources and the diff, then judges it against the step's intent, the exit condition, the relevant acceptance criteria, and the critical-error list. The review is posted as a PR comment. Material findings get fixed on the branch and re-reviewed.
4. **Merge-ready.** Once review passes, apply the `merge-ready` label and summarize in the PR.
5. **Owen merges.** Agents never merge PRs or push to `main`.

## Speed posture (deadline: tonight)
This is a proof-of-concept demo. **Do not spend significant time on non-essential tests, checks, or verification.** Write tests only where they protect financial correctness (the spec §10 invariants and the reference thresholds) or isolation (no future or evaluator leakage). No coverage targets, no exhaustive edge-case suites, no polishing checks. This speed allowance does not permit careless implementation: the economics, provenance, and the decision logic must still be right, and the demo must be coherent and intuitive.

## Product judgment
Ask: *does this help the reviewer decide what to fund and understand the cash that comes back?* Keep contractual, conditional, and expected series distinct. Unknown is a typed state, never zero. Deterministic code does the arithmetic. The agent and Jev interpret evidence.

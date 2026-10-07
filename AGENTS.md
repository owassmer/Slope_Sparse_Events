# Slope Sparse Events

## Purpose
Slope Sparse Events is a work sample for Russell, Slope's Head of Lending. He asked how rare events that bank data can't see, like a lawsuit, could help with lending. An agent researches the event using only what was public on the review date. A judgment model then answers one clear question at a time about what happens next, given the facts at that point. The engine turns those answers into the loan's dated cash flows. The code, not the model, sets every amount and date, and nothing recommends a loan. Russell opens one page, follows a real filing to a judgment, changes that judgment himself, and sees how the loan's collections, timing and exposure move, compared with the business's ordinary ups and downs. Nothing goes out with a critical error, and any other work has to help get that correct page to Russell sooner.

## Map
- `app/evidence` dated snapshots built from the kit (the isolation boundary); `app/agent` the investigation runner, its scoped tools and the judgment-model adapter; `app/disputes` the event model and the forecast walk; `app/finance` the line and ledger; `app/analysis` the engine, the page builder and reweighting; `app/web` the viewer: `/runs/<run>` is the analysis page, `/runs/<run>/investigation` the record behind it.
- `cases/<case>/` case inputs; `runs/recorded/<run>/` recorded runs with `page.json` and the CSVs; `var/` generated and gitignored.
- Lead case: Akoustis reviewed 14 May 2024, run `akoustis_20240514-agent_plus_jev-20260928T052641Z`.
- `outcomes/<case>.json` what actually happened, read by the viewer only. The factory: `outcomes/<NNN>.md` an outcome in Owen's words and `<NNN>.seen.md` its verifier's list, `tasks/`, `notebook.md`, `feature-map.md`, `scripts/`, `tools/{drive,roles,feed,gates}/`, `.pi/` (the coordinator's instructions and run notifications). The top-level `tools/*.py` are the product's own.
- `Slope_Credit_Scenario_Research_and_Design_Kit/` evidence, contracts and reference arithmetic. Never move or rename a file inside it.
- Reference, not instructions: `Slope_Model_Extensions_Spec.md` (§16 governs the 14 May model, then the rest of it), then `Slope_Credit_Scenario_Build_Specification.md`; `Slope_Coding_Agent_Context_and_Alignment.md` (intent); `Jev_Pivot.md` (early rationale). On branch `step-9-memo`: `QUESTIONS_20240514.md` in the kit's Akoustis design folder (the question standard) and `PLAN.md` (its operations log).
- There is no single way to add a case or a page feature yet; see `notebook.md`.

## Commands
- Gates, lint and the full test suite: `gates` (`tools/gates/gates` from a checkout). One full run goes at a time on
  this machine and it takes over half an hour, so it may wait its turn. A focused run of the tests you are working on
  (`uv run pytest tests/<file>`) needs no turn and is the quick feedback while working. CI runs the full suite on
  every pull request.
- Heavy computation (several walks, the full suite, anything over a few minutes or a gigabyte): put one command per
  line in `run.txt` and commit it; it runs on GitHub's runners, and its outputs come back under `var/remote/`.
  Each line is stopped after 20 minutes, so design it to answer within that: sample, split across lines, and write
  results as you go, so a line that is stopped still answers.
- Run and use the product: `drive` (`drive --help`).

## How work is done here
An outcome is finished when it is true in the running product. Keep working until it is.
Before a commitment that is costly to undo, re-read the purpose and your task.
When something you expected does not happen, re-read your task before continuing.
If you notice something that would help the next agent, say it at the end of your final message.

**Isolation.** `case_eval_private.json`, `outcome_checks_synergy.json`, the facts registry, `outcomes/<case>.json`, and any source whose `mission_membership` is `outcome` never reach the investigating agent, the judgment model or the blind reviewer. The agent reaches evidence only through scoped tools built from the dated snapshot. This file, the alignment doc and `Jev_Pivot.md` are builder context and never enter an agent or judgment-model prompt. Forecast prompts forbid remembered facts about the parties or later events. Scenario assumptions and their rationale never enter the judgment model's inputs: code applies their rules, and the model sees the resulting situation and the sourced evidence. Scenario declarations state their substance, with source citations where they exist, and nothing about who decided them or when.

**Critical errors** (nothing ships with one): wrong entity; future-information leakage; a fabricated payment date; a demanded amount treated as paid; overstated cash; a duplicated obligation; an unknown value silently becoming 0 or 1 (an explicit 0%/100% sensitivity is fine); a reading of the evidence (an intention, a score) used as a future-event probability; a model probability presented as an observed frequency rather than model judgment; a low or zero probability used to remove a feasible stress path; an answer that sets an amount or a date.

**Build posture.** This is a demonstration, not a research note: no provenance machinery, verification campaigns, banners or hedging captions. Add a check only where it prevents a demonstrated financial or semantic failure; tests protect financial correctness, probability composition and isolation. The economics, the probability arithmetic and the evidence-to-cash chain must still be right. Keep contractual, path-conditioned and probability-weighted series distinct; unknown is a typed state, never zero.

## Needs you
Deleting data, changing a live schema, releasing, spending money (any billed call: the investigating agent, a
judgment model, cloud compute, or a push that starts a workflow other than CI), merging, and any change to
AGENTS.md, skills, tool configuration, the verify tooling, or CI. Cloud work uses only the personal AWS account
(profile `slope`); never the `default` profile.

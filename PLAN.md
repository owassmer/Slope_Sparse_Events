# Repeatable event-analysis flow: plan and todo list

Updated: 2026-09-30. Owner: Codex, working with Owen. Current focus: establish and validate the connected flow (step 1). Later steps below are planned work, not completed capabilities.

## Objective and reason for the realignment

Given a business, dated evidence about an external event, a financial baseline and supplied financing terms, the agent investigates, Jev judges the relevant uncertainties, and the engine produces the lender’s dated cash-flow analysis and an understandable page. The engine and Jev can interact as new situations require new judgments; the caller should not have to connect the stages manually.

Russell is the immediate audience. The demonstration should make the evidence → judgment → financial mechanism → financial impact relationship tangible and support Owen’s follow-up conversation. A repeatable system is the objective. Completing the Akoustis tree is useful only insofar as it supports that system and its demonstration.

The recent drift was toward bespoke computation and cloud operations for one case. More completed shards, more infrastructure and a command wrapper do not establish repeatability. Existing financial mechanics and valid outputs remain useful; the tree’s representation and the amount of manual intervention remain open architectural questions.

## Boundaries to preserve

- Agent: investigate and record sourced findings; do not pre-answer Jev’s forecasts.
- Engine: apply supported mechanisms, determine dates, amounts, situations and question targets, compose probabilities, and calculate cash flows.
- Jev: interpret evidence and supply conditional judgments for concrete situations. Scenario assumptions and drafting rationales are not evidence or direct Jev inputs.
- Scenario declarations state their substance, with actual source citations where relevant; no project-history or assumption-qualification labels.
- Preserve dated-evidence isolation, financial conservation, conditional probability composition and feasible adverse scenarios, including those assigned zero probability.
- Show the event’s contribution separately from ordinary operating risk using shared operating draws. The output is financial analysis of supplied terms, not a lending recommendation.
- Source and financial precision do not, by themselves, require exhaustive materialization of every history. Any alternative must preserve the distinctions needed for future judgments and financial treatment.

## 1. Connect the existing components into one invocation

Entry point: `uv run slope analyze-case <dated-case>`.

- [x] Map and connect existing snapshot preparation, agent-plus-Jev investigation, engine situations, Jev forecasts, financial analysis and page generation.
- [x] Add an explicit `--run <id>` option to reuse a completed investigation.
- [x] Verify the locked record, dated case, investigation arm and evidence manifest before analysis.
- [x] Stop on incomplete investigations, failed stages and unresolved modeled coverage; report the failing stage.
- [x] Report stage progress and write `flow.json` beside the recorded investigation once it exists.
- [x] Return existing page and financial-artifact locations; document the command and its present limits.
- [x] Pass eight coordinator checks using stubbed provider/analysis boundaries, plus 21 focused financial checks. CLI help and Ruff pass.
- [x] Exercise the connected invocation with real component handoffs through an inspectable financial result. `tests/test_pipeline_engine.py` runs a bounded June case through the real evidence snapshot, locked record, event walk, question interface, financial engine, artifact writers and viewer route. Agent submission and external Jev responses are fixtures; this is not live-provider validation.
- [ ] Complete and inspect a live invocation, including its page, financial artifacts, Jev judgments and investigation links.

Implementation: `app/pipeline.py`, `app/cli.py`, progress callbacks in `app/analysis/build.py`; commit `356043b`, PR [#21](https://github.com/owassmer/Slope_Sparse_Events/pull/21).

**Current limit:** this is a local coordinator over the existing engine. It still builds the existing tree. It does not yet consume distributed walk outputs, resume interrupted analysis, or demonstrate practical end-to-end Akoustis execution. Do not mark step 1 fully validated based on the coordinator tests alone.

## 2. Make execution and recovery belong to the invocation

- [ ] Establish one run identity and explicit input/artifact bindings across the stages, reusing the existing recorded-run format.
- [ ] Adopt the active production run and its saved computation without repeating its investigation or restarting its completed walk.
- [ ] Connect saved walk outputs → coverage-checked pool → Jev judgments → reduction → page under the coordinator.
- [ ] Reuse valid completed stages and cached judgments; determine which downstream work is invalid when an input changes.
- [ ] Resume interrupted work without manual downloads, claim repair or artifact movement; prevent duplicate invocation from duplicating expensive work.
- [ ] Place GitHub/AWS execution, progress reporting, completion detection and resource cleanup behind the same invocation.
- [ ] Report missing evidence, unsupported mechanisms and provider/budget failures clearly, without treating them as financial conclusions.

Acceptance: interrupt and resume a run with its completed work preserved, then reach the page without manual stage handoffs. Keep this bounded: extend the current run/artifact machinery before introducing a new orchestration framework.

### Concrete production handoff

Adopt the existing May investigation and S3 walk under its current run ID. Do not run another investigation or enumerate the tree again. Extend the coordinator to select the saved execution path, retaining heavy path data on AWS; the local disk cannot hold the production pool.

| Handoff | Existing implementation | Work required |
| --- | --- | --- |
| Saved walk → pool | `tools/cloud_control.py`, `tools/cloud_walk.py pool` | Retain the controller's complete-shard selection and coverage checks. Bind the selected run, recorded inputs and model revision before adoption; `pool/ready.json` alone currently contains only completion/count metadata. |
| Pool → judgments | `app/disputes/pool.py judge` | Transfer the control and 16 question-state buckets, validate complete question coverage, then use the existing Jev cache and budget controls. Coverage must exclude classed parent nodes and explicitly account for never-live classes. |
| Judgments → financial reduction | `app/analysis/reduce.py` | Feed the selected pool and answers into reduction on remote compute. The existing GitHub reduction workflow expects GitHub pool artifacts, so it cannot consume this S3 pool unchanged. Check every assigned reduction block before merging. |
| Reduction → inspected page | `app/analysis/tables_page.py` | Use merged financial tables, stress tables and the same judgments to build the page. This writer currently produces page JSON, not the local analysis path's CSV/analysis artifact set; implement and verify the intended output contract before marking the invocation complete. |
| Interrupted stage → resume | Existing recorded run, caches and saved artifacts | Reuse a stage only with matching inputs and complete outputs. Persist stage outcomes through the coordinator and prevent simultaneous invocations from duplicating paid work. |

- [x] Close the pooled question-coverage gap before live provider calls: reject missing/unexpected states, live/dead contradictions and conflicting duplicate states. `tests/test_pool_coverage.py` covers the failure cases and the valid classed-parent/never-live case.
- [ ] Implement the above adoption path and exercise it against the production outputs. This table is an implementation plan, not a claim that the stages are already connected.

## 3. Reassess the expensive computational representation

- [ ] Trace why the main answer currently depends on enumerating the expanded tree. Separate time spent walking, simulating, judging, storing and reducing using existing run evidence.
- [ ] Define which history distinctions affect future financial rules, obligations, timing, evidence and Jev’s conditional judgments.
- [ ] Evaluate exact sharing of equivalent future states, aggregation of financial quantities without full-history retention, and reuse of genuinely equivalent judgment situations.
- [ ] Validate selected exact changes against existing tractable cases for conditional probabilities, dated cash flows, sensitivities and stress outcomes.
- [ ] If exact methods are insufficient, present a concrete bounded-approximation choice, its error treatment and its consequences for expected results, sensitivities and stress analysis before adopting it.
- [ ] Establish practical runtime, cost and memory expectations from measured end-to-end execution.

These are candidate approaches to investigate, not predetermined architecture. Do not merge histories whose future evidence or legal/financial treatment differs, prune low-probability stress paths, or weaken correctness to produce an attractive result. Prefer focused checks to another broad benchmarking campaign.

## 4. Demonstrate repeatability and deliver the Russell follow-up

- [ ] Run Akoustis from declared inputs to the inspected analysis page through the coordinated flow.
- [ ] Demonstrate interruption and recovery without artifact repair.
- [ ] Change one judgment and regenerate the affected result without repeating the investigation; verify the ordinary-risk comparison stays consistent.
- [ ] Choose a small, credible second supported case for a transfer demonstration. Make case data/configuration changes distinct from genuinely new engine capabilities; do not silently expand the lead-demo scope into a new research campaign.
- [ ] Execute that second case through the same invocation and record the actual additional work required.
- [ ] Review the page for the evidence-to-financial-impact sequence and clear separation of judgments, engine assumptions and source facts.
- [ ] Prepare the demonstration and concise explanation for Russell; update the PR to reflect what was actually achieved. Owen merges.

## Active production work and migration

Run: `akoustis_20240514-agent_plus_jev-20260929T052558Z`.

- [x] Deploy the shared-core scheduler and larger worker matrix.
- [x] Verify saved streams and migrate nine older cloud shards: 39, 40, 50, 53, 56, 75, 76, 79 and 81.
- [x] Start replacement attempts for original shards 25 and 26 while retaining their original attempts. Canonical selection accepts one complete result per shard.
- [x] Verify all eleven replacement workers have claimed their work and are computing.
- [x] Restore the controller after the connection interruption; preserve the original shard assignment when the recovery queue expands.
- [ ] Finish all shards and verify complete coverage before accepting the pooled output.
- [ ] Complete production judgments, reduction and page inspection through the coordinated handoff work above.
- [ ] Stop redundant attempts and idle paid resources when their work is no longer needed; retain the final outputs and remove task-specific temporary resources appropriately.

Last verified on 2026-09-30 at approximately 21:10 EDT: **70/100 shards saved**, controller process alive. This is a historical checkpoint, not a live counter or percentage of runtime. Refresh from S3 `done/` and the job APIs when reporting current progress.

Operational pointers:

- Original walk: [36781427817](https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36781427817).
- First improved fleet: [36793444620](https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36793444620).
- Migration fleet and replacement exporter: [36798387748](https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36798387748).
- S3: bucket `slope-walk-381492272971-20260930`, prefix `walk-36781427817`, region `us-east-1`. `done/` is canonical shard completion; `pool/ready.json` indicates completed pooling.
- Old cloud workflows 36789061458 and 36790614008 were cancelled after recovery verification. Their retirement/access-denied statuses must not be confused with lost saved outputs.
- Dedicated GitHub-role policy `drain-old-walk-sessions` denies new claim writes only for sessions issued before `2026-09-30T23:40:00Z`; uploads and completion writes remain allowed. New sessions are unaffected.
- `tools/cloud_control.py` owns recovery monitoring and pooling launch; local log is `/tmp/slope-production-36781427817/cloud-control.log`. Verify the process is alive rather than assuming it survived a session interruption.
- The separate `rust-native-core` run is not part of these managed fleets; do not cancel it as cleanup.

## How to maintain this list

Read this plan when resuming the project. Update the same checklist after substantive work or changed decisions, linking implementation and validation evidence. Mark an item complete only when its stated outcome is demonstrated. Keep transient job counts explicitly dated or labelled as last verified. Preserve unresolved work across turns and distinguish implementation, fixture checks, live validation and delivery.

Next priority: implement the production adoption path above, then finish live validation of step 1 through the resulting page. Continue necessary run recovery, but do not allow infrastructure tuning to displace the repeatable flow again.

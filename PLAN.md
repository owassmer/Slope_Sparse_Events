# Repeatable event-analysis flow: plan and todo list

Updated: 2026-10-01. Owner: Codex, working with Owen. Current focus: integrate the validated decision-state correction while preserving the completed walk and recovery outputs; see the final section before any dispatch. Live end-to-end validation remains outstanding.

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

**Current limit:** the coordinator now has local and saved-pool paths. The saved-pool adapter (commit `c810a30`) binds inputs, verifies coverage, hashes path inputs, and resumes forecasts/reduction batches; 18 focused checks pass. Production pooling is running, but practical end-to-end Akoustis execution and the resulting page are not yet validated. Do not mark step 1 complete based on fixture checks alone.

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
- [x] Finish and save all 100 shards (2026-10-01 UTC).
- [x] Verify global segment coverage: 6,573 original segments, zero missing, 578 saved parts (138 restored); 6,571 whole segments plus two completely covered by subdivisions.
- [ ] Verify question-state completeness before accepting the pooled output.
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

## Urgent AWS billing hold

Owen requested immediate migration away from company billing. Explicit STS checks show `default`, `qed-slack-user`, and `qed-user` all belong to account `381492272971`; `default` authenticates as `qed-slack-user`. No personal billing account has been verified. The automatic controller was stopped. Stop requests were accepted for task workers `i-0e4b0a03a57c5f734` and `i-08c4d2ae17f6bbb80`, preserving EBS volumes; check actual instance state before reporting completion. Do not restart the controller or launch paid compute in this account while resolving this request. Saved S3 data and EBS storage remain in the old account and can still incur storage charges. GitHub workers still use the old account's dedicated role and S3 bucket; migration must include their storage/role configuration, not merely local CLI credentials. Resume project work after verifying the personal account and planning recovery of unfinished worker segments.

AWS transition update: Owen created a standalone account using owassmer1+slope@gmail.com. Browser sign-in for the new local slope profile is awaiting completion; verify STS account ID differs from 381492272971 before launching compute. CLI upgraded to 2.37.6 for aws login; Homebrew Python pyexpat linkage repaired to installed expat. Old default profile remains unchanged. Last S3 count: 83 saved shards. All Reddit cleanup is now verified complete, including disappearance of automated snapshots. The two Slope EC2 workers and launcher remain stopped; original GitHub walk was retired after its original shards were saved.

Migration state: slope login verified in workload account 462947327980, owned by organization management account 266294230851 (owassmer1+slope@gmail.com). AWS reports PAID, ACTIVE, zero Free Tier credits. Ohio EC2 quota is 16 vCPUs; us-east-1 EC2 is denied by SCP p-xuk9jpyn. IAM ListOpenIDConnectProviders is denied by SCP p-x78f7zf6; asked Owen to allow GitHub OIDC setup. No destination compute launched. Public repository standard GitHub runners remain the verified free compute option. Migration selection is in /tmp/slope-production-36781427817/account-migration/plan.json; refresh it before transferring live outputs.

AWS resumed with Owen's explicit paid-compute authorization: r7a.4xlarge (16 vCPU, 128 GiB), i-055c5f6ee1e0b8caf in us-east-2, account 462947327980, profile slope. Instance role/profile slope-walk-20261001; SG sg-0cab20d466c1513fc; subnet subnet-02d0e7608120675ae; AMI ami-0fa99aa8f97f9e30b; pinned revision 5e2d663. Compute rate verified from Pricing API: $1.2172/hour; 200 GiB gp3 and transfer extra. Worker exits and terminates after its three assigned shards; six-hour shutdown backstop.

New private bucket slope-walk-462947327980-20261001, same prefix walk-36781427817. Only shards 32/71/73 recovery streams and the full saved shard 8 are copied so far. New queue config.jobs=[32,71,73]. Worker has claimed all three. Old GitHub fleets continue writing to the original bucket; global progress is the union of canonical done markers from both buckets, counting shard 8 once. Before pooling, transfer each selected complete attempt and publish exactly one done marker per shard in the new bucket. No automatic pooling controller is running. The old controller must not be restarted against company-account config.

Initial new-account instance i-06d48400bf9fe1e39 terminated during boot without a recovered final log. Direct cross-account storage accesses/copies returned AccessDenied. Recovery migration succeeded with source-account signed, two-hour GetObject links, fetched on the new worker and uploaded using its own role. The private manifest containing temporary links was deleted after transfer. Recovery transfer SSM command cfc417a8-6c0f-464c-912e-dc27b54ddbf1 completed successfully. Scripts and updated resource record: /tmp/slope-production-36781427817/account-migration/. Old account bucket policy has temporary read grants for the new worker role and managed/AccountFullAccessRole; remove them after migration. New bucket has temporary old-user grant from the unsuccessful direct-copy approach; remove when no longer needed.


## Tail subdivision deployed (2026-10-01 UTC)

The remaining work was concentrated in indivisible segments: eight GitHub runners used one of four cores, while AWS used three of sixteen. Commit `adb1715` adds targeted nested subdivision under the original segment identities. Completed checkpoint segments remain in place; the merge rejects missing partitions, overlapping whole/refined segments, differing child topology, and duplicate children. Original attempts remain running until replacement work is verified.

Workflow [36811398163](https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36811398163) prepared the latest checkpoint streams. Three more original shards completed during preparation: global saved count reached 94/100. Remaining shards: 32, 35, 44, 71, 73, 76. Checkpoints preserve 386 completed segments; eight unfinished segments are divided into 24 partitions per shard. GitHub owns partitions 0–19 (four processes per runner, up to 40 runners); the existing personal-account AWS instance owns partitions 20–23 with 12 processes alongside its three original attempts. No additional EC2 instance was launched.

Subdivision queue/artifacts live under the existing bucket's `walk-36781427817/refine-v1/`; this remains temporary old-account storage. AWS accesses that prefix through a six-hour delegated S3-only session with no compute permissions. The session bootstrap object was deleted after retrieval. Its original bootstrap shell is held while refinement runs so its old completion trap cannot terminate active subdivisions; the six-hour OS shutdown backstop remains, and the shell resumes when refinement exits.

Validation: six focused tests passed, including a 50-day May fixture with 1,410 merged paths and 1,344 nodes. Subdivided and unsplit paths, nodes and financial rows match exactly, including watched-question behavior. Independent scoped review found no mathematical ordering defect; identified claim recovery, orphan-process cleanup, and empty-root handling were corrected. Full production assembly remains to be verified.

- [x] Implement and deploy subdivision preserving saved segments.
- [x] Prepare immutable checkpoint bases and disjoint GitHub/AWS partitions.
- [x] Finish the remaining shard results and retire redundant walk attempts; shards 32/71 passed subdivision coverage during assembly, while shard 76 completed through its original attempt. Global pool coverage remains a separate pending check.
- [ ] Complete production pool → judgments → financial reduction → inspected page under the repeatable-flow coordinator.


Deployment verification: all nine preparation jobs succeeded. GitHub claimed 120 distinct partitions; AWS claimed 12 concurrently, with its remaining assigned partitions queued locally. SSM verified 15 CPU-bound walker processes on the personal instance (12 refined + 3 original). Live parent subdivisions report 29, 32, and 39 children. The first refined partition, 73-3, completed and published its archives and completion marker; no new workflow job failures were present at that check. A separate real pool.split/control comparison also matched unsplit paths, questions, classes and cumulative event-cash ranges. Review rechecked all three operational fixes with no remaining scoped blocker. Full production assembly is still pending; expired claims are reclaimable by a retry invocation, but workers do not themselves schedule a new invocation.


## Walk complete — 2026-10-01 UTC

All 100 canonical shard completion markers are present in the original bucket, with no missing shard IDs. Shard 76's original GitHub attempt finished and published all 17 data archives plus its log at 04:51:39 UTC, before another subdivision was launched. Its still-running refinement partition became redundant. No further split was needed or deployed.

The GitHub workflow's all-workers dependency unnecessarily held ready assemblies behind that partition. Shards 32 and 71 were instead assembled on the existing personal AWS instance from all 24 saved partitions each; both passed the original-segment and subdivision coverage checks, uploaded their 17 archives, and published canonical completion. The completion log is in `s3://slope-walk-462947327980-20261001/walk-36781427817/boot/assembly-complete.log`. Existing S3 results remain intact. Global pooling, Jev forecasts, financial reduction and the final page have NOT yet completed.

Compute cleanup: GitHub runs 36811398163 and 36798387748 were intentionally cancelled to stop redundant attempts; fleet 36793444620 had already succeeded. All original AWS walk process groups were killed after their replacement outputs were saved. The personal instance i-055c5f6ee1e0b8caf resumes its shutdown trap after successful assembly and is terminating automatically. The two stopped company-account walk instances i-0e4b0a03a57c5f734 and i-08c4d2ae17f6bbb80 were terminated, with their delete-on-termination worker volumes. S3 storage still exists in both accounts and still incurs storage charges; final-output migration and temporary-object cleanup remain outstanding.

Next: finish the uncommitted pooled coordinator, consolidate the selected production outputs in the intended account, and execute coverage-checked pooling → Jev → reduction → inspected page. Do not restart the walk or the old company-account controller.


## Production pooling launched — 2026-10-01 UTC

Canonical inventory: 100 selected shards, all 1,700 required data archives present and nonempty, 68,193,079,339 compressed bytes (63.5 GiB), including 1,306,707,737 control bytes. The topology recorded by the workers has 6,573 original segments; global coverage is checked again during pooling. Shards 32 and 71 use assembled refinements; the other canonical results are whole-shard completions. Retained paths and question counts come from the global pool, not a sum of duplicated worker logs.

Pooling instance i-07454d2f7582ce015, account 462947327980/profile slope, us-east-2: r7a.4xlarge, 16 vCPUs, 128 GiB RAM, 300 GiB temporary gp3. Spot quoted $0.2765/hour but organization policy p-x78f7zf6 explicitly denied the Spot launch; the permitted on-demand launch uses the previously verified $1.2172/hour compute rate. Storage and transfer are extra. Six-hour shutdown backstop and automatic termination on completion/failure.

Commit c810a30 reads the immutable selected archives from the old bucket through a six-hour S3-read-only delegated session; writes all pool outputs, stage checkpoints, input bindings and live logs to the personal bucket. It does not first duplicate 63.5 GiB of raw inputs. Destination: `s3://slope-walk-462947327980-20261001/walk-36781427817/pool/`. Watch `progress.json`, `resources.json`, `live.log`, `walk-summary.json`, `control-ready.json`, `states*-ready.json`, and finally `ready.json`. Four question buckets run concurrently. The readiness marker requires exact question-state coverage and no state-building errors, with no Jev calls during pooling. Saved selection/binding mismatches are rejected before reusing checkpoints.

The original walk used revision 9940c61; subsequent saved-worker pins changed scheduling and verified processor optimizations. Recorded investigation, contracts, bank baseline, run inputs, forecast/event rules are unchanged. Scenario JSON changes are note/basis prose and the verdict-form explanatory amounts paragraph; numeric/timing settings are unchanged. Current production binding was successfully validated against the real locked run before pooling. Source data remains in the old account until retained outputs and any required migration are settled.

Adapter review fixes: validate extracted downloads against their contents, include actual path files and dispute replay code in reduction identity, reject missing paths, and enforce question-state coverage before publishing a pool. Eighteen focused checks pass. Production pool completion remains pending.


Pool execution verified: global coverage passed with zero missing segments. The worker is now in `merge_paths_and_questions`; the source control archive set occupies approximately 17 GiB unpacked. The preliminary inventory/coverage result is saved as `pool/walk-inventory.json` in the personal bucket. The independent review verified c810a30's source/destination separation, coverage/readiness gates, pinned checkpoints and saved-path invalidation, with no remaining scoped blocker. Exact retained-path/question counts and final pool readiness remain pending.

## Pool compute expansion — 2026-10-01 UTC

Owen requested maximum useful parallelism across GitHub and AWS. Live py-spy sampling located the stalled single-core control pass at the per-edge `atoms(key) - nodes.keys()` check. Direct dictionary membership preserves the missing-question check without scanning the full question universe on every edge. Path extraction now runs per part across the existing instance's 16 cores; equivalence merging runs by each group's owning part after the same global ordering and watch resolution.

Question-state construction is partitioned only after each original bucket's rows have been globally ordered and deduplicated. There are 96 independent whole-question partitions: 80 on 40 GitHub runners (each uses its available cores), and 16 on AWS (four concurrent processes each using four cores). AWS prepares each original bucket once; GitHub receives compact prepared row bundles through scoped signed object URLs, with no AWS credentials or live Jev calls. The original walk and the verified, downloaded control inputs are retained. All new outputs live in the personal account under `pool/fleet-v2/`; the coordinator combines exact disjoint question sets into the existing 16-bucket output contract and retains final global coverage checks.

- [x] Implement direct question lookup and multicore path processing.
- [x] Implement distributed question-state preparation, workers and exact coverage assembly.
- [x] Independent scoped review: no changed financial/probability semantics found; fix zero-path adoption rejection and prepared-marker reuse.
- [x] Pass 13 focused checks, including serial/parallel equivalence on the 50-day fixture (1,410 retained paths, 1,344 nodes), exact ordered question-row partitioning, coverage and saved-pool reuse.
- [x] Deploy revision 9cccdda to existing AWS instance i-07454d2f7582ce015 and GitHub workflow [36824836030](https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36824836030).
- [ ] Verify actual AWS core usage, GitHub runner activity, memory, saved partition progress and final readiness.

Existing six-hour instance termination backstop remains; no additional EC2 instance is required. Full live pool completion and the financial deliverable are still outstanding.


Live deployment check: all 16 AWS path workers were observed at 87–99% CPU each. The new pass processed 40/578 parts (200,022 paths) within its first few minutes, versus the replaced pass's 51 parts after over an hour. At that check 39 GitHub jobs had started and one remained queued; state workers wait for the complete control output and their prepared question bundles. This demonstrates active parallel path execution, not completed question-state processing. The old single-core process was retired; the existing instance and downloaded inputs were reused. Both the instance shutdown backstop and scoped source read session were renewed for six hours during the switch. No new EC2 instance was launched.


Global control finished at 06:39:42 UTC, about 13 minutes after the optimized restart: 4,326,830 walked paths, 1,002,539 retained paths, 22,258 question nodes (1,911 classed parent nodes), zero missing segments, zero offering continuations requiring more walk. All 40 GitHub jobs have started. This is not final question-state readiness or a financial result.

Measured EBS writes hit the temporary volume's original 125 MiB/s ceiling. Volume vol-0a3c8957961cc9c01 was raised to 1,000 MiB/s / 4,000 IOPS in the personal account. AWS Pricing API rates: throughput $0.04 per provisioned MiB/s-month above baseline and IOPS $0.005 per provisioned IOPS-month above baseline; incremental cost is about $0.055/hour at 730 hours/month. No new instance was launched.

The retained indexed paths occupy about 111 GiB. Revision 93a7a82 uses pigz for parallel archive compression and binds completed local-control checkpoints to exact input, control, path and index hashes. The one-off live handoff compared local control bytes to the published S3 control and summary, verified positive-path indexes/counts/file lengths, and saved the completed checkpoint. The replacement resumed that checkpoint without recomputing the merge; pigz -p16 was observed using multiple cores. The source read session and six-hour shutdown backstop were renewed during the handoff. Current production pin: 93a7a82 on AWS; GitHub state code stays at compatible 9cccdda. Independent scoped follow-up review found no remaining checkpoint/compression blocker. The compression step and first distributed state outputs still require live completion verification.

## Question-state defect found by the live fleet

The first distributed wave saved states from both GitHub and AWS, but exact coverage rejected post-trial ruling partitions with `int(None)`. Live traceback located `state14.Situation._band`: it was using the original award band as the proposed reduced amount. The top award band has no upper bound, producing the exception; finite-band questions could silently name the wrong reduction range. The walker already correctly books the immediately lower J1b band, so paths and cash booking remain valid.

The correction shares that existing lookup through `Forecaster.reduced_band`, preserving `lo < entered <= hi` and booked midpoints, and uses it for the question's reduction bounds. Lowest-band questions omit reduction fields when that answer is absent. Regression checks compare rendered criteria to the walked band across all bands, the open-ended top, the lowest band and exact cut points. Fourteen focused checks passed; independent financial/probability review confirmed the correction. Every affected state must rebuild, including previously successful finite-band states; old outputs are excluded through code-versioned output prefixes and versioned signed manifests.

All 16 source-row buckets finished preparation before the initial coordinator failed. Their 96 saved prepared bundles remain reusable. Old GitHub run 36824836030 is being retired; AWS's completion trap was held before the failure, preserving the instance and completed data for the corrected state-only restart. No Jev probabilities have been requested.

## Corrected fleet result — 2026-10-01 07:10 UTC

Revision d9e4fe7 ran on 40 GitHub runners (four cores each) and all 16 AWS cores. GitHub run 36827669452 finished with 39 successful jobs and one failed job. The failed job's second, independent task (12-2) was rescued on the now-available 16 AWS cores and saved successfully. Current generation 09e662abd9e4682e has 95/96 partitions saved and 15/16 buckets assembled. Healthy outputs are preserved; no whole-fleet restart is needed.

The remaining partition 4-2 rejects `dispute_002:holders_involuntary|judgment_ruling|motions_pending`: no trajectories from which to construct the question's cash state. This is a correctness blocker, not a compute-capacity shortage. A scan of all 545 retained-path files (118,926,442,915 bytes) found the key; decoding part100 confirmed an exact composite-edge dependency. That example carries an empty class-tag tuple with every code -1, but the base question has no class children or fallback. This single example is not proof that the question can be omitted globally. Do not skip the question, invent a probability, classify it as unused, or publish pool readiness. Next: establish its treatment across all affected retained paths and correct the state/probability interface without changing feasible cash outcomes. Jev, financial reduction and the final page remain pending.

- [x] Verify live multicore execution, GitHub runner activity and saved partition progress.
- [x] Rescue the healthy task stranded behind the failed task onto available AWS cores.
- [ ] Correct the remaining no-cash-state dependency and complete exact 96-partition coverage.
- [ ] Publish final pool readiness, then run judgments and financial reduction through the repeatable invocation.

## Notes decision correction — GitHub-first recovery

The missing state is a live question, not an unused dependency. Original control recovery on GitHub run 36890822835 completed 40/40 workers (160 cores), inspecting 4,326,830 original paths and preserving 37,190 affected histories across 210 original segments. Later-walked settlement decisions can occur before the holders' filing date and remove the prefix's assumed prior filing. The original prefix facts and early termination therefore cannot be reused as authoritative.

- [x] Recover original histories, including those lost behind globally merged representatives.
- [x] Reproduce the defect in a deterministic fixture: prefix has no live holders decision; completed path has a decision on draw 7, day 168, after acceleration on day 108.
- [x] Implement complete-path, before-own-action notes rows with actual trigger-date binding and actual semantic context; bypass pending notes prefix classes and local merges; continue issuer and holders branches through remaining dated decisions.
- [x] Pass four targeted regressions and 17 focused checks in total, including question facts invariant to their own answer and notes-fork probability conservation.
- [ ] Validate all 37,190 recovered histories on 40 GitHub runners, four processes per runner. Validation outputs are separate from production pool inputs.
- [ ] Confirm segment topology and the full affected population, regenerate missing continuations, and rebuild affected classes/states, including previously successful ones.
- [ ] Resolve the independently flagged ripe-response early-termination shortcut with a concrete regression.
- [ ] Complete exact pooled question coverage, then judgments, financial reduction and page delivery.

The temporary repair instance i-0ec1292733be502cc was terminated after its artifacts were saved. Use GitHub capacity first; no new paid AWS compute is authorized by this recovery plan. The saved 95/96 state partitions are not proof of correctness under changed notes classes; no production ready marker or Jev call has been issued.

Continuation update: a second fixture establishes eight retained draws where a later-walked set-aside ruling precedes and removes the ripe-response filing. The grouped response previously ignored its filing continuation callback; ripe responses now honor it, and `ripe_i1` continues through the ruling. Six notes regressions and 19 focused checks pass, including per-draw probability conservation after class expansion. The first population-validation attempt failed during runner setup because the evidence snapshot was missing; the standard snapshot build step was added. Replacement run 36894023811 is processing the saved histories without production writes. Original and corrected segment identifiers differ, so a separate GitHub topology description compares actual root histories before any saved-part reuse.

## Reuse boundary clarified — no full rerun launched

GitHub validation run 36894023811 completed40/40 workers:37,190 original histories replayed,6,169 live histories,42,885 live draw occurrences,41 classes. This validates recovery of the failed question's facts; it does not validate repaired continuation coverage. Scope run36895129643 completed40/40 on its second attempt after an expired input link was refreshed. It inspected4,326,850 raw events (including conditional events outside the original globally selected4,326,830):5,707/6,573 segments containing4,325,762 paths touch notes origins or ripe filings.

**Those counts measure exposure, not invalid cash paths. A complete rerun is not established as necessary.** Owen challenged the earlier overstatement, and no full rerun was launched. Preserve existing histories; rebuild decision facts and identify exactly which continuation forks need regeneration. The original ruling-fork fixture preserves its draw mask when missing filing alternatives are reconstructed, but an earlier I1 fork changes downstream group eligibility; copying an origin and probability edge alone is not generally sufficient. Any targeted replacement must retain per-draw probability conservation and correct conditional facts, with original data kept for comparison.

The notes fix now also preserves no-award context and reads the ruling outcome only when it has occurred by the decision. Active-draw replay matches the full replay's dated cash, obligations, petitions, snapshots and triggers in the regression. The measured example fell from0.0631s to0.00957s per replay (62/512 active draws); this is a local example, not a production-wide speed claim.25 focused tests pass.

Topology description run36894791670 computed the original6573-segment skeleton but failed to save its outputs when temporary storage authorization expired. The diagnostic now saves to GitHub artifacts, without AWS credentials, and includes callback code/defaults and watched conditions in history identity. It does not authorize reuse by itself. Pending: finish the bounded replacement analysis and regenerate only the necessary continuations, then refresh the affected pool. AWS compute remains terminated; no Jev forecasts or production ready marker have been issued.

## Saved-history rebuild started

The next distributed operation rebuilds notes decision rows and per-path question classes from the original control archives. It does not enumerate the tree or alter stored path steps, probability edges, masks, outcomes, financial-equivalence keys, or watch conditions. Per-part patches, replacement rows, new class nodes and completion reports are saved as GitHub artifacts. The original inputs remain intact; adoption still requires the missing-continuation repair and exact pooled coverage. An integration regression verifies that a saved history retains its financial path while its empty notes class becomes the recovered live class.11 notes/rebuild regressions pass. Fleet:40 standard GitHub runners, four worker processes each. No AWS compute launched.

First bounded continuation wave: the210 original affected history prefixes map to exactly210 corrected segments, with zero unmatched prefixes, out of8144 corrected top-level segments. `SLOPE_WALK_PREFIXES` selects those histories by their actual steps rather than obsolete segment numbers. A real50-day walk regression compares the selected raw paths, financial-equivalence keys and watch identities to the corresponding subtrees of the complete walk; it passes and preserves the complete segment topology while marking only selected segments done. Replacement outputs remain separate until coverage and integration pass. This wave addresses the demonstrated failing question; it is not a claim that all other affected continuations are repaired.

## Repair reconciliation — current instructions, 2026-10-01

This section supersedes the broad repair dispatch/adoption instructions above. That campaign remains retired; the exact branch recovery below supersedes the subsequent compute pause. GitHub repair runs 36897600258, 36898639339 and 36900757978 were cancelled; personal AWS worker i-0d1ad1d0761e877d8 was stopped with disk preserved. Do not restart the old replacement manifest. No new Jev forecast or financial result has been produced.

The earlier 2,109 reusable / 6,035 replacement segment plan and its 99.3% raw-path replacement implication are withdrawn as a statement of necessary repair. They came from broad filing exposure flags, strict whole-root identity and an overbroad code change. They do not measure invalid cash paths. The complete original walk remains preserved. The scope scan is an exposure inventory only.

### What the reconciliation establishes

Connor's original messages 795972–796011 approve facts as of the decision, once per history, and six-dimensional per-draw question classes without extra path forks. Message 796104 requires each answer to use the same before-action class. Message 797704 explicitly rejects an apparently redundant offering merge after checking its cash lock; exact continuation-state joins remain valid. These constraints govern the repair as well.

- The reduced-award band correction changes question facts, not booked cash or walk topology.
- In the production-derived notes fixture, draw 7's unfinished history predicts a cash-floor petition on day 164. An I2 settlement walked later occurs on day 108 and averts it. Holders may then decide on day 168. The quiet completed path has no petition; the holders-file path petitions on day 168. Premature termination and prefix-only facts are demonstrated failures.
- `ripe` is an existing phase for the judgment-default availability date. The repair changes its filing continuation, not the date or the contractual scenario. An earlier-dated set-aside ruling removes an unfinished path's predicted filing on eight retained draws in the regression.
- Restore both stronger `joined` checks: they compare unfinished continuation state as well as completed financial traces. Pending judgment branches retain distinct waiting actions and fail this equality check. No demonstrated defect justifies disabling the delisting check.
- Keep the pending restriction on digest-only `unfiled` for now: equality of a finished prefix alone does not prove future continuation equivalence. A separate original raw event (10494, 0, 5020, 753) demonstrates an erroneous merge: the finished prefix digests are equal, but after I2 settlement the quiet path petitions on days 168/164/164 and the holder alternative on day 163 for draws 122/193/408. Their draw masks agree. This is a counterexample to prefix-digest equivalence, not a count of all affected histories.
- Keep completed, before-own-action notes facts with dated context. Existing `_keep_late` deduplication remains. A discriminating check changes the later listing answer and confirms the earlier issuer class and recorded-history count are unchanged. The fixed-suffix sibling probability test passes; it does not establish global probability conservation across all regenerated continuations.

The local rollback restores the two `joined` calls. Seventeen focused notes, scope and pool tests pass. No event-engine timing, source evidence, question registry or scenario assumption was changed by this rollback. Notes class membership and rendered decision facts do change under the preceding chronology repair; they must be validated before Jev consumes them.

### Recovery work, in order

- [x] Reconcile the defect with original Connor decisions and concrete chronology.
- [x] Withdraw unjustified stronger-join exclusions; preserve history deduplication and per-draw classes.
- [x] Withdraw whole-root exposure counts as the required replacement scope.
- [ ] Locate actual premature termination and unsafe digest-only merge sites in raw saved histories. Distinguish facts-only changes from missing continuations. Do not infer invalidity from a filing label alone.
- [ ] Capture exact walk state, continuation and watches by targeted prefix replay. Steps alone omit necessary walk flags. Select nonoverlapping affected prefixes; establish reliable provenance for removing their old descendant events and facts.
- [ ] Present measured replacement/reuse scope before relaunching distributed compute. The original 210 prefixes and 5,825 remainder are not established minimal repair units.
- [ ] Resume only the necessary continuations, replacing their corresponding old descendants and composite alternatives. Preserve incoming probability and draw masks; verify descendant probability mass equals incoming mass per draw.
- [ ] Refresh affected notes facts/classes from complete histories, deduplicate globally and recompute watch coverage. Validate exact question-state coverage and probability conservation across the assembled result.
- [ ] Finish pooling → Jev → lender cash-flow reduction → inspected analysis page through the repeatable invocation. Russell's work sample and follow-up remain the delivery priority.

GitHub is primary compute; personal AWS can accelerate when justified. No recovery estimate is established yet. Do not describe this reconciliation or the passing local regressions as completed production repair.

## Exact branch recovery — 2026-10-01

Read-only GitHub scan 36906787897 completed all40 workers,100 original sources and578 parts. It indexed4,326,850 raw path events (including20 conditional events outside the previous globally selected count), locating actual prefix-terminal predicates and stored digest merges. Replicated top events were excluded using the original top owner; source/part inventory and completion were checked.

The exact recovery manifest at `/tmp/notes-exact-recovery.pkl` selects12,402 nonoverlapping post-answer branches and12,591 continuation starts. It replaces21,646 old raw histories and retains4,305,204 cash paths (99.5%). This is a complete conservative recovery selection for the identified changed rules, not a claim that every selected old history has wrong cash. Unaffected siblings survive. A merged quiet/holder alternative is reconstructed as its two original alternatives. The manifest retains original event conditions as well as ordering keys.

`tools/notes_resume.py` replays only the exact saved prefix to capture actual walk flags and callback, snapshots canonical class maps, and rejects ambiguous capture or an open speculative watch. It never reconstructs flags from steps alone. A recovered full notes fork produced27 descendants and conserved per-draw probability over three randomized assignments. A ripe-file branch produced20 descendants in4.1s; a heavier selected branch produced557 descendants in34.4s. The production worker preserves completed units independently and checks conditional descendant mass, unexpected financing expansion and original metadata consistency.18 focused tests pass; independent review found no blocker to isolated generation. Global integration checks remain mandatory.

Recovery run36909118504 (https://github.com/owassmer/Slope_Sparse_Events/actions/runs/36909118504) is running on40 GitHub workers using all detected cores, with no AWS instance launch. Started18:44:47 UTC; at18:52 all40 workers were running and none had published its final artifact. The GitHub API does not expose in-progress job logs, and the separate browser session is not signed in, so no completed-unit count has yet been independently read. Plan SHA256: `66ce5f812399f43878f3d235f2b0ad73e67306b5c9ac35fa851f58076c179672`. Outputs are isolated GitHub artifacts retained7days. Do not reuse the retired whole-root adoption manifest.

Remaining integration work:
- [ ] Finish every selected continuation; resolve capture, financing or conservation failures without dropping their units.
- [ ] Assemble replacement paths with original conditional provenance and preserve every unselected raw cash history.
- [ ] Rebuild notes facts/classes across final histories. For affected non-notes late facts, group by base question + asking prefix, remove old rows for the whole group and rebuild from ALL final supporting histories (retained and recovered); globally deduplicate by late_key. A parent asking prefix can precede the replaced branch, so prefix-local deletion alone is wrong. Preserve shared early recorded ancestor facts separately.
- [ ] Verify full probability composition, watch coverage, absence of duplicate alternatives and exact question-state coverage.
- [ ] Finish pooling, Jev judgments, financial reduction and the inspected Russell page through the repeatable invocation.

## Live-log failure diagnosis — 2026-10-01, supersedes API-only monitoring

Job-level `in_progress` is not a health check: `notes_recover.one` catches individual exceptions, records them and continues. Read the per-unit log lines and final `report.json`. The first complete browser sweep of all40 workers captured7,107 successful units /209,075 saved paths and31 probability-check failures; those are observations during a sweep, not final totals. Current workers continue saving isolated successes. No output has been adopted and no new campaign has been launched.

Browser access is now direct to the signed-in Chrome process through Apple Events (`appscript`), without coordinate or clipboard handling. `/tmp/read_recovery_browser.py` reads the actual DOM log lines, scrolling GitHub's virtualized log view; `/tmp/notes-live-logs/` holds worker snapshots. The unavailable in-app Node REPL cannot be created by changing permissions; this alternative uses the actual authenticated browser with no credential extraction.

- [x] Reproduce failures6910,5171 and11 locally, including exact reported errors. These emit186,126 and404 descendants respectively. Their failures are all sensitive to offering class assignments. Tying classes was a diagnostic only, never an accepted correction.
- [x] Compare6910 with the original `ccfedd2` downstream `_Walk` methods, starting at the same captured state: identical186 descendants and exact same probability error0.0017321220508337287. This proves the downstream inconsistency predates the recent notes changes, not that the old walk traversed these previously omitted paths.
- [x] Trace concrete chronology: on draw123, file paths32/45 petition on day122, canceling the floor2 offering. Their motion-day stay question nonetheless retains offering-dependent classes; no-file siblings33/46 retain the live offering class. The mixture does not conserve probability.
- [x] Exclude an unused-class explanation for the conflicting stay classes: all16 involved stay motion/approval classes have live row support in this recovered unit;15 were also verified LIVE in original pooled buckets on the initial download (one bucket download retried). They cannot all be collapsed as globally never-live classes.
- [x] Locate the mixed-date source: `Chain._snapshot_day` / `_snapshot_tail` intentionally give a stay prefix its approval-day situation, while `_Prefix.of` / `Forecaster.row_of` retain its motion-day date/cash. `as_of` removes future offering dates but cannot rewind the share ledger. For the actual before-stay prefixes in6910, the reported day/cash agree (day122,543152717 cents), but ledger is0 after offering=yes and306426 after offering=no, even though that offering is absent from the date-filtered history.
- [x] Check contract authority and focused independent review: QUESTIONS§1 requires decision-day general facts; §2.5 deliberately sizes security on the approval day. D4 must retain approval-sized proposed security while receiving motion-day general situation. J3's actual late facts remain approval-day facts. Do not indiscriminately move all stay fields to one date.
- [ ] Finish the isolated mixed-date discriminator on6910/5171/11; no production code has been changed for it.
- [ ] Measure affected saved stay prefixes and class references before adopting a correction. Compare row content as well as class tags; unchanged class does not prove unchanged facts. Rebuild touched questions from all retained and recovered support. This is replay of saved histories for facts/classes, not a demonstrated need to regenerate their cash paths.
- [ ] Reconcile every failed unit and the assembled probability check; preserve successes, rerun only failed recovery units after the correction is established, and validate cash invariance. Passing a unit's local check does not establish correctness of the assembled forecast.

Original raw history incidence of this mixed-date defect remains to be measured. Do not describe the31 observed failures as the total affected original population or discard the original walk. The diagnostic files `/tmp/notes-6910-diagnostic.pkl`, `/tmp/notes-6910-original-diagnostic.pkl`, `/tmp/notes-5171-diagnostic.pkl` and `/tmp/notes-11-diagnostic.pkl` retain the concrete paths/classes and failing assignments for further checks.

### Follow-up discriminator: two distinct defects, no adopted candidate

The motion-day general-snapshot diagnostic passes6910 and5171 at floating-point tolerance and preserves their186/126 histories. On6910 every step, edge, outcome and mask is identical;166 class records change. It does NOT resolve11. A further experimental J3 completed-path counterfactual is rejected: it worsens normalization on11 and6910. These experiments exist only in `/tmp/probe_stay_snapshot_causality.py` and `/tmp/probe_stay_both_dates.py`; no repository implementation or production worker was changed. All16 conflicting stay classes were subsequently verified LIVE in the original pooled records.

Unit11 demonstrates a separate termination error. On draw49, saved path168 ends with cash_floor3=file on day151 but omits cash_out on day103. Siblings169/171 include cash_out=file103;170/172 include neither103. `_candidates` considers floors before cash exhaustion; `ask_distress(file)` calls `_end`, which emits immediately when no callback remains. This can omit an earlier-dated question. Independent review confirmed the branch and masks. It is not repaired by changing stay snapshots.

A direct financial check adds the missing cash_out answer to168: neither preserves the original cash/lock/capacity/petition arrays on ALL512 draws; file matches existing169 on ALL512 arrays. Thus the demonstrated alternatives already have saved financial outcomes. Their probability edges, classes, masks and any remaining decisions still need a complete reconciliation before reuse; this witness does not establish the original population's whole scope.

At the latest merged log/artifact observation,9,087 successful units produced300,267 saved paths and46 units failed the local probability check; six workers had published final artifacts. Counts are partial while the fleet continues, not a final tally. Completed-worker artifacts preserve their successes even when the job conclusion is failure.

Next diagnosis/repair scope includes BOTH the stay question dates/classes and terminal histories omitting earlier distress decisions. Inventory and test those conditions on saved histories; do not substitute blanket filing exposure or whole segments. Reuse financial arrays where direct comparison establishes equivalence, recover genuinely absent outcomes only where demonstrated, and retain the same-before-own-answer classification constraint. No complete correction is yet validated; no full-walk restart is authorized by these findings.

### Joint correction and saved-history scope — ongoing, 2026-10-01

No production stay/terminal correction has been adopted. The motion-snapshot plus terminal diagnostic passes all three actual failures: unit11 has419 descendants (formerly404),6910 has186,5171 has126, with maximum conditional-mass errors below9e-16. The terminal change only checks unanswered distress decisions actually dated before the existing petition before emitting a terminal filed history. For11, all419 corrected histories have an existing financial outcome on their active draws under the engine's full equivalence fields:412 match across all512 draws; the remaining7 match saved alternatives on all their retained draws. This establishes financial reuse for this fixture, not global closure.

The J3 correction needs more than changing a row or removing its canonical class. Independent review traced6910 draw469: motion109, approval147, enforcement109. The old stay=no composite combines no motion and a motion later denied, but only the approval-yes sibling receives the pending-motion enforcement context. An isolated explicit denied-motion representation preserves the no-stay cash booking while retaining the filed motion. Three-way tests pass6910/5171 with270/186 histories; each maps to an existing step history when denied is mapped to old stay=no. Actual denied-motion enforcement/response dates are before approval on all3792/388 live fixture occurrences. These facts do NOT yet validate the joint correction: unit11 still fails because a granted stay skips an I3 settlement on day104 before approval152. Shared pre-approval settlement history is under diagnosis. J3's counterfactual must retain the motion, and D4's motion-day general facts must retain the approval-sized security basis in downstream state consumers.

The original saved indices contain201,140 terminal floor/exhaustion filing histories. All3,713 without a cash_out step were replayed with actual masks/dates: NONE has a live omitted cash_out decision. This separates the demonstrated newly recovered omission from that original subset. Other unanswered candidates still require measurement. Local read-only checks completed31 parts /12,362 histories; outputs are preserved in `/tmp/notes-terminal-scope/`.

Commitd34f706 adds only a read-only terminal-scope tool and workflow. Its discriminator detects the known unit11/path168 omission on draw49. Ruff and diff checks pass. Run36916420788 on32 GitHub workers checks the remaining450 saved parts /188,778 histories; it neither walks new branches nor adopts results. Personal AWS is used only to store the small input archives; no AWS compute was launched. Exact local/remote assignments are saved in `/tmp/notes-terminal-scope-assignments.json`.

At37 final recovery-worker reports,11,375 units succeeded (473,276 saved paths),100 were rejected, and3 workers remained running. All reported rejections are conditional-probability failures; their common error type does not prove a common cause. Preserve every final report and reconcile all assignments before adoption. The three slowest observed rejected units are5533,132,5379; include an actual heavy case in the consequential checks once the joint correction is established.

## Materiality reset — 2026-10-01, user-directed priority

The user stopped the expanding repair diagnosis and asked for lender consequences first. Experimental stay/terminal corrections remain unadopted; do not resume their expansion automatically. The independent review agent is interrupted; its local process89041 (uv parent89029) was SIGSTOPped with its state preserved.

Recovery36909118504 finished: all12,402 assignments reported,12,289 succeeded with514,891 paths,113 rejected. All40 archive inventories were checked: every successful unit file and every rejected unit error file exists; archives total7,567,762,373bytes, earliest expiry8Oct19:12UTC. Terminal scope36916420788 finished successfully: all481 local/remote parts and201,140 original terminal floor/exhaustion histories checked, ZERO missing earlier distress candidates. This finding does not validate the recovery or eliminate separate notes-continuation questions.

Measured the original examples using the recorded production setup, all512 operating draws, and unchanged financial/event engine code (no diff fromccfedd2 in events.py, engine.py or processor.py). Matched the actual stopped original history in raw index event(8182,0,3925,169043). The ledger comparison checks per-day fundings, collections, outstanding, PV, capital-days, frozen claims, future-due balances and preference-window exposure. Collection/funding totals agree with dated arrays; no levy shortfall occurs on the compared active draws. Results: `var/notes-materiality/lender-impact.json`; reproducible calculation: `var/notes-materiality/measure.py` (uses preserved /tmp control and original-history inputs).

- The digest-merge counterexample:334 active draws, three bankruptcy-date differences, ZERO differences in lender funding, collections, outstanding, PV, capital-days or ending exposure/preference measures. Bankruptcy timing differs, but this example establishes no monetary lender consequence through10Nov.
- Completed quiet versus holders-file alternatives in the missing-question example:62 active draws, zero dated cash-flow or PV differences. On draw7, however, $310,460.12principal/$321,947.14total claim changes from future-due exposure to a bankruptcy-frozen claim; $535,795.16previous collections enter the model's preference-window exposure measure. Neither is a booked loss.
- Actual original stopped history versus the proposed earlier-settlement continuation: identical62-draw masks;8 draws change dated lender cash flows. On original witness draw7, bankruptcy moves26Oct→30Oct and permits a $28,230.02collection on28Oct; lender PV rises$27,253.27. Across the62 draws, collection differences range down to-$148,920.62 and up to+$67,452.36; largest absolute lender-PV difference$74,825.90. These are conditional scenario differences, NOT expected forecast changes. The alternative's Jev probability and overall aggregate repair impact have not been measured.

This establishes a concrete lender consequence for one missing continuation, but does not justify the breadth or three-hour cost of the recovery expansion. Preserve the original valid branch-sharing design. Subsequent work must target demonstrated lender consequences and the minimum required correction, with the currently generated recovery still treated as unvalidated.

## Restrictive recovery-invariant diagnosis — latest user scope

User ended impact diagnostics. Active task: identify the causal failure from preserved recovery evidence and propose the smallest correction; no architecture, optimization, alternative tree representation, expanded repair scope, or dispatch. The broader three-way stay/settlement experiments remain paused and are not part of the proposal.

Two preserved faults have a bounded candidate correction:
1. A day-only stay prefix pairs its motion date/cash with an approval-day general situation. On unit6910 draw123, two otherwise corresponding motion-day states report0 vs306,426available shares. Using the motion-day general snapshot makes both306,426 with the same situation class. Decision day, cash, amount owed, collateral and approval-sized stay_offer remain identical on every draw. Full-path approval records and the security sizing rule are unchanged.
2. Terminal filing emission can skip a still-unanswered distress decision dated before that filing (unit11:cash_out103 before floor filing151). Before emitting such a terminal history, ask any existing candidate that is actually inside the horizon and before the petition; otherwise emit normally. Existing callbacks and branch-sharing checks remain intact.

Capture-boundary discriminator completed on6910 and11: uninterrupted execution at the selected branch and capture→restore→resume produce identical parent states, complete paths/classes/masks, financial-equivalence records and probability-total arrays. They fail with exactly the same errors (0.0017321220508337287 and5.6796928517011125e-06). Thus suspending/restoring these branches is not causing their discrepancies. This is a test of the capture boundary, not a new baseline walk.

The two-part candidate passes11/6910/5171 with419/186/126 histories. Additional checks of their saved outputs use16 independent random assignments and3 deterministic branch assignments each; all per-draw errors are below1e-15. Probability composition and the1e-10 rejection threshold were not changed. The same candidate also passes slow failed unit5533:813 histories, maximum per-draw error1.9984e-15,300.62seconds locally. Its terminal check adds no visits, so this heavy witness is resolved by the general-snapshot correction. No claim is made that all113 rejected units are fixed before their checks run.

Reviewable proposed diff (NOT applied): `var/notes-recovery-causal-check/proposed.patch`. Evidence and reproduction scripts are beside it. It changes only the day-only general snapshot line and terminal `_end` handling. No production source change or dispatch has occurred in this diagnosis.

## Authorized two-patch retry — 2026-10-01

User authorized applying the two proposed changes, focused checks, and rerunning only the113 rejected units. Both source patches are applied exactly as proposed. Focused independent review found no demonstrated blocker; existing notes-decision tests, Ruff on changed Python sources and diff checks pass. Direct checks of the applied production code also pass: unit11 produces419 histories with maximum error8.88e-16; unit6910 produces186 with4.44e-16. No diagnostic monkeypatches were used.

The retry selection was derived from all40 final reports of36909118504:12,402 unique reported assignments,113 failures,12,289 successes excluded. `tools/notes-recovery-retry.json` binds the113 IDs to the original plan SHA and distributes them across40 runners, longest previous durations first. The recovery runner checks that retry assignments contain each selected ID exactly once and match the original plan checksum. Original manifest, parent masks, probability formula and rejection threshold remain unchanged. Each runner uses its available CPUs.

- [x] Apply decision-day general snapshot and earlier-distress terminal check.
- [x] Focused independent review and existing notes regression checks.
- [x] Finish direct applied-code recovery checks:11 and6910 pass, alongside all15 existing notes-decision tests.
- [x] Publish the113-unit GitHub retry: commit7755856, run36939735152,40 runners. Attempt1 failed before recovery because the signed manifest download expired (HTTP400). Refreshed the input links in personal AWS account462947327980, verified manifest/control/plan downloads, and started attempt2 with the same commit and113-unit selection. Existing PR21 branch also updated. Latest attempt2 observation:39 jobs running,1 queued;39 in the recovery step, no failed jobs reported yet. Unit-level success remains to be checked from output reports.
- [ ] Reconcile retry outputs with saved successes; perform assembled global checks before adoption.

No AWS compute, broader stay redesign or baseline walk is part of this retry.

### Retry live monitoring — attempt2

First complete browser log sweep observed40 passing units /11,364 paths and two rejected units:2206 (mass error2.664664363061231e-05) and6258 (0.00014999015997929632). These are partial live observations, not final totals. Units11,6910 and5171 pass in GitHub. Other units continue; no failed output is adopted and no further source change is underway. Logs and reports are in `/tmp/notes-retry-monitor/`.

### Retry completed — 2026-10-01 23:30UTC

Run36939735152 attempt2 finished all40 workers. All113 assignments are accounted for exactly once:107 pass with68,885 saved paths; six remain rejected by unchanged conditional-probability conservation:23,59,63,132,2206,6258. All40 GitHub artifacts exist. Earlier12,289 successful units /514,891 paths remain preserved, making12,396 locally passing recovery units /583,776 paths across both runs; six unresolved units still prevent complete repair adoption. No global assembly or Jev execution has been represented as complete.

Final unit reports/logs and artifact metadata: `/tmp/notes-retry-monitor/`. Partial local artifact copies: `var/notes-recovery-retry/`. Local bulk downloads stopped when disk space ran out; GitHub artifacts remain intact. Removed only the task's recreatable uv dependency cache (93.2MiB) to permit recording the result; no source, evidence or recovery output was removed. No new diagnostic, source patch or rerun was started while monitoring.

### Six remaining failures: causal diagnosis and proposed correction

No production source edits or new remote dispatch in this diagnosis. Two concrete continuation/conditioning defects are demonstrated:

1. `_Walk.settle` uses an any-draw feasibility test, then routes settlement=yes to settled tail on every draw. Unit23 draw1 path274 has zero settlement offer and no settlement booked, but skips enforcement because other draws can settle. Proposed correction: reuse existing draw masks to send infeasible draws directly through the ordinary continuation, with no settlement probability edge; retain yes/no settlement branches on feasible draws. Local-only prototype passes6258 with304 paths (maximum mass error4.44e-16), but does not by itself fix2206 or23.
2. `_Walk.offer` derives `after_failed` from traversal-order `s.failed`. Unit2206 draw383 has a post offering on day74 and a floor2 offering on day137; the later failure incorrectly conditions the earlier offering. Four-path witness54/55/61/62 totals0.9186539379637493 under a valid independent probability assignment. Proposed correction: derive prior-failure context from actual offering failures completed by the current initiation date, excluding future/nonexistent offerings and preserving genuinely earlier failures. This follows QUESTIONS §4.4 N1 and requires per-draw late classification rather than a global traversal flag.

Do not freeze offering facts at traversal prefixes: that diagnostic can normalize sums while changing valid decision-time situations. Do not change probability normalization, inactive-class handling globally, or financial booking rules. Combined correction is proposed, not yet validated across all six. Next: implement only these bounded corrections after authorization, check exact witnesses and all six failed units, preserve passing outputs, assemble and run the existing global gates before Jev/reduction/Russell page. Evidence/scripts are `/tmp/probe_settlement_feasibility.py`, `/tmp/retry-23-simple-witness.pkl`, `/tmp/retry-2206-simple-witness.pkl`, and `/tmp/notes-<unit>-diagnostic.pkl`.

### Authorized bounded corrections and integration — in progress

User authorized both corrections, checks on all six failed units, assembly with preserved successes, and the existing global checks. Implemented settlement eligibility masks and dated N1 prior-failure classes; retained financial booking code. Historical prefix replay preserves original offering keys and settlement labels; resumed callbacks use corrected methods. Focused independent review found no demonstrated blocker. All18 notes/coverage tests pass and Ruff passes. Applied-code recovery passes2206 (313 histories, max4.44e-16) and6258 (304, max4.44e-16). Unit6258's captured parent steps and edges match the original saved diagnostic exactly. Actual new N1 records:2206 checks1822 live rows, including40 day74 rows excluding future failure;6258 checks381 genuine earlier-failure rows. Larger23/59/63/132 checks are running; no complete six-unit or global success claim yet.

Assembly must normalize N1 references in ordinary/composite edges and path classes, rebuild dated class support and inactive-class mappings, and preserve incoming boundaries. Refreshing facts alone cannot reconcile old traversal-conditioned probability identities. Existing passing outputs are retained. No new baseline walk or AWS compute launched.

### Side-conversation edit: dated appeal wording

Explicitly authorized local wording/classification correction: the 14 May renderer no longer treats the `final`/`appealed` routing tags as dated factual assertions. Appeal status comes from the decision date, appeal deadline, and filing mark; the deadline day remains open. The same status partitions situation classes and is retained from the before-answer class when recording answers, so an appeal answer does not condition itself. `interval`, `judgment_status`, and rendered appeal events use these facts. Unknown deadlines do not imply expiry. Financial booking and continuation rules are unchanged.

Seven focused appeal tests pass, covering days112/142/143 against deadline142, unknown/future filing dates, mixed groups, unchanged-scenario independence, and an actual engine appeal yes/no pair sharing its before-answer status. The existing notes-decision and Jev-state checks also pass. Ruff and diff checks pass. These are local checks, not validation of the broader repair or saved production artifacts. Situation-class identifiers now include appeal status; saved question identities/rows require refresh before adoption. No production dispatch, commit, push, or interaction with the main thread's workers was performed by this side conversation.


### Isolated decision-state validation — 2026-10-01 evening

Production adoption remains blocked; the changes below are experimental snapshots on `decision-validation-20261001`, not merged into the working production engine. Heavy local validation processes were stopped at the user's request. GitHub run36952648641 completed eight parallel jobs: the before-question-petition/context candidate passes2206(313 paths) and6258(328), fails23(error.01565762162384654),59(.006062295825078223),63(.00021942700950039473),132(.0016721318924661777). Focused checks32pass/1fail. The same candidate plus continuing from settlement into the existing next stage passes23:2064 paths,error1.3322676295501878e-15. Run36953488967 tests that combined candidate on the other five units, including the two previous passes.

The focused failure is a real saved-prefix incompatibility, not an acceptable normalization adjustment. Run36953094864 isolates it in MERGE_STEPS: grouped steps2/7 remain512draws; step10 changes334→365. On draw2, the earlier response is day96, before-question petition=-1, final petition=96. The later same-day filing must not suppress the earlier response. All six production-test incoming masks remain unchanged under the candidate. The old fixture's boundary must move before its changed ancestor; do not bypass the guard.

Local inspection of the preserved failure outputs (no walking) reduces59/63/132 to the same pair: offering145 success ends at I2settlement; offering145 failure includes appeal122. N1 consequently gets appeal1 vsappeal4, so the two answers are not one normalized conditional question. QUESTIONS D3/C2 specifies acceptance by, and claim release on, settlement date; opening the window is not claim release. Unit23's continued witness has eight earlier-decision alternatives, identical cash/lock/capacity to its old terminal on all57active draws; zero of its2064histories contains multiple accepted settlements.

Independent source review and a short local engine check identify a blocker to applying blanket yes→then_no everywhere: an I1 settlement releases at38 on draw0, but post_trial_ruling at122 remains LIVE (419draws in the fixture). Post-trial questions lack release-date gating. The six recovery units all have I0/I1=no and cannot establish that generic compatibility. Preserve existing engine gates for appeal marks, levy, stays and later-effective settlements; do not invent an immediate agreement date, change financial rules, or silently broaden to a baseline rewalk.

- [x] Offload heavyweight replays; preserve per-job logs/reports/artifacts.
- [x] Complete all six petition-only replays and the unit23 combined discriminator.
- [x] Explain the existing saved-prefix regression with actual dates and masks.
- [ ] Complete the five combined replays and inspect final artifacts.
- [ ] Reconcile the minimal coherent dated-release eligibility correction before recommending global adoption; exact saved-work reuse remains unvalidated.
- [ ] Once the correction is validated and scoped, assemble preserved work, run the existing global gates, then Jev→financial reduction→Russell page.


#### Scope correction: the global appeal-class addition is itself overbroad

Before recommending settlement continuation, independent review traced the actual N1 consumer. N1's situation contract reads `judgment_standing`, not `judgment_status`/`appeal_deadline`; cash_out N1 has no final/appealed tags. Its renderer and record routing do not consume appeal status. Therefore the new global `.appeal1`/`.appeal4` partition can create two independent probabilities for identical N1 Jev inputs. The remaining reduced witnesses do NOT establish a need to expand settlement histories. Withdraw that interpretation and do not adopt blanket settlement continuation or build its newly required early-release gates.

Run36954217413 tests the smaller alternative on all six in parallel: preserve original settlement continuation, retain before-question petition and per-draw context correction, and restrict appeal classification to actual dated-appeal consumers (`judgment_status`, `appeal_deadline`, or final/appealed rendered context). Prototype `tools/decision_validation/scoped_appeal.py`; production source untouched. A quick local check demonstrates N1 shares its class while enforcement and appeal retain distinct classes; all seven existing appeal-state tests pass under the scoped candidate. The ongoing broader continuation experiment remains diagnostic only; any successes cannot justify adoption when the narrower explanation suffices.


#### Residual isolated after removing the unnecessary appeal partition

Scoped appeal classification alone still fails the larger units (63 has83affected draws, versus269with global appeal classes). This is not a complete correction. In unit63/draw8, saved path1074 accepts I3settlement(open124,effective154), stops, and omits the creditor's enforcement decision94. Alternative1273/1274 has that decision94 and levy/response125; it changes which offering can occur. This distinction IS present in cash/standing and cannot be removed from the question classes. `_Walk.levy_first` tests the levy/response125 against window124, selecting settlement-first, although the actor's decision94 precedes the window. A direct local check reproduces oldpredicateFalse, actor-date predicateTrue.

Run36955018504 tests the narrower chronology candidate on all six: compare the existing enforcement decision date with I3opening; use the existing `enforce(..., i3=True)` route, whose response alternatives already return through I3. Preserve settlement stopping and all financial/date formulas. Keep consumer-scoped appeal classification, before-question petition, and the per-draw context candidate. No production adoption. Independent review confirms this targets the omitted actor, but notes the predicate affects many I3 routes and therefore requires all six and a dated financial witness. DECOMPOSITION479 records the old comparator as-built under PR18 fixes; it is not independent authority for either alternative.


#### Six-unit ordered candidate complete — run36955018504

All six passed unchanged incoming-population and three conditional-probability assignments:23=1866histories,59=1780,63=1774,132=1747,2206=313,6258=328;7808total. Keep this as validated proposal evidence, not an adopted production repair or global assembly result. The broader all-stage settlement-continuation experiment also passed but is withdrawn as unnecessarily broad and incompatible with existing early-settlement eligibility. The narrowed candidate leaves settlement termination unchanged.

A concrete unit63/draw8 financial check preserves enforcement decision94, response/levy125, settlement window124 and effective154. Declining enforcement preserves all512 cash/lock/capacity/petition arrays; an actual levy125 eliminates the otherwise$685135.68settlement offer at154. The engine already gives the correct amount when the actor is included before settlement. N1's18rendered situation fields were separately identical across the overbroad appeal-class witness. Seven appeal-state tests pass under consumer-scoped classification. Clean proposal fragments and reports are in `var/decision-state-review/github-36952648641/`.

The earlier-boundary regression initially hit a TEST-fixture issue: its original shortcut borrowed a notes-question key for the verdict, which falsely requires a notes origin in newly included alternative histories. Replaced that shortcut with the real verdict composite, without changing tested engine behavior. Run36955976259 now tests rejection of the old14-step boundary and replay from10steps before the changed response. No guard is bypassed and expected incoming mass is unchanged.

#### Boundary failure resolved on preserved histories; affected regression running

Run36955976259 failed on125/512draws (max0.013479467556775). The evidence-preserving rerun36956510025 reproduced it and saved306raw histories /2671expanded records. The assertion is sound; two code defects were isolated without adding histories:

1. Experimental `context_candidate.split` appended context into a fixed-width Unicode notes-class array, truncating identifiers at widths determined by other draws. Actual sibling suffixes ended in `.ctx6a7564676d656e74` and odd-length `.ctx6a7564676d656e745`. Convert the array to object dtype before suffix assignment. This is a prototype bug introduced during this repair.
2. Completed-history notes reconstruction let a later same-day appeal condition the issuer's earlier filing answer. Actual draw5: ruling109, issuer filing109, appeal109. Filing prevents the later appeal; removing that filing to form before-answer facts incorrectly reintroduced appeal=yes on one sibling only. The isolated notes adapter excludes only a subsequent appeal mark equal to the actor's own decision day, retaining earlier appeals and the preceding same-day ruling. Financial rules and paths unchanged. Focused independent review found no concrete blocker.

Rebuilding notes classifications on the SAME306saved histories with both corrections passes all512draws under all3probability assignments, maximum error5.551115123125783e-16. No normalization/tolerance changes, new paths, or changed cash arrays. Evidence: `var/decision-state-review/boundary-failed.pkl.gz`, `boundary-notes-fixed.pkl.gz`, `boundary-tie-probe.pkl.gz`; scripts `/tmp/reclassify_boundary_notes.py` and `/tmp/reclassify_boundary_tie.py`.

Run36957194362 (commit81f4525) validates the exact two corrections against all six ordered units plus the earlier-boundary replay in seven parallel jobs. Production sources remain unadopted. Do not treat the earlier six-unit pass as acceptance of the changed candidate; wait for this affected regression, then record final results. The local attempted full replay was automatically stopped at900MiB RSS; subsequent local work only replayed saved facts.

#### Corrected candidate regression complete — run36957194362

All seven jobs PASSED. Earlier-boundary replay correctly rejects the incompatible14-step saved boundary, resumes at10steps and emits the SAME306histories on512draws, maxerror8.881784197001252e-16. All six recovery units retain their history counts:23=1866,59=1780,63=1774,132=1747,2206=313,6258=328 (7808total); maximum error1.6653345369377348e-15. No production adoption, assembly or global-check completion is implied.

Local existing appeal/notes/Jev-state checks pass (the obsolete14-step capture test was replaced by the explicit rejection+earlier-replay check). Focused actual-history checks preserve every day/cash/owed/collateral/petition value across512draws: issuer109 excludes later appeal109; holder169 retains that appeal109; prior ruling109 retained. Eleven actual context-class suffixes round-trip completely and match the recorded context. Exact reviewable correction: `var/decision-state-review/boundary-correction.patch`; evidence `boundary-correction-checks.json`, `boundary-corrected/boundary-replay.json`, `boundary-regression-results.json`.

Accountability: the latest125-draw failure was explained by bugs in repair-added code (prototype fixed-width class corruption and completed-history same-day appeal conditioning), not an assertion error or proof of another original-walk gap. Both are corrected and validated in the isolated candidate. All dispatched jobs are complete. Next remains the exact reconciled production diff and saved-work adoption, followed by assembly/global gates/Jev/reduction/Russell page; no broader walk is warranted by this failure.

### Step 1 — native production integration (authorized)

The validated candidate is integrated directly into events/forecast/notes/state14 and the recovery capture tool. Application execution imports no experimental adapters or runtime source transformations. Historical node/context/order helpers live only in `tools/notes_resume.py`, where saved-prefix capture restores old identities before corrected execution; the unchanged-population guard remains mandatory.

The renderer now receives the per-scenario context encoded in each class. Classification and wording share `uses_appeal_status`: a derived appeal context states its actual filing date, while deadline status remains partitioned only for consumers that use it. This completes the context consumer without expanding validated grouping. Focused independent review found and resolved that renderer integration gap; no remaining concrete blocker was found.

Local44focused checks pass (decision petition snapshots, dated appeal/notes facts, context identity and independence, renderer consumption, I3 dates and unchanged declined-enforcement cash, Jev states and pool coverage); Ruff and diff checks pass. Native run36958624797 at2f46858 runs all six units without candidate monkeypatches and compares their exact incoming state, paths/classes/edges and financial-equivalence records to candidate36957194362. Units2206/6258 and the306-history earlier restart have passed; four larger comparisons are still running. Standard PR CI will run in parallel on the review commit.

- [x] Integrate the validated rules into ordinary application methods.
- [x] Preserve historical capture and reject incompatible incoming populations.
- [x] Complete per-scenario context rendering with consumer-scoped deadline facts.
- [x] Focused local checks and independent review.
- [ ] Finish native seven-job parity and standard PR CI.
- [ ] Record the completed step1 review commit and validation evidence.

Step2 assembly/adoption, global checks, Jev judgments, reduction and Russell page have NOT been started by this integration task.

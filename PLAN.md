# Repeatable event-analysis flow: plan and todo list

Updated: 2026-10-01. Owner: Codex, working with Owen. Current focus: adopt the now-complete 100-shard walk into the repeatable pool → Jev → financial reduction → page flow. Live end-to-end validation remains outstanding.

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

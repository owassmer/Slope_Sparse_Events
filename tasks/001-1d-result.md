# 001-1d result — incomplete; not a prototype measurement

The requested dated shared-state prototype was **not implemented**. This task is not done. No production walker, pool, reduction, or recorded run was changed. No cloud work or judgment calls were made.

## Local observations

Hardware: Apple M2, 8 logical CPUs, 8 GiB RAM, arm64. Commands below ran in this checkout. The two Python jobs overlapped, so these are diagnostic timings, not an uncontended performance comparison.

```
/usr/bin/time -l uv run pytest -q tests/test_dated_answer_domains.py tests/test_ripe_after_levy.py
/usr/bin/time -l uv run python var/diag/rewalk_other.py 0
```

- All 10 existing dated-domain/payment tests passed. Elapsed process time: 183.12 seconds; maximum resident set size reported by `time`: 591,921,152 bytes.
- The current walk on `other_paths.json` index 0 (draw 279, prefix length 8) emitted 2,243 paths in 235.435 seconds, as timed inside the existing diagnostic. Whole-process time, including setup and full-path domain checking: 278.96 seconds; maximum resident set size: 721,616,896 bytes. This does **not** separate cold compilation/setup from warm walking.
- Full-path checking still found 106 infeasible `cash_floor/@2=initiate_offering` answers. The first witness includes `post_trial_ruling/set_aside`; its floor question is day 179, group 0, and both saved and recomputed support retain draw 279. This reproduces the known failure, rather than validating the current walk as an oracle.
- Logs are local, gitignored: `var/diag/001-1d/domains.log` and `var/diag/001-1d/rewalk0.log`. The existing rewalk script depends on the local `var/diag` fixtures; it is not a newly self-contained benchmark.

## Why these are not shared-state results

Inspection confirmed the prerequisites identified in the design, but did not implement them:

- `Chain.advance` determines some dates by calling `probe.step(node, ctx, branch)` on a clone. That is an answer-dependent booking probe, not the read-only unresolved-decision candidate API required here.
- `Chain.until` advances waiting **already selected answers** and pending levies. It does not enumerate all active unresolved chains or guarantee that an earlier ruling is resolved before a floor question is offered.
- `processor.run_daily` selects a fixed `days // 2` checkpoint for eligible cash-only runs. `_daily_kernel` can restore that checkpoint but then runs to the horizon. It is not a stop-at-next-decision processor. Treating its cache hits as newly advanced draw-days would misstate the work.

These are implementation work still owed, not an external dependency or a request to relax the design. Replaying completed paths, sorting their decisions afterwards, or interning their cash totals would not meet this task.

## Required comparison still missing

No measurements exist from this attempt for per-draw versus cross-draw batching; exact continuation states and edges; cash/obligations-only versus earlier-event-tagged keys; distinct reduction payloads per shared state; advanced draw-days; question situations; raw versus merged paths across all required roots; synthetic terminal-mass conservation; or reduction runtime. Other roots were not exhaustively rewalked here. Passing the existing tests does not supply these missing comparisons.

## Running product and gates

WORKS — `uv run ruff check .` passes.
WORKS — The 10 existing tests above pass, including the mixed-order synthetic distress case and all saved payment-family populations.
WORKS — Started the local product with `drive start`; `drive run -- finance check` reproduces all reference values.
WORKS — Opened the lead analysis page; its recorded situation and forecast render.
BROKEN — The browser reported HTTP 500 from the lead run's `/reweight` route while opening the page. This was not repaired or attributed to a new code change; no application code changed.
BROKEN — The current draw-279 rewalk retains the 106 infeasible floor answers above.
BROKEN — The requested shared-state prototype and measured comparison are absent. The full pytest suite was not run; no full-suite pass is claimed.

## Full-tree projection

**Unavailable, on one machine and on the GitHub fleet.** There is no measured distinct-state growth, overlap, payload growth, or batched transition cost from which to project. The one-draw existing-walker timing above cannot establish a 512-draw speedup, a no-sharing full-tree upper bound, or the dominant component of a replacement walk. No fleet was started. A numerical runtime or dominant-work claim here would be invented.

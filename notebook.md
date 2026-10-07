# Notebook

Observations waiting for Owen: the verifier's repeated findings, scheduled scans, and Owen's own notes from
reading output. One entry per observation, dated. Nothing here is fixed when it is noticed.

## 2026-10-06: the shape on pick-up (coordinator, recorded and not fixed)

### Credentials in reach
- The main checkout's `.env` holds `OPENROUTER_API_KEY` (Jev), `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (Clef). Every worktree under `.claude/worktrees/` reads it (`app/config.py` `_dotenv` looks three directories up). The only guard is `SLOPE_JEV_CACHE_ONLY=1`, which a process has to opt into; `drive`, `scripts/work` and `scripts/verify` set it.
- `~/.aws/credentials` holds long-lived keys for the company account `381492272971` (`default` = `qed-slack-user`, plus `qed-user`, `qed-slack-user` and `r2`). Any agent shell can use them. The scripts hide `~/.aws` through environment variables, which an agent can undo.
- `~/.aws/config` profile `slope` is the personal account's `AccountFullAccessRole` for as long as Owen's `aws login` session lasts.
- The company account still has IAM roles `slope-walk-20260930` and `slope-walk-20260930-github`, with GitHub OIDC trust for this public repo's branches `walk-cloud`, `walk-cloud-replacement` and `walk-refine`. Their bucket was deleted on 6 Oct.
- The personal bucket `slope-walk-462947327980-20261001` grants the company user `qed-slack-user` list, read, write and delete.
- GitHub: `gh` and `git push` are authenticated through the keychain with repo scope, so any agent can push to any branch, and a branch name can start a fleet (below). The repo has 20 Actions secrets, among them AWS session keys `FRESH_WALK_*` (expired 6 Oct 06:19 UTC) and signed S3 links (`MASS_MANIFEST_URL` and others).
- The Claude subscription login (the `claude` CLI) is reachable from every agent shell, including the verifier's: it needs it for its own model, and `slope investigate` could use the same login.
- `~/.hermes/profiles/connor/cache/scratch/split/` holds `ssm.py` and watch and stop scripts that send commands to the AWS hosts, plus a detached worktree `base` at `c837edf`.

### Worktrees and branches
- Six worktrees in `.claude/worktrees/`: `factory`, `step-9-memo` (1.1 GB with `var/`; an uncommitted `tests/test_assumptions.py` change and an untracked `.playwright-cli/`), `step-9-jev-rule`, `diag-joins`, and two detached ones (`fresh-walk-3`, `step-9-old` with untracked files). A seventh is outside the repo in the Hermes scratch directory.
- 49 local branches (many with gone upstreams) and 59 remote ones, most of them named for a fleet or a repair pass.
- PR bases are tangled: PR 20 is based on `step-9-int`, PR 22 on `walk-run`.

### The workflow directory doubles as a compute fleet
- `step-9-memo` carries 28 workflows, and 27 are already registered in Actions. Each starts on a push to its own branch name (`walk-run`, `walk-run-*`, `fresh-walk`, `pool-run-*`, `reduce-run-*`, `question-mass-check`, `notes-*`, ...). Five reach AWS through secrets.
- A branch name is therefore a launch button, invisible to anyone choosing a name, and merging PR 21 brings all 28 into `main`.
- Fleets were steered by editing GitHub secrets (signed links) and pushing commits: deployment and code share one channel.

### Two ways to do one thing
- The judgment model: OpenRouter (the default, "while TypeSafe sign-up is restricted"), TypeSafe, and on step 9 Cloudflare Clef (`JEV_PROVIDER`, `SLOPE_JUDGMENT_PROVIDER`). Documents and the page say "Jev" throughout.
- Pages: `analysis.json` + `analysis.html` (Synergy, recomputed with POST `/runs/<id>/analysis`), `page.json` + `page.html` (Akoustis, POST `/reweight`), the `/dev/akoustis` development page, and on step 9 a fourth (`tables_page`).
- Entry points: `slope analyze --run`, and on step 9 `slope analyze-case` with a local path and an S3 `--pool` path.
- Adding a case: per-case `make_bank_feed.py` and `make_run_inputs.py`, a kit mission, `SNAPSHOT_MISSION_KEYS`. There is no single way.
- `outcomes/` holds both the isolation-protected reveal files (`<case>.json`, read by the viewer) and the factory's outcomes (`<NNN>.md`). Moving the reveal files means changing `app/config.py`.

### Files and places that invite mistakes
- Very large modules: `app/disputes/forecast.py` (1,953 lines on main, far more on step 9), `app/agent/tools.py` (1,520), `app/analysis/events.py` (1,423), `app/analysis/page.py` (1,159).
- Party names in code, against spec §16.5: `app/disputes/akoustis_pre_d.py`, the `/dev/akoustis` routes, `tests/akoustis_fixture.py`.
- `slope analyze` writes into tracked run directories, and the smoke test rewrites the tracked `runs/recorded/runtime_smoke.json`.
- The kit's `PACKAGE_MANIFEST.json` is refreshed by hand; no test guards it.
- Stale reference text: the kit README (Synergy as lead), build spec §2 and §9 (ChromaDex), the extension spec's "one actor's decision" (PR 20 changes it).
- The run index lists 15 Synergy runs, most `INCOMPLETE_REVIEW`, above the two Akoustis runs.
- Test traps (from Connor's skill): `SLOPE_JEV_CACHE_ONLY=1` breaks `tests/test_semantic_layer.py` and `tests/test_investigation_tools.py`; `pytest -q` prints no summary; digest pins need rounded floats across platforms.

### Checks that accept their own results
- The "independent focused reviews" on PR 21 were written by agents of the same session that wrote the code, and posted under Owen's account.
- Step 9's global probability check and 100-question reading were run by the agent that wrote the code under check.
- Digest pins (`tests/test_opening_exposure.py` and others) take the current output as the truth.
- An investigation sets its own status (`CANDIDATE_READY`) from its own gates, and escalations are "non-blocking on the reviewer checklist".

### Gates and enforcement
- `main` has no branch protection, so `.github/CODEOWNERS` enforces nothing until a rule requires code-owner review.
- CI has a 10-minute timeout. On `step-9-memo` it does not pass (the assumptions fixture, `boto3` imported in `tests/test_tail_walk.py`, the full-tree test timing out).

### Standing cost and owner decisions
- The personal account bills about $143 a month while idle: three stopped hosts' disks (1,024 + 60 + 60 GB gp3 with extra IOPS on the 1 TB, about $121) and the bucket (about 938 GB, no expiry, about $22).
- Automatic shutdown was prohibited after the 3 Oct loss; idle hosts then cost about $55 on 5 Oct. One rule should own both.

## 2026-10-06: first verifier pass (outcome 000, the map)
- Verifier (its line was lost with its throwaway checkout, restated here): two pieces of setup are missing from a fresh checkout. The economic-assumption variants' page states (`var/analysis/<run>/assumptions/*.page_state.pkl.gz`) are gitignored, so every assumption radio returns HTTP 500 there. The evidence snapshots need `slope evidence build` before `slope evidence search` works.
- Coordinator: to reach evidence search, the verifier ran `slope evidence build` (local, hash-checked, no billed call). That is a build step, and Owen's rule is that the verifier never triggers compute. Whether a local build counts is Owen's call.
- Coordinator: the verifier's own notebook edit was discarded with its throwaway checkout. `scripts/verify` copies back only the list and the feature map.
- Coordinator: six BROKEN lines on the product as it stands: the assumption radios (500 in a fresh checkout), the assumption section ignoring overrides, "Jev $771k" labelling an overridden value, the 20 Jun page blank (an old `page.json` shape), unknown override names silently ignored by `/reweight`, and evidence search before a build. The map is not yet checked against Owen's own use.
- 2026-10-06, task 001-1: `part1840009.pkl` draw 145 closes the ripe-response offering on day 101 before the floor on day 155; the old prefix domain is wrong, not the `nocapacity` snapshot. Rewalk rather than re-pool; the self-contained regression and local check limits are in `tests/test_dated_answer_domains.py` and `tasks/001-1-result.md`.

## 2026-10-06: running the loop
- Coordinator: the first `scripts/work` on task 001-1 started twice in the same worktree (13:29 and 13:34), apparently from one command being run twice. Both were stopped and one restarted under a lock. If it recurs, `scripts/work` should refuse to start while another worker holds the worktree.
- Coordinator: `scripts/verify`'s final check looked for lines starting `WORKS`, while the verifier wrote `- WORKS`, so a clean list would still have exited 1. Fixed with the live-run change (the check accepts both).
- Task 001-1b: ripe responses must respect the pending-levy bound in `Chain._upto_dated`; the six saved payment paths now agree prefix-to-full. Do not rewalk yet: part1830010 draw 279 also exposes a day-74 ruling moving a floor from day 77/group 2 to day 179/group 0; full reproducer in `tasks/001-1b-result.md`.
- Task 001-1d remains incomplete: `processor.run_daily` resumes only at its fixed midpoint and `_daily_kernel` runs onward to the horizon; cache-hit counts are not measurements of decision-boundary advanced draw-days. Baseline observations, not prototype results, are in `tasks/001-1d-result.md`.
- Task 001-1e: `Chain.next_decisions(Cursors(...))` dates enabled prerequisites without answers; cursors belong to the continuation, not the cash state. Recompute after the selected boundary: draw 279's old day-77 floor includes a still-pending levy, while the answer-free frontier correctly stops first at the day-74 ruling.
- 2026-10-06, coordinator: 001-1d asked one worker run for a whole walk prototype plus measurements; it built nothing and said so. 001-1e, cut to one piece (the read-only decision frontier), landed in one run. A task should be one piece a worker can finish and check in one run.
- Task 001-1f: after `Chain.until` books waiting answers, call the existing `restay` normalization before reading the next frontier (as `advance`/`finish` do); otherwise draw 145 reports floor 164 until a no-effect listing step re-sizes security and reveals the actual floor at 155, behind the already selected day-159 boundary.
- Task 001-1f: when the ruling precedes an already queued writ (draw 279: ruling 74, levy 77), the I1 response is unavailable but `Chain.response_day('post')` is 77; the post-ruling continuation must expose that response rather than silently canceling it with the I1 window.
- Task 001-1f: partition the forward chain with existing `Chain.sliced` and `Draws.sub`, retaining a map to original draw IDs; otherwise a one-draw root repeatedly simulates 511 unsupported draws. A fresh `Draws(1)` would resample the wrong trajectory, not perform this partition.
- 2026-10-06, coordinator: tasks 001-1f and 001-1g told the worker to explain every difference from the current walker, which made the walker known to be wrong the reference. The worker spent about 1 h 45 min reconciling histories one by one. Judge a replacement by an invariant on its own output (001-1h), never by agreement with what it replaces.
- 2026-10-06, coordinator: the Codex plan hit its usage limit three minutes into 001-1g; the worker's run ended "finished exit 0" with nothing committed, which reads like a finished run. Read the end of the log before trusting a worker's exit status. The worker runs on Claude until Codex resets.
- Task 001-1h: `Chain.restay` must re-size when a waiting decision or a pending levy is queued, not only when cash is booked (`_stay_key`); otherwise a one-draw slice keeps a stale stay and the prefix cache can return a different answer than a scratch replay. Check a walk with `tests/walk_order.py ROOT` (its own histories, no reference walker).
- 2026-10-06, coordinator: two worker leaks on this Mac: a `drive` viewer from an earlier task left running for 7 hours, and full local `pytest` runs (2.1 GB, 30+ minutes, started twice) to list known failures. `scripts/work` now stops the product when the worker exits; full suites go to GitHub through `run.txt`. AGENTS.md's "Gates: uv run pytest" invites the local full run; worth a line when the guidance is next touched.

# 001-1e — answer-free decision dates

## Interface and scope

`app/analysis/frontier.py` defines the continuation's `Cursors`: enabled unresolved prerequisites for litigation, distress, listing and notes. Each `Decision` contains only a node and context, never an answer. `Chain.next_decisions(cursors, support)` returns immutable per-draw candidate dates, the earliest candidate per chain (`for_chain`) and globally (`pick`). `BIG` means none pending in this horizon; `pick == -1` means no candidate. An unavailable cursor is retained, not marked completed. The caller owns cursor advancement/completion, just as it owns which decisions are unresolved; this API does not infer a new legal control flow from cash balances.

Date discovery calls existing state/timing readers, not `step`, `advance`, `_trace`, or a cloned answer. It can populate calculation caches but does not change booked semantic state. `Chain.advance` also no longer steps an answered clone to discover a date. Settlement-window arithmetic is shared with booking. Amounts and booking rules are unchanged.

Pending levies are included as deterministic boundaries. A levy-day response sorts before its levy; a ripe response does not. The levy sorts before other same-day questions, and floor before cash-out. Remaining equal-day prerequisites retain cursor order. The current production walk still uses its replay scheduler: this delivers its requested prerequisite, not the shared-state walker or resumable processor from 001-1d.

## Saved failures

- Draw 145, `SAVED[:8]`: the earliest candidate is the unresolved ripe response at day 96, before the prospective floor. The saved completed path confirms that response at 96 and the floor at 155. Discovery assumes neither offering initiation nor its day-101 close.
- Payment families `NONE[:8]` (279, 383) and `OFFER[:9]` (7, 63, 149, 215, 225, 325, 329, 380, 393, 406): the pending levy is earlier than the ripe response. Even with only the notes cursor enabled, selection stops at the levy, not the ripe payment's prospective cash.
- Draw 279, `NONE[:8]`: ruling at 74, pending levy at 77. The old floor replay books that levy and reports floor 77. The read-only frontier reports a prospective floor of 117 on currently booked cash, but correctly selects ruling 74 before either. Replaying set-aside at 74 removes that floor. Reporting 77 as final without resolving 74 would repeat the original mistake.

Tests also cover per-draw mixed ordering, same-day ties, support/petition exclusion, re-enabling a previously unavailable candidate, cold reads forbidden from booking, byte-identical warm state, and dates matching the existing booking methods across answers on all 512 draws.

## Local timing

Command: `uv run python tests/benchmark_decision_frontier.py`.
Apple M2, 8 logical CPUs, 8 GiB RAM, macOS 26.2, arm64. The pytest process was paused during timing; no competing test job ran. Imports precede setup timing. Each state contains 512 draws, all supported. Each replay selection makes six actual `_Walk._trace(prefix + (candidate,))` calls; existing prefix/financial caches remain enabled. Repeated-query figures are medians of 25 calls.

| Root | State setup, ms | Replay prefix setup, ms | First frontier, ms | First six replays, ms | Repeated frontier, ms | Six cached replays, ms |
|---|---:|---:|---:|---:|---:|---:|
| ripe96 | 403.64 | 46.36 | 0.450 | 164.42 | 0.238 | 0.103 |
| ruling74 | 28.12 | 37.55 | 0.387 | 164.98 | 0.232 | 0.112 |
| payment_offer | 30.00 | 42.36 | 0.407 | 159.66 | 0.249 | 0.112 |

Discovery avoids the first candidate replays. Already cached replay lookups are faster than rediscovering dates; this is not a claim of universal speedup or a full-tree projection. Raw output: `/tmp/001-1e-benchmark.log`.

## Running product and tests

WORKS — `uv run ruff check .` and `git diff --check`.
WORKS — 59 tests passed: `test_decision_frontier.py`, `test_dated_answer_domains.py`, `test_ripe_after_levy.py`, `test_dated_writs.py`, `test_cash_processing.py`, `test_decision_snapshots.py`.
WORKS — The local engine benchmark calls the new API on all three saved prefix families, without a model or cloud call.
WORKS — Started this checkout with `drive start`; `drive run -- finance check` reproduced all reference values; `drive run -- version` returned 0.1.0.
WORKS — Opened the lead analysis page, then clicked Investigation record; both recorded views rendered.
BROKEN — Opening the lead page still returns HTTP 500 from `/reweight`, as already reported in 001-1d. No recorded run or page/reweight code was changed.
BROKEN — Full `uv run pytest -q` was stopped after about 14 minutes, during `test_pending_claim.py::test_1_every_path_family_sums_to_one`; 280 tests had completed (260 passes, 19 failures, 1 skip). Every one of those 19 failures reproduced in a separate process loading `HEAD:app/analysis/events.py` instead of the edited engine. They span analysis, assumptions, court facts, investigation tools and notes decisions. No full-suite pass is claimed. Logs: `/tmp/001-1e-pytest.log`, `/tmp/001-1e-baseline-failures.log`, `/tmp/001-1e-regressions.log`.

No cloud, judgment calls, recorded-run changes, or full-tree rewalk.

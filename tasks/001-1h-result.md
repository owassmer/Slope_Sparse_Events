# 001-1h: the walk never skips a decision, checked on its own output

## The check
`tests/walk_order.py` (`Checker.history`) judges each emitted history on the root's draw by itself, with no second
walker as the reference:
- Dated facts: the plain engine replays the whole history from scratch (no prefix cache, nothing shared).
- Pending decisions come from the question rules written in the check, independent of the walk's continuations.
  The rules: ruling → I2 settlement and post response; I2 'no' → appeal → I4 settlement → post default; set-aside,
  agreed settlement, payment or filing close the dispute; the response's open/closed state. Listing, delisting and
  distress follow from the steps.
- At every decision asked, the 001-1e frontier (`Chain.next_decisions`), on the state before that answer with
  every booking dated before it made, may report no other pending decision dated earlier (same-day ties in the
  engine's phase/cursor order) that is due. Due means a probe replay dates it inside the horizon, before any
  petition, and offers it (settlement amount > 0, option group ≥ 0). A decision found not due is resolved as the
  walk resolves it without a question.
- After the last decision, nothing pending may be due inside the horizon.

## settle I4 on none279: the walk skipped it, and the cause was in the engine
Dated: on draw 279 the stay approved on day 112 opens the stay-window settlement that day, with a $2,104,541.92
offer. The chronological walk selected it at 112, but `_Walk.settle`'s replay said "never, $0", so it was
dropped with no step.

Cause: `Chain.restay` re-sized a stay only when booked cash or the amount owed changed. Queuing the day-77 levy,
or a waiting offering dated before the approval, books nothing, yet `seen_at` includes them in the balance the
security is sized on. On the full 512-draw chain, bookings on other draws happened to force the re-size. On a
one-draw slice nothing did, and the walk's cached whole-draw prefix, resumed on the one draw, carried a stale
stay. Fix (`events.py`): the re-size key (`_stay_key`) also covers the waiting decisions and the pending levy.
Tests: `test_stay_resizes_when_a_waiting_decision_or_levy_changes_its_balance` and
`test_walk_order_check_finds_the_skipped_stay_settlement`. `test_day74_ruling_precedes_day77_floor`'s prospective
floor moves from 117 to 179: the stay is sized after the queued levy and locks nothing. 117 was the stale lock.

## Results on none279 (local, Apple M2, one walk at a time)
| walk output | histories | violations | check |
|---|---:|---:|---:|
| 001-1f's saved chronological walk (before the fix) | 1,840 | 108, all `settle I4` (14 missing, 94 skipped) | 187 s |
| chronological walk with the fix | 1,910 | 0 | 207 s (walk 639 s) |

The full set of roots is in `run.txt`, one per job (report: `var/diag/001-1h/<root>.json`), with the focused tests.

## Gates and product
- WORKS: `uv run ruff check .`; focused tests pass: `test_chronological_walk.py`, `test_decision_frontier.py`,
  `test_ripe_after_levy.py`, `test_cash_processing.py`, `test_decision_snapshots.py`.
- WORKS: `drive run -- finance check` reproduced all reference values. The lead page and its investigation record
  render.
- BROKEN (already there at HEAD, checked with HEAD's `events.py`): `/reweight` on the lead page returns 500
  (`distress_day` int(''), as in 001-1d/1e). `test_dated_answer_domains.py` fails 2 cases at HEAD too (cash_out
  day 161 → never, since 001-1g's `other5` change). Neither was changed here.
- Recorded runs were not rebuilt. The re-size fix can change stay sizing in a fresh tree where a levy or waiting
  decision is queued before a stay's approval.

No cloud and no judgment calls.

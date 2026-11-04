# 001-1j: a stay's security is computed from the state, never re-sized on triggers

## What changed (`app/analysis/events.py`)
- `restay`, `_stay_key`, `_stay_cv`, `_restaying`, `_sizing_stay` and every `restay()` call are gone (in `advance`,
  `_upto_dated`, `finish`, `levy`, `settle`, `release_lock`, and in `chronological.py` and `tests/walk_order.py`).
- A stay keeps only its terms (approval, motion, approved or denied, the trigger days at its walk, the denial mark
  before it). Daily processing never books a stay's lock into the event cash.
- `Chain._stay_effects()` computes every approved stay's security from the chain's state when read. The rule is the
  same as before: sized on its approval day, on the balance after every event dated before it (`seen_at(..., levy=True)`),
  never above that balance, nothing on or after a petition or once the dispute has ended, and released on
  `release_at`. Each stay is sized on a copy of the chain without it, so the other stays' locks and releases are
  computed on that copy too. The results are the lock array, the stayed day and mark, the lock still held, and each
  stay's facts and court question.
- Results are memoized on a digest of the chain's whole state (`_state_digest`): the booked event cash by its version
  and every other field except a short `STATE_SKIP` list. That list covers memos of other state, the per-step record
  (`rec`, `grec`, `_grp`, `late`, `_last_node`, `_booked_to`, `_finished`) and what the sizing itself produces.
  A field nobody lists is in the key, so a new route that changes an input changes the key.
- Everything read from the security is read through it:
  - the chain's `ev` is a `LiveEventCash` whose `lock` is `Chain.lock_cash()`;
  - `stayed_from`, `lock_amount` and `lock_day` are properties;
  - the stayed mark is read with `marks_now()`;
  - `cum`, `tau`, `cash_out` and the engine run key are memoized on `_cash_v()` (cash version plus lock digest), so
    the available cash and the cash triggers follow;
  - `_new_money_after` reads the computed releases;
  - a trace keeps a plain `EventCash` with the lock as read at `finish`.
- `_stay_effects()` is also evaluated at the end of `advance`. Nothing depends on that call for correctness. It only
  lets the read-only frontier (`test_frontier_never_books_an_answer_or_changes_semantic_state`, which forbids
  `clone`) find it already computed.
- Facts for a stay that was not approved (its proposed security, for the court's question and the denial mark) are
  sized on a copy without it, at its walk and again at `finish`, as before. They are kept in `_stay_read`, apart
  from the stay's terms.
- `court_questions` asked `"question" in st`, which would have skipped every court replay once stays held only their
  terms. It now asks `stay_facts(i)`. `test_court_identity_uses_completed_before_court_row_after_intervening_levy`
  caught this.

## Tests (same meaning)
- Unchanged and passing locally:
  - the four pinned cases: other5 floor (`test_floor_after_an_offering_that_closes_before_the_stay_approval`), none279
    day-112 settlement (`test_stay_resizes_when_a_waiting_decision_or_levy_changes_its_balance`,
    `test_walk_order_check_finds_the_skipped_stay_settlement`), draw 145's cash-out
    (`test_dated_answer_domains.py`), and 001-1f's `until` route (`test_chronological_walk.py`);
  - `test_decision_frontier.py`, `test_ripe_after_levy.py`, `test_cash_processing.py`, `test_decision_snapshots.py`,
    `test_views.py`, `test_stay_motion_history.py`, `test_prepared_question_wording.py`.
- Adapted to the new internals:
  - `test_stay_sizing_reentrancy.py::test_initial_sizing_still_releases_prior_stay_in_read` still checks that a release
    inside the later stay's sizing read releases the prior stay's lock in that read, and that the later stay locks
    nothing. It reads `stay_facts` instead of `st["lock"]`, `_sizing_stay` and `_restaying`.
  - `test_dated_writs.py`: a foreign `_stay_memo` key replaces the foreign `_stay_owed` key.
  - Two `seen_at` patches accept the new `inplace` keyword.
- Wider local run, same failures as HEAD before the change (checked on a HEAD checkout):
  `test_chains.py::test_each_court_ruling_on_a_motion_has_the_facts_of_its_own_day`,
  `test_notes_decision.py::test_later_walked_settlement_restores_a_live_notes_decision`,
  `test_notes_decision.py::test_prompt_uses_actual_context_and_classes_override_stale_prefix`.
  `test_pending_claim.py`'s module fixture (`fc.paths`, all 512 draws) did not finish in about 55 minutes either before
  or after the change, so it is left to `run.txt`.

## Walk on the local root (`tests/walk_order.py saved`, draw 145; Apple M2, one process)
| | histories | violations | walk | check |
|---|---:|---:|---:|---:|
| before (HEAD 2405e70) | 360 | 0 | 165.7 s | 14.0 s |
| after | 360 | 0 | 67.0 s | 23.2 s |

- The walk's emitted histories (`var/diag/001-1h/saved-chronological.pkl`) are byte-identical before and after.
- The check (plain replays from scratch) is slower: each cash read now hashes the state, and the sizing is redone
  whenever the state changes, not only at the old trigger points.
- Slowest tests, before → after: `test_dated_answer_domains` rewalks 91 s → about 187 s each;
  `test_saved_root_domains_and_synthetic_mass[ruling71]` 104 s → 172 s; `[ripe96]` 51 s → 77 s.

## Cached the same way elsewhere on `Chain` (not changed here)
- `share_price` is recomputed only when `_owed_key()` changes (`_price_key`), a hand-kept key of what the price reads.
- The at-the-market booking (`_atm_rebook`) books derived proceeds into the event cash, and rebooks only when
  `_eq_key()` moves. `_eq_v` is bumped by hand "where any of them changes" (five sites). `_offer_memo` is keyed
  on the same `_eq_v`.
- A priced coupon (`_coupon_rebook`) stores its cash part in `coupons` and is rebooked only when `_atm_rebook` runs.
- `collateral_required` is the last stay step's read, copied into later waiting decisions' records (`upto`).
- Each stay's `triggers` are the trigger days at its walk, exported as its facts.
- An unapproved stay's denial mark and facts are sized at its walk and again at `finish`.

## Product
- WORKS: `drive start`; the lead page `/runs/akoustis_20240514-agent_plus_jev-20260928T052641Z` renders. Moving the
  featured judgment's slider to 30% moves collected from $959k to $876k.
- WORKS: `drive run -- finance check` reproduced all reference values.
- Recorded runs were not rebuilt.

No cloud and no judgment calls. The full set of roots, the focused tests and the full suite (one file per runner)
are in `run.txt`.

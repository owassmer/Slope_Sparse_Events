# No question carries its future (§1)

`walk_order.FutureChecker` compares every live recorded question occurrence on
an emitted history with a replay from which all steps dated after that question
have been removed. It compares the key (including amount/stage/interval), the
path's recorded class and the recorded facts, in exact cents. Same-day engine
order and known scheduled dates are retained. An interval whose prerequisite
has not happened is **unopened**, not assigned a guessed replacement interval.

`question_history.question_records(walk)` captures the actual recording boundary
while either walker runs; histories alone contain keys/classes, not occurrence
facts. Completed records are attached to their whole emitted history, and early
records to their probe prefix. The checker uses those records, not a second walk
as its reference. Static verdict-form questions without dated cash records and
inactive class domains are not counted as live dated occurrences.

Differences are returned, not repaired. A deletion witness identifies a later
step whose removal breaks agreement with the recorded key, class or facts. A
record/replay mismatch without such a witness is reported separately, not called
a proven future dependency. Counts are **question/history/draw occurrences**;
one question shared by several histories is checked on each history.

## Reproduce without judgment calls

```sh
# Full review-date walk: runner only, even with one draw (see run.txt).
# uv run python -m tools.check_question_futures --walker current --draws 1 --row 0
uv run python -m tools.check_question_futures --walker chronological --root saved
# Recheck the same emitted histories and recorded facts, without walking again:
uv run python -m tools.check_question_futures --walker current --saved
uv run python -m tools.check_question_futures --walker chronological --root saved --saved
uv run pytest tests/test_question_futures.py
```

The current walker starts at review, without a root cut or a history limit. The
chronological walker uses the existing `saved` root (native row 145). One-draw
runs preserve the native draw's keyed uniforms and operating cash; they do not
regenerate a different one-draw population. The adapter raises on any attempt to
hydrate or request a judgment. The full-tree one-draw command and both 512-draw
commands are in `run.txt`. No cloud work was submitted. This diagnostic does not
have a full-tree segment mode; substituting cut-root runs would omit the early
settlement questions.

Data: `var/diag/001-1n/` (`*.pkl` histories and recording sidecars, `*.json`
summaries by question node, `*-violations.jsonl` full differences and deletion
witnesses). The saved-root check includes questions below the root, not questions
whose answers were supplied as its initial state.

## Completed local results

Saved chronological root, native draw 145: **360 histories, 4,114 live dated
question occurrences, zero violations**. By question node:

| Question node | Checked | Violations |
| --- | ---: | ---: |
| holders_act_judgment | 359 | 0 |
| judgment_response | 1,192 | 0 |
| offering_closes | 677 | 0 |
| petition_on_notes | 288 | 0 |
| financing_at_floor | 416 | 0 |
| holders_involuntary | 173 | 0 |
| bid_compliance | 314 | 0 |
| hearing_request | 250 | 0 |
| holders_act_delisting | 125 | 0 |
| petition_cash_out | 320 | 0 |

The focused tests detect both future-labelled settlement intervals: I1 after
motions=yes and I2 after motions=no. They also protect native draw identity,
wide-population row selection, unchanged questions followed by later decisions,
and failure on missing recorded facts. The existing start diagnostic confirms
the 22 May offer / 19 June motions dates on native draw 0.

The local full review-date walk produced no saved histories or report. At the
user's stop request, both its Python and uv processes were already gone.
**There is no full-tree result yet.** Its queued
one-draw run is distinct from the queued 512-draw runs. Neither the walker nor
the question definitions were changed.

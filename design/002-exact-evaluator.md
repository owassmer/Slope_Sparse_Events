# 002: the exact evaluator

Owen, 28 October: replace the answer-first sampling with an exact evaluator, compiled, that walks the whole tree on a
laptop in under an hour; and identify questions faithfully. This note is the design; it supersedes the sampling parts
of `002-answer-first.md` (the worst-path search and the provable bounds stay).

## Why, in numbers

- The full tree is about 8 million joint histories (all 512 draws walked together), about 100 million history-draws
  (est-2 and the walked waves, October).
- Python today: 0.28 s per joint history to walk, 0.4 s per history for the cash analysis; about 1,500 core-hours in
  all. An hour on 8 cores is 8 core-hours: about 200 times faster is needed.
- Profile of the exact walk (512 draws, 4 minutes, 886 histories): about 60% re-tracing whole histories from the
  review date to record deferred questions, about 45% (inside it) booking at-the-market sales under the baby-shelf
  limit in a Python loop, and 274,000 Python calls per decision. The rules are cheap; Python running them as many
  small calls, and re-running whole histories, is not.

## Shape

**One tree per draw, depth first, compiled (numba, nopython).** Draws are independent (each draw's situation classes
and option groups are its own), so the 512 trees run in parallel across cores with no coordination. Within a draw,
the walk carries one state down the tree: at each decision the state is saved, each answer's subtree walked from it,
and the state restored, so nothing is ever re-traced, and the line steps only the days since the parent decision.

**State per draw (a fixed record of arrays and scalars):** the event cash booked so far by kind and day (judgment,
settlement, levy, fees, coupons, equity proceeds), the lock and capacity, the petition day and cause, the marks,
the judgment's entry, amount and standing, stays (motion, approval, security), writs, takes, the notes and default
state, listing dates and status, offerings (initiated, close, outcome) and at-the-market sales, the share price
state, the daily processor's state (balances, arrears by class, first unpaid day), the line's state (outstanding
draws with their installment schedules, collected, funded), the decision cursors (which decisions are enabled and
when), and the waiting questions. Every array is fixed-size per draw (days, slots, installments are known), so a
save is a copy of a few kilobytes and a restore its reverse.

**Rules as code over that state, one function per decision kind.** Each Python rule in `events.Chain` (bookings,
day finding, accessors) and the walk's constructors in `forecast._Walk` / `chronological.ChronologicalWalk` (which
decisions, which answers, which question keys, which situation classes) becomes a compiled function over the state.
Question keys and situation tags are integers into tables built in Python once (node, context, class tags); the
compiled walk never builds strings.

**Two passes.**
1. Pass 1 (structure): walks every draw's tree, and for each question reached records its key and the dated record
   its facts are built from (`events.Chain.question_row`'s fields on that draw, as of the question's own day, taken
   when the decision is selected, before its answer and before anything later is booked: dated by construction).
   Python then pools each key's records over draws exactly as `state14.build` does today and renders the questions.
2. Pass 2 (figures), once every question has an answer: walks again with each history's probability, and on each
   history's leaf accumulates every page figure per draw (the line's collections, due, funded, petition, stayed, the
   daily and monthly series, the histograms the percentiles read) weighted by the probability, plus, per question and
   answer, the weighted sums that let the page recompute any changed judgment exactly. No sampling, so no ranges: the
   figures are exact.

## Fidelity

The Python is the definition while both exist. Each component is ported against it and checked on real histories of
the Akoustis case before the next is started:
1. The line and the daily processor per draw, stepping from day a to day b (`engine.py`, `processor.py`): identical
   trajectories to `engine.run` on the same event cash, per draw, on every day.
2. The equity block: share price, at-the-market sales under the baby-shelf limit, offerings (`share_price.py`,
   `baby_shelf.py`, the Chain's equity accessors): identical proceeds, shares and price per draw and day.
3. The dispute's bookings and accessors (`events.Chain`): for random histories of the real tree, identical event
   cash, marks, petition day and cause, and identical question records at every step.
4. The walk (`chronological.py`, `frontier.py`, `forecast._Walk`): on bounded subtrees of the real case, identical
   histories (steps, edges, masks), question keys and dated records, per draw.
5. The two passes: identical figures to the Python analysis on the same histories.
A component that disagrees on any draw, day or field stands as an error to find, not a tolerance.

## Question identity

A question's key is the decision, its context and its situation class (the judgment band, payment, notes and listing
status, offering availability, option group, and context fragments the constructors pass). That is what the judge
reads. Faithful identity: two histories share an answer exactly when they share a key, which is when the judge would
read the same question; no key is coarsened, and none carries route detail that does not change what the judge
reads. Pass 1 gives the full tree's distinct keys and their facts; Clef's question budget comes to Owen with that
count and a cost estimate before anything is asked.

## What leaves

The answer-first walk's sampling (`answer_first.py`'s sampling, `sampling.py`'s ranges, the stub tables, the
divisor and replicate fields, the page's ranges) once pass 2 serves the page; the old full-tree pooling machinery;
the delayed-records queue (`record_late`, `capture_decisions`' late path), which pass 1's dated records replace.
The worst-path search and the provable bounds stay.

## Open

- Memory for pass 1's records: one record per (question, draw, history reaching it); kept as running pools per key
  (the medoid and ranges `state14` needs) rather than every record, if the full tree's count requires it.
- The ordinary view (no dispute cash) is a second, much smaller tree per draw, through the same code.

## After the review of 28 October (`002-exact-evaluator.review.md`)

1. **What a question is.** The question standard (QUESTIONS_20240514 §1, Grouping) defines a question as the decision
   in its situation class, asked of every trajectory in that class, its facts one representative trajectory (the
   medoid by decision date and cash) with the class's range beside each figure; the classes are what separate legal
   status, the actions open and the ability to pay. That is what the judge reads, and the evaluator keeps it: a key's
   records are pooled over every trajectory in its class, as `state14` does today. The earlier proposal to identify a
   question at each reached point (`002-answer-first.md`, "Node-local questions") is withdrawn: it would be a new
   standard, and with exhaustive enumeration it would make nearly every point its own question, beyond any judge's
   budget. "Faithfully" here means no key is coarsened and none carries route detail that does not change what the
   judge reads.
2. **Deferred questions keep their own date.** The constructors branch some two-part decisions together at the
   first date (a stay motion and its approval; acceleration, the issuer's and the holders' petitions); the later
   question's record is taken when the walk reaches its own date (`freeze_due`), from the state then, so it includes
   every earlier decision between and nothing later. The compiled walk is at that date with the state in hand, so it
   records then; what goes is only the re-tracing of whole histories (`record_late` re-running the chain from the
   review date), not the deferral.
3. **Changing judgments.** Pass 2's per-question terms give any single changed judgment exactly and at once. Several
   changed together interact (two answers that matter only jointly), so the page recomputes them by running pass 2
   again on the server with the changed answers (exact; a minute or so), showing that it is doing so.
4. **Percentiles** are read from histograms, within half a bin of the exact weighted percentile, as today; the page
   states the bin width. Expectations are exact.
5. **The worst feasible history** is found exactly in pass 2: every history's unrecovered amount and frozen claim
   are accumulated over its feasible draws (keyed by its steps across the draws' trees), and the worst by the stress
   ranking is kept. The sampled search goes.
6. **512 operating draws** stay: they are the business's ordinary variation (the bank-data scenarios), which the
   event tree's enumeration does not touch.
7. **Draws walked apart.** The joint walk tags a question with the conditions that hold on every trajectory of its
   prefix (`_Walk.node`, `situation`); walked one draw at a time, a condition holds or not on that draw. Where the two
   differ, the keys differ. The walk component's fidelity check measures this on the real tree before anything is
   built on it; if they differ, which is the standard's question comes to Owen with the cases.

## The frame (28 October)

**The chain is draw-independent.** On random real histories (`app/exact/oracle.py`, uniform and "long"), a draw's
event cash, petition, marks and per-step day, cash and owed are identical whether computed with 2, 3 or all 512
draws (`tests/test_exact_oracle.py`). The per-draw design rests on this; the walk's tags (item 7) are checked with
the walk.

**State as a stack, no allocation.** Each draw's walk keeps its state in preallocated arrays with a leading depth
axis (depth up to the longest history, about 40 decisions): scalars `S[depth, NI]` (int64) and `F[depth, NF]`
(float64), per-day arrays `D[depth, ND, days]` (the event cash by kind, the lock, the capacity, the at-the-market
booking) and `P[depth, NP, days]` (float: the share price), fixed-capacity tables for what the Python keeps as lists
(takes, writs, settlement parts, offerings, coupons, pending writs, waiting decisions: `T_<name>[depth, rows, cols]`
with their counts in `S`), the line's state (task 002-6), and the answers given so far (`A[depth, decisions]`, -1
unanswered). Saving at a decision is copying slot d into d + 1 (a few tens of kilobytes, microseconds); restoring is
moving back to d. Field indices are module constants in `app/exact/fields.py`, each with the Python attribute it
holds, so numba compiles them as constants and each area adds its own.

**The walk as a state machine.** The chronological walk already decides what is open from the answers given
(`ChronologicalWalk._pending`, `_cursors`) and the order by date with explicit ties (`frontier.select`); those become
compiled functions of `A` and the flags. Each decision kind's question (key parts as integers into tables built in
Python once), its answers (option groups per draw), its booking (`Chain.step` and what it calls) and its children's
flags (the `_S` fields) are compiled functions per decision kind.

**Port order** (each against the oracle, per draw, every field, before the next):
1. the line and the daily processor (task 002-6);
2. the chain's core: judgment entry, interest and the amount owed, pay, levy, petition, resolve, the cash reads
   (`cum`, `processing_balance`, `decision_cash`), marks;
3. the equity block (share price, at-the-market sales under the baby shelf, offerings, the ledger);
4. stays and their security; 5. settlements; 6. the notes, the judgment default and the holders' acts, listing;
7. the cash floor, cash-out, nonpayment and the waiting decisions (`upto`, `until`, `next_floor`);
8. the walk: cursors, frontier, constructors, keys and classes, the dated question records;
9. pass 2 and the page.

## The dated walk (29 October)

**What it replaces.** The Python books each answer's consequences when the walk reaches it, and then reconstructs
what a read on an earlier day would have seen: `seen_at`, `until`, `upto` and `_upto_dated` book the answered
decisions waiting for their own day into throwaway views; `_stay_effects`, `_without_stay` and `_size_stay` re-size
every approved stay's security from the whole state on every read; `proposed_terms` projects settlement and stay
terms by replaying the history with the later answers removed; `court_questions` re-reads the court's stay question
by replaying the path to the approval day; `record_late`, `capture_decisions` and `freeze_due` re-trace a deferred
question's record at its date; `_court_read_before`, `_prospective_before`, `_vfired` and `divergence` support these.
Each exists because the reconstruction has to match a dated read. Both real walk faults came from this
(a question read a ledger that held later events).

**The compiled walk processes one draw in date order.** The state (S, D, P, the tables, and the line advanced to
`today`) and a dated agenda:
- the questions the cursors enable, each dated by `decision_day` on the state now;
- answered decisions waiting for their own day (the cash floor, cash-out, nonpayment, the offering's outcome, the
  levy-day and ripe responses, the judgment default), each dated on the state now by its own day function;
- scheduled reads and bookings: the pending writs (the levy), each stay's approval (its security sized, the court's
  question read, the lock booked), an offering's close. Bookings already dated (settlement installments, coupons,
  the at-the-market sales) are in D.
The loop takes the earliest item, ties broken as `frontier.select` breaks them (the levy-day response, then the levy,
then other decisions, then cash-out) and, for an approval, as `court_questions` reads it (before the same day's
waiting decisions and writs). It advances the line to the item's day; a deterministic item it books; a question it
asks: the question's record is taken from the state now, and for each answer the state is pushed, the answer
booked, the walk continued, and the state popped.

**What becomes a plain read.**
- `seen_at(day)`, `until(day)`: the state when the agenda reaches the day.
- A stay's security: sized when its approval comes due, from the state then (QUESTIONS §2.5: on the approval day,
  on the balance after every event dated before it, never more than that balance, none after a petition); booked in
  D_LOCK then and released when the dispute ends. It is never re-sized: the walk inserts no decision before the day
  already committed (`ChronologicalWalk._loop` raises on one), and no booking is dated before its decision's day, so
  nothing dated before the approval can be booked once the agenda has passed it.
- Proposed terms (a settlement offered on day d and paid from day p; a stay's proposed security): the state at d
  copied, the court answers dated after d taken as not given (a stay not approved, a registration not ordered), the
  waiting answers dropped, the line run forward to p with no new decision, and cash, owed and need read there. This is
  what `proposed_terms` computes by replay; the state at d is the replay's result.
- The court's stay question: read at the approval item.
- A deferred question's record (an approval, the holders' petition after the declaration and notice): taken when its
  item comes due.

**Checks.**
1. Dated by construction (QUESTIONS §1, Two dates): a question's record is taken before any later answer exists,
   so changing every later answer leaves it byte-identical; tested on random histories with the later answers
   changed.
2. Against the Python, history by history: random histories of the real case (`oracle.histories`, both styles), the
   compiled walk driven along each history's answers on each of its draws; every question's day, cash, owed,
   collateral, petition, marks, triggers, situation and offers, and each history's event cash, lock, marks, petition
   and cause, compared exactly. A disagreement is traced to its cause. A compiled fault is fixed. Where the Python
   reads a state other than its own day's (a reconstruction that sees a later booking or misses an earlier one), the
   standard (§1 Two dates; §2.5; §5, Date order) decides, and the class of difference goes to Owen with examples
   before the compiled rule is taken, since it changes what the model computes.

**Slices** (each checked as in 2 before the next):
1. the agenda and its deterministic items: the writs and the waiting decisions with their day functions (`tau`,
   `cash_out`, `_fall_after`, `_new_money_after`, `offering_day`, `nonpayment_day`, `response_day`,
   `judgment_default`) and bookings (`decide_waiting`, `decide_distress`, `decide_floor`, `respond`, `book_default`);
2. stays, their security and the court's read;
3. settlements and the projected terms;
4. the cursors (`_pending`, `_cursors`), the constructors (`forecast._Walk`: which questions, answers and option
   groups, keys and situation classes) and pass 1.

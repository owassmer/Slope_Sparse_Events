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

## The dated walk (29 October; revised after its review, `.runs/review-002-dated-walk-20261028T213238.md`)

**What it replaces.** The Python books each answer's consequences when the walk reaches it, and then reconstructs
what a read on an earlier day would have seen: `seen_at`, `until`, `upto` and `_upto_dated` book the answered
decisions waiting for their own day into throwaway views; `_stay_effects`, `_without_stay` and `_size_stay` re-size
every approved stay's security from the whole state on every read; `proposed_terms` projects settlement and stay
terms by replaying the history with some later answers neutralized; `court_questions` and `completed_probe` re-read a
question by replaying the whole history with its own answer neutralized; `record_late`, `capture_decisions`,
`freeze_due` and `notes.decision_row` re-trace a deferred question's record at its date; `as_of` blanks outcomes dated
after a row's day. Both real walk faults came from this (a question read a ledger that held later events), and the
review found more of the same class: a court row reading a later ruling's surviving amount (`remitted_amount`, set by
a structural step dated after the approval and read undated by `c_situation`); a settlement projection keeping a
holders' petition the holders decide only after the proposal (`book_default('holders_file')` books it at the
default's origin, and `proposed_terms` keeps the origin step).

**One draw in date order, every answer at its own date.** The state (S, D, P, the tables, the line advanced to
`today`) and a dated agenda. A decision the Python's constructors branch together with a later one (a stay motion and
its approval; acceleration, the issuer's and the holders' petitions; the bid-price compliance and the hearing request;
registration and its order) is split: each answer is its own agenda item, asked and booked at its own date, so no
state ever holds an answer before its date and no record holds its own answer (QUESTIONS §1, Two dates and What a
question is conditioned on). Items:
- the questions the cursors enable, each dated by `decision_day` on the state now;
- answered decisions waiting for their own day (the cash floor, cash-out, nonpayment, the offering's outcome, the
  levy-day and ripe responses), dated on the state now by their own day functions;
- deterministic items: the pending writs (the levy, with every consequence `_take` books: payment, resolution,
  release, the at-the-market rebooking), each stay's approval.

**Order within a day** (the frontier's, made complete):
0. the stay approval: the court's order is read and its security sized and locked first, from the state after
   every event dated before the approval day (QUESTIONS §2.5), so every other item that day sees the lock, and the
   lock depends on nothing that day. The balance is the approval day's balance at processing (the day before's close
   and the day's receipts, before its obligations and decisions): the same cash every question that day states (Owen,
   29 October, choosing it over the day before's close and over the Python's closing cash of the approval day, which
   included that day's other events while they read the lock). The Python is corrected to it (`_size_stay`, and
   `proposed_terms(processing=True)` for the stay's proposed security);
1. levy-day responses (answered or asked: phase 0 in `frontier.select`);
2. the levy (the pending writs dated that day, in the order they were ordered);
3. answered waiting decisions, in the order they were answered (phase 1; `_upto_dated` breaks ties by walk order);
4. questions, in cursor order: litigation, distress, listing, notes (phase 2, insertion order);
5. an unanswered cash-out (phase 3).
An item that a booking creates for the same day enters at its place in this order.

**Reads within a day are named, not "the state at day d":** a question's cash is the processing balance (the day
before's close, plus the day's receipts and event inflows, less the day's lock change, plus any levy booked that day:
`processing_balance`); its `cash_end` and arrears are the day's close as booked when it is read; a security is sized as
in 0. Each is a function of the line run over the bookings present when the read is taken.

**What becomes a plain read.** `seen_at(day)` and `until(day)`: the state when the agenda reaches the day. A stay's
security: booked once at 0 and released when the dispute ends; nothing dated on or before its approval can be booked
after it (no step books before its own day: `var/diag/dated/probe2.py`, 20,663 step calls, strictly-before; same-day
items come after it by the order above). A question's record, the court's included: taken when its item comes up,
before its own answer. Proposed terms (a settlement offered on day d, paid from day p): the state at d copied and run
forward to p through deterministic items only (writs execute with their consequences; answered waiting decisions and
pending court answers are dropped, as `proposed_terms` drops them), reading cash, owed and need at p. Since the
composite answers are split, the state at d holds no answer dated after d, which the replay does not guarantee.

**No joining.** The exact evaluator sums over every history; it never treats two sibling histories as one, so the
constructors' `joined`/`_same_after` and `divergence` have no role. Semantically different states that are equal in
cash (no motion, a denied motion) stay apart where the judge reads them apart: in the question key and situation class.

**Checks.**
1. Dated by construction: a question's record is byte-identical under every one of its own answers and every later
   answer; tested on random histories by changing the question's own answer and every later one.
2. Against the Python, history by history: random histories of the real case (`oracle.histories`, both styles), the
   compiled walk driven along each history's answers on each of its draws; every question's day, cash, owed,
   collateral, petition, marks, triggers, situation and offers, and each history's event cash, lock, marks,
   petition and cause, compared exactly. A disagreement is traced to its cause. A compiled fault is fixed. Where the
   Python reads a state other than its own day's (the classes above, and any others found), the standard decides (§1
   Two dates; §2.5; §5, Date order and "the cash facts each question states equal the engine's state on its decision
   date"); the classes found go to Owen with examples and their effect on the page, and the Python is corrected to the
   same rule where the page is still served from it.

**Slices** (each checked as in 2 before the next):
1. the agenda, its deterministic items and the waiting decisions (day functions and bookings), on histories without
   an approved stay or an accepted settlement;
2. stays (motion, approval, security, the court's read) and settlements (window, projection, installments);
3. the cursors (`_pending`, `_cursors`), the constructors as split decisions (`forecast._Walk`: which questions,
   answers and option groups, keys and situation classes) and pass 1.

## The walk, compiled (slice 3, 29 October)

**What it is.** `ChronologicalWalk` (app/disputes/chronological.py) on one draw: at each point the decisions the
answers so far leave open (`_pending`, `_cursors`), each dated on the state now (`Chain.decision_day`), the earliest
taken (`frontier.select`'s phases), the deterministic items (the levy, the answered decisions waiting for their day,
a stay's approval) booked by the agenda, a question asked by its constructor (`_ask` → the `_Walk` method of its kind),
and each answer's child walked, the state pushed before and popped after. The chronological walk overrides every
`_Walk` continuation to return to the scheduler, so a constructor is one decision: its questions, its answers (with
their edges and option groups) and the path flags (`_S`) each answer sets. That is what is ported, constructor by
constructor: `settle`, `verdict`, `motions`, `q1`, `stay_i1`/`stay_post` (`stay_answers`), `j9_i1`, `appeal`,
`enforce`, `a4_grouped` with `offer`, `ruling_pending`, `notes_petition`, `listing`, `delisting`, `ask_distress`,
`nonpayment`, and the recurrence bounds (`_ask`'s `bounds.reached`).

**Per draw, not per population.** The Python asks a question when it arises on some draw of the population and
takes population-wide decisions in a few places (`settle`'s feasibility, `_closes_after`, `inside`, the option groups
offered, the depth rule's watch). Per draw each becomes the draw's own condition. Figures are unchanged where the
answer not asked books nothing on the draw (the depth rule's premise); which draws contribute records to a question
can differ (item 7 above); the fidelity check measures it.

**Not ported.** `joined` and `unfiled` merge sibling answers that book and read the same; the exact evaluator sums
every history, so both are walked and their probabilities add (the merged edge is the union of the same disjoint
conjunctions). `first`, `_first_ripe`, `_first_listing` and `_first_distress` are disabled in the chronological walk.

**The depth rule** (`_watched`: the pre-ruling execution and the appeal are asked only where a later question reads
them): the no-answer subtree is walked first with a watch; where a later question reads the event on the draw, the
question is asked: the yes-answer subtree is walked and the no-subtree's accumulations are multiplied by the no
answer's probability (pass 2 sums are linear in each history's weight, so this is exact); elsewhere the no-subtree
stands with weight 1.

**Question identity.** A question's key is its structural key (the node and its context: the interval, the amount
class `_S.cls`, the occasion, the stage) and its class on the draw (the record's situation class: the judgment's band
and standing, the notes, the listing, the offering, payment, the appeal and stay status where the question reads them,
the context conditions held, the option group). The compiled walk emits integers: a structural key id (from a table
built in Python from the case's vocabulary before the walk) and a class vector; Python renders both to the key and
class strings `forecast` and `state14` use. A question's record (the dated facts state14 renders) is a fixed-width
numeric row taken when its item comes up.

**Checks.** Replay against the Python walk: every oracle history (`oracle.histories`: chronological-walk paths, both
styles) driven through the compiled walk on each of its draws: at each step the compiled frontier selects the
history's decision, constructs the same questions (structural keys), the same answer set and option groups, and the
history's answer's edges; at the end each classed question's class on the draw equals the path's
(`DisputePath.classes`). Then pass 1 on whole draws, with the count of distinct questions.

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

## The walk, compiled (slice 3, 29 October; revised after its review, `.runs/review-002-walk-20261029T104309.md`)

**What it is.** `ChronologicalWalk` (app/disputes/chronological.py) on one draw: at each point the decisions the
answers so far leave open (`_pending`, `_cursors`), each dated on the state now (`Chain.decision_day`), the earliest
item taken (`frontier.select`'s phases, a stay's approval first on its day), the deterministic items (the levy, the
answered decisions waiting for their day, a stay's approval) booked by the agenda, a question asked, and each answer's
child walked, the state saved before and restored after. The scheduler is `app/exact/walk.py` (checked: it follows the
Python walk's own histories step for step, booking identically).

**Elementary questions, each at its own date.** A `_Walk` constructor can hold several questions answered at
different dates (the stay motion and its approval; the holders' acceleration, the issuer's petition and the holders'
petition; the bid-price compliance and the hearing request; the enforcement request and the early-registration order)
and the initiation and the offering's outcome. Each is its own agenda item at its own date, its record taken then:
| constructor | questions, each at its date |
|---|---|
| `settle` | settlement_offer and settlement_accept, both at the window's start (one composite answer) |
| `verdict` | the verdict questions (one composite answer per award class) at the verdict |
| `motions`, `q1`, `appeal` | one question at its date |
| `stay_answers` | stay_motion at the motion; stay_approved at the approval (court order) |
| `j9_i1` | registration_early at the order date (the creditor's motion is assumed) |
| `enforce` | enforce_after_final at finality; after an appealed 'yes' not registered early, registration_early (post) at the order date |
| `a4_grouped` | the response, per option group, at its day; after 'initiate_offering', offering_closes at its close |
| `ruling_pending` | post_trial_ruling and remittitur_elected at the ruling |
| `notes_petition` | holders_act_judgment at the ripe date; petition_on_notes at the notes' due date; holders_involuntary at the holders' route date |
| `listing` | bid_compliance at the deadline; hearing_request at its deadline |
| `delisting` | holders_act_delisting at delisting; petition_on_notes and holders_involuntary at their dates |
| `ask_distress` | financing_at_floor / petition_cash_out per option group at its day; offering_closes after an initiation |
| `nonpayment` | petition_on_notes and holders_involuntary at the §3.3 day (one composite answer) |
The offering recurrence bound is the model's restriction on available actions (`offering_initiations`, failed
initiations counted); it removes the initiation where reached (`ChronologicalWalk.offer`), never forcing a quiet
answer. The tables sized from it (`MAX_OFFERS`, `MAX_FLOORS`) raise when exceeded, so the 'unbounded' sensitivity
stops loudly rather than truncating.

**Case-level inputs.** The award partition (`verdict_lines`, `verdict_classes`: cut points from the reachable cash
over all draws, booked amounts at band midpoints) and the reduced band (`reduced_band`) are one partition of the case
shared by every draw: computed once in Python and passed as tables (each award class: its booked amount, band, the
verdict questions' composite answer, and its reduced band). Never per draw.

**Depth (QUESTIONS §1: a question is asked only where its answer can change cash, timing, an obligation or a later
decision inside the horizon on some trajectory).** The Python decides it on the population of draws sharing the walk's
prefix (`_Watch.read`, one flag; `_edge_after`), an artifact of the walk. The compiled walk decides it per question
key, over every draw where that question arises, as the standard says: pass 1 walks each watched question's no-answer
subtree and records on which keys a later question read the event (`_Watch`'s reads: the nodes, the walks, the
conditions); the keys read on some draw are asked wherever they arise, both answers walked; pass 1 repeats until no new
key is asked (each repetition only adds). Where both answers of an asked question book identically on a draw (an
offering closing after the horizon), the draw is walked once with weight 1 and the question still recorded there.

**No joining.** `joined` merges sibling answers that book the same into the first's label (`_same_after`, through
`Chain.divergence`, which ignores facts-only state such as `hearing_requested`). The merged continuation then states the
first label's situation to every later question on the other's trajectories: after a merged 'hearing', later questions
are told the stock is listed. The compiled walk walks each answer (its probability is its own), so a later question
states its own situation. `unfiled` is not on the pending-claim routes.

**Pooling.** A question's members are its distinct (draw, record) pairs: the same record on the same draw reached by
two routes is one situation (Owen: identical situations get one answer however reached), so the pool does not depend
on how the walk branches.

**Checks.**
1. The scheduler: done (above).
2. Each constructor, on the histories: every Python step maps to elementary answers (a stay 'yes' to motion 'yes'
   and approval 'yes'; a joined label to each of its constituents); the walk driven along them books as the Python
   does; a decision the frontier selects that the history does not answer must not arise on the draw by the compiled
   constructor's own test (an extra question fails); a history step never reached fails (a missing one); each question
   asked carries the Python path's question key and, on the draw, its class (`DisputePath.classes`), except where a
   difference above (depth, joining, the dated record) explains it, counted by class.
3. A stay approved on the day of another event: the trace shows the approval processed before every other item of
   that day, and its security sized on the balance at processing before them.
4. Pass 1 on whole draws: the distinct questions and their count.

## The judge's input: a question's facts pooled over the draws (29 October)

**What the judge reads.** A question (its rendered key and situation class, `question_keys`) is asked once, on the
14 May state `state14.build` writes: the case, the standard, the evidence routed to the decision, what the question is
asked on (its constructor's assumptions: `pool.assumptions`, checked against the walk's nodes), and the situation:
one representative trajectory's joint facts (the medoid of the question's members by decision day and available cash)
with the members' range beside each figure. Its members are the distinct (draw, record) pairs on which the question
arises: identical situations get one answer however reached, so a record reached on one draw by two routes is one
member. This is a choice, not the Python's pooling, which counts every route's occurrence: the representative can
differ where routes repeat a record (the ranges do not). The members are taken in a fixed order (draw, then record),
so the medoid's tie rule does not depend on the sharding. Each member reads its own draw's operating figures (the
state builder indexes the operating need by position: the reduce gives it the members' draws' rows). Every member is
one on which the question's situation holds (`Forecaster.live`: inside the period, before a petition, and for a
question about an unpaid judgment, something owed: the dated walk does not ask such a question where nothing is owed,
and the reduce raises on a member that is not live). A deferred question's key carries the amount class on its own
date, not its origin's (the Python's walk asks the court's answer with the motion, under the motion's class). A
remittitur's proposed amount and band and the verdict form's questions come from the case's tables
(`fc.remitted`, `fc.verdict_classes`).

**Size.** A draw has 14,000-48,000 distinct members (measured: draws 145 and 365), about 37 million over the 512
draws, at 92 integers and a float each: too much to hold in one place, too much to pass whole between runs.

**Map and reduce on GitHub** (`run.yml` lets a line download an earlier run's artifacts):
1. Map (pass 1, 64 lines of 8 draws, the depth rule's forced keys given): each line writes its draws' members, distinct
   by (question identity, record digest, draw), split by question identity into S shards, each shard a compressed file
   of the members' rows (the question row, the class code, the record's integers and float, the draw); and the watched
   keys read. If a read is new, the map runs again with it forced (the depth rule's next round).
2. Reduce (S lines): each downloads its shard from every map output, groups the members by question identity, and for
   each question builds its node (the walk's key, its class as the class tag, the branches its constructor offers:
   the group's for a grouped question, `group_branches`) and its state (`state14.build` on the members as one row,
   every member live), writing the questions with their states.
3. The judge (on this Mac: 44,001 requests, network-bound): each question asked alone, its answer cached
   (`var/jev_cache`), so a stopped run resumes.
4. Pass 2 reads the answers by question identity.

**Checks.** Every question of a draw has its state (the reduce raises on a member that is not live, a missing
field, a question it cannot render); what each question is asked on equals the Python walk's nodes'; every question
pass 2 meets has an answer (pass 2 raises on one it does not). The records themselves were checked against the
Python's field by field (002-9, 002-10).

**Questions that do not arise at their own date** (29 October). Asked at its own date, a question can find its
premise gone: a court's answer on a stay of a judgment nothing is owed on; an early registration once the judgment is
final. Neither is asked; each books its quiet answer. Finality is the law's: the time for appeal has run (the deadline
falls on a court day, FRAP 26(a)(1)(C)) and no appeal was taken (28 U.S.C. §1963: the judgment is then registrable as of
right). Enforcement after finality is the creditor's own decision (`enforce_after_final`, asked when the judgment is
enforceable: whether it levies once the judgment is final, unstayed and unpaid); a moot application carries no further
right to collect, so no writ is booked for it. One writ per route is the model's, as before: a creditor whose levy
collected in part can obtain further writs, which the model does not book (a modelling bound to state as one).

**The parties by their roles** (Owen, 30 October). Everything a forecast question carries names the parties only by
their roles ("the Company", "the Claimant"; the docket withheld): the case declares each party's names and identifiers
(`cases/<case>/scenario.json` parties), and the judge's boundary (`DisputeProfile.forecast`, `app/agent/roles.py`)
replaces them. The forecast is the model's judgment from the record as of the review date; told who the parties are,
it can draw on what it knows of them, including what happened later, which no instruction reliably keeps out. Asked
both ways, real questions moved by up to 0.3, and most of the questions the judge declined were answered. The page
keeps the names.

What a case declares is the parties and their own circle: each party's legal and short names and their misspellings,
its ticker, its insiders, products and licensors, the docket. Third parties (the court, counsel, experts, underwriters,
noteholders) keep their names. The reasons (30 October, Owen asking the tradeoff to be weighed against the goal): a
user adding their own case can declare the parties and their circle, but listing every person in a record is a heavy,
error-prone burden for each case, and a name given the wrong role is a wrong-entity error; for a new lawsuit, the kind
the product is for, there is no later outcome for the model to recall; and for a historical case such as this one, the
case's substance identifies it to a model that knows it whatever third parties are named, so their names add little.
The limit stated plainly: a backtest on a famous public case may still be recognised from its facts.

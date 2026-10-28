# 002: the answer-first walk

Outcome 002 (`outcomes/002.md`): on a fresh clone, one command runs the Akoustis 14 May case with the answer-first walk
and serves its page in under an hour on a laptop; every collections, timing and exposure figure is a range no wider
than 1% of the commitment, with the worst feasible path beside it; changing a judgment moves them; no question's facts
can see anything after its own date, shown by a test that changes every later answer and finds the facts unchanged.

## How it works today (read 28 Oct at a5b18b3e)

1. `Forecaster.all_paths()` walks the whole tree under every answer (`ChronologicalWalk` in
   `app/disputes/chronological.py`, on `_Walk` in `app/disputes/forecast.py`), the 512 draws together. A history is a
   `DisputePath`: steps (node, ctx, answer), edges (question key, branch) that carry its probability, a draw mask, and
   per-draw situation classes.
2. The engine records each step's facts before booking the answer (`Chain.advance`: a clone's `question_row` at the
   step's day). Each history leaves its record at the question's key (`Forecaster._keep_late` into `facts[key]`).
3. Only then is the judgment model asked, once per key: `Forecaster.state` -> `built` -> `state14.build` shows one
   representative trajectory (the medoid by decision date and cash) and each figure's range, over every record of
   every history and draw that reaches the key. So a question's facts are pooled over the whole tree, which is why
   the tree had to exist before anything was asked, and why dated records travel through a queue
   (`capture_decisions`, `freeze_due`, `record_late`).
4. `EventModel` holds paths and judgments, `Analysis` computes each path's cash, the page and `reweight` read them.

## The design

Revised after the other family's review of 28 October (`002-answer-first.review.md`, eight findings, cited by
number below).

**A question is a function of its own dated state.** At a node of the tree (a history's prefix, the decision the
frontier selects, and the draws on that node in one situation class), the question's facts are built from that
node's draws as they stand on the decision's day: the representative and ranges of `state14`, over those draws only,
from the chain booked up to that day and no further. Identical rendered questions share one answer (the judge's cache
already keys on content), so alike situations reached by different histories are asked once. This replaces pooling
over every history that reaches a key: pooled facts cannot be known until the whole tree is, and they depend on paths
the answers make improbable. What changes for the standard: a question's group (QUESTIONS_20240514 §1) becomes the
draws on one node of the tree in one class, rather than every trajectory reaching the key.

**Dated by construction, every decision its own.** The facts are taken from the chain at selection time (`selected`
in the walk: `before`, booked until the candidate's day), before the answer and before anything later is booked. No
queue of late records and no composite edges: a decision whose answer is given later than its motion (a stay motion
and the court's approval, sized on approval day; an acceleration, the issuer's filing and the holders' petition) is
split into dated decisions of their own, each asked on its own day in the frontier with what stood then, so a child's
probability never needs a question not yet asked (review finding 4: keeping today's deferral would leave the mass used
for pruning undefined). Three things are shown, each by its own means: facts dated, by a test that walks again with
every later answer changed and finds each question's facts byte-identical; no decision skipped and same-day order
kept, by the independent no-skip checker (`app/analysis/walk_checks.py`) and the levy-day regression; the cash, by an
independent replay through the plain engine. The answer-first tree is small, so these checks run on every expanded
history of every run, not a sample (finding 5: invariance alone does not show a decision was asked).

**Answer-first, in order of probability.** Each new question is put to the judge when reached; its answer gives each
child a probability per draw (the parent's times the answer's, on the draws of that class). A child whose highest
probability over draws is below a threshold τ is not expanded: it becomes a stub, keeping its prefix, its draws and
their mass. Unanswered questions are gathered and asked together (concurrently for the real judge), and the walk
resumes from the stubs they unblock, through the walk's existing continuation routes, never re-walking. τ is lowered
until the unexpanded mass on every draw is at most ε.

**Two kinds of range, kept apart** (finding 2). The page's spread (P5 and P95 of collections, P5 of headroom) is the
dispersion of operating and event outcomes, the comparison with the business's ordinary ups and downs that the purpose
asks for. The walk's stopping adds a second, computational uncertainty, and each figure carries it as follows:
- an expectation (expected collections, exposure, a month's collections, a probability) lies between the expanded
  part plus the stubs' mass at the figure's lowest and highest possible value: a dollar range;
- a quantile at level q lies between the expanded distribution's quantiles at the levels the stub mass can shift it
  to, from (q - ε)/(1 - ε) to q/(1 - ε): exact as a statement about the level (P5 is between P4.5 and P5.5 at
  ε = 0.5%), and shown as the dollar range that implies, which is wide where the distribution has an atom at that
  level (Owen, 28 October: the 1% dollar width holds for expected values; quantiles hold to this level tolerance).

**Sound bounds come from the line's own limits** (finding 1). The line is reusable: freed capacity funds new invoices,
fees put a funded amount above its principal, and the limit moves with trailing receipts. So the bounds are per draw,
from that draw's operating series and the line's rules, not from the commitment: the most a continuation can collect
is what is booked plus every routed supplier invoice still to come on that draw funded at the fee, plus the opening
schedule (to confirm in `app/analysis/engine.py` that routed invoices never depend on the event path); the most it can leave frozen is the contractual balance that funding implies; the least it collects is what
is already booked. ε then follows from the widest per-draw range: ε = 0.01 x commitment / (that range). The engine's
histogram approximation for quantiles stays and is stated (its fine bins are within half a bin of exact).

**The worst feasible path** (finding 3), by its own search, on the existing stress ranking (unrecovered at maturity,
then the claim frozen at the worst petition), not on collections: depth first over answers, ignoring probability,
every answer one the question offers on its date, pruned where a subtree's bound on that ranking (the same per-draw
bounds) cannot exceed the worst found. Shown beside the ranges whatever its probability.

**Changing a judgment** (finding 6). A lever is a kind of question in one answer domain (its node, and its option group
where the domain depends on it, so a payment answer is never put where payment is not offered). It sets that
distribution on every question of the kind and domain. Expanded histories reweight exactly; the stubs' mass is
recomputed from their prefixes, so the ranges move and widen honestly if the change shifts mass into stubs.

**The ordinary view** (finding 7). For this case the bank view is the same forecast with the dispute given no cash
effect (`_OrdinaryWalk`), with its own distress, listing and notes decisions. It runs through the same answer-first
walk, dated the same way, with its own ε and ranges; the lawsuit's effect carries the sum of both views' widths.

**What is kept, and why** (finding 8). The 512 operating draws stay: they are the business's ordinary variation, which
ε does not touch (ε bounds unexpanded event probability on each draw, not sampling of the draws). The representative
trajectory (the medoid by decision date and cash) and the ranges beside it stay: they keep the shown facts on one
real joint trajectory and expose the spread within the question's group. Questions merge only when their rendering is
identical; the rendering is not coarsened to save questions. If the run shows too many questions, that is a decision
about what precision a judgment needs, taken with the purpose, not a tuning knob.

**Judges.** The real judgment model only on Owen's start. For building and for keyless runs, a seeded stand-in judge
(`ForecastJudge`) whose answers are a deterministic function of the rendered question, and say so on the page.

## Order of work, and what each run reports

1. Dated state at selection, and the test that changing later answers leaves facts unchanged.
2. The threshold walk with stubs and the stand-in judge; figures as intervals; the worst path.
3. The page reads intervals and the worst path (a worker, in parallel once the payload shape is fixed).
4. One local run on Akoustis with the stand-in judge reports histories expanded, questions asked, unexpanded mass,
   widths and time. That is the cost number; it decides whether the engine's speed (the `rust-native-core` port) is
   needed for the hour.
5. The real judge on Owen's start, with the number of questions and a cost estimate first.
6. Then the old full-tree machinery is deleted whole: the wave scripts, pooling stages, estimators and concentration
   tools. The no-skip and dated-facts checkers stay and run on every expanded history.

## Open

- The page's levers by kind and domain: which kinds the page offers.

## After the code review of 28 October (`002-walk-code.review.md`)

Owen chose (28 October) the exact head and a sampled tail, each range a 99% interval within 1% of the commitment. The
review showed that a t interval from eight replicates is not a 99% interval: a sampled tail can miss a rare loss in
every replicate and report no width at all, and nothing compared the widths with the 1%. So:

- **A valid interval, for any distribution.** Each stub (a prefix below the threshold) has an exact mass under the
  current judgments (its prefix probability) and, per draw, a lowest and highest value any continuation can give the
  figure, from the stub's own state and the line's rules (collections: at least what is booked at the stub, at most
  that plus what is owed and every routed invoice still to come, funded at the fee; the same bounds 002-2 derives for
  exposure). Replicates sample continuations independently across stubs, so the error of an expected figure has a
  Hoeffding bound: 99% half-width sqrt(ln(200) / (2n) x sum over stubs of (mass x range x ratio)^2), where ratio is
  the largest factor by which the current judgments raise a sampled continuation's weight over the judgments it was
  sampled under (1 when unchanged). It holds whatever the tail holds, never collapses when a rare loss is missed, and
  widens honestly when a changed judgment makes a thinly sampled region matter. The t table goes.
- **The run meets the 1% or says it did not.** After the walk the run computes every expected figure's half-width;
  while any exceeds 1% of the commitment and time remains, it adds replicates to the stubs that contribute most to the
  sum, and the page states the widths reached. On the page, a changed judgment that pushes a width past 1% says so.
- **What is fixed already:** a restored run keeps each path's divisor and replicate; the charts and server metrics use
  the tiles' weights (no renormalising); the three question kinds lacking a timing statement have one, from the
  standard (N1, D6a, D6b).
- **Still to do, with the questions:** questions built from each node's own dated state, every deferred decision
  split into dated decisions, and the no-skip check and the plain-engine replay run on every expanded history of each
  run (findings 5 and 6). The worst-path search evaluates a history on its own draws only (finding 7; the 002-2 worker
  was told).

## The questions: the plan as of 28 October, after reading the chronological walk again

The note above says every two-part decision becomes two dated decisions. Reading `app/disputes/chronological.py`
again, that is not needed for dating, and it would be the largest change to the engine in the outcome: the walk already
branches each two-part decision at its first date and freezes its later question's facts when the frontier reaches the
later date (`capture_decisions`, `freeze_due`: "Those questions wait at the frontier, so intervening earlier decisions
are included, never later ones"), and the engine already treats a motion as moved and unruled until its ruling day
(`_prospective_before`). What answer-first needs from a two-part decision is a probability for its children before
its later question can be asked (design review, finding 4). Two facts settle that without splitting:
- pruning only needs an upper bound: a child's mass is at most its prefix probability times the first part's answer,
  so a child is set aside only when that bound is below the threshold; both children are walked until the later
  question is asked, at its own date, and the exact mass replaces the bound from then on;
- sampling needs a guide, not the answer: until the later question is asked, the guide gives its two outcomes even
  odds; a path's weight still uses the judgments at the end, so the guide costs precision, never correctness.
The cost is walking both children between the two dates, which the run reports.

So the question work is:
1. **Node-local questions.** A question's facts come from the records of one point of the tree (the prefix at which
   it is asked, the draws there in its class), not pooled over every prefix that reaches its key. The records already
   carry their prefix (`_dated_records`: key, prefix, row); rendering groups by (key, prefix) instead of key, keeping
   the representative trajectory and the ranges over that point's draws. A question's identity is its rendered
   content, so identical questions at different points share one answer and no rendering is coarsened.
2. **Asked when its facts freeze.** A question is put to the judge when its facts are complete: at capture for a
   date-local question, at `freeze_due` for the later part of a two-part decision. Questions reached together are
   asked together, and the walk resumes from the points they unblock.
3. **A read-only view of the day.** Both leaks were a question's facts reading a value computed for a later day (a
   stay's security sized on approval day; a day booked before a same-day decision was asked). The facts are built from
   the chain as of the question's day with its forward projections (memoised later rulings and writs) removed, so a
   later value cannot be read; where a fact needs a projection (a pending motion's proposed terms) it is sized from
   that day, as 001-14a did for collateral.
4. **Shown in every run:** walking again with every later answer changed leaves each question's facts byte-identical
   (a test on the real case); the no-skip checker and the plain-engine replay run on every expanded history.
5. **What reaches the judge is the definition:** the node specs (decision, timing, branches, situation keys) in
   `dispute_model.json` and the question standard. They are listed in `tools/roles/definitions` as they are touched,
   so each change is judged against its sources before it is committed.

## After the review of the question plan (`002-questions-plan.review.md`): the split stands

The plan above kept two-part decisions whole. The review showed why that fails, not for dating but for sampling:
- A two-part branch decided at its first date already carries the later answer, whose probability depends on what
  happens in between (approval after a financing that succeeded or failed). A point set aside before the later date
  then has no exact mass (finding 2), and the records of the later question keep the first part's prefix, so
  questions at different points pool again (finding 1).
- Sampling with any guide other than the judgment itself makes each sampled observation the value times a likelihood
  ratio, which the value's floor and ceiling do not bound (finding 3: two continuations both worth $100, sampled at
  even odds, judged 0.99/0.01, give observations of $198 and $2). A distribution-free interval holds only where the
  guide is the judgment.
Both are answered by the original design: **every decision is branched at its own date, after its own question has
been asked at that point of the tree.** Then the guide is the judgment wherever the walk branches, a set-aside point's
mass is the product of answers already given, and a sampled continuation is drawn from the judged distribution
itself, so its observation is the figure's value and lies between the point's floor and ceiling. For two-part
decisions that means the first part (a motion moved, an acceleration declared, an offering initiated) is answered at
its date and leaves a pending state, and the later part (the ruling, the petition, the close) is a decision of its own
at its date. The engine already distinguishes moved-and-unruled before a ruling day (`_prospective_before`); the work
is to make that pending state a booked state and the ruling a step of its own, for each kind in `deferred_decision`.

The intervals, corrected:
- **Width, not half-width** (finding 4): the requirement is a range no wider than 1% of the commitment, so the run
  stops when every expected figure's half-width is at most 0.5%.
- **Under the judgments as walked,** each stub's samples are drawn from the judged distribution, so its Hoeffding
  half-width uses the figure's floor and ceiling at that point. **Under changed judgments** each observation carries the
  ratio of new to old answers along its continuation; the bound uses the largest such ratio any continuation of the
  stub can reach (the product, over the questions below it, of each changed question's largest ratio), so the range
  widens with the change, and the page says when it passes 1% and the region needs walking again.
- **Percentiles** (finding 5): the sampled tail also moves the probability below any value. The level tolerance Owen
  set (P5 within P4.5 to P5.5) is checked the same way: a Hoeffding bound on the sampled mass below the reported value
  (each stub's mass times an indicator, range one), and the run's stopping rule includes it.

## One rule instead of twelve splits (28 October, evening)

Splitting each of the twelve two-part kinds in the engine answers the question-plan review, but what its findings
require is narrower: nothing may be set aside, and nothing sampled, between a two-part decision's first part and the
day its later question is asked (its window). Finding 2 needs an exact mass at every set-aside point; finding 3 needs
every sampled decision to be one whose own question has been asked at that point. Both hold if, while a window is open
on a path, the walk walks every child exactly:
- **No set-aside point inside a window.** The walk knows which later questions are still waiting on a path
  (`ChronologicalWalk._question_queue`, frozen by `freeze_due` when the frontier reaches each one's day), so a point
  with one waiting is never a stub. Every stub's prefix then has all its answers given: its mass is exact (finding 2).
- **No sampling inside a window.** Inside a sampled continuation, a decision that opens a window (a child carrying a
  deferred question) or is reached while one is open walks all its children, as a question read by a later one already
  does (`_watched`). The window's paths are enumerated; their later answers, asked at their own points, sum to one
  across each pair, so a replicate's observation is a convex combination of continuation values and lies between the
  point's floor and ceiling. Every sampled decision outside a window has had its own question asked, so the guide is
  the judgment (finding 3).
- **A later question is identified by the point where it is asked** (the reached prefix at `freeze_due`), not by the
  first part's prefix (finding 1), so questions at different points never pool.
The engine and the twelve kinds stay as they are. The cost is walking windows exactly; the run reports it per kind,
and only a kind whose window proves expensive is split in the engine.

## Ranges after a judgment is changed (after `002-window-rule.review.md`)

The window rule stands for the judgments the run walked under. The review showed that no bound from the run's samples
can hold after a judgment is changed: a changed answer can make a region the samples never visited matter (an answer
the judgment gave 0, finding 2; a question of the same kind at a point no replicate reached, finding 3), and the
weighted observations are not bounded by the figure's floor and ceiling (finding 1). So the page's two states carry
two kinds of range, each true:
- **At the run's judgments:** each expected figure's 99% range from sampling (the Hoeffding bound over stubs, valid
  because every sample is drawn from the judged distribution and lies between its stub's floor and ceiling).
- **After a judgment is changed:** the exactly walked paths reweight exactly, and every stub's mass under the new
  judgments is exact (the product of answers already given on its prefix). The figure's range is then guaranteed,
  with no sampling claim: the walked part plus, for each stub, its new mass times its floor and its ceiling. The figure
  shown inside it takes each stub's sampled average as it was, and the page says the unexplored part is estimated
  under the run's judgments. Where the guaranteed range is wider than 1% of the commitment, the page offers a re-walk
  under the changed judgments (the same walk on the server, with every answer already cached), which restores a 99%
  range at those judgments.
The guide stays the judgment itself (no reweighting of samples is relied on), and no likelihood ratio is used.

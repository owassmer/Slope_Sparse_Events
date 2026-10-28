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

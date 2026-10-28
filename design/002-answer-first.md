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

**A question is a function of its own dated state.** At a node of the tree (a history's prefix, the decision the
frontier selects, and the draws on that node in one situation class), the question's facts are built from that
node's draws as they stand on the decision's day: the representative and ranges of `state14`, over those draws only,
from the chain booked up to that day and no further. Identical rendered questions share one answer (the judge's cache
already keys on content), so alike situations reached by different histories are asked once. This replaces pooling
over every history that reaches a key: pooled facts cannot be known until the whole tree is, and they depend on paths
the answers make improbable. What changes for the standard: a question's group (QUESTIONS_20240514 §1) becomes the
draws on one node of the tree in one class, rather than every trajectory reaching the key.

**Dated by construction.** The facts are taken from the chain at selection time (`selected` in the walk: `before`,
booked until the candidate's day), before the answer and before anything later is booked. No queue of late records:
a question that needs a later day's facts (court approval sized on approval day; a notes petition after its
declaration and notice) is a decision at that later day in the frontier, built then. The proof is one test: for
sampled questions on a walked tree, walk again with every later answer changed and find each question's facts
byte-identical.

**Answer-first, in order of probability.** Each new question is put to the judge when reached; its answer gives each
child a probability per draw (the parent's times the answer's, on the draws of that class). A child whose highest
probability over draws is below a threshold τ is not expanded: it becomes a stub, keeping its prefix, its draws and
their mass. Unanswered questions are gathered and asked together (concurrently for the real judge), and the walk
resumes from the stubs they unblock, through the walk's existing continuation routes, never re-walking. τ is lowered
until the unexpanded mass on every draw is at most ε.

**Every figure an interval.** Each figure is a probability-weighted sum over draws and histories. A stub's
contribution lies between its mass times the figure's lowest and highest possible value under any continuation:
collections between those already booked and everything still contractually due, exposure between zero and the
commitment, each month's collections between zero and what that month could collect. So a figure's width is at most
the stub mass times the figure's range, and ε is chosen from the outcome's 1% of the commitment: ε = 0.01 x
commitment / (the widest figure's range). Expanded histories are exact.

**The worst feasible path,** by its own search: depth first over answers, ignoring probability, every answer one the
question offers on its date, pruned where the collections already booked are no lower than the worst found
(collections booked only grow along a path). It is shown beside the ranges, whatever its probability.

**Changing a judgment.** The page's lever for a kind of question (for example, settlement at I1) sets that
distribution on every question of the kind. Expanded histories reweight exactly; the stubs' mass is recomputed from
their prefixes, so the ranges move and widen honestly if the change shifts mass into stubs.

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
6. Then the old full-tree machinery is deleted whole: the wave scripts, pooling stages, every-history checks,
   estimators and concentration tools.

## Open, to settle while building

- Whether every composite question can be a dated decision of its own in the frontier; any that cannot keep the
  existing deferral, still built from the chain at its own day.
- The rendering's granularity, which decides how many questions merge. A question's answer must not hinge on figures
  finer than a decision would; the run reports how many questions there are.
- The page's levers by kind of question, and how the bank view's own walk (`_BankWalk`) sits beside this.

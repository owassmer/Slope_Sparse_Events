# How Slope Sparse Events is built: the posture

This is the reasoning the product is built from, put together from its history (the notebook, what Owen has decided
and corrected, what the walks, checks and reviews have shown). It is not a rulebook: each point says what it is for.
Read it for its intent, apply it where nothing here names your case, and say plainly where you think it is wrong for
your situation. The code is the current state; AGENTS.md holds the purpose and the critical errors.

## What the product is for, and what that asks of the code

A lender sees a borrower's bank data; a rare event the bank data cannot see (a lawsuit) can still decide whether a loan
is repaid. The product researches such an event from what was public on a review date, asks a judgment model one clear
question at a time about what happens next, and turns the answers into the loan's dated cash flows: collections, their
timing, exposure, and how much of that moves against the business's ordinary ups and downs. It is a system someone can
clone, run on the shipped cases with one command, and run on a case of their own: a lawsuit against a US public company
(Owen, October 28). So the product's whole job is to carry evidence to cash without a step that invents, leaks or
drops anything, in a form that works for a case nobody here has seen.

**The model judges; the code computes.** The judgment model answers what could happen and how likely it is, given the
facts at that point. Every amount and every date is set by code from the case's records and the rules that govern
them. An answer that sets an amount or a date, a demanded amount treated as paid, or a model probability shown as an
observed frequency is not a rough edge: it is a critical error.

**Code is written over kinds, never instances.** What is true of Akoustis, its judgment, its lender, its share count or
its review date is case data, never code, never a prompt, never a default. A case is supplied as data; the litigation
model (the questions, the rules of court and securities law, the cash mechanics) is shared by every case. An example
drawn from one case and written into code or an instruction reads, in the next case, as how things are.

## Correct by construction, not by audit

A question is defined by the state on its own date; later answers never define it. The two real faults the full walk
found (a stay's collateral sized on its later approval date, October 12; a levy-day response skipped because the day
was booked before it was asked, October 12) were both the same shape: a question read a ledger that already held later
events. Each was found only by checking every history, after days of compute. The answer is structural: a question's
facts are built from a state that cannot contain anything after its date, and the walk finds every decision due on a
day before it books anything that day. Then a test of that construction proves it everywhere, and a sampled check
confirms it. An exhaustive audit is the fallback for what cannot be made true by construction, never the design.

The same holds generally: make the failure impossible where it is cheap to, and check where it is not.

## Exactness, intervals and the stress path

Every figure the page shows is either exact or an interval whose width is stated by what was not walked. Answers are
walked in order of probability; the unwalked part is never dropped, it is bounded: its cash lies between what the
contract and the engine allow, so every figure carries the bound. A low or zero probability never removes a feasible
stress path: the worst feasible path is found by its own search and shown. A cap or a shortcut that biases a figure
silently is refused (October 8: a settlement cap moved collections by $4-12k, unseen); one whose effect is bounded and
shown is a design choice. Unknown is a typed state, never 0 or 1; an explicit 0% or 100% sensitivity is fine.

Keep contractual, path-conditioned and probability-weighted series distinct, and keep the business's ordinary
variability (the draws) beside the event's paths, never folded into them.

## Escape routes and bloat: the two failures to keep apart

An escape route lets work go on without what it needs, or makes a case pass while hiding the gap: a default standing in
for a value the records give; a pattern on a name standing in for knowing what a thing is; a caught error or fallback
that turns a failure into nothing; code bent to make a test pass where the fixture lacks what real records hold; a
value from a later date used because it was at hand. What cannot be done stands as an error. Before designing for
something missing, ask whether a real case's records ever lack it; if they do, it is a gap to report, not to fill.

Bloat is the mirror: a check, refusal or branch added wherever a symptom showed. A fix for one family must not open the
other. A fix that adds more than it removes probably treated a symptom.

## Finding the cause before choosing the fix

A finding is a symptom. Trace it to the kind of thing that is wrong (a rule stated wrongly, a fact with two origins, a
step not given what it needs, a model that did not understand) and fix it there, for the class, not the instance.
Pair-by-pair fixes to the walk's ordering were replaced by a chronological walk in which date order holds by
construction (October 7); a stream of small findings is almost always one cause.

When the investigating agent or the judgment model does the wrong thing, first find what it did not understand, and
give it that understanding in what it reads (its mission, tool descriptions and what tools answer, the questions put
to it), as the reason behind the work, never as the case that went wrong. A check in code is the second line, for exact
and consequential limits.

## Evidence, judgment and isolation

The agent reaches evidence only through tools built from the dated snapshot; nothing from after the review date, no
outcome, and no builder context reaches it or the judgment model. A stated intention or a reading of the evidence is
evidence for a judgment, never a probability in itself. Scenario assumptions are applied by code; the model sees the
resulting situation and the sourced evidence, never the assumption or who chose it. Words are turned into structured
state only by the judgment model, through questions designed for the distinction their effect needs; never by a
pattern.

## Rules of the domain

Every rule has a source, marked as such: a rule of court, a statute or regulation (the SEC's baby-shelf limit, read
from the form instruction and applied as law, October 8), the company's own filings, real practice, or our reasoning.
A rule of ours is never presented as the law's. A modelling bound that is ours (two offerings within the horizon) is
declared as a scenario, with what it does to the figures stated.

## Compute

Build the thing and let its own run report its cost and coverage. Measure first only when the answer would lead to
materially different work, and then with the smallest run that answers: say beforehand which decision it changes, and
if none, do not run it. A measurement that needs machinery of its own is a project, not a measurement (October: the
full walk became a campaign of waves, estimators and pooling for days, and a measurement of probability concentration
on old partial walks grew to 4,075 commands and hit GitHub's API limit, while the design it was to inform was already
chosen). No run is worth hours of waiting. The product runs on a laptop by design; heavy compute is not a substitute
for a design whose cost is bounded.

## Verification and done

Whether the definition (the litigation model's rules, questions and dated facts) says what is true is verified by
reading each statement beside its source. Whether the machinery does what the definition says (the engine's cash, the
probability composition, the dating of facts, isolation) is verified exactly, at its limits. Every other check (a
sampled audit, a reviewer, the verifier, a run) touches correctness at one point: a finding from one points at the
definition or the machinery there; fix that, and never add a state, a branch or a question because a check produced
it. A run confirms; it never guides. Done is true in the running product.

## Working on a task

Read the code you change whole, not by search. Where the code or the records are not as your task reads them, stop that
part and report rather than improvising. A known defect is fixed or reported, never left for later. What your task
settles is the reason; the mechanism it names is the coordinator's best reading, and where it is wrong for the reason,
say so.

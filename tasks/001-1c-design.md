# 001-1c — dated decisions, shared futures

Part 1 only. This proposes a replacement for the pending-claim walk; it does not implement it, repair the recorded run, or claim a measured speedup. Stop here before the local prototype.

## One scheduling rule

At each state, every active chain reports its next unresolved decision, its **decision date on each supported draw**, and its prerequisite. Select the earliest per draw, partition the support by that selection, advance to its before-answer boundary, then derive its situation class and offered answers. Branch over every feasible answer. Repeat across all chains, not through a litigation → distress → listing → notes call sequence.

A candidate is a read-only description, not `_trace(s.steps + (quiet_answer,))`. Asking what happens next must neither assume an answer nor book its consequences. A temporarily unavailable candidate is not permanently exhausted: changes to cash, obligations, listing, security or equity can enable it or move its date. Completion, cancellation and not-yet-enabled are distinct states.

Include deterministic events in the same frontier: receipts, obligations, writs, security releases, offering settlement and legal clocks. Advance the existing financial processing only to the next frontier boundary; discover cash-floor, first-unpaid and general-nonpayment triggers during that advance. Do not forecast distress past an unresolved earlier decision and treat that forecast as final. Recompute candidates after each transition. A branch cannot insert an effect before the committed frontier; such an insertion is a defect, not a reason to revise an earlier answer domain.

Dates alone do not order events on the same day. Use `(day, semantic phase, prerequisite order)` boundaries taken from the current booking rules: a levy-day response precedes its levy; a ripe-default response does not acquire that exemption; a court's before-answer snapshot excludes its own approval; receipts/levies/scheduled payments/operations keep their existing processing order. D8 and D9 filing on the same day remain one question. Preserve existing explicit tie conventions (including floor before cash-out where tied), not incidental Python call order. Enumerating these boundaries from `Chain` is a prerequisite of the prototype, not permission to invent new economics.

This makes the three failures ordinary cases: the day-96 ripe response and day-101 close precede the day-155 floor; a prior levy precedes the ripe payment's domain; the day-74 ruling precedes and can move the prospective day-77 floor. A petition can end decision enumeration only after the frontier establishes that no earlier decision remains; its contractual and frozen-exposure tail still runs.

## What decides the future

Use an immutable continuation state. Its exact key contains:

- The input/scenario namespace, draw identity (and therefore its fixed operating and timing draws), date and within-day phase. Never resample on a fork or merge.
- The processor's complete resumable state: available and restricted cash; line capacity, outstanding and installments with due/retry/incurrence order; each arrear's class, amount and age; first-unpaid and the rolling obligations/unpaid history used by §7.01(j)(v). Equal cash or equal arrears totals alone are insufficient.
- Legal and financial state: judgment/band, amount remaining, payments/levies, motions and ruling, settlement installments, stays and security, appeal, writs, default clocks, notes due amount/date/route, listing and petition status.
- Equity state: issued/reserved/available shares, pending ATM settlements, pending offering terms, pricing/close dates, lock-ups, and the inputs to share-price calculation.
- The dated pending-event queue and each chain's cursor, decisions already consumed, floor re-arm state, and live obligations/prerequisites. No answered question may be asked twice merely because the walk revisits a state.
- All earlier-event tags and dated facts read by later question construction: verdict-form answers, prior offerings and failures, stay/appeal events, floor choice, and conditioning identities. `state14.Situation`, `assumed_events`, and `_Walk` context construction define these reads. Equal cash with different later question situations is **not** an equivalent future.

Keep unknown/not applicable distinct from zero and from no pending event. Start conservatively with complete semantic state; remove a field only when neither a future transition nor a future question reads it. Exclude caches, recording indices and entire path strings from equality. Hash canonical contents for lookup, then compare contents; do not round money or merge on a hash alone. Initially share only within the same draw; batch identical transition shapes across draws without equating their futures.

`Chain` currently combines semantic state, full event arrays, speculative views and recording caches. It is not already this continuation API. The processor has debit/arrears checkpoints, but its current prefix cache is not a general decision-boundary resumable engine. Extracting resumable boundaries must preserve its arithmetic, rather than writing a second cash model.

## Advance once, share once

Intern continuation states in a directed acyclic graph. Expand an interned state once; arrivals from other histories attach incoming edges to it. Each edge holds its supported draws, question/class and answer (or deterministic transition), booked segment, and display labels. Immutable segments and copy-on-write state prevent one answer mutating a sibling. A same-day transition must consume a decision or pending event, so progress does not depend on the date increasing.

Keep question grouping separate from financial-state interning. Preserve the current per-draw legal-status/action/payment-capacity classes and their representative joint facts and ranges. Collect distinct supported decision situations first, then bind their question identities. Count a reused situation once in its population, not once per incoming history. A change in scheduling must not silently pool differently conditioned questions or reuse a saved judgment for a changed situation.

For draw `d`, root mass is 1; an answer edge carries incoming mass times its conditional answer probability; mutually exclusive incoming routes to a shared state add. Deterministic edges carry mass unchanged. Support partitioning is not another probability factor. Retain the probability expression as a sum/product graph, not one frozen weight or expanded list of conjunctions. This preserves reweighting, including zero-to-positive overrides, without enumerating all histories. Branches remain structurally present at 0%. Missing judgments remain unavailable; only explicitly declared synthetic distributions may be used for local arithmetic checks.

## What does not change

`QUESTIONS_20240514.md` still owns the questions, evidence cutoff, grouping, depth and answer meanings. `events.Chain`, `processor` and `engine` still own amounts, dates, payment priorities, arrears, retries, security, share capacity and booking rules. Exposing their incremental transition boundaries changes execution, not rules. No new evidence or judgment call is part of this work. Preserve every feasible stress route and conditional probability composition; do not use probability, economic similarity or a later cash match to erase a feasible decision.

## Reduction and the page without every history

Today `pool.py` merges completed paths, `_Walk.emit` retraces them, and `reduce.Tables.add` consumes full trajectories plus edge expressions. Those interfaces cannot just be fed one representative history per shared future: arrivals can have identical future cash but different past collections, minima, preference exposure or display labels.

The replacement output needs the state/edge graph, draw support, immutable cash-flow segments, question catalog and bindings, outcome labels and reconstructible witness routes. Reduction propagates weights through this graph:

- Additive daily flows/expectations can be accumulated on segments using arrival mass. Preserve contractual, path-conditioned and weighted series separately.
- History-dependent statistics need separate reduction payloads: cumulative collections, running minima, first floor, and dated collections needed by a later petition's preference window. Carry exact distinct payloads/distributions through shared financial states; never average histories before taking a minimum or quantile. Keep the current histogram conventions. These payloads may grow even when future simulation shares well; measure that cost separately.
- Re-evaluate the probability graph for overrides; derivatives must work at 0% without dividing by a probability. Preserve current single-question sensitivity semantics and attribution settings. More general overrides require graph evaluation, not an assumed linear shortcut.
- Resolution summaries currently read `Tables.trie` and verdict/ruling/outcome sums. Retain the bounded display-prefix labels as reduction payloads, or replace that view explicitly with dated decision summaries; do not present a shared state's one witness as all its histories.
- Stress selection is independent of weights. Retain feasible route witnesses and their past financial payloads, including the adverse-placement calculation; the page must still open a real dated route. A graph does not justify weakening the current path-conditioned stress ranking.

Thus the page still gets its table-shaped series, sensitivity effects, resolution totals, stress rows and filing → question links. Small-root path materialization is useful for comparison only; expanding all histories in the production adapter would discard the benefit.

## Next part: bounded local comparison, not a fleet

Use `tests/test_dated_answer_domains.py` (draw 145 and the mixed-order synthetic case), all payment families/draws in `tests/test_ripe_after_levy.py`, and `var/diag/rewalk_other.py`'s roots in `other_paths.json` (prefix length 9 for index 3, otherwise 8). Include the day-74 set-aside family described in `tasks/001-1b-result.md`. Replay prefixes only to seed a root; candidate selection below it must not replay histories.

Compare supported draws and all feasible continuations, not just a matching path count: before-answer domain validity, prefix/full dated facts, exact cents and dates where the current walker is correct, and per-draw terminal mass 1 under several explicit synthetic distributions including endpoint assignments. The known-bad walker is not the oracle for the disputed domains; those require the dated event facts and question rules. Record discrepancies rather than blessing old output.

On identical roots and local hardware, report cold setup separately from warm walk time, peak memory, current raw/merged paths and trace calls, versus unique continuation states, edges, advanced draw-days, cache reuse, question situations and reduction payloads. Time reduction too. Project by root family using measured distinct-state growth and overlap, with a no-sharing upper case; do not extrapolate a one-draw speedup to 512 draws as a fact. Expected work is proportional to unique state transitions and newly processed draw-days, plus question construction and reduction payload work—not emitted histories. Whether that is enough is a measurement for part 2. No runtime or full-tree estimate has been measured in this design-only part.

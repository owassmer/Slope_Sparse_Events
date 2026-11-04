# Findings: `design/002-exact-evaluator.md`

## 1. Retained keys do not establish the claimed identity of questions

**Sources:** design lines 41–44, 68–72; `app/disputes/forecast.py:331–375`; `app/disputes/state14.py:80–88,266–267,379–383,1002–1021`; `design/002-answer-first.md`, “Node-local questions” and “One rule instead of twelve splits”; `design/002-questions-plan.review.md`, finding 1.

The plan equates the existing categorical key with what the judge reads, but the rendering also contains numerical amounts, dates and historical facts absent from that key. Pooling all records under the key makes their renderings identical only by replacing the different situations with one pooled situation.

The previously documented case still applies: histories share a stay motion but differ in financing before approval. Approval-day cash differs without necessarily changing the categorical situation class. The proposed pooling gives both histories one representative cash amount and range, rather than their own before-answer situations. The preceding design expressly identifies the question at the reached prefix and prohibits this cross-prefix pooling; the new note says it supersedes sampling, but also reverses that question-definition decision.

The existing classes separate legal status, available actions and ability to pay; that purpose still holds. The medoid keeps the displayed facts on a real joint trajectory, and ranges expose variation within its group; those purposes also hold. Neither establishes that different histories in one class pose the same question. The plan retains the grouping mechanism without explaining why the whole-tree group, rather than the reached situation, is now the intended object of judgment.

## 2. Selection-time capture does not replace the deferred-record queue for the retained constructors

**Sources:** design lines 34–44, 59–62, 77–79; `app/disputes/forecast.py:2748–2777,3106–3129`; `app/disputes/chronological.py:306–354`; `app/analysis/frontier.py:90–97`.

The plan ports the existing decision constructors and requires identical steps and edges, but deletes the mechanism that captures their later questions. Those constructors do not select every question separately on its own date.

For example, `stay_answers` branches on motion and approval together at the motion, while `stay_court` defers the approval record. `notes_petition` similarly combines acceleration, the issuer's filing and the holders' filing. At their later dates, the frontier processes waiting answers as deterministic bookings, not newly selected unanswered decisions. Recording only at selection therefore does not produce the promised approval-day or petition-day record.

A financing between motion and approval demonstrates the consequence: a motion-time record misses its effect on approval-day cash and security. The queue exists specifically to include intervening earlier decisions while excluding later ones; `freeze_due` documents and implements that purpose. It still holds under exhaustive enumeration. The replacement described in pass 1 does not cover it, and the retained fidelity requirement prevents treating a changed decision structure as an unnoticed implementation detail.

## 3. Per-question sums do not preserve the current page's joint judgment changes

**Sources:** design lines 45–49; `app/analysis/page.py:1368–1382`; `app/analysis/core.py:118–157`; `app/analysis/tables_page.py:1–8`; `tests/tables_controls.test.cjs:9–14,44–55,80–85`.

Pass 2 retains per-question/per-answer weighted sums as sufficient for exact reweighting, without stating the one-live-judgment restriction that makes the existing tables representation sufficient. The current path-based page accepts multiple overrides and recomputes products of their probabilities.

The existing control test supplies the counterexample: two independent 50% answers A and B collect $100 only when both are yes. Baseline collections are $25; forcing either answer alone gives $50; forcing both gives $100. Combining the two marginal changes gives $75 instead. The tables page deliberately resets the previous override, while the path-based page deliberately retains joint overrides.

Per-question tables exist to avoid retaining a large tree in the page. That purpose holds for this evaluator, but their exactness depends on the interaction restriction. The plan keeps that aggregation mechanism without naming its restriction or which of the two existing page behaviors it preserves.

## 4. Exhaustive traversal does not make the retained histogram percentiles exact

**Sources:** design lines 46–49, 63–64, 77–78; `app/analysis/core.py:6–12,189,200–232`; `design/002-answer-first.md`, “Sound bounds come from the line's own limits.”

The plan retains histograms and requires identical Python figures, then concludes that there are no ranges because all figures are exact. Existing histogram quantiles report bin midpoints, not exact values. Walking every history removes missing probability mass, not this value approximation.

For example, with a 100-cent-wide bin starting at zero, a percentile falling on a one-cent observation is reported as 50 cents. This remains true with a completely enumerated distribution. `Bins.quantiles` expressly promises only half-bin accuracy; the preceding design says that approximation stays and is stated.

Histograms bound storage while preserving useful distributional summaries; that purpose remains applicable at the proposed scale. Their approximation also remains. The plan retains the mechanism while dropping the distinction between exact probability accumulation and approximate percentile locations.

## 5. The retained “worst-path search” is still a sampled search

**Sources:** design lines 3–5, 77–80; `app/analysis/stress_search.py:60–65,97–130,149–157`.

The existing search does not establish the worst feasible history. It evaluates the original walk's 25 largest conditional unrecovered means, then requests 48 uniform-answer samples, with a 420-second walk budget and a further evaluation allowance. Its returned note explicitly says the result is not claimed to be the worst.

A more severe feasible continuation absent from those candidates remains undiscovered. Exhaustive weighted scalar and histogram accumulation in pass 2 does not, as specified, retain the history attaining the stress ranking; leaving the existing search unchanged therefore leaves this gap even after the main tree becomes exact. The search also directly uses `AnswerFirstWalk`'s sampling configuration, which the deletion section removes.

The separate search exists to retain feasible stress outcomes regardless of their model probability; that purpose still holds. The contractual ceiling bounds what an undiscovered history could do, but does not identify the worst history. The plan keeps both mechanisms without distinguishing that ceiling and sampled incumbent from the promised worst path.

## Retained choices whose purpose the new note does not state

Besides the grouping, aggregation restriction and histogram approximation above, the note keeps **512 operating draws** (lines 9, 21) without explaining their role. The preceding design's “What is kept, and why” identifies them as ordinary business variation, distinct from event-tree coverage. That purpose still holds: complete event enumeration is not complete enumeration of the operating distribution. This is an omitted distinction, not evidence that 512 is itself the wrong count.

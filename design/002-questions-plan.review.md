# Findings: `design/002-answer-first.md`

## 1. The retained record prefix is not the prefix at which a deferred question is asked

**Sources:** design lines 157–161; `app/disputes/chronological.py:306–334`; `app/disputes/forecast.py:1825–1850`; `app/disputes/notes.py:105–140`.

Grouping the existing records by `(key, prefix)` does not establish node-local questions. For a deferred question, the recorder stores `steps[:i]`, where `i` is the originating motion or acceleration, not the reached prefix when the later question freezes. `freeze_due` reconstructs facts using the reached history but preserves that truncated identity.

For example, two histories share a stay motion, then differ in an intervening financing answer before court approval. Their approval-day cash can differ while remaining in the same situation class. Both approval records retain the same key and pre-motion prefix. The proposed grouping therefore pools the two situations again; alternatively, an answer obtained from the first record would precede completion of that pooled group.

The existing prefix associates a deferred record with its originating decision. That purpose remains useful, but it does not identify the later decision's complete before-answer history. The retained medoid and ranges describe the supplied group faithfully; they cannot make this group node-local.

## 2. Retaining composite branches leaves some sampled stubs without the claimed exact mass

**Sources:** design lines 120–125, 142–154; `app/disputes/forecast.py:2756–2772,3106–3123`; `app/disputes/answer_first.py:79–101`.

The upper-bound workaround permits a stub before a composite branch's later answer is known, but the interval construction still assumes every stub has an exact prefix probability. These are different objects when the branch already encodes a future answer whose distribution depends on intervening events.

Suppose a motion is certain, an intervening financing succeeds with probability 0.5, and approval has probability 0.9 after success and 0.1 after failure. The pre-financing branch labelled “approved” has mass 0.5. Neither the first answer's upper bound of 1 nor either sampled continuation's approval probability is that mass. Computing it requires integrating the intervening histories. The existing stay constructor already places the approval answer in that branch's composite edge before those histories are walked.

Composite deferral exists to include intervening events in approval-day and petition-day facts; that purpose still holds. The proposed upper bound suffices for deciding not to expand a branch exactly, but it does not supply the exact stub mass used by the subsequent error formula. Final complete-path importance weights do not establish that missing quantity either.

## 3. The proposed Hoeffding range does not bound the retained importance-weighted estimator

**Sources:** design lines 120–128, 152–153; `app/disputes/answer_first.py:126–166`; `app/analysis/page.py:833–844`.

The formula bounds an unweighted continuation value's range, multiplied by a judgment-change ratio. The retained estimator instead samples a guide and multiplies each value by its judgment probability divided by its sampling probability. Even unchanged judgments need not equal that guide: the latest plan explicitly uses even odds before a deferred answer is available.

Consider a stub with two continuations that both collect $100, sampled with guide probabilities 0.5/0.5, whose unchanged judgments are 0.99/0.01. The continuation-value range is zero and the stated judgment-change ratio is 1, so the proposed half-width is zero. The retained estimator's individual observations are nevertheless $198 or $2. With eight independent replicates, its mean equals the true $100 only when four of each are sampled: probability `70/256`, about 27.3%. The claimed zero-width 99% interval misses otherwise. Multiplying by any positive stub mass preserves the failure.

The fixed divisor exists to make complete-path estimates unbiased and reweightable. That purpose survives, but unbiasedness does not make the raw weighted observations obey the financial value range. Replacing the t table with the stated formula therefore does not establish the promised distribution-free coverage.

## 4. The stopping rule silently doubles the retained dollar-width allowance

**Sources:** design lines 4–5, 63–64, 116–118, 129–131.

The retained requirement is a range **no wider than 1% of commitment**. The new stopping rule compares the **half-width** with 1%. A figure with a half-width of 0.75% passes that rule even though its displayed interval is 1.5% wide. The changed-judgment warning uses the same inconsistent threshold.

The 1% number limits computational uncertainty separately from ordinary operating dispersion. Its purpose still holds after switching from unresolved-mass bounds to confidence intervals. The plan tunes its effective allowance without identifying that change; the cited decision about 99% intervals does not state a change from full width to half-width.

## 5. Sampling replaces the quantile construction without carrying forward its precision criterion

**Sources:** design lines 59–64, 120–131; `tasks/002-3.md:18–23`; `app/analysis/core.py:196–210,234–238,425–428`.

The earlier construction bounds a quantile using an exact expanded distribution and a bounded unresolved mass. The sampled-tail revision supplies a concentration bound and stopping rule only for expected figures; the page task expressly leaves percentiles as they are. It establishes neither the retained quantile-level tolerance nor a 99% interval for those percentiles.

A small financial spread demonstrates why the expectation criterion cannot substitute. Let an exact head put 90% at $101 and a sampled tail divide the remaining 10% in proportions 60/40 between $100 and $101. The true total mass at $100 is 6%, so both P4.5 and P5.5 are $100. If only three of eight tail replicates collect $100, the estimated lower mass is 3.75% and the reported P5 becomes $101, outside that tolerated range. This can happen despite tiny uncertainty in expected dollars relative to a $100 commitment.

The existing P5/P95 figures show operating and event dispersion, and the histogram bins approximate their dollar locations. Both purposes remain relevant. The retained histogram approximation does not bound error in the sampled probability mass, and the plan's new expected-value interval does not account for that separate uncertainty.

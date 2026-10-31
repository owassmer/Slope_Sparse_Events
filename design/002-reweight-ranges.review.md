# Findings: `design/002-answer-first.md`

## 1. The promised re-walk cannot rely on every answer already being cached

**Sources:** design lines 27–33, 231–245, especially “with every answer already cached”; `app/agent/jev.py:158–177`; `AGENTS.md`, “Needs you”.

The new re-walk is meant to explore precisely the regions the original samples did not visit. Those regions can contain new node-local questions, not merely new probabilities for previously answered questions.

For example, a financing answer inside a stub originally has probability zero. No sample follows it. Changing that answer to 100% reaches a later court question with different cash and security facts. The financing lever supplies the financing distribution, not the court's answer in that unseen state. The latter is absent from the cache. This is also possible with positive probabilities when every original replicate misses the branch.

The existing content-keyed cache reuses a judgment only for the same rendered question and state. That purpose still holds under the plan's node-local question definition; it cannot supply judgments for unseen situations. The adapter raises on a cache-only miss and otherwise makes a billed request. Consequently, the promised server re-walk either cannot complete from saved answers or requires new model calls. The retained requirement that Owen authorize real-judge spending remains applicable, but the plan presents this recovery route as already answered and does not account for that boundary.

## 2. The changed-judgment revision leaves the retained percentile guarantee unsupported

**Sources:** design lines 56–64, 204–206, 236–245; `app/analysis/page.py:855–864,879–890`; `tasks/002-3.md:18–23`.

The latest revision replaces reweighted sampling intervals with guaranteed stub bounds for expected figures. It does not replace the earlier sampled-CDF construction for percentiles after a judgment changes. Samples kept under the original judgments no longer estimate the changed distribution's probability below a value. Dollar bounds on expectations do not establish the retained P4.5–P5.5 tolerance.

For example, let an exact head put 94% probability at $101 and a stub carry 6%. Its continuations collect either $100 or $101. Under the original judgment all its samples collect $100; after an override the stub certainly collects $101. Keeping its sampled distribution reports P5 at $100, although both P4.5 and P5.5 under the changed judgment are $101. The guaranteed expected-collections range is only [$100.94, $101], far narrower than 1% of a $100 commitment, so the stated dollar-width trigger does not request a re-walk in this case.

The existing percentiles describe operating and event dispersion, while the retained level tolerance limits computational error in locating them. Both purposes still hold when Russell changes a judgment. The histogram approximation only controls dollar binning; neither it nor the new expected-value bounds protects the changed percentile. The plan retains that tolerance and the page's percentile figures without accounting for them in its new two-state range construction.

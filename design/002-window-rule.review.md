# Findings: `design/002-answer-first.md`

## 1. The retained reweighting formula still does not bound its observations

**Sources:** design lines 120–128, 199–203, 218–222; `app/disputes/answer_first.py:11–15`.

Walking windows exactly makes the unchanged-judgment observation a convex combination of financial values. It does not preserve that property after a judgment changes. The retained formula multiplies the financial range by the maximum likelihood ratio; that is not generally the range of the importance-weighted observation.

For example, outside any deferred window, let a stub have two continuations both collecting $100. Sample under the original judgment, 0.5/0.5, then change it to 0.99/0.01. The observations become $198 and $2. The financial range remains zero, so multiplying it by the maximum ratio, 1.98, still reports zero uncertainty. With eight replicates, that zero-width interval covers the true $100 only when four of each continuation were sampled: probability `70/256`, about 27.3%, not 99%. A positive stub mass simply scales the failure.

The financial floor and ceiling protect the unchanged-judgment sampling bound; the fixed divisor preserves unbiased reweighting. Both purposes still apply, but neither establishes the retained formula after reweighting. The latest window rule does not address this case because the sampled decision has no window.

## 2. Making the guide equal the judgment removes the support needed by the retained sensitivity controls

**Sources:** design lines 79–82, 186–190, 199–203, 222–223; `app/disputes/answer_first.py:17–20,126–166`; `app/disputes/forecast.py:191–203`; `app/web/static/page.js:488`.

The plan requires sampling from the judgment itself while retaining changes to that judgment through likelihood ratios. Existing judgment conversion permits zero probabilities. A continuation assigned zero probability is therefore never sampled, and its new-to-old ratio becomes undefined when a lever assigns it positive probability.

For a sampled stub outside a window, let the two continuations collect $0 and $100, with original probabilities 1/0. Every retained sample collects $0. Moving the judgment to 0/1 leaves those retained samples with zero weight, although the true conditional collections are now $100. Neither more replicates under the original judgment nor exact enumeration of deferred windows supplies the missing continuation.

The existing guide explicitly gives every answer positive probability to keep reweighting supported, not merely to improve precision. That purpose still holds for the retained controls, which allow 0% and 100%. The plan changes this support mechanism without accounting for its purpose; a width warning based on a finite maximum ratio does not cover a division by zero.

## 3. The promised maximum reweighting ratio requires answers the sampled run does not obtain

**Sources:** design lines 27–33, 50–54, 79–82, 201–203, 218–223.

Even when all original probabilities are positive, the proposed maximum over **any** continuation cannot be obtained from the questions reached by sampling. Node-local questions depend on the actual dated state, and a kind/domain lever also affects questions in states that no replicate visits. Their original judgments are unknown.

For example, suppose all reached questions of a lever's kind assign an answer probability 0.5. An unvisited continuation can reach another question of that same kind and domain whose original answer probability is `10^-12`. Setting that answer to 100% gives a local ratio of 2 on the recorded questions but `10^12` on the unvisited one. Runs with these different unseen judgments have identical retained records, yet require radically different bounds. Enumerating deferred windows does not reveal an unsampled branch outside those windows.

The maximum ratio is intended to protect precisely against a changed judgment making a missed region important. That purpose remains necessary. The plan retains it as the basis of the displayed interval without identifying how unknown node-local judgments enter the bound; multiplying ratios from the questions actually asked does not establish the stated maximum.

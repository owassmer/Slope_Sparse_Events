## 1. Restoring a run loses its sampling weights

**Sources:** `app/analysis/build.py:127–161,483–499`; `app/disputes/forecast.py:99–103`; `app/analysis/core.py:132–136`.

The persisted model omits `DisputePath.divisor` and `replicate`. Loading `analysis.json` therefore restores every sampled path as an exact path, with divisor 1 and replicate −1. Executing the repository’s serialization functions on a path with divisor 0.4 and replicate 3 reproduced that change. Its financial weight after restoration is only 40% of its original weight.

This occurs when the viewer rebuilds its local page state from a recorded run without the cached pickle. The browser retains the original divisors from `page.json`, while server-side reweighting uses the restored, incorrect model.

The existing record format exists to reproduce a run without new judgments. That purpose still holds, but the retained shape no longer carries enough information to reproduce the sampled analysis.

## 2. Retained normalization makes the charts disagree with the tiles

**Sources:** `app/analysis/page.py:895–908,1287–1298`; `app/web/static/page.js:58–70,94`; `tests/test_page_data_20240514.py:84–93`.

The tiles sum importance-weighted path values directly; `chart_view` and server-returned metrics divide those weights by their sum. Importance weights do not necessarily sum to one in a realized sample, so these are different estimates of the same collections.

For example, take an exact head of weight 0.8 collecting $100 and a tail of weight 0.2. Suppose all eight replicates sample a zero-collection continuation whose guide probability is 0.8. After its judgment is changed to 0.5, the retained weights sum to 0.925. The collections tile reports $80; the chart’s final collections and summed monthly collections report approximately $86.49.

The retained normalization test explicitly protects interpreting relative weights 3:7 as probabilities 0.3:0.7. That purpose does not justify normalizing the new importance estimator. Nor does it apply to conditional path weights on disjoint draw masks: two half-population paths of weight 1 each collecting $100 contribute $100 in total, not the chart’s $50.

## 3. The asserted 99% intervals can report certainty after missing the relevant tail

**Sources:** `app/analysis/page.py:828–844`; `app/disputes/answer_first.py:126–166`; `app/web/static/page.js:46–54`; `tasks/002-3.md:10–21`.

Student’s t times the empirical standard error is presented as a 99% interval under any changed judgments. The replicate estimates can instead be rare, highly skewed importance-weighted outcomes; eight replicates do not establish that coverage.

Consider a sampled subtree where a loss requires five consecutive answers, each sampled with probability 0.2, and other continuations have zero loss. All eight replicates miss the loss with probability `(1 − 0.2^5)^8 ≈ 99.744%`. On that event, the function returns a zero-width interval for the loss contribution. Changing those five judgments to 100% still leaves every retained contribution zero, although the true conditional loss is now the entire loss amount.

Replicate spread is intended to expose sampling uncertainty; that purpose remains necessary. A positive sampling probability protects unbiasedness across repeated runs, not the coverage of this reported interval. The instruction to describe narrow intervals as below rounding would conceal this case rather than distinguish it from genuine precision.

## 4. The fixed sampling budget does not enforce the retained precision requirement

**Sources:** `design/002-answer-first.md:4–5,55–72,103–104`; `app/cli.py:339–364`; `app/disputes/answer_first.py:87–101`; `app/analysis/build.py:408–435`.

The design retains a maximum expected-value range width of 1% of commitment. The command instead uses a fixed threshold of 0.01 and eight replicates, walks once, and writes the page without comparing any resulting width with that requirement.

A subtree below the threshold can have a large financial spread, and many such subtrees can carry substantial aggregate mass. Neither its prefix probability nor the count of replicates bounds the dollar error. A run whose intervals exceed 1% is still reported as complete.

The design’s per-draw financial bounds and ε were for connecting omitted probability to consequential dollar error, not merely shortening the walk. That purpose survives the move to sampling, but there is no replacement precision criterion. The new numerical choices **0.01** and **eight** have computational roles stated in the code, but no account of why they meet the retained financial tolerance.

## 5. The answer-first route still uses pooled, terminally collected questions

**Sources:** `design/002-answer-first.md:27–42,89–96`; `app/analysis/build.py:321–358`; `app/disputes/chronological.py:265–277,306–378`; `app/disputes/forecast.py:1881–1888,2215–2240`.

The new route walks first, then creates judgments from node keys. Its guide also receives only the key and answer domain. Neither builds the promised rendered question from the selected node’s dated state. `stand_in_judgments` explicitly removes the situation-class suffix before selecting the stand-in answer.

Meanwhile, the inherited continuation machinery still queues composite questions and retains their records at terminal emission into `facts[key]`; question rendering still reads all those records. Two prefixes reaching the same key with different dated cash can therefore supply one pooled question rather than two node-local questions. Changing which continuations are sampled can change the records used for that earlier question’s representative and ranges.

The existing pooling mechanism served the former whole-tree question definition; composite deferral served approval-day security and later petition facts. Their financial purposes are not replaced merely by sampling their children. The settled design explicitly changes the grouping and splits dated decisions, while retaining the medoid to represent one real joint trajectory. Those changes are absent from this execution route.

## 6. The new run bypasses the promised independent history checks

**Sources:** `design/002-answer-first.md:43–47,107–109`; `app/analysis/build.py:348–358,408–435`; `app/analysis/walk_checks.py:1–16`; `tests/test_same_day_frontier.py:36–58`.

The answer-first build proceeds from emitted paths directly to analysis and page output. It invokes neither the independent no-skip checker nor the plain-engine replay promised for every expanded history. There is also no answer-first test in the supplied tests exercising the promised later-answer invariance.

The retained levy-day regression demonstrates why this matters: deleting the judgment response on day 39 is rejected by the independent checker. Merely recording unchanged facts for the questions that remain does not detect that omitted decision. The new route has no equivalent run-time check before producing its page.

These checks exist to protect decision completeness, same-day ordering and agreement with independently replayed cash, not to support full-tree pooling. Their purpose still holds for sampled histories; naming them in the design does not make them part of the new run.

## 7. The stress-search plan treats a supported history as feasible on every draw

**Sources:** `tasks/002-2.md:11–13,27–35`; `app/disputes/forecast.py:92–98`; `app/disputes/chronological.py:261–265,395–408`; `app/analysis/events.py:3978–3984,4003–4020`; `app/analysis/core.py:545–549`.

The task says feasibility follows from taking any history emitted by the chronological walk, then directs evaluation of the winner on all 512 draws. An emitted history is guaranteed feasible only on its draw mask.

For example, a history found on a cash-rich draw may answer `pay` in option group 1. A cash-poor draw in group 0 offers only filing or doing nothing. Replaying the same steps over all draws does not establish a feasible stress history there: the engine canonicalizes grouped answers to their plain branch, and the referenced stress measure averages the resulting trajectories.

The existing mask and option groups prevent unaffordable payments and unavailable financing from becoming offered actions. Their purpose still holds when probability is ignored. The plan retains the history’s steps and all-draw stress mean without accounting for that support restriction, so its claimed feasible incumbent—and its comparison with the bound—can be invalid.

## 8. Valid replicate counts can crash page generation

**Sources:** `app/cli.py:341–342`; `app/disputes/answer_first.py:68–71`; `app/analysis/page.py:829–843,972–975`.

`--replicates 18` passes both validation points, but the t table has no entry for 17 degrees of freedom. A run with sampled paths reaches `T99[17]` during page generation and raises `KeyError`, after walking and financial analysis. Other accepted counts fail similarly.

The t table exists to scale the replicate standard error without a SciPy dependency. Even apart from the coverage problem above, its supported counts do not match the exposed tuning control; increasing sampling effort can prevent the page from being produced.

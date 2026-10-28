# Findings: `design/002-answer-first.md`

## 1. The proposed continuation bounds do not bound this reusable line

**Source:** design lines 48–53; `app/analysis/engine.py:9–29`; `app/analysis/core.py:643–685`; `cases/akoustis_20240514/run_inputs.json:50–76`.

“Everything still contractually due” at a stub excludes installments created by future invoice funding. A continuation can collect the existing balance, reuse the freed capacity, and collect again. Even adding booked collections to the currently outstanding contractual schedule does not bound that continuation.

Likewise, “exposure between zero and the commitment” does not bound all existing exposure figures. The stayed claim includes fees: funding a full limit at this case’s 370-basis-point fee produces an initial obligation of 103.7% of that limit. The line also reassesses its limit from trailing receipts; the review-date limit is not a fixed ceiling on later principal.

The existing limit controls **concurrent principal funding**, not lifetime collections or fee-inclusive claims. That purpose still holds. Treating it as a universal monetary bound breaks the claimed interval coverage and the ε calculation. The existing collection-bin bound explicitly accounts for repayment-driven reuse and opening exposure; the plan does not account for why those terms exist.

## 2. The mass-times-range argument does not cover the page’s quantiles

**Source:** design lines 48–53, 69–70; `app/analysis/page.py:838–873`; `app/analysis/core.py:342–385,405–442`.

The premise that “each figure is a probability-weighted sum” is false for the existing page. Its collection ranges are P5/P95; its monthly headroom is P5. Other analysis outputs include conditional exposure and collection-rate ratios.

For example, suppose expanded histories place 4.9% probability at zero collections and 94.6% at the commitment, leaving 0.5% unresolved. Putting that remainder at zero makes P5 zero; putting it at the commitment makes P5 the commitment. The expectation’s uncertainty is only 0.5% of commitment, but P5’s uncertainty is 100%.

These existing quantiles describe dispersion in operating and event outcomes, not uncertainty caused by stopping computation. Their purpose remains relevant to the comparison with ordinary business variation. The plan neither distinguishes these two kinds of range nor establishes its 1% guarantee for the retained nonlinear figures. “Expanded histories are exact” does not remove the existing histogram approximation either.

## 3. Minimum collections is not the worst lender-exposure path

**Source:** design lines 55–57; `app/analysis/page.py:1180–1189`; `app/analysis/core.py:541–550`; `app/analysis/engine.py:9–12,27–33`.

The pruning rule establishes a minimum of total collections only. With reusable funding, that is not an ordering of lender harm. A path that collects $100 and makes no further advances can have no unpaid balance; a path that collects $120 after further lending can leave $80 stayed. Once the first path establishes a $100 incumbent, the second can be pruned despite its worse exposure. Equal total collections can also have different collection dates and capital tied up.

The existing unweighted stress ranking uses unpaid/stayed obligations and frozen exposure to show lender downside without letting low probability hide it. That purpose still applies. Ignoring probability preserves only part of it; changing the objective to collections alone does not establish the “worst feasible path” beside timing and exposure figures.

## 4. Keeping composite deferral leaves child probabilities dependent on unanswered future questions

**Source:** design lines 34–46 and 80–81; `app/disputes/forecast.py:2729–2760,3074–3115`; `app/disputes/chronological.py:306–363`.

The fallback to “existing deferral” conflicts with calculating each child’s probability when its parent is answered. Existing stay continuations already select approved/denied branches using a composite edge containing both the motion answer and the later court answer. Notes continuations similarly combine acceleration, the issuer’s later filing decision, and potentially holders’ still-later petition decision.

If an intervening offering changes cash before approval, the approval distribution cannot be known at the motion node under the proposed dated-state rule. Nevertheless, the existing approved child’s mass requires that distribution before the threshold comparison. Retaining that composite continuation while deferring its question therefore leaves the mass used for pruning undefined.

Deferral exists to capture intervening bookings and size security or petition facts on their actual decision dates. Its purpose still holds; postponing record capture alone does not resolve the new answer-first dependency.

## 5. The replacement test does not cover the demonstrated failure protected by the deleted checks

**Source:** design lines 37–39, 68, 75–76; `app/analysis/walk_checks.py:1–16`; `tests/test_same_day_frontier.py:36–58`; `tasks/001-14h.md`, opening two paragraphs.

The plan deletes the every-history checks and identifies later-answer invariance as its proof. A walk can omit a required decision and still return byte-identical facts for every question it does ask when later answers change.

This is a demonstrated case, not a hypothetical checker concern: the saved levy-day regression requires the judgment response before the levy and cash-floor decision on day 39. Removing that response triggers the independent no-skip checker. An invariance test over the remaining questions does not establish that the missing response ever occurred. It also does not establish equality with the independently replayed financial state.

The existing checks serve three distinct purposes: correct dated facts, complete decision coverage, and same-day ordering. Those purposes survive the removal of full-tree pooling. The plan treats them as full-tree machinery without accounting for the latter two protections.

## 6. One distribution per question kind can assign mass to unavailable actions

**Source:** design lines 59–61, 84; `app/analysis/events.py:3988–4009`; `app/disputes/chronological.py:395–408`.

The stated override applies one distribution to every question of a kind, but questions of the same kind do not necessarily have the same answer domain. A judgment-response question in option group 3 offers payment and an offering; group 0 offers only filing or doing nothing. A distribution with positive payment mass cannot be applied unchanged to both. Dropping the unavailable branch also loses probability mass, invalidating exact reweighting and stub masses.

The retained situation classes and option-group rule prevent an answer from paying an unaffordable balance or initiating unavailable financing. That purpose still holds under sensitivity controls. The plan leaves “kind” open without reconciling its promised uniform distribution with those existing financial eligibility constraints.

## 7. The required ordinary-risk comparison is left outside the stated interval and dating construction

**Source:** design lines 25–46, 48–53, 84; `cases/akoustis_20240514/scenario.json:71–74`; `app/disputes/forecast.py:4003–4005,4021–4029`; `app/analysis/page.py:978–980`.

For this case, `_BankWalk` dispatches to `_OrdinaryWalk`: the same forecast with the dispute given no cash effect, including its own distress, listing and notes decisions. It is not a fixed bank-cash baseline. The page displays and reweights its own paths and judgments.

The plan leaves this walk’s treatment open while promising dated questions and bounded figures for the page. A stubbed research forecast compared with an ordinary walk still using its existing pooled/late records is not covered by the proposed construction or proof; no error allocation is stated for differences between two uncertain views either.

The existing ordinary-view rule isolates what the lawsuit adds while retaining ordinary financing and operating risk. Its purpose holds here and is expressly required by `AGENTS.md`’s Purpose. The plan names this dependency without carrying that purpose through its guarantees.

## 8. Retained numerical and rendering choices lack a purpose account

**Source:** design lines 11, 17, 27–32, 82–83; `app/analysis/setup.py:11–13`; `app/disputes/state14.py:1–8,61–95`.

The plan carries forward **512 operating draws** and the **date-and-cash medoid with per-field ranges**, and proposes tuning **rendering granularity**, without explaining what the retained count and representative rule are for or what must survive the tuning.

The draws represent ordinary operating variation; ε bounds omitted event probability conditional on those draws, not sampling error in that variation. The medoid keeps the displayed facts on one actual joint trajectory, with ranges exposing variation within the group. Both purposes still apply after changing the group from all reaching histories to one prefix. Counting merged questions alone does not show that coarser rendering preserves materially different situations. The plan states the computational benefit of merging but leaves these retained purposes and limits unaccounted for.

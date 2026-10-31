# 001-1b result: not ready for the full re-walk

No cloud, judgment calls, or full re-walk were started. The saved parts were read locally and their full paths replayed through the dated engine. Day numbers below are engine indices (day 0 = 15 May 2024).

## Petition at cash exhaustion: the existing fix covers the saved failures

Both `part1840009.pkl` findings have trajectory 145 and the same full prefix as `SAVED` in `tests/test_dated_answer_domains.py`, ending with `judgment_default/I1/accelerated` or `/no` instead of `/holders_file`.

The ripe response initiates an offering on day 96, closing on day 101. The floor is day 155; exhaustion is day 161. At exhaustion the answer group is 0 (`file`, `neither`), not the group's saved offering answer. The I1 offering also initiates on day 161: its reservation is pending at exhaustion. These are later-walked decisions whose earlier/same-day effects invalidate the old exhaustion domain. `fc03d5b` schedules the ripe response first. The regression now covers both saved endings and rewalks all answers from their common registration prefix; every emitted floor/exhaustion/response answer is feasible in its dated group. Stress branches remain available.

## Judgment payment: a different cause, fixed in `app/analysis/events.py`

Inspected all three matching full paths in each of `part1830010.pkl` and `part1220001.pkl` (not just their tails). The former has entry `none`, floor `neither`, an exhaustion offering, then ripe `pay`; the latter has a failed entry offering, an I1 offering, floor `neither`, then ripe `pay`.

For trajectory 279 of part1830010, exhaustion/offering is day 90 and ripe payment is day 99. The same payment prefix produced cash 1,043,021,722 cents, owed 326,308,733 cents, group 3. Adding the day-159 listing step changed that **earlier** question to cash 780,609,815 cents, owed 1,078,621,305 cents, group 2. The listing answer itself was not the cause: advancing toward its date flushed waiting decisions differently than finishing the payment prefix.

`Chain._upto_dated` exempted *all* judgment responses from the pending-levy date bound. Only levy-day responses should precede their levy; the notes' ripe response must see earlier levies. The non-equity route already made this distinction. Applying it to the equity route makes the full path and prefix agree at group 3; the recorded `pay` is feasible, rather than a reason to remove payment branches. The part1220001 example similarly has I1 offering on day 76 and ripe response on day 98 (trajectory 7).

`tests/test_ripe_after_levy.py` preserves the six full-path variants and checks dated cash, owed, day and answer domains against their payment prefixes across the saved populations. All six fail without the engine fix and pass with it.

## A remaining blocker found by the prefix replays

Replayed every continuation from the registration prefixes, without probabilities or model calls:

- part1220001 trajectory 7: 3,178 emitted paths, zero infeasible grouped answers.
- part1830010 trajectory 279: 2,243 emitted paths, 106 infeasible answers, all `cash_floor/@2=initiate_offering` (no remaining `pay` mismatches).

One complete remaining failure, trajectory 279:

```python
(('settle', 'I0', 'no'),
 ('verdict', 'I0', 'award:1065000100:1000000000:1130000200'),
 ('judgment_response', 'entry', '@3=none'),
 ('post_trial_motions', '', 'yes'),
 ('settle', 'I1', 'no'),
 ('execute_pre_ruling', 'I1', 'yes'),
 ('stay', 'I1', 'yes'),
 ('registration_early', 'I1', 'yes'),
 ('cash_floor', '1', '@2=initiate_offering'),
 ('offering', 'floor1', 'yes'),
 ('judgment_response', 'ripe', '@3=initiate_offering'),
 ('offering', 'ripe', 'yes'),
 ('judgment_default', 'I1', 'yes'),
 ('post_trial_ruling', '', 'set_aside'),
 ('listing', '', 'suspended'),
 ('delisting_notes', 'delisted_suspension', 'accelerated'))
```

The floor prefix says day 77, group 2. The full path includes a **day-74 ruling setting the judgment aside**; the floor moves to day 179, group 0, after listing suspension (159) and notes acceleration (170). Both stored and recomputed path masks retain trajectory 279. The offering answer is no longer feasible. This needs the earlier ruling resolved before the floor domain is selected; the two-family check is not a clean bill for `fc03d5b`'s scheduling overall. Local reproduction and traces are in `var/diag/rewalk_other.py`, `rewalk0-fixed.log`, and `new_bad.pkl`.

## Checks and running product

WORKS — 34 targeted tests: payment paths, dated domains, stay-sizing reentrancy, court-before-answer, decision snapshots.
WORKS — `uv run ruff check .`.
WORKS — Opened the lead analysis page in the locally running product; dated situation and recorded analysis render.
WORKS — Opened its investigation route; the investigation record renders.
WORKS — `drive run -- finance check`: exit 0, reference arithmetic checks pass.
BROKEN — Broad prefix replay retains the infeasible floor answer described above. Do not start the full re-walk yet.
BROKEN — Full pytest run exceeded 30 minutes with failures, so no full-suite pass is claimed. Its first failure, `test_conditional_probabilities_compose_and_conserve_mass` (`StopIteration`), also fails with this engine change removed. Logs: `var/diag/pytest-1b.log`, `baseline-1b.log`.

The page still serves its recorded artifacts; it does not demonstrate regenerated forecasts. No recorded run was overwritten. **Code is not ready for one full re-walk.**

# 001-1 result

## Diagnosis

The walk chose an infeasible answer; the class was not reading a later situation.

Replayed `part1840009.pkl`, trajectory 145, using the saved steps and the case's deterministic operating basis:

| Event | Engine day | Calendar date |
|---|---:|---|
| Response when the entered judgment default ripens | 96 | 19 Aug 2024 |
| That response's offering closes | 101 | 24 Aug 2024 |
| Cash-floor decision on the completed path | 155 | 17 Oct 2024 |
| I1 levy-day response | 161 | 23 Oct 2024 |

At the floor decision the share ledger is genuinely zero and the offered answers are `file` and `neither`.
The old walk visited the I1 response first, inserting the floor before it, then visited the earlier ripe-date
response. Before that earlier offering was known, the floor probe was day 116 and allowed an offering.
The completed path's `nocapacity` class is correct. Changing it to match the prefix would misstate the situation.

## Change

`app/disputes/forecast.py` now resolves the earlier entered-default response and its notes decisions before
continuing a later decision, on the trajectories where that ordering holds. Returning through the original I1
continuation does not ask those decisions twice. The distress scheduler also selects the earliest outstanding
floor, exhaustion or nonpayment decision per trajectory, rather than choosing the first candidate in list order.
No probabilities filter branches, and no payment, amount, date or classification rule changed.

**The saved walk must be redone. Re-pooling is not enough.** Prefix answer domains and path masks were chosen
before the earlier event. Regenerate the walk and its subdivisions with the corrected scheduling, then pool it.
Do not repair the old tree by dropping the offending answers or renaming their classes.

## Local checks

- WORKS: replayed the supplied failing path and confirmed the dated ledger exhaustion above.
- WORKS: ran the real walker from that path's last prefix before the bad ordering, on trajectory 145: 618
  continuations; every active response/floor/exhaustion answer belongs to its dated engine domain. Filing and
  continued-operation branches remain. The regression also checks each recorded class's answer domain.
- WORKS: disabling the earlier-ripe scheduling makes the regression fail: `initiate_offering` is not in
  `('file', 'neither')` at the floor.
- WORKS: 71 focused tests (dated answer domains, decision snapshots, stay-motion history, notes decisions and
  dated writs); `uv run ruff check .`.
- WORKS: started the product with `drive`; opened the lead analysis page and its investigation record;
  `drive run -- finance check` reproduced every reference value. Stopped the product afterwards.
- BROKEN: the full `uv run pytest` gate is not green. It reached 258 tests with 17 failures before I stopped
  its long-running pending-claim test after about seven minutes. All 17 observed failures reproduce with
  `HEAD`'s unchanged forecast module: one each in analysis, assumptions and chains, and fourteen
  investigation-tool failures because cache-only mode has no saved semantic responses.

No cloud work or judgment calls were made. This is a local regression of the supplied full path, not a claim
that all 279,889 saved paths have been regenerated. The diagnostic gives only tails for the other jobs,
including the 32 payment findings; their complete path files were not supplied here. A complete corrected
walk and its probability check remain necessary before using new aggregate results. Recorded pages were not
rewritten from this partial walk.

# Slope Sparse Events

A proof-of-concept for external-event credit review. An agent, with Jev making focused judgments, researches an unusual borrower event such as a lawsuit. Deterministic code turns the sourced economic effects into a financing decision and the loan's dated collections.

- Lead case: **Synergy CHC**, decision date 13 Aug 2024 (supplier-settlement obligations and an actual merchant loan)
- Transfer case: **Barfresh**, decision date 25 Oct 2024 (production disruption and a receivables facility)

Start with `Slope_Coding_Agent_Context_and_Alignment.md`, then `Slope_Credit_Scenario_Build_Specification.md`. Evidence and design contracts are in `Slope_Credit_Scenario_Research_and_Design_Kit/`.

```sh
uv sync
uv run slope --help
uv run slope evidence build                     # dated evidence DBs in var/evidence/
uv run slope evidence search synergy_20240813 "future payments settlement"
```

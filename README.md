# Slope Sparse Events

A proof of concept for sparse-event credit analysis. An agent researches a rare external event affecting a borrower, such as a lawsuit. Jev, a forecasting model, answers focused conditional questions about what each party does next. Deterministic code composes those probabilities into event paths, simulates the borrower's cash, and carries the result into the dated cash flows of a supplied Slope line. The output is scenario and sensitivity analysis of the loan's cash flows; it does not make the lending decision.

- Lead case: **Akoustis Technologies**, reviewed 14 May 2024, with Qorvo's trade-secret and patent claims at jury trial.
- Governing documents: `CLAUDE.md`, then `Slope_Coding_Agent_Context_and_Alignment.md` and `Slope_Model_Extensions_Spec.md` (§16 governs the current build).

```sh
uv sync
uv run slope --help
uv run slope analyze --run <run_id>     # the analysis and its page for a recorded run
uv run slope viewer                     # read-only viewer at http://127.0.0.1:8000
uv run pytest
```

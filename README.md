# Slope Sparse Events

A proof of concept for sparse-event credit analysis. An agent researches a rare external event affecting a borrower, such as a lawsuit. Jev, a forecasting model, answers focused conditional questions about what each party does next. Deterministic code composes those probabilities into event paths, simulates the borrower's cash, and carries the result into the dated cash flows of a supplied Slope line. The output is scenario and sensitivity analysis of the loan's cash flows; it does not make the lending decision.

- Lead case: **Akoustis Technologies**, reviewed 14 May 2024, with Qorvo's trade-secret and patent claims at jury trial.
- Agent guidance: `AGENTS.md` (`CLAUDE.md` links to it). The specifications are reference; its map says which governs.

```sh
uv sync
uv run slope --help
uv run slope analyze-case akoustis_20240514  # evidence → agent + Jev → engine → page
uv run slope analyze-case akoustis_20240514 --run <run_id>  # reuse a ready investigation
uv run --with boto3 slope analyze-case akoustis_20240514 --run <run_id> --pool s3://<bucket>/<prefix>/pool --processes 4
uv run slope analyze --run <run_id>     # analysis only for a recorded run
uv run slope viewer                     # read-only viewer at http://127.0.0.1:8000
uv run pytest
```

`analyze-case` uses the dated case’s configured evidence, baseline and financing inputs. It verifies the evidence snapshot, runs the agent-plus-Jev investigation, checks its locked record, obtains the engine’s conditional Jev forecasts, calculates financial outcomes, and writes the existing analysis page and CSVs. It reports each stage and writes `flow.json` beside the recorded run once that run exists. An incomplete investigation, changed evidence or failed forecast stops the invocation with a nonzero exit status.

Without `--pool`, the coordinator uses the existing local analysis engine; `--run` reuses the investigation and runs analysis again. With `--pool`, it adopts a completed local or S3 pool bound to that investigation and its case/model inputs, verifies question coverage, and resumes valid forecasts and financial reduction batches. Run this path on a host with room for the saved paths; production data is too large for the current local disk. Its outputs are `page.json`, `daily.csv`, `tree_answers.json`, and `tree_judgments.json.gz`. Remote compute launch remains a separate step, and full production end-to-end validation is pending. Fresh investigations and uncached Jev questions use the configured provider budgets. Start `slope viewer` to open the returned page route.

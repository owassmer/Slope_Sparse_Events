# Slope Sparse Events

A proof of concept for sparse-event credit analysis. An agent researches a rare external event affecting a borrower, such as a lawsuit. Jev, a forecasting model, answers focused conditional questions about what each party does next. Deterministic code composes those probabilities into event paths, simulates the borrower's cash, and carries the result into the dated cash flows of a supplied Slope line. The output is scenario and sensitivity analysis of the loan's cash flows; it does not make the lending decision.

- Lead case: **Akoustis Technologies**, reviewed 14 May 2024, with Qorvo's trade-secret and patent claims at jury trial.
- Governing documents: `CLAUDE.md`, then `Slope_Coding_Agent_Context_and_Alignment.md` and `Slope_Model_Extensions_Spec.md` (§16 governs the current build).

```sh
rustup toolchain install 1.98.1 --profile minimal
uv sync
uv run --no-sync python scripts/build_native.py
uv run slope --help
uv run slope analyze --run <run_id>     # the analysis and its page for a recorded run
uv run slope viewer                     # read-only viewer at http://127.0.0.1:8000
uv run pytest
```

The deterministic execution core is a Rust extension (`app._native`), built by
`uv sync` using the pinned toolchain. Rebuild after changing Rust source with
`uv run --no-sync python scripts/build_native.py`, then run checks in fresh Python
processes. The rebuild installs atomically so active processes retain their
loaded extension. Production uses `rust` and fails
clearly if the extension or a required native method is missing. Set
`SLOPE_EXECUTION_BACKEND=python` to run the retained differential reference.

The native modules cover:

- `walk*`: branching control flow, feasible masks, situation classes, ordered
  probability expressions, verdict/ruling classes, equity proceeds, and
  class-compatible path merging.
- `event*`: dated dispute and instrument transitions, equity issuance, waiting
  decisions, branch snapshots, divergence and question-state reads.
- `cash`, `owed`, `atm`, `price`: loan recurrences, ordered arrears, integer-cent
  obligations, share issuance and structural share prices.
- `reduction`: ordered probability products, sparse histograms and weighted
  distributions and quantiles.

Python retains configuration, keyed draw generation, memo caches, stored fact
records, Jev, application orchestration and parallel scheduling. NativeChain
executes transitions and queries in Rust over NumPy state buffers. Branches
share immutable inputs and replace-only arrays; mutable records are independent,
and shared event buffers copy on their first write. The separate EventLedger
type has Rust-owned buffers; it is not NativeChain's storage representation.
Calls borrow NumPy inputs for their duration: callers must keep those buffers
unchanged through all aliases and threads until the call returns. Native numeric
outputs own their buffers; shared branch event buffers follow the copy-on-write
rules above. Independent numerical work may release the GIL.

Financial integer arithmetic, half-even rounding, keyed trajectory identity,
question distinctions and record order must match the reference. Floating-point
operation order and bits are part of the contract: fast-math, fused multiply-add
and reassociation are prohibited.

The isolated differential harness checks both backends, records actual native
calls, and compares serial and parallel reconstruction:

```sh
uv run python scripts/verify_native.py --scenario all --draws 4 --horizon 45 --processes 2
SLOPE_EXECUTION_BACKEND=rust uv run pytest
SLOPE_EXECUTION_BACKEND=python uv run pytest
```

The report records draw count, tree horizon, covered paths, ordered records,
comparison digests and measured runtimes. Increase `--draws` and `--horizon` for
larger runs; a reduced run's timings do not establish a production speedup.
For a complete production-sized tree, use `--draws 512 --horizon 180` and
increase `--timeout` to allow for the much larger reference run.

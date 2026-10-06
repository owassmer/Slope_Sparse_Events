# Feature map

Each feature a verifier has reached as a user, and the steps it took. Only the verifier writes this file.

- Run list: `drive open /`; each run links to `/runs/<run>`.
- Analysis page (lead case): `drive open /runs/akoustis_20240514-agent_plus_jev-20260928T052641Z`; `drive see --all` for the whole page (headline under "The line's forecast to 10 Nov").
- Override the featured judgment: on the analysis page, `drive type <c-slider number> <0-100>` or `drive press ArrowRight <c-slider number>`; "Reset" under the slider restores Jev's answer.
- Open another judgment: on the analysis page, `drive click "<swing-card title>"` (e.g. "Akoustis responds to the judgment") under "Collections by judgment"; a drawer opens with its own d-slider; `drive click "×"` closes it.
- Reset all overrides: after any override, `drive click "Reset all"` (bottom of the page).
- Compare one economic assumption: `drive click "<radio label>"` under "One economic assumption"; table below; "Back to central" at the bottom reverts.
- Shared assumptions: `drive click "Assumptions"` (summary near the top).
- Case terms: `drive click "Case terms"` (bottom of the analysis page).
- What actually happened: `drive click "Show what actually happened"`; "Hide" closes it.
- Investigation record: `drive click "Investigation record"` on the analysis page, or `drive open /runs/<run>/investigation`; expand "Host checks (n)" with `drive click <number>`. Runs without a page (Synergy) show this record at `/runs/<run>`.
- Page HTTP routes: `drive send GET /runs/<run>/payload`, `drive send GET /runs/<run>/analysis`, `drive send POST /runs/<run>/reweight '{"overrides":{...},"assumption":"<id>"}'`, `drive send GET /runs/<run>/outcome`.
- Command line: `drive run -- --help`, `drive run -- version`, `drive run -- finance check` (all reference values reproduced).
- Engine's answer-free decision frontier: `uv run python tests/benchmark_decision_frontier.py`; seeds the three saved prefix families and calls `Chain.next_decisions(Cursors(...))` against the existing walk's candidate replays, with no model calls.
- Evidence snapshots: `drive run -- evidence build` (once per checkout), then `drive run -- evidence search <snapshot> "<query>"` and `drive run -- evidence read <snapshot> "<item_id>"`.

import numpy as np
from app.analysis.events import Chain
from app.exact import oracle, pass1, export_notes, replay
from app.exact import fields as f
DRAWS = (0, 145, 365)
fc0, d0 = oracle.context(DRAWS)
for i, h in enumerate(oracle.histories(8, DRAWS, seed=5, style="long")):
    fc, d = oracle.context(h.draws)
    py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens); py.run(h.path.steps)
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens); ch0.instrument_cash()
    tk = pass1.case_tables(fc0, d0, ch0)
    for r in range(ch0.n):
        found, st, _, _, _ = pass1.follow(fc0, d0, ch0, r, tk, h.path.steps, questions=True)
        want = export_notes.notes_state(py, r, replay.OCCASION_IDS)
        diff = [int(j) for j in np.flatnonzero(st.S[:60] != want[0][:60]) if j not in (58,)]
        print(i, h.draws[r], "same" if not diff else f"DIFF {[(j, int(st.S[j]), int(want[0][j])) for j in diff]}", h.path.steps[-4:], flush=True)

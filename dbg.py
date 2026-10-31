import numpy as np
from app.analysis.events import Chain
from app.exact import oracle, pass1, export_notes, replay
from app.exact import fields as f
DRAWS = (0, 145, 365)
fc0, d0 = oracle.context(DRAWS)
for h in oracle.histories(8, DRAWS, seed=5, style="long"):
    if 0 not in h.draws: continue
    fc, d = oracle.context(h.draws)
    py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens); py.run(h.path.steps)
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens); ch0.instrument_cash()
    tk = pass1.case_tables(fc0, d0, ch0)
    r = list(h.draws).index(0)
    found, st, _, _, _ = pass1.follow(fc0, d0, ch0, r, tk, h.path.steps, questions=True)
    want = export_notes.notes_state(py, r, replay.OCCASION_IDS)
    if st.S[f.DELISTED] != want[0][f.DELISTED]:
        print("steps:", h.path.steps)
        print("found:", found)
        diff = np.flatnonzero(st.S[:200] != want[0][:200])
        print("diff fields:", [(int(i), int(st.S[i]), int(want[0][i])) for i in diff])
        print("py AD", py.AD[r], "E_ix", py.E_ix[r], "F", py.F[r], "EF", py.EF[r], "e_ix", py.e_ix[r], "marks", {k: int(v[r]) for k, v in py.marks_now().items() if v[r] < 10**6})
        print("ex AD", st.S[f.AD], "E_IX", st.S[f.E_IX], "F", st.S[f.F], "EF", st.S[f.EF], "EX_IX", st.S[f.EX_IX])

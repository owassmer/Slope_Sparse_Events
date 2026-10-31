import numpy as np
from app.analysis.events import Chain
from app.exact import oracle, pass1, export_notes, replay
from app.exact import fields as f, fields_notes as n
DRAWS = (0, 145, 365)
fc0, d0 = oracle.context(DRAWS)
hs = list(oracle.histories(8, DRAWS, seed=5, style="long"))
h = hs[1]
print("STEPS", h.path.steps, "DRAWS", h.draws, flush=True)
fc, d = oracle.context(h.draws)
py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens); py.run(h.path.steps)
r = list(h.draws).index(0)
print("PY petition", py.ev.petition[r], "cause", getattr(py, "pet_cause", None) is not None and py.pet_cause[r], "suspended", py.suspended[r], "delisted", py.delisted[r], "notes_due", py.marks_now()["notes_due"][r])
print("PY listing dates", {k: (int(v[r]) if isinstance(v, np.ndarray) else int(v)) for k, v in py.listing_dates().items()})
print("PY recs", [(s, int(dd[r]) if isinstance(dd, np.ndarray) else dd) for s, dd in zip(h.path.steps, py.rec[0])][:40] if hasattr(py, "rec") else None)
ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens); ch0.instrument_cash()
tk = pass1.case_tables(fc0, d0, ch0)
found, st, _, _, _ = pass1.follow(fc0, d0, ch0, r, tk, h.path.steps, questions=True)
S = st.S
print("EX petition", S[f.PETITION], "cause", S[f.PET_CAUSE], "suspended", S[f.SUSPENDED], "delisted", S[f.DELISTED], "notes_due", S[f.MARK_NOTES_DUE])
k, _ = replay.constants(ch0, r)
print("EX ldates", k.ldates.tolist())
print("FOUND", [list(map(int, x)) for x in found])
print("EX steps", st.steps[:int(S[f.N_TAKES])].tolist() if hasattr(st, "steps") else None)

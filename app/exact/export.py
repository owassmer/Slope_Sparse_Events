"""A Python `events.Chain`'s state on one draw, in the exact evaluator's layout (`app/exact/fields.py`): what every
port is checked against, step by step (the same state before a step, the compiled step run, the Python's state after
it compared field by field)."""
from __future__ import annotations

import numpy as np

from app.analysis.events import KINDS, MARKS, OBLIGATIONS, PETITION_CAUSES
from app.exact import fields as f


def _int(x, r):
    a = np.asarray(x)
    return int(a[r]) if a.ndim else int(a)


def chain_state(chain, r: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(S, D, T_TAKES) for row r of `chain` (an index among its draws)."""
    S = np.zeros(f.NI, dtype=np.int64)
    for ix, attr in ((f.V, "V"), (f.E_IX, "E_ix"), (f.EX_IX, "e_ix"), (f.E0, "E0"), (f.AD, "AD"), (f.A, "A"),
                     (f.F, "F"), (f.EF, "EF"), (f.EI, "EI"), (f.FEE_DAY, "fee_day"),
                     (f.MOTIONS_FILED, "motions_filed")):
        v = getattr(chain, attr, None)
        S[ix] = f.BIG if v is None else _int(v, r)
    S[f.ENTERED] = int(chain.entered)
    S[f.CLS_AMOUNT] = f.NONE if chain.cls_amount is None else int(chain.cls_amount)
    S[f.CLS_FEES] = int(chain.cls_fees)
    S[f.INCREASE] = int(chain.increase)
    S[f.RETRIAL], S[f.Q1], S[f.APPEALED] = int(chain.retrial), int(chain.q1), int(chain.appealed)
    br = chain.band_range
    S[f.BAND_LO] = f.NONE if br is None else int(br[0])
    S[f.BAND_HI] = f.NONE if br is None or br[1] is None else int(br[1])
    for ix, attr in ((f.REMITTED, "remitted_amount"), (f.ADVERSE_FROM, "adverse_from"),
                     (f.ADVERSE_UNTIL, "adverse_until"), (f.RESOLVED, "resolved"), (f.TAKEN, "taken"),
                     (f.LEVIED, "levied"), (f.PET_CAUSE, "pet_cause"), (f.EARLY_REGISTRATION, "early_registration"),
                     (f.STAYED_FROM, "stayed_from"), (f.LOCK_AMOUNT, "lock_amount"), (f.LOCK_DAY, "lock_day"),
                     (f.COLLATERAL_REQUIRED, "collateral_required"), (f.RELEASE_AT, "release_at"),
                     (f.DELISTED, "delisted"), (f.HEARING_REQUESTED, "hearing_requested"),
                     (f.SUSPENDED, "suspended"), (f.JD_ACTED, "jd_acted"), (f.SETTLE_OFFER, "settle_offer"),
                     (f.STAY_OFFER, "stay_offer"), (f.RAISE_OFFER, "raise_offer"), (f.READS, "reads")):
        S[ix] = _int(getattr(chain, attr), r)
    S[f.PETITION] = _int(chain.ev.petition, r)
    for name in MARKS:
        S[f.MARK[name]] = _int(chain.marks[name], r)
    for name in OBLIGATIONS:
        S[f.INCURRED[name]] = _int(chain.ev.incurred[name], r)
    takes = [(_int(t, r), _int(a, r)) for t, a in chain.takes]
    if len(takes) > f.MAX_TAKES:
        raise ValueError(f"{len(takes)} takes: more than the {f.MAX_TAKES} the table holds")
    T_TAKES = np.zeros((f.MAX_TAKES, 2), dtype=np.int64)
    for k, (t, a) in enumerate(takes):
        T_TAKES[k] = t, a
    S[f.N_TAKES] = len(takes)
    D = np.zeros((f.ND, chain.N), dtype=np.int64)
    ev = chain.ev
    D[f.D_CASH] = ev.cash[r]
    D[f.D_LOCK] = (ev.base_lock if hasattr(ev, "base_lock") else ev.lock)[r]
    D[f.D_CAPACITY] = ev.capacity[r]
    for name in KINDS:
        D[f.D_KIND[name]] = ev.kinds[name][r]
    assert len(PETITION_CAUSES) < 127
    return S, D, T_TAKES

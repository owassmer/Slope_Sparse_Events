"""The core's bookings (app/exact/core.py: pay, petition, resolve) against `events.Chain`'s, at every call they make
in random real histories: the state exported just before the call, the compiled booking run on it, and the Python's
state just after compared on what that booking writes."""
import numpy as np
import pytest

from app.analysis import events
from app.analysis.events import PETITION_CAUSES
from app.exact import core, export, oracle
from app.exact import fields as f

DRAWS = (0, 145, 365)
# what the stays compute from the whole state when read (`Chain._stay_effects`), so any booking moves them: the stays'
# port compares them
DERIVED = (f.STAYED_FROM, f.LOCK_AMOUNT, f.LOCK_DAY)


def _same(S, S_after, where):
    S, S_after = S.copy(), S_after.copy()
    S[list(DERIVED)] = S_after[list(DERIVED)] = 0
    np.testing.assert_array_equal(S, S_after, err_msg=where)


def _per(x, r):
    a = np.asarray(x)
    return int(a.ravel()[r]) if a.ndim else int(a)


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_bookings_match_the_python(monkeypatch, style):
    counts = {"pay": 0, "petition": 0, "resolve": 0}
    original = {name: getattr(events.Chain, name) for name in counts}

    def hooked(name):
        def wrapped(ch, *args, **kw):
            before = [export.chain_state(ch, r) for r in range(ch.n)]
            out = original[name](ch, *args, **kw)
            case = core.Case.of(ch)
            for r in range(ch.n):
                S, D, _ = (x.copy() for x in before[r])
                S_after, D_after, _ = export.chain_state(ch, r)
                if name == "pay":
                    day, cents, kind = args[0], args[1], args[2]
                    incurred = kw.get("incurred", args[3] if len(args) > 3 else None)
                    ix = f.INCURRED.get(kind, -1) if incurred is not None else -1
                    core.pay(S, D, _per(day, r), _per(cents, r), f.D_KIND[kind], ix,
                             _per(incurred, r) if incurred is not None else 0)
                    np.testing.assert_array_equal(D, D_after, err_msg=f"pay {kind} draw {r}")
                    _same(S, S_after, f"pay {kind} draw {r}")
                elif name == "petition":
                    day = args[0]
                    where = kw.get("where", args[1] if len(args) > 1 else None)
                    cause = kw.get("cause", args[2] if len(args) > 2 else "enforcement")
                    core.petition(S, _per(day, r), True if where is None else bool(_per(where, r)),
                                  PETITION_CAUSES.index(cause), case.petition_lag, ch.N)
                    for ix in (f.PETITION, f.PET_CAUSE):  # the at-the-market rebooking after it is the equity's
                        assert S[ix] == S_after[ix], ("petition", ix, r)
                else:
                    day, where = args
                    core.resolve(S, D, ch.basis.legal[r], _per(day, r), bool(_per(where, r)))
                    np.testing.assert_array_equal(D, D_after, err_msg=f"resolve draw {r}")
                    _same(S, S_after, f"resolve draw {r}")
                counts[name] += 1
            return out
        return wrapped

    for name in counts:
        monkeypatch.setattr(events.Chain, name, hooked(name))
    for h in oracle.histories(6, DRAWS, seed=5, style=style):
        fc, d = oracle.context(h.draws)
        events.event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
    assert counts["pay"] and counts["resolve"]
    if style == "long":
        assert counts["petition"]
    print(style, counts)

"""The exact evaluator's fidelity harness (app/exact/oracle.py): random complete histories of the real case, and the
Python's per-draw reference. The design rests on the chain being draw-independent: a draw's results do not depend on
which other draws are computed with it."""
import numpy as np

from app.exact import oracle


def test_the_chain_is_draw_independent_on_random_real_histories():
    from app.analysis.events import event_trace

    fc, d = oracle._full_context()
    for style in ("uniform", "long"):
        for h in oracle.histories(3, (0, 145, 365), seed=5, style=style):
            tr = event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
            for draw, rec in oracle.reference(h).items():
                np.testing.assert_array_equal(rec["cash"], tr.events.cash[draw])
                assert rec["petition"] == int(tr.events.petition[draw])
                for k, v in rec["kinds"].items():
                    np.testing.assert_array_equal(v, tr.events.kinds[k][draw], err_msg=k)
                for i, s in enumerate(rec["steps"]):
                    assert (s["day"], s["cash"], s["owed"]) == (int(tr.day[i][draw]), int(tr.cash[i][draw]),
                                                                int(tr.owed[i][draw]))


def test_long_histories_reach_deeper():
    short = oracle.histories(6, (0, 145), seed=1)
    deep = oracle.histories(6, (0, 145), seed=1, style="long")
    assert np.mean([len(h.path.steps) for h in deep]) > np.mean([len(h.path.steps) for h in short])

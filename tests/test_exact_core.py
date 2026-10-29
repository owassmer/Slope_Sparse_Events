"""The exact evaluator's chain core (app/exact/core.py) against the Python Chain, step by step on random real
histories (app/exact/oracle.py): the same state before a step, the compiled step run on it, the Python's state after
it compared on every core field; and the compiled amount owed against `Chain.owed_at` on every draw at many days."""
import numpy as np
import pytest

from app.analysis import events
from app.exact import core, export, oracle
from app.exact import fields as f

DRAWS = (0, 145, 365)
DAYS = np.array([0, 1, 20, 45, 60, 90, 120, 150, 179, f.BIG], dtype=np.int64)


def _run_with_hook(history, hook):
    original = events.Chain.step

    def step(self, node, ctx, branch):
        before = [export.chain_state(self, r) for r in range(self.n)]
        day = original(self, node, ctx, branch)
        hook(self, node, ctx, branch, before, day)
        return day

    events.Chain.step = step
    try:
        fc, d = oracle.context(history.draws)
        events.event_trace(d, history.path, fc.setup, fc.m, fc.draws, fc.sens)
    finally:
        events.Chain.step = original


def _compiled_step(chain, node, ctx, branch, S, r, case, DC, nbd):
    """The compiled step for `node` on S (row r), or None where the core does not book that node."""
    args = (case.entry_next_business_day, nbd[r], case.frcp_50b_59, case.briefing_days, case.frcp_62a,
            case.frap_4a1a)
    if node == "verdict":
        if branch.startswith("award:"):
            _, total, lo, hi = branch.split(":")
            return core.step_verdict_award(S, DC[r], int(total), int(lo), 0 if hi == "top" else int(hi), hi == "top",
                                           *args)
        spec = events.pending_template(chain.m)["verdict_branches"][branch]
        amount = events.verdict_amount(chain.d, chain.m, branch, chain.sens) if spec["judgment"] else 0
        return core.step_verdict_branch(S, DC[r], bool(spec["judgment"]), amount, bool(spec.get("adverse")), *args)
    if node == "post_trial_motions":
        return core.step_post_trial_motions(S, DC[r], branch == "yes", case.frcp_50b_59, case.briefing_days,
                                            case.frap_4a1a, case.sensitive_lag, chain.N)
    return None


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_core_steps_and_the_amount_owed_match_the_python(style):
    checked = {"steps": 0, "owed": 0}

    def hook(chain, node, ctx, branch, before, day):
        case = core.Case.of(chain)
        DC = core.draw_constants(chain)
        nbd = core.entry_days(chain)
        for r in range(chain.n):
            S, _, T = (x.copy() for x in before[r])
            got = _compiled_step(chain, node, ctx, events.plain(branch), S, r, case, DC, nbd)
            S_after, _, T_after = export.chain_state(chain, r)
            if got is not None:
                assert got == int(np.asarray(day)[r]), (node, branch, r)
                np.testing.assert_array_equal(S, S_after, err_msg=f"{node} {branch} draw {r}")
                checked["steps"] += 1
            for enf in (False, True):
                for t in DAYS:
                    want = int(chain.owed_at(np.full(chain.n, t, dtype=np.int64), enforceable=enf)[r])
                    assert core.owed_at(S_after, T_after, case.bps, case.pending, t, enf) == want, (node, t, r)
                    checked["owed"] += 1

    for h in oracle.histories(5, DRAWS, seed=11, style=style):
        _run_with_hook(h, hook)
    assert checked["owed"] > 0
    if style == "long":
        assert checked["steps"] > 0

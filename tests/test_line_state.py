"""Integer parity of the resumable draw state with the defining daily engine."""
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from test_line import SETUP, tiny

from app.analysis import line_state as ls
from app.analysis.engine import prepare, run, with_petition
from app.analysis.events import BIG, EventCash, event_trace
from app.analysis.setup import Exposure


def assert_result(bundle, reference, draw, line):
    state, tables, _, outputs, draws, _ = bundle
    for row, name in enumerate(ls.OUTPUT_NAMES[:7]):
        np.testing.assert_array_equal(outputs[row], getattr(reference, name)[draw], err_msg=name)
    np.testing.assert_array_equal(outputs[9:].T, reference.processed.arrears[draw])
    hr = reference.headroom_rows == draw
    days = np.flatnonzero(outputs[8])
    np.testing.assert_array_equal(days, reference.headroom_days[hr])
    np.testing.assert_array_equal(outputs[7, days], reference.headroom[hr])
    rr = reference.draw_rows == draw
    td, slot = np.nonzero(draws)
    np.testing.assert_array_equal(td, reference.draw_days[rr])
    np.testing.assert_array_equal(draws[td, slot], reference.draw_amounts[rr])
    for index, name in ((ls.FUNDED, 'drawn'), (ls.CONTRACTUAL, 'contractual'),
                        (ls.COLLECTED, 'collected'), (ls.FAILED, 'failed_debits')):
        expected = getattr(reference, name)[draw]
        if name == 'drawn':
            expected += line.setup.exposure.principal_cents
        assert state[index] == expected, name
    assert state[ls.FIRST_UNPAID] == reference.processed.first_unpaid[draw]
    assert state[ls.LEVY_UNMET] == reference.processed.levy_unmet[draw]
    expected = BIG if reference.processed.nonpayment is None else reference.processed.nonpayment[draw]
    assert state[ls.NONPAYMENT] == expected
    names = ('stayed', 'stayed_principal', 'preference', 'not_yet_due', 'uncollected')
    assert ls.claims(state, tables) == tuple(int(getattr(reference, name)[draw]) for name in names)
    assert state[ls.DAY] == line.days


def check_splits(line, opening, ev, terms=(7, 5000), every_split=False):
    ref = run(line, opening, ev, nonpayment=terms)
    for r in range(line.ops.draws):
        bundle = ls.prepare_draw(line, opening, ev, r, terms)
        state, tables, cash, outputs, draws, scratch = bundle
        initial = ls.copy_state(state)
        ls.advance(state, tables, cash, 0, line.days, outputs, draws, scratch)
        assert_result(bundle, ref, r, line)
        terminal = ls.copy_state(state)
        expected_outputs, expected_draws = outputs.copy(), draws.copy()
        cuts = range(line.days + 1) if every_split else (0, 1, line.days // 2, line.days - 1, line.days)
        for a in cuts:
            ls.restore(state, initial)
            outputs[:] = -999
            draws[:] = -999
            ls.advance(state, tables, cash, 0, a, outputs, draws, scratch)
            saved = ls.copy_state(state)
            assert state[ls.COLLECTED] == ref.collections[r, :a].sum()
            assert state[ls.FUNDED] == line.setup.exposure.principal_cents + ref.fundings[r, :a].sum()
            for index, expected in ((ls.FIRST_UNPAID, ref.processed.first_unpaid[r]),
                                    (ls.NONPAYMENT, BIG if ref.processed.nonpayment is None
                                     else ref.processed.nonpayment[r])):
                assert state[index] == (expected if expected < a else BIG)
            if a:
                assert state[ls.AVAILABLE] == ref.cash[r, a - 1]
                np.testing.assert_array_equal(outputs[:, :a], expected_outputs[:, :a])
            prefix = outputs[:, :a].copy()
            b = min(line.days, a + 11)
            ls.advance(state, tables, cash, a, b, outputs, draws, scratch)
            ls.advance(state, tables, cash, b, line.days, outputs, draws, scratch)
            # Restoring the parent must not alias any child's mutation.
            ls.restore(state, saved)
            ls.advance(state, tables, cash, a, line.days, outputs, draws, scratch)
            np.testing.assert_array_equal(outputs[:, :a], prefix)
            np.testing.assert_array_equal(state, terminal)
            np.testing.assert_array_equal(outputs, expected_outputs)
            np.testing.assert_array_equal(draws, expected_draws)
        # Every day can be a decision boundary, including consecutive empty segments.
        ls.restore(state, initial)
        for a in range(line.days):
            ls.advance(state, tables, cash, a, a, outputs, draws, scratch)
            ls.advance(state, tables, cash, a, a + 1, outputs, draws, scratch)
        assert_result(bundle, ref, r, line)


@pytest.mark.parametrize('collection', ['debit', 'protect_need'])
@pytest.mark.parametrize('order', ['scheduled_first', 'operating_first'])
@pytest.mark.parametrize('petition', [-1, 0, 45, 100, 179])
def test_daily_rules(collection, order, petition):
    setup = replace(SETUP, fee_bps=5000, cash_processing='daily', collection=collection,
                    same_day_order=order,
                    exposure=Exposure(principal_cents=1, past_due_cents=3, cash_cents=13,
                                      installments=((SETUP.review + timedelta(days=60), 3),)))
    ops = tiny(1, 3)
    ops.inflow, ops.outflow = np.maximum(ops.total, 0), np.minimum(ops.total, 0)
    line = prepare(setup, ops)
    line.limit[:] = 5
    line.routes[:] = 1
    ev = with_petition(EventCash.zeros(3, line.days, kinds=True), petition)
    for c, day, amount, incurred in [('judgment', 20, 1000, 10), ('settlement', 21, 15, 1),
                                      ('notes_interest', 21, 7, -1000000), ('levy', 50, 900, 0)]:
        ev.kinds[c][:, day] = -amount
        if c in ev.incurred:
            ev.incurred[c][:] = incurred
    ev.kinds['inflow'][:, 70] = 2000
    ev.lock[:, 4], ev.lock[:, 8] = 10, -10
    ev.capacity[:, 5], ev.capacity[:, 9] = 20, -20
    ev.cash[:] = sum(ev.kinds.values())
    check_splits(line, 100, ev)


def real_histories(full_horizon=False):
    import akoustis_20240514_fixture as fx

    from app.analysis.build import basis_for
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import Forecaster, path_mask
    from app.finance.bank import load_feed

    setup = fx.setup() if full_horizon else replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=14))
    dispute = fx.pending()
    fc = Forecaster([dispute], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=setup,
                    basis=basis_for(load_feed(fx.SNAP), setup))
    fc.draws = fc.draws.sub(np.isin(np.arange(fc.draws.n), [0, 145, 365]))
    if full_horizon:
        from app.disputes.answer_first import AnswerFirstWalk, Guide

        guide = Guide(fc, lambda key, branches: dict.fromkeys(branches, 1 / len(branches)))
        paths = AnswerFirstWalk(fc, dispute).configured(
            guide=guide, tau=2, groups=6, min_groups=6, group_size=1, enumerate_days=0, seed=731).run()
    else:
        paths = ChronologicalWalk(fc, dispute).run()
    assert len(paths) > 1
    terms = tuple(int(fc.m['parameters'][key]['value']) for key in
                  ('nonpayment_window_days', 'nonpayment_unpaid_share_bps'))
    for path in paths:
        mask = path_mask(path, fc.draws.n)
        mask = np.ones(fc.draws.n, dtype=bool) if mask is None else mask
        draws = fc.draws.sub(mask)
        ev = event_trace(dispute, path, setup, fc.m, draws, fc.sens).events
        yield draws.basis.line, draws.basis.opening, ev, terms


@pytest.mark.parametrize('full_horizon', [False, True])
def test_real_walked_histories(full_horizon):
    for line, opening, ev, terms in real_histories(full_horizon):
        check_splits(line, opening, ev, terms, every_split=True)


def test_branch_event_replacement_and_invalid_day():
    setup = replace(SETUP, cash_processing='daily')
    ops = tiny(1, 1)
    ops.inflow, ops.outflow = np.maximum(ops.total, 0), np.minimum(ops.total, 0)
    line = prepare(setup, ops)
    ev = EventCash.zeros(1, line.days, kinds=True)
    bundle = ls.prepare_draw(line, 100, ev)
    state, tables, cash, outputs, draws, scratch = bundle
    ls.advance(state, tables, cash, 0, 30, outputs, draws, scratch)
    parent = ls.copy_state(state)
    for pet in (-1, 30, 45):
        child = with_petition(ev, pet)
        child.kinds['judgment'][:, 30] = -1000
        child.incurred['judgment'][:] = 30
        child.cash[:] = sum(child.kinds.values())
        child_cash = ls.prepare_draw(line, 100, child)[2]
        ls.restore(state, parent)
        ls.advance(state, tables, child_cash, 30, line.days, outputs, draws, scratch)
        assert_result(bundle, run(line, 100, child), 0, line)
    for a, b in ((0, 1), (line.days, line.days + 1), (line.days, 0)):
        with pytest.raises(ValueError):
            ls.advance(state, tables, cash, a, b, outputs, draws, scratch)

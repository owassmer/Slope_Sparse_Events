"""The exposure ceiling holds path by path, not just in probability-weighted means."""
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from test_line import SETUP, tiny

from app.analysis.core import _petition_at
from app.analysis.engine import prepare, run, with_petition
from app.analysis.events import EventCash, event_trace
from app.analysis.setup import Exposure
from app.analysis.stress_search import exposure_bound


@pytest.mark.parametrize('processing', ['net', 'daily'])
@pytest.mark.parametrize('opening', [Exposure(principal_cents=1, past_due_cents=3),
                                     Exposure(principal_cents=1,
                                              installments=((SETUP.review + timedelta(days=60), 3),))])
def test_rounding_and_opening_ratio(processing, opening):
    # A one-cent rounded fee on a one-cent invoice doubles the contractual ratio.
    setup = replace(SETUP, fee_bps=5000, cash_processing=processing,
                    exposure=opening)
    ops = tiny(1, 3)
    ops.inflow = np.maximum(ops.total, 0)
    ops.outflow = np.minimum(ops.total, 0)
    line = prepare(setup, ops)
    line.limit[:] = 5
    line.routes[:] = 1
    bound = exposure_bound(line)
    assert bound.tolist() == [15, 15, 15]  # opening ratio 3 exceeds even rounded invoice ratio 2
    for day in [0, 1, 30, 90, 179]:
        ev = with_petition(EventCash.zeros(3, line.days), day)
        t = run(line, 100, ev)
        assert np.all(t.stayed + t.uncollected <= bound)
        peak = _petition_at(line, t, ev.petition, day)[0]
        np.testing.assert_array_equal(peak, t.stayed)
    no_opening = prepare(replace(setup, exposure=Exposure()), tiny(1, 3))
    no_opening.limit[:] = 5
    assert exposure_bound(no_opening).tolist() == [10, 10, 10]


def test_every_history_of_walked_tree_is_below_bound(monkeypatch):
    import akoustis_20240514_fixture as fx

    from app.analysis.build import basis_for
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import Forecaster, path_mask
    from app.finance.bank import load_feed

    # A complete short-horizon tree, not a truncated list of leaves or a sample.
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=14))
    d = fx.pending()
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=setup,
                    basis=basis_for(load_feed(fx.SNAP), setup))
    fc.draws = fc.draws.sub(np.isin(np.arange(fc.draws.n), [0, 145, 365]))
    paths = ChronologicalWalk(fc, d).run()
    assert len(paths) > 1
    line = fc.draws.basis.line
    bound = exposure_bound(line)
    from types import SimpleNamespace

    from app.analysis import events
    from app.analysis.stress_search import history_row
    from app.disputes.forecast import pack_mask

    a = SimpleNamespace(ops=line.ops, _draws=fc.draws, days=line.days, setup=setup, m=fc.m,
                        sens=fc.sens, opening=fc.draws.basis.opening)
    model = SimpleNamespace(disputes={d.instance_id: d})
    for path in paths:
        mask = path_mask(path, fc.draws.n)
        mask = np.ones(fc.draws.n, dtype=bool) if mask is None else mask
        draws = fc.draws.sub(mask)
        ev = event_trace(d, path, setup, fc.m, draws, fc.sens).events
        t = run(draws.basis.line, draws.basis.opening, ev)
        assert np.all(t.stayed + t.uncollected <= bound[mask])
        peak = int(np.argmax(t.outstanding.mean(axis=0)))
        assert np.all(_petition_at(draws.basis.line, t, ev.petition, peak)[0] <= bound[mask])

        # A candidate restricted to one feasible draw must not book answers on any others.
        singleton = np.zeros(fc.draws.n, dtype=bool)
        singleton[np.flatnonzero(mask)[0]] = True
        restricted = replace(path, mask=pack_mask(singleton))

        def scoped_trace(d, p, setup, model, draws, sens):
            assert draws.n == 1
            return event_trace(d, p, setup, model, draws, sens)

        with monkeypatch.context() as patch:
            patch.setattr(events, 'event_trace', scoped_trace)
            row, actual = history_row(a, model, (restricted,))
        np.testing.assert_array_equal(actual, singleton)
        assert row['uncollected_maturity_cents'] <= bound[singleton].mean()


def test_grouped_filing_is_named_in_the_history():
    from app.analysis.page import filing_cause, sequence

    steps = (('cash_floor', '1', '@2=file'),)
    assert filing_cause(steps) == 'filed_cash'
    text = sequence(steps, [np.array([5])], np.array([5]), SETUP.review, 180, {}, np.array([3]))
    assert 'files, short of cash' in text
    assert '26 Jun' in text


def test_answer_first_page_contains_conditional_stress():
    from pathlib import Path

    from app.analysis.build import judged_model, run_context, run_page
    from app.analysis.core import Analysis
    from app.analysis.stress_search import search
    from app.analysis.walk_measurement import LEAD_RUN

    ctx = run_context(LEAD_RUN, Path('runs/recorded'))
    fc, model = judged_model(ctx, ctx['setup'], None, [], answer_first=dict(tau=2, groups=2, group_size=1, seed=0))
    a = Analysis(ctx['feed'], ctx['setup'], model, dispute_model=ctx['m'])
    original_nodes = dict(fc.nodes)
    stress = search(a, fc, model, ctx['borrower'], samples=2)
    assert fc.nodes == original_nodes  # search facts must not alter judged questions
    page = run_page(a, model, fc, borrower=ctx['borrower'], snapshot_id=ctx['meta']['snapshot_id'],
                    stress_rows=[], stress_search=stress)['payload']
    winner = page['stress']['worst_found']
    ceiling = page['stress']['bound']
    assert winner['draws'] == len(winner['draw_indices']) > 0
    assert ceiling['winner_draws_mean_cents'] == pytest.approx(
        np.array(ceiling['per_draw_cents'])[winner['draw_indices']].mean())
    assert ceiling['gap_to_found_mean_cents'] >= 0
    assert winner['unrecovered'] <= ceiling['winner_draws_mean_cents'] + 0.5
    assert winner['sequence']
    stopped = search(a, fc, model, ctx['borrower'], samples=2, seconds=0)
    assert stopped['search']['budget_reached']
    assert stopped['search']['sampled_histories'] == 0
    assert stopped['worst_found']['draws'] > 0  # only completed original histories, never a cut-off prefix

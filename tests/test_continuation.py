"""Envelopes must cover other histories, never just the history used to bound them."""
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from test_line import SETUP, tiny

from app.analysis.continuation import continuation_bounds
from app.analysis.engine import prepare, run, with_petition
from app.analysis.events import EventCash, event_trace
from app.analysis.setup import Exposure


def figures(t, ev):
    due = t.due.sum(axis=1)
    unpaid = due - t.collected
    pet = t.petition >= 0
    return dict(funded=t.drawn, due=due, collected=t.collected, unpaid=unpaid,
                past_due=np.where(pet, 0, unpaid), frozen_due=np.where(pet, unpaid, 0),
                not_yet_due=t.not_yet_due, petition_p=pet.astype(float), clawback=t.preference,
                stayed=t.stayed, peak_outstanding=t.outstanding.max(axis=1),
                avg_outstanding=t.outstanding.mean(axis=1),
                **(ev.proceeds or {k: np.zeros(len(pet)) for k in ('atm_proceeds', 'offering_proceeds')}))


def first_difference(a, b):
    n, horizon = a.cash.shape
    diff = np.zeros((n, horizon), dtype=bool)
    for key in ('cash', 'lock', 'capacity'):
        diff |= getattr(a, key) != getattr(b, key)
    for key in set(a.kinds or {}) | set(b.kinds or {}):
        diff |= (a.kinds or {}).get(key, 0) != (b.kinds or {}).get(key, 0)
    for key in set(a.incurred or {}) | set(b.incurred or {}):
        diff[:, 0] |= (a.incurred or {}).get(key, 0) != (b.incurred or {}).get(key, 0)
    dates = np.arange(horizon)
    diff |= (a.petition[:, None] == dates) != (b.petition[:, None] == dates)
    return np.where(diff.any(axis=1), diff.argmax(axis=1), horizon)


def compare(line, ta, ea, tb, eb, day):
    bounds = continuation_bounds(line, ta, ea, day)
    other = continuation_bounds(line, tb, eb, day)
    for key, value in figures(tb, eb).items():
        low, high = bounds[key]
        np.testing.assert_array_equal(low, other[key][0], err_msg=key)
        assert np.all(value >= low - 1e-6), (key, value, low, day)
        if high is None:
            assert other[key][1] is None
        else:
            np.testing.assert_array_equal(high, other[key][1], err_msg=key)
            assert np.all(value <= high + 1e-6), (key, value, high, day)
    return bounds


@pytest.mark.parametrize('processing', ['net', 'daily'])
@pytest.mark.parametrize('collection', ['debit', 'protect_need'])
def test_contractual_prefixes(processing, collection):
    setup = replace(SETUP, fee_bps=5000, cash_processing=processing, collection=collection,
                    exposure=Exposure(principal_cents=1, past_due_cents=3,
                                      installments=((SETUP.review + timedelta(days=60), 3),)))
    ops = tiny(1, 3)
    ops.inflow, ops.outflow = np.maximum(ops.total, 0), np.minimum(ops.total, 0)
    line = prepare(setup, ops)
    line.limit[:] = 5
    line.routes[:] = 1
    histories = []
    for pet in ([-1, -1, -1], [0, 30, 90], [45, 100, 179]):
        ev = with_petition(EventCash.zeros(3, line.days), np.array(pet))
        histories.append((run(line, 100, ev), ev))
    for shift in (0, 45):
        ev = EventCash.zeros(3, line.days, kinds=True)
        ev.cash[:, shift] = -1000
        ev.kinds['judgment'][:, shift] = -1000
        ev.incurred['judgment'][:] = shift
        histories.append((run(line, 100, ev), ev))
    for ta, ea in histories:
        complete = continuation_bounds(line, ta, ea, line.days)
        for key, value in figures(ta, ea).items():
            if complete[key][1] is not None:
                np.testing.assert_allclose(complete[key][0], value, err_msg=key)
                np.testing.assert_allclose(complete[key][1], value, err_msg=key)
        for tb, eb in histories:
            day = first_difference(ea, eb)
            compare(line, ta, ea, tb, eb, day)
            compare(line, ta, ea, tb, eb, np.zeros(3, dtype=int))
    for invalid in (-1, line.days + 1, 0.5):
        with pytest.raises(ValueError):
            continuation_bounds(line, *histories[0], invalid)


def test_every_pair_of_real_walked_histories():
    import akoustis_20240514_fixture as fx

    from app.analysis.build import basis_for
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import Forecaster, path_mask
    from app.finance.bank import load_feed

    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=14))
    dispute = fx.pending()
    fc = Forecaster([dispute], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=setup,
                    basis=basis_for(load_feed(fx.SNAP), setup))
    fc.draws = fc.draws.sub(np.isin(np.arange(fc.draws.n), [0, 145, 365]))
    paths = ChronologicalWalk(fc, dispute).run()
    assert len(paths) > 1
    by_draw = [[] for _ in range(fc.draws.n)]
    for path in paths:
        mask = path_mask(path, fc.draws.n)
        mask = np.ones(fc.draws.n, dtype=bool) if mask is None else mask
        # Only feasible histories, on their own operating draw. No off-mask replay.
        for r in np.flatnonzero(mask):
            draws = fc.draws.sub(np.arange(fc.draws.n) == r)
            ev = event_trace(dispute, path, setup, fc.m, draws, fc.sens).events
            line = draws.basis.line
            by_draw[r].append((line, run(line, draws.basis.opening, ev), ev))
    measurements = []
    pairs = 0
    for histories in by_draw:
        widths = {}
        for line, ta, ea in histories:
            for _, tb, eb in histories:
                pairs += 1
                bounds = compare(line, ta, ea, tb, eb, first_difference(ea, eb))
                for key, (lo, hi) in bounds.items():
                    if hi is not None:
                        widths.setdefault(key, []).append(float((hi - lo)[0]))
        vals = [figures(t, ev) for _, t, ev in histories]
        measurements.append({key: dict(spread=float(np.ptp([v[key][0] for v in vals])),
                                      max_width=max(w), mean_width=float(np.mean(w)))
                             for key, w in widths.items()})
    dest = Path('var/diag/002-5')
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'tightness.json').write_text(json.dumps(dict(paths=len(paths), pairs=pairs,
                                                       per_draw=measurements), indent=2))

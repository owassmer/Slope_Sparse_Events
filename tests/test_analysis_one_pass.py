"""Deferred histograms preserve bins and counts exactly, including masked draws."""
from tempfile import TemporaryFile

import numpy as np
from akoustis_fixture import SETUP, SNAP

from app.analysis.core import Analysis, EventModel, Reduction
from app.analysis.engine import run
from app.analysis.events import EventCash
from app.analysis.setup import DRAWS
from app.finance.bank import load_feed


def test_one_trace_per_history_and_identical_deferred_histograms(monkeypatch):
    original = Analysis.event_cash
    calls = []

    def traced(self, combo):
        calls.append(combo)
        ev = original(self, combo)
        ev.cash[:, 29] += (np.arange(DRAWS) - DRAWS // 2) * 1_000_000
        ev.lock[:, 60] += 20_000_000
        return ev

    monkeypatch.setattr(Analysis, "event_cash", traced)
    a = Analysis(load_feed(SNAP), SETUP, EventModel({}, {}, {}, []))
    assert len(calls) == len(a.model.bank_combos) + len(a.model.combos)
    old_bins = a._bins()
    for k, b in old_bins.items():
        np.testing.assert_array_equal(b.lo, a.r.bins[k].lo)
        np.testing.assert_array_equal(b.width, a.r.bins[k].width)

    ev = EventCash.zeros(DRAWS, a.days)
    ev.cash[:, 29] = -300_000_000
    t = run(a.line, a.opening, ev)
    mask = np.arange(DRAWS) % 2 == 0

    def reduction():
        return Reduction(1, DRAWS, a.days, SETUP, a.line.limit, old_bins, a.month_of_day)

    direct, deferred = reduction(), reduction()
    direct.add(0, t, ev, mask)
    direct.finish()
    with TemporaryFile() as spool:
        deferred._histogram_spool = spool
        deferred.add(0, t, ev, mask)
        deferred.finish_histograms(old_bins)
    deferred.finish()
    for key in direct.counts:
        for attr in ("idx", "cnt", "lens"):
            np.testing.assert_array_equal(getattr(direct.counts[key], attr), getattr(deferred.counts[key], attr))
    assert direct.metrics(np.ones(1)) == deferred.metrics(np.ones(1))
    assert direct.daily(np.ones(1)) == deferred.daily(np.ones(1))

"""Optimized grouping and row reads preserve dated questions and stored identities."""
from types import SimpleNamespace

import numpy as np

from app.analysis.events import BIG
from app.disputes import forecast as f
from app.disputes.parallel import _same
from app.disputes.rules import load_model


def test_context_patterns_match_dated_trace_classification():
    fc = f.Forecaster.__new__(f.Forecaster)
    fc.days = 365
    walk = f._Walk.__new__(f._Walk)
    walk.fc, walk.N, walk.pend, walk.d = fc, 365, True, None
    rng = np.random.default_rng(7)
    conditions = {tuple(n.get('situation', ())) for n in f._q(load_model('akoustis_20240514')).values()}
    for conds in conditions:
        for label in ('award123', 'set_aside', 'no_award'):
            for ctx in ((), ('I4',), ('stay_pending',), ('after_seek',)):
                day = rng.choice([0, 40, 104, 168, 364, 365, BIG], 512)
                row = {'day': day, 'petition': rng.choice([-1, 0, 104, 365], 512),
                       'marks': {c: rng.choice([0, 40, 168, BIG], 512) for c in conds}}
                state = f._S(cls=label)
                actual = walk.contexts(state, ctx, row, conds)
                expected = np.full(512, '', dtype=object)
                live = (day < walk.N) & ((row['petition'] < 0) | (day < row['petition']))
                for i in np.flatnonzero(live):
                    selected = np.arange(512) == i
                    tr = SimpleNamespace(day=[np.where(selected, day, BIG)],
                                         marks=row['marks'], petition=row['petition'])
                    expected[i] = '|'.join(walk._tags(state, conds, ctx, tr, ()))
                np.testing.assert_array_equal(actual, expected)


def test_saved_row_decode_and_late_identity_unchanged(monkeypatch):
    import zlib

    row = {'day': np.array([1, BIG, 2]), 'petition': np.array([-1, -1, -1]),
           'cash': np.array([40, 0, 50]), 'owed': np.array([20, 0, 20]), 'marks': {}}
    blob = f.pack_row(row)
    eager, lazy = f.unpack_row(blob), f.lazy_row(blob)
    monkeypatch.setattr(f.zlib_ng, 'decompress', zlib.decompress)
    assert _same(eager, f.unpack_row(blob))
    assert _same(dict(lazy), dict(f.lazy_row(blob)))
    assert f.pack_row(row) == blob
    assert f.Forecaster.late_key('k', (), row) == f.Forecaster.late_key('k', (), row, blob=blob)

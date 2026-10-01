"""Subdivision preserves conditional paths and the exact financial facts sent to Jev."""
import pickle
import sys
from dataclasses import replace
from datetime import timedelta

import akoustis_20240514_fixture as fx
import pytest

from app.analysis.build import basis_for
from app.disputes import parallel
from app.disputes.forecast import Forecaster
from app.finance.bank import load_feed


def test_refined_walk_preserves_paths_questions_and_financial_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, 'CUT', 2)
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=50))
    basis = basis_for(load_feed(fx.SNAP), setup)

    def context():
        dispute = fx.pending().model_copy(update={'status': 'interpreted'})
        return Forecaster([dispute], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                          hydrate=lambda f: {}, model=fx.model(), setup=setup, basis=basis), dispute

    whole, refined = tmp_path / 'whole', tmp_path / 'refined'
    whole.mkdir()
    refined.mkdir()
    fc, d = context()
    parallel._fork(fc, d, 1, str(whole), sys.stderr)
    originals = parallel.load_parts(str(whole))
    roots = tmp_path / 'roots.pkl'
    roots.write_bytes(pickle.dumps(originals[0]['done']))
    monkeypatch.setenv('SLOPE_WALK_ROOTS', str(roots))
    for i in range(4):
        monkeypatch.setenv('SLOPE_WALK_REFINE', f'{i}/4/2')
        fc, d = context()
        parallel._fork(fc, d, 1, str(refined), sys.stderr, ks=[100 + i])
    monkeypatch.delenv('SLOPE_WALK_REFINE')
    monkeypatch.delenv('SLOPE_WALK_ROOTS')
    parts = parallel.load_parts(str(refined))
    assert not parallel.missing_segments(parts)
    assert parallel.missing_segments(parts[1:])  # the shared prefix cannot be omitted
    with pytest.raises(RuntimeError, match='duplicate subdivision'):
        parallel.missing_segments(parts + parts[:1])
    with pytest.raises(RuntimeError, match='both whole and subdivided'):
        parallel.missing_segments(originals + parts)
    results = []
    for folder in (whole, refined):
        monkeypatch.setenv('SLOPE_WALK_PARTS', str(folder))
        fc, d = context()
        paths = parallel.walk(fc, d, 1)
        results.append((paths, fc.nodes, {k: list(v) for k, v in fc.facts.items()}))
    assert len(results[0][0]) > 1000  # includes grouped paths and watched questions
    assert parallel._same(*results)

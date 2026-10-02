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
        parallel._fork(fc, d, 1, str(refined), sys.stderr, ks=[i])
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

    # The global pool preserves the same order, watch edges and equivalence groups across worker counts.
    from app.disputes import pool
    split = tmp_path / 'split'
    pool.split(str(refined), str(split))
    controls, pooled_paths = [], []
    for workers in (1, 2):
        monkeypatch.setenv('SLOPE_POOL_PROCESSES', str(workers))
        out = tmp_path / f'pool{workers}'
        pool.control(str(split / 'control'), str(out))
        controls.append(pickle.loads((out / 'control.pkl').read_bytes()))
        pooled_paths.append({f.name: pool.read_paths(str(f)) for f in (out / 'paths').glob('*.pkl')})
    assert parallel._same(controls[0], controls[1])
    assert parallel._same(pooled_paths[0], pooled_paths[1])


def test_history_prefix_selection_preserves_the_selected_subtrees(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, 'CUT', 2)
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=50))
    basis = basis_for(load_feed(fx.SNAP), setup)

    def context():
        d = fx.pending().model_copy(update={'status': 'interpreted'})
        fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                        hydrate=lambda f: {}, model=fx.model(), setup=setup, basis=basis)
        return fc, d

    whole, selected = tmp_path / 'all', tmp_path / 'selected'
    whole.mkdir()
    selected.mkdir()
    fc, d = context()
    parallel._fork(fc, d, 1, str(whole), sys.stderr)
    all_part = parallel.load_parts(str(whole))[0]
    paths = [value for _, kind, value, _ in all_part['events'] if kind == 'path']
    prefix = next(p.steps[:2] for p, _, _ in paths if len(p.steps) > 2 and p.steps[1][0] == 'verdict')
    selector = tmp_path / 'prefixes.pkl'
    selector.write_bytes(pickle.dumps([prefix]))
    monkeypatch.setenv('SLOPE_WALK_PREFIXES', str(selector))
    fc, d = context()
    parallel._fork(fc, d, 1, str(selected), sys.stderr)
    part = parallel.load_parts(str(selected))[0]
    actual = [value for _, kind, value, _ in part['events'] if kind == 'path']
    expected = [value for value in paths if value[0].steps[:len(prefix)] == prefix]
    assert expected and len(expected) < len(paths)
    assert parallel._same(actual, expected)
    assert part['segs'] == all_part['segs']
    assert 0 < len(part['done']) < len(all_part['done'])


def test_finer_tail_matches_original_partition(tmp_path, monkeypatch):
    from collections import Counter

    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tools'))
    from tail_fanout import collapse

    monkeypatch.setattr(parallel, 'CUT', 2)
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=50))
    basis = basis_for(load_feed(fx.SNAP), setup)

    def context():
        d = fx.pending().model_copy(update={'status': 'interpreted'})
        return Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                          hydrate=lambda f: {}, model=fx.model(), setup=setup, basis=basis), d

    whole = tmp_path / 'whole'
    whole.mkdir()
    fc, d = context()
    parallel._fork(fc, d, 1, str(whole), sys.stderr)
    part = parallel.load_parts(str(whole))[0]
    counts = Counter(e[:3] for e, kind, _, _ in part['events'] if kind == 'path' and e[1] == 0)
    root = counts.most_common(1)[0][0]
    selector = tmp_path / 'roots.pkl'
    selector.write_bytes(pickle.dumps([root]))
    monkeypatch.setenv('SLOPE_WALK_ROOTS', str(selector))
    for original_index in (0, 2):
        original = tmp_path / f'original{original_index}'
        finer = tmp_path / f'finer{original_index}'
        original.mkdir()
        finer.mkdir()
        monkeypatch.setenv('SLOPE_WALK_REFINE', f'{original_index}/4/2')
        fc, d = context()
        parallel._fork(fc, d, 1, str(original), sys.stderr, ks=[500])
        for j in range(8):
            monkeypatch.setenv('SLOPE_WALK_REFINE', f'{original_index + 4*j}/32/2')
            fc, d = context()
            parallel._fork(fc, d, 1, str(finer), sys.stderr, ks=[600 + j])
        a, b = parallel.load_parts(str(original)), parallel.load_parts(str(finer))
        collapse(b, {'roots': [root], 'partitions': 4, 'depth': 2}, original_index, 8)
        assert parallel._same(parallel._live(a), parallel._live(b))
        for field in parallel.FIELDS:
            assert parallel._same(parallel._union(a, field), parallel._union(b, field))
        assert a[0]['subdivisions'] == b[0]['subdivisions']


def test_nested_refinement_materializes_original_paths_and_facts(tmp_path, monkeypatch):
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    from nested_tail import combine

    from app.disputes import pool

    monkeypatch.setattr(parallel, 'CUT', 2)
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=50))
    basis = basis_for(load_feed(fx.SNAP), setup)

    def context():
        d = fx.pending().model_copy(update={'status': 'interpreted'})
        return Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=setup.horizon,
                          hydrate=lambda f: {}, model=fx.model(), setup=setup, basis=basis), d

    whole, nested, joined = (tmp_path / name for name in ('whole', 'nested', 'joined'))
    for folder in (whole, nested, joined):
        folder.mkdir()
    fc, d = context()
    parallel._fork(fc, d, 1, str(whole), sys.stderr)
    original = parallel.load_parts(str(whole))[0]
    roots = tmp_path / 'roots.pkl'
    roots.write_bytes(pickle.dumps(original['done']))
    monkeypatch.setenv('SLOPE_WALK_ROOTS', str(roots))
    monkeypatch.setenv('SLOPE_WALK_REFINE', '0/1/2')
    for i in range(4):
        monkeypatch.setenv('SLOPE_WALK_NESTED_REFINE', f'{i}/4/1')
        fc, d = context()
        parallel._fork(fc, d, 1, str(nested), sys.stderr, ks=[600 + i])
    parts = parallel.load_parts(str(nested))
    with pytest.raises(ValueError, match='coverage'):
        combine(parts[:-1], 0)
    combined = combine(parts, 0)
    (joined / 'part0.pkl').write_bytes(pickle.dumps(combined))
    assert not parallel.missing_segments([combined])
    results = []
    for folder in (whole, joined):
        monkeypatch.setenv('SLOPE_WALK_PARTS', str(folder))
        fc, d = context()
        paths = parallel.walk(fc, d, 1)
        results.append((paths, fc.nodes, {k: list(v) for k, v in fc.facts.items()}))
    assert len(results[0][0]) > 1000
    assert parallel._same(*results)

    # The production pool must also apply newly split watch edges in the same order.
    outputs = []
    for folder in (whole, joined):
        split, out = folder / 'split', folder / 'pool'
        pool.split(str(folder), str(split))
        pool.control(str(split / 'control'), str(out))
        outputs.append(pool.read_paths(str(out / 'paths/part0.pkl')))
    assert parallel._same(*outputs)

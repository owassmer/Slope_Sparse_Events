"""Packaging may move path ranges, but must neither omit nor reorder financial histories."""
import pickle

from app.analysis.reduce import _blocks
from app.disputes.pool import read_paths, write_paths
from tools.reduce_fleet import PROCS, pack_job


def test_pack_disjoint_ranges_preserves_global_block_membership(tmp_path):
    source = tmp_path / 'ctl'
    (source / 'paths').mkdir(parents=True)
    ctl = {'part_cost': {'part0.pkl': (23, 50), 'part1.pkl': (19, 2)}}
    write_paths(str(source / 'paths/part0.pkl'), list(range(23)))
    write_paths(str(source / 'paths/part1.pkl'), list(range(23, 42)))
    blocks = _blocks(ctl['part_cost'], 3 * PROCS)
    all_paths = []
    for j in range(3):
        target = tmp_path / f'job{j}'
        pack_job(source, target, ctl, blocks, j)
        ranges = pickle.loads((target / 'blocks.pkl').read_bytes())
        for k, parts in ranges.items():
            actual = [p for f, lo, hi in parts for p in read_paths(str(target / 'paths' / f), lo, hi)]
            expected = [p for f, lo, hi in blocks[k] for p in read_paths(str(source / 'paths' / f), lo, hi)]
            assert actual == expected
            all_paths.extend(actual)
    assert all_paths == list(range(42))


def test_completion_requires_all_exact_blocks_with_unchanged_contents(tmp_path):
    from app.analysis.pooled import digest, save
    from tools.reduce_fleet import complete, expected

    for name in expected(0):
        (tmp_path / name).write_bytes(b'completed table')
    save(tmp_path / 'complete.json', {name: digest(tmp_path / name) for name in expected(0)})
    assert complete(tmp_path, 0)
    (tmp_path / 'tab0.pkl').write_bytes(b'truncated')
    assert not complete(tmp_path, 0)
    (tmp_path / 'tab0.pkl').unlink()
    assert not complete(tmp_path, 0)

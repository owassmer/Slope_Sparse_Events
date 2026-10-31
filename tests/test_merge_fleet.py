"""Distributed grouping must reproduce global grouping and preserve path conditioning."""
import pickle

from app.disputes import pool
from app.disputes.forecast import DisputePath
from tools import merge_fleet


def test_distributed_merge_matches_original_with_cross_part_classes_and_empty_home(tmp_path, monkeypatch):
    monkeypatch.delenv('SLOPE_MERGE_FLEET', raising=False)
    monkeypatch.setenv('SLOPE_POOL_PROCESSES', '1')
    def path(answer, cls):
        return DisputePath('d', (('q', 'dated', answer),), 'cash-outcome', (('q', answer),),
                           classes=(('status', (cls,), None),))
    files = {'part0.pkl': [path('b', 'same'), path('c', 'different')],
             'part1.pkl': [path('a', 'same')]}
    meta = [((3, 0), 'cash', 'part0.pkl', 0), ((4, 0), 'cash', 'part0.pkl', 1),
            ((1, 0), 'cash', 'part1.pkl', 0)]
    branches = {'q': ('a', 'b', 'c')}
    for name in ('reference', 'distributed'):
        root = tmp_path / name
        (root / 'walked').mkdir(parents=True)
        (root / 'paths').mkdir()
        for f, paths in files.items():
            (root / 'walked' / f).write_bytes(pickle.dumps(paths))
    reference, distributed = tmp_path / 'reference', tmp_path / 'distributed'
    counts = pool._merge(str(reference), list(meta), branches)
    spec = merge_fleet.prepare(distributed, list(meta), branches)
    for home in spec['homes']:
        merge_fleet.build_home(distributed / 'merge-fleet' / (home + '.gz'),
                               distributed / 'merge-fleet/branches.pkl.gz', distributed / 'result')
    actual = {}
    merge_fleet.adopt(distributed / 'result', distributed, spec['homes'], actual)
    assert actual == counts == {'part0.pkl': 0, 'part1.pkl': 2}
    for home in spec['homes']:
        assert pool.read_paths(str(distributed / 'paths' / home)) == pool.read_paths(str(reference / 'paths' / home))

import json
import pickle

from app.analysis.reduce import _blocks
from app.disputes.pool import read_paths, write_paths
from tools.merge_fleet import archive
from tools.path_archives import catalog, materialize
from tools.reduce_fleet import PROCS, pack_remote


def test_archived_ranges_match_original_block_order(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    ctl = {'part_cost':{'part0.pkl':(23,50),'part1.pkl':(19,2)}}
    write_paths(str(source/'part0.pkl'), list(range(23)))
    write_paths(str(source/'part1.pkl'), list(range(23,42)))
    packed = tmp_path/'saved.tgz'
    archive(source, packed, [p.name for p in source.iterdir()])
    homes = list(ctl['part_cost'])
    parts = catalog(packed, homes, 'original.tgz')
    assert [parts[f]['count'] for f in homes] == [23,19]
    blocks = _blocks(ctl['part_cost'], 3*PROCS)
    recovered = []
    for j in range(3):
        target = tmp_path/f'job{j}'
        pack_remote(target, ctl, blocks, j, {'parts':parts}, lambda key:packed.as_uri())
        materialize(json.loads((target/'remote.json').read_text()), target/'paths')
        ranges = pickle.loads((target/'blocks.pkl').read_bytes())
        for k, values in ranges.items():
            actual = [p for f, lo, hi in values for p in read_paths(str(target/'paths'/f),lo,hi)]
            expected = [p for f,lo,hi in blocks[k] for p in read_paths(str(source/f),lo,hi)]
            assert actual == expected
            recovered.extend(actual)
    assert recovered == list(range(42))

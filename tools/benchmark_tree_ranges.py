"""Compare whole-piece and split reductions on a downloaded real piece; no model calls."""
import argparse
import gzip
import json
import pickle
import sqlite3
import time
import zlib
from pathlib import Path

import numpy as np

from app.analysis.tree_parts import combine, finalize, summarize
from app.analysis.tree_pool import connect, pool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run')
    parser.add_argument('--artifact')
    parser.add_argument('--input-database', type=Path, help='Previously ingested real input, offline.')
    parser.add_argument('--max-histories', type=int, default=128,
                        help='Canonical bounded comparison (default two blocks).')
    parser.add_argument('--piece', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    database = args.input_database or root / 'input.sqlite'
    if not args.input_database:
        if not args.run or not args.artifact:
            parser.error('provide --input-database or both --run and --artifact')
        result = pool(database=database, output=root / 'input', runs=[args.run], artifacts=[args.artifact],
                      finish=False, workers=args.workers)
        if result['status'] != 'ingested':
            raise RuntimeError(result)
    if args.max_histories < 128 or args.max_histories % 64:
        parser.error('--max-histories must be a multiple of 64, at least 128')
    with connect(database) as db:
        rows = [(json.loads(meta), source) for meta, source in db.execute('SELECT meta,source FROM pieces')
                if Path(source).name == args.piece]
        if len(rows) != 1:
            raise ValueError('expected exactly one matching piece')
        meta, _ = rows[0]
        count = min(meta['histories'], args.max_histories)
        middle = (count // 2 // 64) * 64
        if not middle:
            raise ValueError('choose a piece with at least 128 histories')
        timings = {}
        for name, lo, hi, workers in [('whole', 0, count, 1), ('a', 0, middle, args.workers),
                                     ('b', middle, count, args.workers)]:
            t = time.monotonic()
            result = summarize(db, root / name / 'partial.sqlite', gates_only=True, workers=workers,
                               history_ranges=[f'{args.piece}:{lo}:{hi}'])
            if result['status'] != 'partial_ready' or not result['selection_complete']:
                raise RuntimeError(result)
            timings[name] = time.monotonic() - t
            print(json.dumps({'stage': name, 'seconds': timings[name]}), flush=True)
    if count < meta['histories']:
        combine(database=root / 'combined.sqlite', output=root / 'combined',
                local=[root / 'b', root / 'a'], merge_only=True)
        # Compare all reduced content without pretending the bounded selection is
        # the whole piece. Final coverage checks remain unchanged.
        with sqlite3.connect(root / 'whole/partial.sqlite') as a, sqlite3.connect(
                root / 'combined/partial.sqlite') as b:
            for table in ('pieces', 'ranges', 'nodes', 'questions', 'samples'):
                assert sorted(a.execute(f'SELECT * FROM {table}')) == sorted(
                    b.execute(f'SELECT * FROM {table}')), table
            ma = list(a.execute('SELECT piece,state FROM metrics ORDER BY piece'))
            mb = list(b.execute('SELECT piece,state FROM metrics ORDER BY piece'))
            assert [k for k, _ in ma] == [k for k, _ in mb]
            replay_errors = []
            for (_, x), (_, y) in zip(ma, mb, strict=True):
                state = pickle.loads(zlib.decompress(x))
                np.testing.assert_equal(state, pickle.loads(zlib.decompress(y)))
                replay_errors.extend(v for name in ('no_future', 'no_skip')
                                     for e in state[5][name]['examples'] for check in e['checks']
                                     for v in check['violations'] if v['kind'] == 'replay_error')
            query = 'SELECT piece,identity,mask,blob FROM paths ORDER BY piece,identity,mask'
            assert list(a.execute(query)) == list(b.execute(query)), 'paths'
        report = {'piece': args.piece, 'range': [0, count], 'exact_reduced_content': True,
                  'timings': timings, 'seconds': time.monotonic() - started,
                  'replay_errors': replay_errors}
        (root / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)
        if replay_errors:
            raise RuntimeError('comparison encountered replay errors; inspect comparison.json')
        return
    finalize(root / 'whole/partial.sqlite', root / 'whole')
    combine(database=root / 'combined.sqlite', output=root / 'combined', local=[root / 'b', root / 'a'])
    whole = json.loads((root / 'whole/gates.json').read_text())
    split = json.loads((root / 'combined/gates.json').read_text())
    assert whole == split, 'gate or mass difference'
    for name in ('histories.jsonl.gz', 'catalog.jsonl.gz'):
        assert gzip.decompress((root / 'whole' / name).read_bytes()) == gzip.decompress(
            (root / 'combined' / name).read_bytes()), name
    replay_errors = [v for name in ('no_future', 'no_skip') for e in whole[name]['examples']
                     for check in e['checks'] for v in check['violations'] if v['kind'] == 'replay_error']
    report = {'piece': args.piece, 'histories': count, 'history_draws': meta['history_draws'],
              'exact_gates_masses_sample_catalog': True, 'timings': timings,
              'seconds': time.monotonic() - started, 'replay_errors': replay_errors}
    (root / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    if replay_errors:
        raise RuntimeError('comparison encountered replay errors; inspect comparison.json')


if __name__ == '__main__':
    main()

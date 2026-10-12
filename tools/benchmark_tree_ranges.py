"""Compare whole-piece and split reductions on a downloaded real piece; no model calls."""
import argparse
import gzip
import json
import time
from pathlib import Path

from app.analysis.tree_parts import combine, finalize, summarize
from app.analysis.tree_pool import connect, pool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--artifact', required=True)
    parser.add_argument('--piece', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    database = root / 'input.sqlite'
    result = pool(database=database, output=root / 'input', runs=[args.run], artifacts=[args.artifact],
                  finish=False, workers=args.workers)
    if result['status'] != 'ingested':
        raise RuntimeError(result)
    with connect(database) as db:
        rows = [(json.loads(meta), source) for meta, source in db.execute('SELECT meta,source FROM pieces')
                if Path(source).name == args.piece]
        if len(rows) != 1:
            raise ValueError('expected exactly one matching piece')
        meta, _ = rows[0]
        count = meta['histories']
        middle = (count // 2 // 64) * 64
        if not middle:
            raise ValueError('choose a piece with at least 128 histories')
        timings = {}
        for name, lo, hi, workers in [('whole', 0, count, 1), ('a', 0, middle, args.workers),
                                     ('b', middle, count, args.workers)]:
            t = time.monotonic()
            result = summarize(db, root / name / 'partial.sqlite', gates_only=True, workers=workers,
                               history_ranges=[f'{args.piece}:{lo}:{hi}'])
            if result['status'] != 'partial_ready':
                raise RuntimeError(result)
            timings[name] = time.monotonic() - t
            print(json.dumps({'stage': name, 'seconds': timings[name]}), flush=True)
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

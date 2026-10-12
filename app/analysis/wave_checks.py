"""Wave-local independent replay, bound to exact canonical input ranges."""
import hashlib
import json
import pickle
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

from app.analysis import tree_history_checks


def fingerprint(db, pid, lower, upper):
    digest = hashlib.sha256()
    # Ingestion's piece ID hashes the entire stream (including all facts and
    # node domains). Range row identities guard against a changed scratch DB.
    digest.update(pid.encode())
    for query, args in (
        ('SELECT identity,mask,blob FROM paths WHERE piece=? AND id>? AND id<=? ORDER BY id',
         (pid, lower, upper)),
    ):
        for row in db.execute(query, args):
            for value in row:
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                digest.update(len(data).to_bytes(8, 'big'))
                digest.update(data)
    return digest.hexdigest()


def check_block(db, seeds, lower, upper):
    from app.analysis.tree_parts import zero

    nodes = {k: pickle.loads(b) for k, b in db.execute('SELECT * FROM nodes')}
    checker = tree_history_checks.HistoryChecks(db, nodes)
    histories = [(pid, pickle.loads(zlib.decompress(blob))) for pid, blob in db.execute(
        'SELECT piece,blob FROM paths WHERE id>? AND id<=? ORDER BY id', (lower, upper))]
    checker.batch(histories)
    state = zero(seeds)
    return (*state[:5], checker.state, len(histories))


def load_parts(paths, *, selection=None):
    return dict(iter_parts(paths, selection=selection))


def iter_parts(paths, *, selection=None):
    from app.analysis.tree_parts import config

    seen = set()
    for path in paths:
        with closing(sqlite3.connect(path)) as db:
            cfg = config(db)
            if cfg.get('kind') != 'wave-checks-v1' or not cfg.get('complete'):
                raise ValueError(f'invalid check part: {path}')
            counts = {pid: json.loads(meta)['histories'] for pid, meta in db.execute('SELECT id,meta FROM pieces')}
            for pid, lo, hi in db.execute('SELECT * FROM ranges'):
                count = counts[pid]
                if lo % 64 or hi != min(lo + 64, count) or not 0 <= lo <= hi <= count:
                    raise ValueError(f'noncanonical checked range: {pid}:{lo}:{hi}')
                key = pid, lo, hi
                if key in seen:
                    raise ValueError(f'duplicate checked range: {key}')
                seen.add(key)
                if selection is not None and key not in selection:
                    continue
                blob = db.execute('SELECT state FROM metrics WHERE piece=?', (f'{pid}:{lo:012d}',)).fetchone()
                state = pickle.loads(zlib.decompress(blob[0]))
                if state[6] != hi - lo or any(r['histories'] != hi - lo for r in state[5].values()):
                    raise ValueError(f'incomplete checked range: {key}')
                yield key, (cfg[f'fingerprint:{pid}:{lo}'], state[5])


def report(path, *, require_complete=False):
    ranges = set()
    totals = tree_history_checks.empty()
    for key, (_, checks) in iter_parts([path]):
        ranges.add(key)
        tree_history_checks.merge(totals, checks)
    with closing(sqlite3.connect(path)) as db:
        expected = {(pid, lo, min(lo + 64, count))
                    for pid, meta in db.execute('SELECT id,meta FROM pieces')
                    for count in [json.loads(meta)['histories']]
                    for lo in (range(0, count, 64) if count else [0])}
    complete = ranges == expected
    if require_complete and not complete:
        raise ValueError('missing checked ranges in wave')
    result = {'ranges': len(ranges), 'coverage_complete': complete,
              'pass': complete and all(not r['failed_histories'] for r in totals.values()), **totals}
    Path(path).with_name('checks.json').write_text(json.dumps(result, indent=2) + '\n')
    return result

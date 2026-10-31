"""Mergeable tree reductions. Trusted pickle inputs, no model calls.

A node-union pass precedes reduction: inactive class substitution depends on the
whole question universe. Each reduced range retains its piece's input cover,
terminal identity/support and canonical block arithmetic, but no cash arrays or
history references. Final combine requires every block of each piece exactly once.
Gate-only parts retain facts in the review sample, not the population catalog's
expanded facts variants: all offered-answer/date checks already ran on every
history before reduction. Original run/artifact/local locators remain in
pieces for the later cash pass. Hash-priority sampling is uniform over histories,
independent of part sizes, scheduling and traversal order.
"""
from __future__ import annotations

import gzip
import heapq
import json
import pickle
import random
import sqlite3
import subprocess
import tempfile
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing, nullcontext
from dataclasses import asdict
from pathlib import Path

import numpy as np

from app.analysis import tree_history_checks as history_checks
from app.analysis import tree_pool as tree
from app.disputes.forecast import atoms, class_firsts, expand_classes, path_mask, unpack_row


def connect(path):
    db = tree.connect(Path(path))
    db.executescript('''
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS metrics (piece TEXT PRIMARY KEY, state BLOB);
        CREATE TABLE IF NOT EXISTS samples (priority TEXT PRIMARY KEY, record TEXT);
        CREATE TABLE IF NOT EXISTS ranges (piece TEXT, lower INTEGER, upper INTEGER,
                                           PRIMARY KEY(piece, lower));
    ''')
    return db


def put_config(db, key, value):
    db.execute('INSERT OR REPLACE INTO config VALUES (?,?)', (key, tree.encode(value)))


def config(db):
    return {k: json.loads(v) for k, v in db.execute('SELECT * FROM config')}


def copy_nodes(source, dest):
    for key, blob in source.execute('SELECT * FROM nodes ORDER BY key'):
        old = dest.execute('SELECT blob FROM nodes WHERE key=?', (key,)).fetchone()
        if old and pickle.loads(old[0]) != pickle.loads(blob):
            raise ValueError(f'question domain changed: {key}')
        dest.execute('INSERT OR IGNORE INTO nodes VALUES (?,?)', (key, blob))


def node_signature(db):
    # Hash semantics, not pickle memo layout.
    from dataclasses import asdict
    return tree.digest([(k, asdict(pickle.loads(b))) for k, b in db.execute('SELECT * FROM nodes ORDER BY key')])


def save_nodes(db, target):
    with closing(connect(target)) as out, out:
        copy_nodes(db, out)
        put_config(out, 'kind', 'nodes-v1')
        put_config(out, 'complete', True)
    return {'status': 'nodes_ready', 'file': str(target)}


def zero(seeds):
    return np.zeros((len(seeds), 512)), {}, {}, [], 0, history_checks.empty(), 0


def add(a, b):
    mass, counts, supports, errors, failures, checks, histories = a
    m, c, s, e, n, other_checks, h = b
    mass += m
    for key, values in c.items():
        old = counts.setdefault(key, [0, 0])
        old[0] += values[0]
        old[1] += values[1]
        supports[key] = supports.get(key, np.zeros(512, bool)) | s[key]
    errors.extend(e[:max(0, 50 - len(errors))])
    return mass, counts, supports, errors, failures + n, history_checks.merge(checks, other_checks), histories + h


def sample_record(db, pid, p, nodes):
    on = path_mask(p, 512)
    rows = np.arange(512) if on is None else np.flatnonzero(on)
    draw = int(random.Random(tree.digest((pid, p.steps))).choice(rows))
    source = db.execute('SELECT source FROM pieces WHERE id=?', (pid,)).fetchone()[0]
    record = {'piece': pid, 'source': source, 'steps': p.steps, 'outcome': p.outcome,
              'draws': rows.tolist(), 'sample_draw': draw, 'edges': p.edges, 'questions': []}
    keys = {k for q in expand_classes([p], nodes, 512, first=class_firsts(nodes))
            for e, _ in q.edges for k in atoms(e)}
    for key in sorted(keys):
        for qid, prefix, history in db.execute(
                'SELECT DISTINCT hash,prefix,history FROM refs WHERE piece=? AND key=? ORDER BY hash,prefix,history',
                (pid, key)):
            pre, hist = json.loads(prefix) or [], json.loads(history)
            steps = [list(s) for s in p.steps]
            match = pre if hist is not None else pre[:-1]
            if (hist is not None and hist != steps) or steps[:len(match)] != match:
                continue
            blob = db.execute('SELECT blob FROM questions WHERE key=? AND hash=?', (key, qid)).fetchone()[0]
            row = unpack_row(blob)
            day = row.get('day')
            if isinstance(day, np.ndarray) and day[draw] >= 1_000_000:
                continue
            record['questions'].append({'key': key, 'prefix': pre, 'facts_id': qid,
                                        'facts': tree.draw_facts(row, draw),
                                        'offered_answers': nodes[key].branches})
    return tree.encode(record)


def trim_samples(db, size):
    db.execute('DELETE FROM samples WHERE priority NOT IN '
               '(SELECT priority FROM samples ORDER BY priority LIMIT ?)', (size,))


def summarize(db, target, *, seeds=(17, 91, 307), sample_size=32, seconds=900,
              workers=1, node_catalog=None, gates_only=False, history_ranges=(),
              checks_only=False, check_parts=()):
    """Reduce selected half-open ordinal ranges, checkpointing canonical blocks.

    Node catalogs must be frozen before evaluation. Full coverage is required at
    final combine, not at intermediate merges (which may contain disjoint ranges).
    """
    start = time.monotonic()
    if node_catalog:
        with closing(sqlite3.connect(node_catalog)) as catalog, db:
            cfg = config(catalog)
            if cfg.get('kind') != 'nodes-v1' or not cfg.get('complete'):
                raise ValueError('incomplete node catalog')
            original = {k: b for k, b in db.execute('SELECT * FROM nodes')}
            for key, blob in original.items():
                row = catalog.execute('SELECT blob FROM nodes WHERE key=?', (key,)).fetchone()
                if not row or pickle.loads(row[0]) != pickle.loads(blob):
                    raise ValueError(f'question domain absent or changed in node catalog: {key}')
            copy_nodes(catalog, db)
    signature = node_signature(db)
    settings = {'kind': 'wave-checks-v1' if checks_only else 'tree-part-v4',
                'external_checks': bool(check_parts), 'nodes': signature, 'seeds': list(seeds),
                'sample_size': sample_size, 'gates_only': gates_only,
                'selection': sorted(history_ranges)}
    target = Path(target)
    database = db.execute('PRAGMA database_list').fetchone()[2]
    nodes = {k: pickle.loads(b) for k, b in db.execute('SELECT * FROM nodes')}
    with closing(connect(target)) as out:
        old = config(out)
        if old and any(old.get(k, False if k == 'external_checks' else None) != v
                       for k, v in settings.items()):
            raise ValueError('partial settings changed; use a new output directory')
        with out:
            for key, value in settings.items():
                put_config(out, key, value)
            put_config(out, 'complete', False)
            copy_nodes(db, out)
        # Canonical 64-history blocks are the arithmetic and coverage unit. Their
        # order never depends on line boundaries, process scheduling or merge tiers.
        selections = {}
        for value in history_ranges:
            name, lo, hi = value.rsplit(':', 2)
            selections.setdefault(name, []).append((int(lo), int(hi)))
        matched = set()
        jobs = []
        for pid, meta, source in db.execute('SELECT * FROM pieces ORDER BY id'):
            name = Path(source).name
            if selections and name not in selections:
                continue
            matched.add(name)
            lower, maximum, count = db.execute(
                'SELECT MIN(id),MAX(id),COUNT(*) FROM paths WHERE piece=?', (pid,)).fetchone()
            base = lower - 1 if count else 0
            if count and maximum - base != count:
                raise ValueError('noncontiguous piece rows')
            selected = selections.get(name, [(0, count)])
            seen = set()
            for lo, hi in selected:
                if not (0 <= lo <= hi <= count) or lo % 64 or (hi != count and hi % 64) or (lo == hi and count):
                    raise ValueError('history range must use canonical 64-history boundaries')
                for offset in range(lo, hi, 64) if count else [0]:
                    if offset in seen:
                        raise ValueError('duplicate history range')
                    seen.add(offset)
                    upper = min(offset + 64, count)
                    if not out.execute('SELECT 1 FROM ranges WHERE piece=? AND lower=?', (pid, offset)).fetchone():
                        jobs.append((pid, meta, source, base, offset, upper))
        if selections.keys() - matched:
            raise ValueError(f'unknown piece ranges: {sorted(selections.keys() - matched)}')
        # Each committed block is mergeable; retain untouched pieces for coverage checks.
        with out:
            put_config(out, 'complete', True)
            for pid, meta, source, *_ in jobs:
                out.execute('INSERT OR IGNORE INTO pieces VALUES (?,?,?)', (pid, meta, source))
            put_config(out, 'remaining_ranges', [f'{Path(source).name}:{lo}:{hi}'
                       for _, _, source, _, lo, hi in jobs])
        from app.analysis.wave_checks import load_parts
        saved_checks = load_parts(check_parts, selection={(pid, lo, hi) for pid, _, _, _, lo, hi in jobs}) if check_parts else None
        deadline = start + seconds
        with ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext() as executor:
            tasks = [(database, seeds, base + lo, base + hi,
                      'only' if checks_only else (False if saved_checks is not None else True))
                     for _, _, _, base, lo, hi in jobs]
            results = (bounded_results(executor, tasks, workers, deadline) if executor else
                       serial_results(db, tasks, deadline))
            for index, (state, block_seconds) in results:
                pid, meta, source, base, lo, hi = jobs[index]
                from app.analysis.wave_checks import fingerprint
                stamp = fingerprint(db, pid, base + lo, base + hi) if checks_only or saved_checks is not None else None
                if saved_checks is not None:
                    key = (pid, lo, hi)
                    if key not in saved_checks or saved_checks[key][0] != stamp:
                        raise ValueError(f'missing or changed checked range: {key}')
                    checked = saved_checks[key][1]
                    native_draws = sum(int.from_bytes(mask).bit_count() for (mask,) in db.execute(
                        'SELECT mask FROM paths WHERE piece=? AND id>? AND id<=?', (pid, base + lo, base + hi)))
                    if set(checked) != {'no_future', 'no_skip'} or any(
                            row['histories'] != hi - lo or row['history_draws'] != native_draws
                            for row in checked.values()):
                        raise ValueError(f'incomplete history-draw checks: {key}')
                    state = (*state[:5], checked, state[-1])
                with out:
                    out.execute('INSERT OR IGNORE INTO pieces VALUES (?,?,?)', (pid, meta, source))
                    out.execute('INSERT INTO ranges VALUES (?,?,?)', (pid, lo, hi))
                    out.execute('INSERT INTO metrics VALUES (?,?)',
                                (f'{pid}:{lo:012d}', zlib.compress(pickle.dumps(state, protocol=5))))
                    out.executemany('INSERT INTO paths(piece,identity,mask,blob) VALUES (?,?,?,NULL)',
                                    ((pid, identity, mask) for identity, mask in db.execute(
                                        'SELECT identity,mask FROM paths WHERE piece=? AND id>? AND id<=? ORDER BY id',
                                        (pid, base + lo, base + hi))))
                    if checks_only:
                        put_config(out, f'fingerprint:{pid}:{lo}', stamp)
                    else:
                        save_block_details(db, out, pid, base, lo, hi, nodes, sample_size, gates_only)
                    remaining_ranges = [f'{Path(s).name}:{a}:{b}' for p, _, s, _, a, b in jobs
                                        if not out.execute('SELECT 1 FROM ranges WHERE piece=? AND lower=?',
                                                           (p, a)).fetchone()]
                    put_config(out, 'remaining_ranges', remaining_ranges)
                draws = sum(int.from_bytes(mask).bit_count() for (mask,) in db.execute(
                    'SELECT mask FROM paths WHERE piece=? AND id>? AND id<=?', (pid, base + lo, base + hi)))
                print(tree.encode({'stage': 'history_checks' if checks_only or saved_checks is None else 'reduction',
                                   'piece': pid, 'source': source,
                                   'range': [lo, hi], 'history_draws': draws, 'block_seconds': block_seconds,
                                   'elapsed_seconds': time.monotonic() - start}), flush=True)
                # Drain running blocks after the deadline instead of discarding them.
        with out:
            put_config(out, 'complete', True)
        histories = out.execute('SELECT COALESCE(SUM(upper-lower),0) FROM ranges').fetchone()[0]
        pieces = out.execute('SELECT COUNT(*) FROM pieces').fetchone()[0]
        remaining_ranges = config(out)['remaining_ranges']
    if checks_only:
        from app.analysis.wave_checks import report
        report(target)
    target.with_name('remaining.json').write_text(tree.encode(remaining_ranges) + '\n')
    return {'status': 'partial_ready', 'file': str(target), 'seconds': time.monotonic() - start,
            'pieces': pieces, 'histories': histories, 'bytes': target.stat().st_size,
            'selection_complete': not remaining_ranges, 'remaining_ranges': remaining_ranges}


def timed_block(task):
    start = time.monotonic()
    state = tree.accumulate_worker(task)
    return state, time.monotonic() - start


def serial_results(db, tasks, deadline):
    for i, (_, seeds, lo, hi, checks) in enumerate(tasks):
        if i and time.monotonic() >= deadline:
            break
        start = time.monotonic()
        state = tree.accumulate(db, seeds, lo, hi, check_histories=checks)
        yield i, (state, time.monotonic() - start)


def bounded_results(executor, tasks, workers, deadline=float('inf')):
    """Keep cores fed without queuing a whole line past its time budget."""
    from concurrent.futures import FIRST_COMPLETED, wait
    from itertools import islice

    tasks = iter(enumerate(tasks))
    pending = {executor.submit(timed_block, task): i for i, task in islice(tasks, workers)}
    while pending:
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            yield pending.pop(future), future.result()
            item = next(tasks, None) if time.monotonic() < deadline else None
            if item is not None:
                i, task = item
                pending[executor.submit(timed_block, task)] = i


def save_block_details(db, out, pid, base, lo, hi, nodes, sample_size, gates_only):
    # Gzip members concatenate losslessly. Gate-only retains sampled facts.
    for key, qid, blob in db.execute(
            'SELECT DISTINCT q.key,q.hash,q.blob FROM questions q JOIN refs r '
            'ON q.key=r.key AND q.hash=r.hash WHERE r.piece=?', (pid,)) if not gates_only else ():
        if out.execute('SELECT 1 FROM questions WHERE key=? AND hash=?', (key, qid)).fetchone():
            continue
        record = tree.encode({'type': 'facts', 'key': key, 'id': qid,
                              'facts': tree.population_facts(unpack_row(blob))}) + '\n'
        out.execute('INSERT INTO questions VALUES (?,?,?)',
                    (key, qid, gzip.compress(record.encode(), mtime=0)))
    candidates = heapq.nsmallest(sample_size, (
        (tree.digest(('sample-731', pid, ordinal)), row_id)
        for ordinal, (row_id,) in enumerate(db.execute(
            'SELECT id FROM paths WHERE piece=? AND id>? AND id<=? ORDER BY id',
            (pid, base + lo, base + hi)), start=lo)))
    cutoff = out.execute('SELECT MAX(priority) FROM samples').fetchone()[0]
    full = out.execute('SELECT COUNT(*) FROM samples').fetchone()[0] >= sample_size
    for priority, row_id in candidates:
        if full and cutoff is not None and priority > cutoff:
            continue
        blob = db.execute('SELECT blob FROM paths WHERE id=?', (row_id,)).fetchone()[0]
        record = sample_record(db, pid, pickle.loads(zlib.decompress(blob)), nodes)
        out.execute('INSERT INTO samples VALUES (?,?)', (priority, zlib.compress(record.encode())))
    trim_samples(out, sample_size)


def merge_part(db, path, *, nodes_only=False, checks_only=False):
    with closing(sqlite3.connect(path)) as incoming:
        cfg = config(incoming)
        expected = 'nodes-v1' if nodes_only else ('wave-checks-v1' if checks_only else 'tree-part-v4')
        if not cfg.get('complete') or cfg.get('kind') != expected:
            raise ValueError(f'incomplete or wrong kind of partial: {path}')
        old = config(db)
        if not nodes_only and old:
            for key in (('kind',) if checks_only else ('nodes', 'seeds', 'sample_size', 'kind', 'gates_only')):
                if old.get(key) != cfg.get(key):
                    raise ValueError(f'partial {key} differs; all parts need the same frozen node catalog and settings')
        # Validate domains before any writes, even when all hashes agree.
        with db:
            copy_nodes(incoming, db)
            if not nodes_only:
                for pid, meta, source in incoming.execute('SELECT * FROM pieces'):
                    old_piece = db.execute('SELECT meta,source FROM pieces WHERE id=?', (pid,)).fetchone()
                    if old_piece and old_piece != (meta, source):
                        raise ValueError(f'piece metadata changed across ranges: {source}')
                    for lo, hi in incoming.execute('SELECT lower,upper FROM ranges WHERE piece=?', (pid,)):
                        if db.execute('SELECT 1 FROM ranges WHERE piece=? AND lower=?', (pid, lo)).fetchone():
                            raise ValueError(f'duplicate piece range across parts: {source}:{lo}:{hi}')
                        db.execute('INSERT INTO ranges VALUES (?,?,?)', (pid, lo, hi))
                    db.execute('INSERT OR IGNORE INTO pieces VALUES (?,?,?)', (pid, meta, source))
                # Bulk SQLite copies avoid round-tripping millions of rows through Python.
                # Detach after the transaction below; failed merges abort the job.
                db.execute('ATTACH DATABASE ? AS part_input', (str(path),))
                for table in ('metrics', 'samples'):
                    db.execute(f'INSERT INTO {table} SELECT * FROM part_input.{table}')
                db.execute('INSERT OR IGNORE INTO questions SELECT * FROM part_input.questions')
                db.execute('INSERT INTO paths(piece,identity,mask,blob) '
                           'SELECT piece,identity,mask,NULL FROM part_input.paths')
                trim_samples(db, cfg['sample_size'])
            for key, value in cfg.items():
                put_config(db, key, value if key != 'complete' else False)
        if not nodes_only:
            db.execute('DETACH DATABASE part_input')


def finalize(database, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with closing(connect(database)) as db:
        cfg = config(db)
        if not cfg.get('complete') or cfg.get('kind') != 'tree-part-v4':
            raise ValueError('incomplete partial')
        for pid, raw in db.execute('SELECT id,meta FROM pieces'):
            count = json.loads(raw)['histories']
            expected = [(lo, min(lo + 64, count)) for lo in range(0, count, 64)] if count else [(0, 0)]
            actual = list(db.execute('SELECT lower,upper FROM ranges WHERE piece=? ORDER BY lower', (pid,)))
            if actual != expected:
                raise ValueError(f'missing or invalid piece range: {pid}')
        state = zero(cfg['seeds'])
        # Identical summation order for one pool, any grouping and any worker count.
        for (blob,) in db.execute('SELECT state FROM metrics ORDER BY piece'):
            state = add(state, pickle.loads(zlib.decompress(blob)))
        mass, counts, supports, errors, failures, checks, histories = state
        with (output / 'catalog.jsonl.gz').open('wb') as f:
            for key, blob in db.execute('SELECT * FROM nodes ORDER BY key'):
                node = pickle.loads(blob)
                record = asdict(node)
                record.pop('assumptions', None)
                record.update(question_class=key.split('|#', 1)[1] if '|#' in key else node.cls,
                              histories=counts.get(key, [0, 0])[0],
                              history_draws=counts.get(key, [0, 0])[1],
                              draws=int(supports[key].sum()) if key in supports else 0,
                              facts_encoding='population ranges; distinct situations retained')
                f.write(gzip.compress((tree.encode({'type': 'question', **record}) + '\n').encode(), mtime=0))
                for (member,) in db.execute('SELECT blob FROM questions WHERE key=? ORDER BY hash', (key,)):
                    f.write(member)
            if not db.execute('SELECT 1 FROM nodes').fetchone():
                f.write(gzip.compress(b'', mtime=0))
        with gzip.open(output / 'histories.jsonl.gz', 'wt') as f:
            for (record,) in db.execute('SELECT record FROM samples ORDER BY priority'):
                f.write(zlib.decompress(record).decode() + '\n')
        pieces = [{'id': pid, 'source': source, **json.loads(meta)}
                  for pid, meta, source in db.execute('SELECT * FROM pieces ORDER BY id')]
        (output / 'pieces.json').write_text(json.dumps(pieces, indent=2) + '\n')
        size = db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
        review = {'status': 'sample_ready_for_review', 'histories': size, 'file': 'histories.jsonl.gz'}
        report = {'coverage': tree.coverage(db),
                  'probability': {'pass': bool(np.allclose(mass, 1, rtol=0, atol=1e-9)) and not failures,
                                  'seeds': cfg['seeds'], 'draws': 512,
                                  'max_absolute_error': float(np.max(np.abs(mass - 1))), 'sums': mass.tolist()},
                  'answers': {'pass': not failures, 'failed_histories': failures, 'examples': errors,
                              'scope': 'all native class-rewritten edge atoms, including composite complements; '
                                       'live class support must have matching dated question facts'},
                  **{name: {**result, 'pass': not result['failed_histories'] and result['histories'] == histories}
                     for name, result in checks.items()},
                  'independent_replay': review, 'histories': histories,
                  'catalog_facts': 'sample_only' if cfg.get('gates_only') else 'all_variants',
                  'catalog_counts': 'terminal histories referencing question atoms; draws are unique native draws; '
                                    'history_draws counts repeated draw reach across histories'}
        report['pass'] = all(report[name].get('pass', False)
                             for name in ('coverage', 'probability', 'answers', 'no_future', 'no_skip'))
        (output / 'gates.json').write_text(json.dumps(report, indent=2) + '\n')
        return report


def combine(*, database, output, local=(), runs=(), artifacts=(), nodes_only=False,
            merge_only=False, checks_only=False):
    """Consume one reduction artifact at a time; intermediate levels remain mergeable."""
    start = time.monotonic()
    database, output = Path(database), Path(output)
    if database.exists():
        raise ValueError('combine needs a fresh database (prevents accidentally combining a part twice)')
    filename = 'nodes.sqlite' if nodes_only else ('checks.sqlite' if checks_only else 'partial.sqlite')
    with closing(connect(database)) as db:
        found = 0
        for root in local:
            root = Path(root)
            paths = [root] if root.is_file() else sorted(root.rglob(filename))
            if not paths:
                raise ValueError(f'{root}: no {filename}')
            for path in paths:
                merge_part(db, path, nodes_only=nodes_only, checks_only=checks_only)
                found += 1
        for run in runs:
            result = subprocess.run(['gh', 'api', f'repos/{{owner}}/{{repo}}/actions/runs/{run}/artifacts',
                                     '--paginate', '--jq', '.artifacts[].name'],
                                    check=True, capture_output=True, text=True)
            prefix = 'out-' if nodes_only else 'heavy-'
            names = sorted(n for n in result.stdout.splitlines() if n.startswith(prefix))
            if artifacts:
                if set(artifacts) - set(names):
                    raise ValueError(f'{run}: missing requested summary artifacts')
                names = [n for n in names if n in artifacts]
            if not names:
                raise ValueError(f'{run}: no summary artifacts')
            for name in names:
                with tempfile.TemporaryDirectory(prefix='pool-summary-', dir=database.parent) as temp:
                    subprocess.run(['gh', 'run', 'download', str(run), '--name', name, '--dir', temp], check=True)
                    paths = sorted(Path(temp).rglob(filename))
                    if not paths:
                        raise ValueError(f'{run}/{name}: no {filename}')
                    for path in paths:
                        merge_part(db, path, nodes_only=nodes_only, checks_only=checks_only)
                        found += 1
                print(tree.encode({'combined': found, 'bytes': database.stat().st_size}), flush=True)
        if not found:
            raise ValueError('no partials supplied')
        with db:
            put_config(db, 'complete', True)
        if nodes_only:
            return save_nodes(db, output / filename)
    merge_seconds = time.monotonic() - start
    if merge_only or checks_only:
        target = output / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != database.resolve():
            with closing(sqlite3.connect(database)) as src, closing(sqlite3.connect(target)) as dest:
                src.backup(dest)
        if checks_only:
            from app.analysis.wave_checks import report
            return report(target, require_complete=not merge_only)
        return {'status': 'partial_ready', 'parts': found, 'seconds': merge_seconds,
                'bytes': target.stat().st_size}
    report = finalize(database, output)
    measurement = {'parts': found, 'pieces': report['coverage'].get('pieces'),
                   'histories': report['histories'], 'merge_seconds': merge_seconds,
                   'export_seconds': time.monotonic() - start - merge_seconds,
                   'seconds': time.monotonic() - start, 'database_bytes': database.stat().st_size}
    (output / 'combine-timing.json').write_text(tree.encode(measurement) + '\n')
    return report

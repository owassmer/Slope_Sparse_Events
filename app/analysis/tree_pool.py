"""Disk-backed pooling of trusted walk pieces; never invokes a model or a walk.

Only downloaded temporary artifacts are removed. Local pieces are read-only.
The scratch database is restartable and belongs on the runner, not on the Mac.
Exported reports contain no financial arrays. Legacy checkpoints without input
routes cannot certify continuation coverage: supply those routes with --links.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import pickle
import random
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing, nullcontext
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.analysis.piece_store import read_events
from app.disputes.forecast import (
    Dist,
    _conjunctions,
    atoms,
    class_firsts,
    expand_classes,
    lazy_row,
    path_mask,
    path_probability,
    unpack_row,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def facts(value):
    """Lossless scalar values; explicitly labelled ranges for population arrays."""
    if isinstance(value, np.ndarray):
        if value.dtype.kind in 'iuf' and value.size:
            return {'range': [value.min().item(), value.max().item()], 'shape': list(value.shape)}
        return {'values': sorted(set(value.ravel().tolist()), key=str), 'shape': list(value.shape)}
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): facts(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [facts(v) for v in value]
    return value


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS pieces (id TEXT PRIMARY KEY, meta TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS nodes (key TEXT PRIMARY KEY, blob BLOB);
        CREATE TABLE IF NOT EXISTS questions
            (key TEXT, hash TEXT, blob BLOB, PRIMARY KEY(key, hash));
        CREATE TABLE IF NOT EXISTS refs
            (piece TEXT, key TEXT, hash TEXT, prefix TEXT, history TEXT);
        CREATE INDEX IF NOT EXISTS refs_lookup ON refs(piece, key);
        CREATE TABLE IF NOT EXISTS paths
            (id INTEGER PRIMARY KEY, piece TEXT, identity TEXT, mask BLOB, blob BLOB);
        CREATE INDEX IF NOT EXISTS path_identity ON paths(identity);
        CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS evaluation (signature TEXT PRIMARY KEY, state BLOB);
    ''')
    return db


def ingest(db, directory, source, links=None):
    meta = json.loads((directory / 'piece.json').read_text())
    stream = directory / 'events.pkl.gz'
    if not stream.exists():
        stream = directory / 'events.pkl'
    # Distinct shares can emit identical streams (including no histories).
    # Bind byte identity to the input cover, while still rejecting renamed retries.
    h = hashlib.sha256()
    cover = {k: meta.get(k) for k in ('partition', 'partitions', 'depth', 'input_routes')}
    if cover['input_routes'] is not None:
        cover['input_routes'] = sorted(cover['input_routes'])
    h.update(json.dumps(cover, sort_keys=True).encode())
    with stream.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    pid = h.hexdigest()
    if db.execute('SELECT 1 FROM pieces WHERE id=?', (pid,)).fetchone():
        raise ValueError(f'duplicate piece: {source}')
    if links and source in links:
        routes = links[source]['input_routes']
        if 'input_routes' in meta and meta['input_routes'] != routes:
            raise ValueError(f'input routes changed: {source}')
        meta = {**meta, 'input_routes': routes}
    if 'events_bytes' in meta and stream.stat().st_size != meta['events_bytes']:
        raise ValueError(f'piece byte count mismatch: {source}')
    count = draws = 0
    classified = False
    with db:
        for kind, payload in read_events(stream):
            if kind == 'nodes':
                for key, node in payload.items():
                    blob = pickle.dumps(node, protocol=5)
                    old = db.execute('SELECT blob FROM nodes WHERE key=?', (key,)).fetchone()
                    if old and pickle.loads(old[0]) != node:
                        raise ValueError(f'question domain changed: {key}')
                    db.execute('INSERT OR IGNORE INTO nodes VALUES (?,?)', (key, blob))
            elif kind in ('questions', 'dated_question'):
                if kind == 'questions':
                    prefix, records = payload
                    history = None
                else:
                    key, prefix, history, blob = payload
                    records = [(key, blob)]
                for key, blob in records:
                    qid = hashlib.sha256(blob).hexdigest()
                    db.execute('INSERT OR IGNORE INTO questions VALUES (?,?,?)', (key, qid, blob))
                    db.execute('INSERT INTO refs VALUES (?,?,?,?,?)',
                               (pid, key, qid, encode(prefix), encode(history)))
            elif kind == 'path':
                p, _ = payload
                mask = path_mask(p, 512)
                mask = np.ones(512, dtype=bool) if mask is None else mask
                identity = digest((p.instance_id, p.steps, p.outcome, p.edges))
                db.execute('INSERT INTO paths(piece,identity,mask,blob) VALUES (?,?,?,?)',
                           (pid, identity, np.packbits(mask).tobytes(), zlib.compress(pickle.dumps(p, protocol=5), 1)))
                count += 1
                draws += int(mask.sum())
            elif kind == 'classification':
                classified = True
        if not classified or (count, draws) != (meta['histories'], meta['history_draws']):
            raise ValueError(f'truncated or inconsistent piece: {source}')
        db.execute('INSERT INTO pieces VALUES (?,?,?)', (pid, encode(meta), source))
    return pid


def coverage(db):
    pieces = [(pid, json.loads(m), s) for pid, m, s in db.execute('SELECT * FROM pieces')]
    errors = []
    settings = {(m['partitions'], m['depth']) for _, m, _ in pieces}
    if len(settings) != 1:
        return {'pass': False, 'errors': ['missing pieces or mixed partition settings']}
    partitions, _ = next(iter(settings))
    by_partition = {}
    for pid, m, source in pieces:
        if not 0 <= m['partition'] < partitions:
            errors.append(f'{source}: partition outside top cover')
        by_partition.setdefault(m['partition'], []).append((pid, m, source))
        if m['complete'] != (not m['remaining']):
            errors.append(f'{source}: inconsistent complete flag')
        for label in ('input_routes', 'remaining'):
            if label not in m:
                errors.append(f'{source}: missing {label}; legacy checkpoint needs --links')
                continue
            routes = sorted(tuple(r) for r in m[label])
            if any(b[:len(a)] == a for a, b in zip(routes, routes[1:], strict=False)):
                errors.append(f'{source}: overlapping {label}')
        if 'input_routes' in m:
            for r in m['remaining']:
                if not any(r[:len(a)] == a for a in m['input_routes']):
                    errors.append(f'{source}: remaining route outside input cover')
    missing = sorted(set(range(partitions)) - by_partition.keys())
    if missing:
        errors.append(f'missing top partitions: {missing}')
    for partition, group in by_partition.items():
        pending = {()}
        unused = list(group)
        while unused:
            # A continuation consumes any exact subset of the frontier. Its
            # deferred descendants replace only that subset, not its siblings.
            matches = [p for p in unused if 'input_routes' in p[1]
                       and {tuple(r) for r in p[1]['input_routes']} <= pending]
            if not matches:
                break
            item = matches[0]
            unused.remove(item)
            pending.difference_update(tuple(r) for r in item[1]['input_routes'])
            pending.update(tuple(r) for r in item[1]['remaining'])
        if pending:
            errors.append(f'partition {partition}: gap or overlapping continuations ({len(pending)} pending routes)')
        if unused:
            errors.append(f'partition {partition}: {len(unused)} unlinked pieces')
    overlaps = 0
    last, seen = None, np.zeros(512, bool)
    for identity, mask in db.execute('SELECT identity,mask FROM paths ORDER BY identity'):
        if identity != last:
            last, seen = identity, np.zeros(512, bool)
        on = np.unpackbits(np.frombuffer(mask, np.uint8)).astype(bool)
        overlaps += int((seen & on).sum())
        seen |= on
    if overlaps:
        errors.append(f'{overlaps} duplicate terminal history/draw supports')
    return {'pass': not errors, 'pieces': len(pieces), 'errors': errors,
            'duplicate_history_draws': overlaps, 'missing_partitions': missing}


class GateDist(Dist):
    """Do not hide overlapping composite alternatives by clipping their mass."""

    def __missing__(self, key):
        if not key.startswith('='):
            raise KeyError(key)
        p = sum(math.prod(self[k][b] for k, b in c) for c in _conjunctions(key))
        if not -1e-12 <= p <= 1 + 1e-12:
            raise ValueError(f'composite probability outside [0,1]: {p}')
        self[key] = value = {'yes': p, 'no': 1 - p}
        return value


def random_dist(nodes, seed):
    """Order-independent independent Dirichlet(1) answers for each class key."""
    out = GateDist()
    for key, node in nodes.items():
        rng = np.random.default_rng(int(digest((seed, key))[:16], 16))
        values = rng.dirichlet(np.ones(len(node.branches)))
        out[key] = dict(zip(node.branches, values, strict=True))
    return out


def answer_atoms(key, branch):
    if key.startswith('='):
        if branch not in ('yes', 'no'):
            raise ValueError(f'invalid composite answer {branch}')
        return [a for c in _conjunctions(key) for a in c]
    return [(key, branch)]


def accumulate(db, seeds, lower, upper):
    """Independent, bounded history range; reduction order is fixed by row ID."""
    nodes = {k: pickle.loads(b) for k, b in db.execute('SELECT * FROM nodes')}
    first = class_firsts(nodes)
    dists = [random_dist(nodes, seed) for seed in seeds]
    mass = np.zeros((len(seeds), 512))
    counts, supports = {}, {}
    errors = []
    total_errors = 0
    histories = 0
    @lru_cache(maxsize=8192)
    def live_mask(key, qid):
        blob = db.execute('SELECT blob FROM questions WHERE key=? AND hash=?', (key, qid)).fetchone()[0]
        row = lazy_row(blob)
        day, pet = row.get('day'), row.get('petition')
        if not isinstance(day, np.ndarray):
            return None
        on = day < 1_000_000
        if isinstance(pet, np.ndarray):
            on &= (pet < 0) | (day < pet)
        return on

    @lru_cache(maxsize=2048)
    def dated_records(pid, key):
        # Many references share facts. Decode the two date arrays once per facts
        # variant, and index support by exact history / legacy decision prefix.
        legacy, exact = {}, {}
        for prefix, history, qid in db.execute(
                'SELECT DISTINCT prefix,history,hash FROM refs WHERE piece=? AND key=?', (pid, key)):
            on = live_mask(key, qid)
            if on is None:
                continue
            pre = tuple(tuple(s) for s in (json.loads(prefix) or []))
            hist = json.loads(history)
            hist = None if hist is None else tuple(tuple(s) for s in hist)
            if hist is not None and hist[:len(pre)] != pre:
                continue
            target, index = (legacy, pre[:-1]) if hist is None else (exact, hist)
            target[index] = target.get(index, np.zeros(512, bool)) | on
        return legacy, exact

    for pid, blob in db.execute(
            'SELECT piece,blob FROM paths WHERE id>? AND id<=? ORDER BY id', (lower, upper)):
        p = pickle.loads(zlib.decompress(blob))
        histories += 1
        reached = {}
        try:
            for q in expand_classes([p], nodes, 512, first=first):
                on = path_mask(q, 512)
                on = np.ones(512, bool) if on is None else on
                for key, branch in q.edges:
                    for k, b in answer_atoms(key, branch):
                        if k not in nodes or b not in nodes[k].branches:
                            raise ValueError(f'answer {b} not offered by {k}')
                    for k in atoms(key):
                        reached[k] = reached.get(k, np.zeros(512, bool)) | on
                for i, dist in enumerate(dists):
                    mass[i, on] += path_probability(q.edges, dist)
            # Class codes name the question offered at this history's decision date.
            # An inactive substitute may carry probability, but must not be mistaken
            # for a live dated question or counted as reach.
            native = path_mask(p, 512)
            indexes = np.arange(512) if native is None else np.flatnonzero(native)
            for base, tags, codes in p.classes:
                for i, tag in enumerate(tags):
                    rows = indexes if codes is None else indexes[np.frombuffer(codes, np.int8) == i]
                    available = np.zeros(512, bool)
                    legacy, exact = dated_records(pid, base + '|' + tag)
                    if p.steps in exact:
                        available |= exact[p.steps]
                    for length in range(len(p.steps) + 1):
                        if p.steps[:length] in legacy:
                            available |= legacy[p.steps[:length]]
                    if not available[rows].all():
                        raise ValueError(f'no dated offered question for {base}|{tag} '
                                         f'on draws {rows[~available[rows]].tolist()}')
        except (KeyError, ValueError) as e:
            total_errors += 1
            if len(errors) < 50:
                errors.append({'piece': pid, 'steps': p.steps, 'error': str(e)})
        # Inactive fallback classes carry probability but were not asked on that draw.
        support = path_mask(p, 512)
        indexes = np.arange(512) if support is None else np.flatnonzero(support)
        for base, tags, codes in p.classes:
            for key in [k for k in reached if k.startswith(base + '|#')]:
                del reached[key]
            for i, tag in enumerate(tags):
                on = np.zeros(512, bool)
                on[indexes if codes is None else indexes[np.frombuffer(codes, np.int8) == i]] = True
                if on.any():
                    reached[base + '|' + tag] = on
        for k, on in reached.items():
            c = counts.setdefault(k, [0, 0])
            c[0] += 1
            c[1] += int(on.sum())
            supports[k] = supports.get(k, np.zeros(512, bool)) | on
        # Composite expression caches are otherwise unbounded on a large tree.
        if histories % 1000 == 0:
            _conjunctions.cache_clear()
            for dist in dists:
                for k in [k for k in dist if k.startswith('=')]:
                    del dist[k]
    return mass, counts, supports, errors, total_errors, histories


def accumulate_worker(task):
    database, seeds, lower, upper = task
    with closing(sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True)) as db:
        return accumulate(db, seeds, lower, upper)


def write_catalog(db, target, keys, counts, draws):
    with gzip.open(target, 'wt') as f:
        for key in keys:
            node = pickle.loads(db.execute('SELECT blob FROM nodes WHERE key=?', (key,)).fetchone()[0])
            record = asdict(node)
            record.pop('assumptions', None)
            record.update(question_class=key.split('|#', 1)[1] if '|#' in key else node.cls,
                          histories=counts.get(key, [0, 0])[0],
                          history_draws=counts.get(key, [0, 0])[1], draws=draws.get(key, 0),
                          facts_encoding='population ranges; distinct situations retained')
            f.write(encode({'type': 'question', **record}) + '\n')
            for qid, blob in db.execute('SELECT hash,blob FROM questions WHERE key=? ORDER BY hash', (key,)):
                f.write(encode({'type': 'facts', 'key': key, 'id': qid,
                                'facts': population_facts(unpack_row(blob))}) + '\n')


def catalog_worker(task):
    database, target, keys, counts, draws = task
    with closing(sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True)) as db:
        write_catalog(db, target, keys, counts, draws)
    return target


def export(db, output, *, seeds=(17, 91, 307), sample_size=32, seconds=900, workers=1):
    output.mkdir(parents=True, exist_ok=True)
    nodes = {k: pickle.loads(b) for k, b in db.execute('SELECT * FROM nodes')}
    first = class_firsts(nodes)
    mass = np.zeros((len(seeds), 512))
    counts, supports, errors = {}, {}, []
    total_errors = histories = last_id = 0
    signature = digest(('range-reduction-v2', list(db.execute('SELECT id FROM pieces ORDER BY id')), seeds))
    saved = db.execute('SELECT state FROM evaluation WHERE signature=?', (signature,)).fetchone()
    if saved:
        last_id, mass, counts, supports, errors, total_errors, histories = pickle.loads(saved[0])
    maximum = db.execute('SELECT COALESCE(MAX(id),0) FROM paths').fetchone()[0]
    database = db.execute('PRAGMA database_list').fetchone()[2]
    start = time.monotonic()
    # Fixed ranges and ordered reduction make arithmetic identical at every worker
    # count. Bound submitted work to one batch, including checkpoint/retry work.
    with ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext() as executor:
        while last_id < maximum:
            ranges = [(lo, min(lo + 4096, maximum))
                      for lo in range(last_id, min(last_id + 4096 * workers, maximum), 4096)]
            results = (executor.map(accumulate_worker, [(database, seeds, lo, hi) for lo, hi in ranges])
                       if executor else (accumulate(db, seeds, lo, hi) for lo, hi in ranges))
            for (_, upper), result in zip(ranges, results, strict=True):
                m, c, s, e, n, h = result
                mass += m
                for key, values in c.items():
                    old = counts.setdefault(key, [0, 0])
                    old[0] += values[0]
                    old[1] += values[1]
                    supports[key] = supports.get(key, np.zeros(512, bool)) | s[key]
                errors.extend(e[:max(0, 50 - len(errors))])
                total_errors += n
                histories += h
                last_id = upper
            state = (last_id, mass, counts, supports, errors, total_errors, histories)
            with db:
                db.execute('INSERT OR REPLACE INTO evaluation VALUES (?,?)',
                           (signature, pickle.dumps(state, protocol=5)))
            if time.monotonic() - start >= seconds:
                return {'status': 'unfinished_export', 'histories': histories, 'next': 'repeat the same command'}
    # Reservoir sample IDs only, not all blobs. Same sample in serial and parallel,
    # independent of checkpoint boundaries; uniform over histories, not mass.
    rng = random.Random(731)
    sample_ids = []
    for n, (row_id,) in enumerate(db.execute('SELECT id FROM paths ORDER BY id'), 1):
        slot = len(sample_ids) if len(sample_ids) < sample_size else rng.randrange(n)
        if slot < sample_size:
            if slot == len(sample_ids):
                sample_ids.append(row_id)
            else:
                sample_ids[slot] = row_id
    samples = []
    for row_id in sample_ids:
        pid, blob = db.execute('SELECT piece,blob FROM paths WHERE id=?', (row_id,)).fetchone()
        samples.append((pid, pickle.loads(zlib.decompress(blob))))
    keys = sorted(nodes)
    draws = {k: int(on.sum()) for k, on in supports.items()}
    if workers == 1:
        write_catalog(db, output / 'catalog.jsonl.gz', keys, counts, draws)
    else:
        # Facts expansion can be larger than history accumulation. Stream ordered
        # gzip members from workers instead of sending population arrays over IPC.
        with tempfile.TemporaryDirectory(prefix='catalog-', dir=output) as temp:
            tasks = [(database, str(Path(temp) / f'{i}.gz'), keys[i:i + 32],
                      {k: counts[k] for k in keys[i:i + 32] if k in counts},
                      {k: draws[k] for k in keys[i:i + 32] if k in draws})
                     for i in range(0, len(keys), 32)]
            with ProcessPoolExecutor(max_workers=workers) as executor, (output / 'catalog.jsonl.gz').open('wb') as f:
                for target in executor.map(catalog_worker, tasks):
                    with open(target, 'rb') as part:
                        shutil.copyfileobj(part, f)
                    Path(target).unlink()
            if not keys:
                with gzip.open(output / 'catalog.jsonl.gz', 'wt'):
                    pass
    with gzip.open(output / 'histories.jsonl.gz', 'wt') as f:
        for pid, p in samples:
            on = path_mask(p, 512)
            rows = np.arange(512) if on is None else np.flatnonzero(on)
            draw = int(random.Random(digest((pid, p.steps))).choice(rows))
            source = db.execute('SELECT source FROM pieces WHERE id=?', (pid,)).fetchone()[0]
            record = {'piece': pid, 'source': source, 'steps': p.steps, 'outcome': p.outcome,
                      'draws': rows.tolist(), 'sample_draw': draw, 'edges': p.edges, 'questions': []}
            keys = {k for q in expand_classes([p], nodes, 512, first=first)
                    for e, _ in q.edges for k in atoms(e)}
            for key in sorted(keys):
                for qid, prefix, history in db.execute(
                        'SELECT DISTINCT hash,prefix,history FROM refs WHERE piece=? AND key=?', (pid, key)):
                    pre, hist = json.loads(prefix) or [], json.loads(history)
                    steps = [list(s) for s in p.steps]
                    # Early records use a probe including its answer; siblings share
                    # the situation before that answer. Dated records retain exact prefixes.
                    match = pre if hist is not None else pre[:-1]
                    if (hist is not None and hist != steps) or steps[:len(match)] != match:
                        continue
                    blob = db.execute('SELECT blob FROM questions WHERE key=? AND hash=?', (key, qid)).fetchone()[0]
                    row = unpack_row(blob)
                    day = row.get('day')
                    if isinstance(day, np.ndarray) and day[draw] >= 1_000_000:
                        continue
                    record['questions'].append({'key': key, 'prefix': pre, 'facts_id': qid,
                                                'facts': draw_facts(row, draw),
                                                'offered_answers': nodes[key].branches})
            f.write(encode(record) + '\n')
    (output / 'pieces.json').write_text(json.dumps([
        {'id': pid, 'source': source, **json.loads(meta)}
        for pid, meta, source in db.execute('SELECT * FROM pieces')], indent=2) + '\n')
    cover = coverage(db)
    probability_ok = bool(np.allclose(mass, 1, rtol=0, atol=1e-9)) and not total_errors
    report = {'coverage': cover,
              'probability': {'pass': probability_ok, 'seeds': seeds, 'draws': 512,
                              'max_absolute_error': float(np.max(np.abs(mass - 1))),
                              'sums': mass.tolist()},
              'answers': {'pass': not total_errors, 'failed_histories': total_errors, 'examples': errors,
                          'scope': 'all native class-rewritten edge atoms, including composite complements; '
                                   'live class support must have matching dated question facts'},
              'no_skip': {'status': 'sample_ready_for_review', 'histories': len(samples),
                          'file': 'histories.jsonl.gz'},
              'no_future': {'status': 'sample_ready_for_review', 'histories': len(samples),
                            'file': 'histories.jsonl.gz'},
              'histories': histories,
              'catalog_counts': 'terminal histories referencing question atoms; draws are unique native draws; '
                                'history_draws counts repeated draw reach across histories'}
    (output / 'gates.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def population_facts(row):
    """Do not report unpack_row's inactive zero-fill as an observed cash value."""
    day = row.get('day')
    if not isinstance(day, np.ndarray):
        return facts(row)
    pet = row.get('petition')
    on = day < 1_000_000
    if isinstance(pet, np.ndarray):
        on &= (pet < 0) | (day < pet)

    def cut(v):
        if isinstance(v, np.ndarray) and v.ndim and v.shape[0] == len(on):
            return facts(v[on]) if on.any() else {'state': 'not_live'}
        if isinstance(v, dict):
            return {str(k): cut(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [cut(x) for x in v]
        return facts(v)

    return {'native_draws': np.flatnonzero(on).tolist(), 'values': cut(row)}


def draw_facts(value, draw):
    if isinstance(value, np.ndarray):
        return facts(value[draw]) if value.ndim and value.shape[0] == 512 else facts(value)
    if isinstance(value, dict):
        return {str(k): draw_facts(v, draw) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [draw_facts(v, draw) for v in value]
    return facts(value)


def ingest_worker(task):
    """Download and validate independently; return a compact SQLite shard to merge."""
    shard, source, directory, remote, links, committed = task
    with tempfile.TemporaryDirectory(prefix='slope-download-', dir=Path(shard).parent) as temp:
        if remote:
            run, names = remote
            for name in names:
                subprocess.run(['gh', 'run', 'download', str(run), '--name', name, '--dir', temp], check=True)
            manifests = sorted(Path(temp).rglob('piece.json'))
            if not manifests:
                raise ValueError(f'{source}: no piece.json')
            event_dirs = {p.parent for pattern in ('events.pkl', 'events.pkl.gz')
                          for p in Path(temp).rglob(pattern)}
            if event_dirs != {p.parent for p in manifests}:
                raise ValueError(f'{source}: events and piece.json directories do not match')
            directories = [(p.parent, f'{source}/{p.parent.relative_to(temp)}') for p in manifests]
        else:
            directories = [(Path(directory), source)]
        with closing(connect(Path(shard))) as db:
            for folder, label in directories:
                # Also resume databases made by the older piece-at-a-time writer,
                # which could commit a piece before its artifact source marker.
                if label not in committed:
                    ingest(db, folder, label, links)
            with db:
                db.execute('INSERT INTO sources VALUES (?)', (source,))
    return shard


def merge_shard(db, shard):
    """Native bulk copy, no parent-process history unpickling; atomic source commit."""
    db.execute('ATTACH DATABASE ? AS incoming', (str(shard),))
    try:
        with db:
            duplicate = db.execute('SELECT source FROM incoming.pieces WHERE id IN (SELECT id FROM pieces)').fetchone()
            if duplicate:
                raise ValueError(f'duplicate piece: {duplicate[0]}')
            for key, old, new in db.execute(
                    'SELECT n.key,n.blob,i.blob FROM nodes n JOIN incoming.nodes i ON n.key=i.key'):
                if pickle.loads(old) != pickle.loads(new):
                    raise ValueError(f'question domain changed: {key}')
            for table in ('pieces', 'sources', 'refs'):
                db.execute(f'INSERT INTO {table} SELECT * FROM incoming.{table}')
            for table in ('nodes', 'questions'):
                db.execute(f'INSERT OR IGNORE INTO {table} SELECT * FROM incoming.{table}')
            db.execute('INSERT INTO paths(piece,identity,mask,blob) '
                       'SELECT piece,identity,mask,blob FROM incoming.paths ORDER BY id')
    finally:
        db.execute('DETACH DATABASE incoming')


def pool(*, database, output, local=(), runs=(), links=None, seconds=900, finish=True, workers=1):
    """Restart at artifact boundaries. An interrupted artifact's DB transaction rolls back.

    --runs is ordered only for convenience; coverage uses input routes, not wave
    order. Pair heavy-N with out-N when present. Local roots may contain many
    whole pieces. Independent validated shards are merged in stable input order.
    --links maps source identifiers (run/heavy-N/relative-directory, or local
    directory) to input_routes for legacy pieces whose writer did not save them.
    """
    if workers < 1:
        raise ValueError('workers must be positive')
    start = time.monotonic()
    with connect(database) as db:
        # Route links may arrive after the large artifacts have already been consumed.
        with db:
            for pid, raw, source in db.execute('SELECT * FROM pieces').fetchall():
                if links and source in links:
                    meta = json.loads(raw)
                    routes = links[source]['input_routes']
                    if 'input_routes' in meta and meta['input_routes'] != routes:
                        raise ValueError(f'input routes changed: {source}')
                    meta['input_routes'] = routes
                    db.execute('UPDATE pieces SET meta=? WHERE id=?', (encode(meta), pid))
        jobs = []
        directories = set()
        for root in local:
            manifests = sorted(Path(root).rglob('piece.json'))
            if not manifests:
                raise ValueError(f'{root}: no piece.json')
            for manifest in manifests:
                directory = manifest.parent
                if directory.resolve() in directories:
                    continue
                directories.add(directory.resolve())
                source = str(directory)
                if not db.execute('SELECT 1 FROM sources WHERE source=?', (source,)).fetchone():
                    jobs.append((source, directory, None))
        for run in dict.fromkeys(runs):
            result = subprocess.run(['gh', 'api', f'repos/{{owner}}/{{repo}}/actions/runs/{run}/artifacts',
                                     '--paginate', '--jq', '.artifacts[].name'],
                                    check=True, capture_output=True, text=True)
            artifacts = set(result.stdout.splitlines())
            names = sorted(s for s in artifacts if s.startswith('heavy-'))
            if not names:
                raise ValueError(f'run {run}: no heavy artifacts')
            for name in names:
                source = f'{run}/{name}'
                if db.execute('SELECT 1 FROM sources WHERE source=?', (source,)).fetchone():
                    continue
                small = name.replace('heavy-', 'out-', 1)
                paired = [name, small] if small in artifacts else [name]
                jobs.append((source, None, (run, paired)))
        committed = {source for (source,) in db.execute('SELECT source FROM pieces')}
        with tempfile.TemporaryDirectory(prefix='slope-pool-', dir=database.parent) as temp:
            with ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext() as executor:
                for offset in range(0, len(jobs), workers):
                    if time.monotonic() - start >= seconds:
                        return {'status': 'unfinished_ingest', 'next': 'repeat the same command',
                                'next_artifact': jobs[offset][0]}
                    tasks = [(str(Path(temp) / f'{i}.sqlite'), *job, links,
                              {s for s in committed if s == job[0] or s.startswith(job[0] + '/')})
                             for i, job in enumerate(jobs[offset:offset + workers], offset)]
                    results = executor.map(ingest_worker, tasks) if executor else map(ingest_worker, tasks)
                    for shard in results:
                        merge_shard(db, shard)
                        Path(shard).unlink()
        remaining = seconds - (time.monotonic() - start)
        if finish and remaining <= 0:
            return {'status': 'unfinished_export', 'next': 'repeat the same command'}
        return export(db, output, seconds=remaining, workers=workers) if finish else {'status': 'ingested', 'database': str(database)}

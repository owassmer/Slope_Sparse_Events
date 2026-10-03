"""Bounded saved-question assembly on GitHub; global support remains a coordinator reduction."""
from __future__ import annotations

import concurrent.futures
import contextlib
import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import sqlite3
import tarfile
import tempfile
import threading
import time
import traceback
from array import array
from pathlib import Path

from app.disputes import pool
from tools import question_refresh_assemble as assembly
from tools import question_refresh_fleet as queue
from tools.pool_fleet import fetch, put
from tools.question_refresh import Results

_INFO = None
_CONTROL = None


def version():
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(b''.join((root / p).read_bytes() for p in (
        'tools/question_assembly_fleet.py', 'tools/question_refresh_assemble.py',
        'tools/question_refresh.py', 'app/disputes/forecast.py',
        'app/analysis/events.py', 'app/disputes/pool.py'))).hexdigest()[:16]


def bundles(source, databases, max_histories=5000, max_bytes=96 * 1024**2):
    """Keep contiguous histories together, bounding unique serialized question rows."""
    source = Path(source)
    paths = pickle.loads(source.read_bytes())
    _, (_, meta, missing, count, seconds) = pickle.loads(source.with_suffix('.meta').read_bytes())
    if missing or count != len(paths) or len(meta) != count:
        raise ValueError('Incomplete preserved source')
    start, chosen, selected, consumers, size = 0, [], {}, [], 0
    for offset in range(0, len(paths), 128):
        block = paths[offset:offset + 128]
        refs, needed = [], {}
        for index, path in enumerate(block, offset):
            entry = meta[index]
            if entry[2:] != (source.name, index):
                raise ValueError('Preserved source metadata order changed')
            found = databases[path.instance_id].execute(
                'SELECT bindings FROM consumers WHERE part=? AND row=?', (source.name, index)).fetchone()
            if found is None:
                raise ValueError('Missing source consumer')
            ids = array('Q')
            ids.frombytes(found[0])
            refs.append((path.instance_id, found[0], ids))
            needed.setdefault(path.instance_id, set()).update(ids)
        rows = {}
        for iid, ids in needed.items():
            assigned = set(ids)
            found = {b: selected[iid, b] for b in ids if (iid, b) in selected}
            ids = sorted(assigned - found.keys())
            for at in range(0, len(ids), 800):
                group = ids[at:at + 800]
                sql = ('SELECT b.id,b.question,b.prefix,r.row,r.classes FROM bindings b '
                       'JOIN results r ON r.binding=b.id WHERE b.id IN (' + ','.join('?' * len(group)) + ')')
                found.update((r[0], r) for r in databases[iid].execute(sql, group))
            if set(found) != assigned:
                raise ValueError('Missing calculated bindings')
            rows[iid] = found
        for index, (path, (iid, blob, ids)) in enumerate(zip(block, refs, strict=True), offset):
            additions = [rows[iid][b] for b in set(ids) if (iid, b) not in selected]
            cost = sum(len(r[3]) + len(r[4]) + len(r[1]) for r in additions)
            if chosen and (len(chosen) >= max_histories or size + cost > max_bytes):
                yield {'source': source.name, 'start': start, 'paths': chosen,
                       'meta': meta[start:index], 'rows': selected, 'consumers': consumers,
                       'seconds': seconds * len(chosen) / max(1, count)}
                start, chosen, selected, consumers, size = index, [], {}, [], 0
                additions = [rows[iid][b] for b in set(ids)]
                cost = sum(len(r[3]) + len(r[4]) + len(r[1]) for r in additions)
            selected.update(((iid, r[0]), r) for r in additions)
            chosen.append(path)
            consumers.append((iid, blob))
            size += cost
    if chosen:
        yield {'source': source.name, 'start': start, 'paths': chosen, 'meta': meta[start:],
               'rows': selected, 'consumers': consumers,
               'seconds': seconds * len(chosen) / max(1, count)}


def rebind_bundle(fc, bundle, tag, directory):
    """Adapt a bounded input to the existing, checked assembly implementation."""
    directory = Path(directory)
    name = f'part{int(tag)}.pkl'
    source = directory / name
    count = len(bundle['paths'])
    if len(bundle['consumers']) != count or len(bundle['meta']) != count:
        raise ValueError('Incomplete assembly bundle')
    meta = []
    for i, (event, financial, old_file, old_row) in enumerate(bundle['meta']):
        if (old_file, old_row) != (bundle['source'], bundle['start'] + i):
            raise ValueError('Assembly source range changed')
        meta.append((event, financial, name, i))
    source.write_bytes(pickle.dumps(bundle['paths'], protocol=5))
    source.with_suffix('.meta').write_bytes(pickle.dumps(
        ('saved', (int(tag), meta, set(), count, bundle['seconds'])), protocol=5))
    databases = {}
    for iid in {p.instance_id for p in bundle['paths']}:
        filename = directory / f'{len(databases)}.sqlite'
        db = sqlite3.connect(filename)
        Results(db)
        db.executescript('CREATE TABLE bindings(id INTEGER PRIMARY KEY, question TEXT, prefix INTEGER);'
                         'CREATE TABLE consumers(part TEXT,row INTEGER,bindings BLOB,PRIMARY KEY(part,row));')
        db.executemany('INSERT INTO bindings VALUES(?,?,?)',
                       ((r[0], r[1], r[2]) for (instance, _), r in bundle['rows'].items() if instance == iid))
        db.executemany('INSERT INTO results VALUES(?,?,?)',
                       ((r[0], r[3], r[4]) for (instance, _), r in bundle['rows'].items() if instance == iid))
        db.executemany('INSERT INTO consumers VALUES(?,?,?)',
                       ((name, i, blob) for i, (instance, blob) in enumerate(bundle['consumers']) if instance == iid))
        db.commit()
        db.close()
        databases[iid] = filename
    out = directory / 'out'
    (out / 'walked').mkdir(parents=True)
    assembly._CONTEXT = fc, databases, out
    try:
        checkpoint = Path(assembly._part(source))
    finally:
        assembly._CONTEXT = None
    report = pickle.loads(checkpoint.read_bytes())
    report['source'] = bundle['source']
    report['start'] = bundle['start']
    report['tag'] = tag
    report['identity'] = bundle.get('identity')
    report['input_digest'] = bundle.get('input_digest')
    checkpoint.write_bytes(pickle.dumps(report, protocol=5))
    return checkpoint


def validate_output(filename, task, identity):
    tag = task['tag']
    with tarfile.open(filename) as tar:
        if set(tar.getnames()) != {f'part{tag}.pkl', f'part{tag}.refresh'}:
            raise ValueError('Unexpected assembly output members')
        report = pickle.load(tar.extractfile(f'part{tag}.refresh'))
        paths = pickle.load(tar.extractfile(f'part{tag}.pkl'))
    if (report['source'], report['start'], report['source_count'], report['tag'],
            report['identity'], report['input_digest']) != (
            task['source'], task['start'], task['count'], tag, identity, task['input_digest']):
        raise ValueError('Assembly output preparation or coverage differs')
    if len(paths) != report['retained'] or len(report['meta']) != len(paths):
        raise ValueError('Assembly retained coverage differs')
    if report['retained'] + report['removed'] != report['source_count']:
        raise ValueError('Assembly source accounting differs')
    if any(m[2:] != (f'part{tag}.pkl', i) for i, m in enumerate(report['meta'])):
        raise ValueError('Assembly output metadata order differs')
    return report


def execute(task):
    tag = task['tag']
    with tempfile.TemporaryDirectory(prefix=f'assembly-{tag}-') as temporary:
        root = Path(temporary)
        output = root / 'output.tgz'
        if fetch(task['existing'], output, missing_ok=True):
            validate_output(output, task, _INFO['identity'])
            return tag, 'preserved'
        log = root / 'progress.log'
        log.write_text(json.dumps({'stage': 'loading', 'task': tag, 'time': time.time()}) + '\n')
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(15):
                try:
                    put(task['log'], log.read_bytes())
                except Exception:
                    traceback.print_exc()
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            put(task['log'], log.read_bytes())
            fetch(task['input'], root / 'input.gz')
            if hashlib.sha256((root / 'input.gz').read_bytes()).hexdigest() != task['input_digest']:
                raise ValueError('Assembly input digest changed')
            with gzip.open(root / 'input.gz', 'rb') as fh:
                bundle = pickle.load(fh)
            if bundle['identity'] != _INFO['identity'] or bundle['tag'] != tag:
                raise ValueError('Assembly bundle identity changed')
            bundle['input_digest'] = task['input_digest']
            # A fresh forecaster keeps each report independent of previous assignments.
            fc = pool.forecaster(_INFO['run'], _CONTROL)
            fc.grouped.update(k for k in fc.classed if fc.nodes[k].node in
                              ('judgment_response', 'financing_at_floor', 'petition_cash_out'))
            with log.open('a') as fh:
                print({'stage': 'rebinding', 'histories': len(bundle['paths']), 'time': time.time()}, file=fh)
            checkpoint = rebind_bundle(fc, bundle, tag, root)
            with tarfile.open(output, 'w:gz', compresslevel=1) as tar:
                tar.add(checkpoint, arcname=checkpoint.name)
                tar.add(checkpoint.with_suffix('.pkl'), arcname=checkpoint.with_suffix('.pkl').name)
            put(task['output'], output.read_bytes())
            with log.open('a') as fh:
                print({'stage': 'saved', 'time': time.time()}, file=fh)
            return tag, len(bundle['paths'])
        finally:
            stop.set()
            thread.join()
            put(task['log'], log.read_bytes())


def consume(slot):
    try:
        return queue._consume(slot, execute)
    except Exception as error:
        traceback.print_exc()
        raise RuntimeError(f'Assembly failed: {type(error).__name__}') from None


def worker(job, url):
    global _INFO, _CONTROL
    fetch(url, 'assembly-manifest.gz')
    _INFO = json.loads(gzip.decompress(Path('assembly-manifest.gz').read_bytes()))
    if _INFO['version'] != version():
        raise ValueError('Assembly worker implementation differs')
    fetch(_INFO['control'], 'assembly-control.pkl')
    _CONTROL = pickle.loads(Path('assembly-control.pkl').read_bytes())
    queue._INFO = _INFO
    cores = os.cpu_count() or 1
    print({'job': job, 'cores': cores, 'tasks': len(_INFO['tasks']), 'jev_started': False}, flush=True)
    with multiprocessing.get_context('fork').Pool(cores) as processes:
        for _ in processes.imap_unordered(consume, [job] * cores, chunksize=1):
            pass


def _export_source(args):
    import boto3

    ordinal, source, filenames, identity, bucket, base, region, out = args
    s3 = boto3.client('s3', region_name=region)
    databases = {iid: sqlite3.connect(f'file:{name}?mode=ro', uri=True) for iid, name in filenames.items()}
    for db in databases.values():
        db.execute('PRAGMA cache_size=-131072')
    tasks = []
    try:
        for number, bundle in enumerate(bundles(source, databases)):
            # Source index and chunk index are stable regardless of finish order.
            if number >= 100000:
                raise ValueError('Source exceeds reserved task numbering range')
            tag = str(ordinal * 100000 + number)
            bundle.update(tag=tag, identity=identity)
            filename = Path(out) / f'{tag}.gz'
            with filename.open('wb') as raw:
                with gzip.GzipFile(filename='', fileobj=raw, mode='wb', compresslevel=1, mtime=0) as fh:
                    pickle.dump(bundle, fh, protocol=5)
            digest = hashlib.sha256(filename.read_bytes()).hexdigest()
            s3.upload_file(str(filename), bucket, base + '/inputs/' + tag + '.gz')
            filename.unlink()
            tasks.append({'tag': tag, 'source': Path(source).name, 'start': bundle['start'],
                          'count': len(bundle['paths']), 'input_digest': digest})
    finally:
        for db in databases.values():
            db.close()
    return Path(source).name, tasks


def publish(s3, bucket, base, run, control, refresh, walked, out):
    """Publish bounded work; no financial replay and no question recalculation."""
    out, refresh = Path(out), Path(refresh)
    out.mkdir(parents=True, exist_ok=True)
    prepared = json.loads((refresh / 'prepared.json').read_text())
    preparation = json.loads((refresh / 'fleet.json').read_text())['preparation']
    identity = preparation + ':' + hashlib.sha256(Path(control).read_bytes()).hexdigest() + ':' + version()
    databases = {}
    for iid, item in prepared['instances'].items():
        db = sqlite3.connect(refresh / item['folder'] / 'plan.sqlite')
        Results(db).require_complete(item['calculations'])
        databases[iid] = str(refresh / item['folder'] / 'plan.sqlite')
        db.close()
    def url(op, key, **kwargs):
        return s3.generate_presigned_url(op, Params={'Bucket': bucket, 'Key': key, **kwargs}, ExpiresIn=21600)
    s3.upload_file(str(control), bucket, base + '/control.pkl')
    manifest = {'version': version(), 'identity': identity, 'run': run, 'workers': 80,
                'control': url('get_object', base + '/control.pkl'), 'tasks': [],
                'pending': url('get_object', base + '/pending.json')}
    histories, sources = 0, {}
    files = sorted(Path(walked).glob('part*.pkl'), key=lambda p: int(p.stem[4:]))
    tasks = [(i, str(source), databases, identity, bucket, base, s3.meta.region_name, str(out))
             for i, source in enumerate(files)]
    with concurrent.futures.ProcessPoolExecutor(max_workers=14,
            mp_context=multiprocessing.get_context('fork')) as executor:
        futures = [executor.submit(_export_source, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            name, records = future.result()
            sources[name] = sum(t['count'] for t in records)
            histories += sources[name]
            for task in records:
                tag = task['tag']
                task.update(input=url('get_object', base + '/inputs/' + tag + '.gz'),
                    output=url('put_object', base + '/outputs/' + tag + '.tgz'),
                    existing=url('get_object', base + '/outputs/' + tag + '.tgz'),
                    claim=url('put_object', base + '/claims/' + tag + '.json', IfNoneMatch='*'),
                    release=url('delete_object', base + '/claims/' + tag + '.json'),
                    log=url('put_object', base + '/logs/' + tag + '.log'))
                manifest['tasks'].append(task)
            progress = {'stage': 'prepare_assembly', 'sources': len(sources), 'histories': histories,
                        'tasks': len(manifest['tasks']), 'time': time.time(), 'jev_started': False}
            (out / 'progress.json').write_text(json.dumps(progress))
            print(progress, flush=True)
    manifest['tasks'].sort(key=lambda t: int(t['tag']))
    if histories != prepared['histories']:
        raise ValueError('Assembly assignment coverage differs')
    manifest.update(histories=histories, sources=sources)
    body = gzip.compress(json.dumps(manifest).encode())
    s3.put_object(Bucket=bucket, Key=base + '/manifest.gz', Body=body)
    s3.put_object(Bucket=bucket, Key=base + '/pending.json', Body=json.dumps({
        'remaining': len(manifest['tasks']), 'available': [t['tag'] for t in manifest['tasks']]}).encode())
    (out / 'manifest.gz').write_bytes(body)
    (out / 'fleet.json').write_text(json.dumps({'base': base, 'bucket': bucket, 'identity': identity}))
    return url('get_object', base + '/manifest.gz')


def collect(s3, bucket, base, directory):
    """Persist each completed bundle, then globally union its exact population support."""
    directory = Path(directory)
    (directory / 'walked').mkdir(parents=True, exist_ok=True)
    manifest = json.loads(gzip.decompress(s3.get_object(Bucket=bucket, Key=base + '/manifest.gz')['Body'].read()))
    tasks = {t['tag']: t for t in manifest['tasks']}
    done = set()
    while len(done) < len(tasks):
        available = [o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(
            Bucket=bucket, Prefix=base + '/outputs/') for o in page.get('Contents', [])]
        keys = [k for k in available if k.rsplit('/', 1)[-1].removesuffix('.tgz') not in done]
        with contextlib.closing(queue.downloaded_outputs(s3, bucket, keys, directory)) as downloads:
            for key, target in downloads:
                tag = key.rsplit('/', 1)[-1].removesuffix('.tgz')
                task = tasks[tag]
                validate_output(target, task, manifest['identity'])
                with tarfile.open(target) as tar:
                    tar.extractall(directory / 'walked', filter='data')
                done.add(tag)
        active = queue.release_stale(s3, bucket, base, done)
        s3.put_object(Bucket=bucket, Key=base + '/pending.json', Body=json.dumps({
            'remaining': len(tasks) - len(done), 'available': sorted(tasks.keys() - done - active)}).encode())
        progress = {'stage': 'github_question_assembly', 'completed': len(done), 'total': len(tasks),
                    'histories': sum(tasks[t]['count'] for t in done), 'time': time.time(), 'jev_started': False}
        (directory / 'progress.json').write_text(json.dumps(progress))
        print(progress, flush=True)
        if len(done) < len(tasks):
            time.sleep(15)
    return manifest


def reduce_saved(run, control_file, refresh, directory, manifest):
    """Exactly-once source coverage and global population union before facts."""
    refresh, directory = Path(refresh), Path(directory)
    control = pickle.loads(Path(control_file).read_bytes())
    prepared = json.loads((refresh / 'prepared.json').read_text())
    stores = {}
    for iid, item in prepared['instances'].items():
        db = sqlite3.connect(refresh / item['folder'] / 'plan.sqlite')
        stores[iid] = Results(db)
        stores[iid].require_complete(item['calculations'])
        db.execute('DELETE FROM wanted')
        db.commit()
    fc = pool.forecaster(run, control)
    fc.grouped.update(k for k in fc.classed if fc.nodes[k].node in
                      ('judgment_response', 'financing_at_floor', 'petition_cash_out'))
    meta, total, retained, cost, coverage = [], 0, 0, {}, {}
    for task in manifest['tasks']:
        tag = task['tag']
        report = pickle.loads((directory / 'walked' / f'part{tag}.refresh').read_bytes())
        if report['identity'] != manifest['identity'] or report['input_digest'] != task['input_digest']:
            raise ValueError('Saved assembly output identity differs')
        coverage.setdefault(task['source'], []).append((task['start'], task['count']))
        meta.extend(report['meta'])
        total += report['source_count']
        retained += report['retained']
        for key, node in report['nodes'].items():
            if key in fc.nodes and fc.nodes[key] != node:
                raise ValueError('Parallel question definitions differ')
            fc.nodes[key] = node
        for key, group in report['node_group'].items():
            if key in fc.node_group and fc.node_group[key] != group:
                raise ValueError('Parallel answer groups differ')
            fc.node_group[key] = group
        fc.classed |= report['classed']
        cost[f'part{tag}.pkl'] = report['retained'], report['seconds']
        for iid, wanted in report['wanted'].items():
            with stores[iid].db:
                stores[iid].db.executemany('INSERT INTO wanted VALUES(?,?) ON CONFLICT(binding) '
                                         'DO UPDATE SET mask=mask_union(mask,excluded.mask)', wanted)
    for source, expected in manifest['sources'].items():
        end = 0
        for start, count in sorted(coverage.pop(source, [])):
            if start != end or count <= 0:
                raise ValueError('Overlapping or missing assembly source range')
            end += count
        if end != expected:
            raise ValueError('Incomplete assembly source')
    if coverage or total != prepared['histories'] or total != control['walked']:
        raise ValueError('Assembly history coverage differs')
    assembly.finalize(fc, stores, control, meta, total, retained, cost, directory, fact_processes=14)


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'worker':
        worker(int(sys.argv[2]), os.environ['ASSEMBLY_MANIFEST_URL'])
    else:
        raise SystemExit('usage: python -m tools.question_assembly_fleet worker JOB')

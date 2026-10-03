"""Distribute globally unique refresh requests; retain engine caches across batches."""
from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import heapq
import json
import multiprocessing
import os
import pickle
import threading
import time
from pathlib import Path

from tools.pool_fleet import fetch, put
from tools.question_refresh import run_batch

_FC = None
_INFO = None


def version():
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(b''.join((root / name).read_bytes() for name in (
        'app/analysis/events.py', 'app/disputes/forecast.py', 'app/disputes/notes.py',
        'app/disputes/state14.py',
        'tools/question_refresh.py', 'tools/question_refresh_fleet.py'))).hexdigest()[:16]


def publish(s3, bucket, prefix, folder, control, github_jobs=80, aws_jobs=8):
    folder = Path(folder)
    prepared = json.loads((folder / 'prepared.json').read_text())
    inputs = []
    identity = hashlib.sha256(Path(control).read_bytes())
    for instance, item in sorted(prepared['instances'].items()):
        for source in sorted((folder / item['folder'] / 'inputs').glob('*.pkl.gz')):
            tag = f'{item["folder"]}-{source.name.removesuffix(".pkl.gz")}'
            identity.update(pickle.dumps((instance, tag), protocol=5))
            identity.update(hashlib.sha256(source.read_bytes()).digest())
            inputs.append((source, instance, tag))
    preparation = identity.hexdigest()
    base = f'{prefix}/{version()}/{preparation[:24]}'

    def url(operation, key):
        return s3.generate_presigned_url(operation, Params={'Bucket': bucket, 'Key': key}, ExpiresIn=21600)

    control_key = f'{base}/control.pkl'
    s3.upload_file(str(control), bucket, control_key)
    info = {'run': prepared['run'], 'version': version(), 'preparation': preparation,
            'control': url('get_object', control_key),
            'github_jobs': github_jobs, 'jobs': [[] for _ in range(github_jobs + aws_jobs)]}
    # Whole batches remain contiguous prefix ranges. Small batches balance the tail;
    # the existing input byte size accounts for history length and number of consumers.
    loads = [(0, j) for j in range(len(info['jobs']))]
    heapq.heapify(loads)
    transfers = []
    for source, instance, tag in sorted(inputs, key=lambda x: (-x[0].stat().st_size, x[2])):
        cost, job = heapq.heappop(loads)
        capacity = 2 if job < github_jobs else 1
        heapq.heappush(loads, (cost + source.stat().st_size / capacity, job))
        key = f'{base}/inputs/{tag}.pkl.gz'
        transfers.append((source, key))
        info['jobs'][job].append({'tag': tag, 'instance': instance, 'input': url('get_object', key),
                                 'output': url('put_object', f'{base}/outputs/{tag}.pkl.gz'),
                                 'existing': url('get_object', f'{base}/outputs/{tag}.pkl.gz'),
                                 'log': url('put_object', f'{base}/logs/{tag}.log')})
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(s3.upload_file, str(source), bucket, key) for source, key in transfers]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    manifest = f'{base}/manifest.json'
    s3.put_object(Bucket=bucket, Key=manifest, Body=json.dumps(info).encode())
    (folder / 'fleet.json').write_text(json.dumps({'base': base, 'tasks': len(inputs), 'version': version(),
                                                'preparation': preparation}))
    return url('get_object', manifest)


def collect(s3, bucket, folder):
    """Ingest finished batches while the remaining workers calculate."""
    import sqlite3

    from tools.question_refresh import Results

    folder = Path(folder)
    fleet = json.loads((folder / 'fleet.json').read_text())
    prepared = json.loads((folder / 'prepared.json').read_text())
    stores, expected = {}, {}
    for iid, item in prepared['instances'].items():
        db = sqlite3.connect(folder / item['folder'] / 'plan.sqlite')
        stores[iid] = Results(db)
        db.execute('CREATE TABLE IF NOT EXISTS ingested_batches (tag TEXT PRIMARY KEY)')
        db.commit()
        for source in (folder / item['folder'] / 'inputs').glob('*.pkl.gz'):
            expected[f'{item["folder"]}-{source.name.removesuffix(".pkl.gz")}'] = iid
    done = {tag for store in stores.values() for tag, in store.db.execute('SELECT tag FROM ingested_batches')}
    if not done <= expected.keys() or len(expected) != fleet['tasks']:
        raise ValueError('Refresh result assignment differs from preparation')
    prefix = fleet['base'] + '/outputs/'
    while len(done) < len(expected):
        available = {o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix)
                     for o in page.get('Contents', [])}
        for key in sorted(available):
            tag = key.removeprefix(prefix).removesuffix('.pkl.gz')
            if tag not in expected:
                raise ValueError(f'Unassigned refresh output {tag}')
            if tag in done:
                continue
            target = folder / 'ingesting.pkl.gz'
            s3.download_file(bucket, key, str(target))
            store = stores[expected[tag]]
            store.ingest(target, expected[tag])
            with store.db:
                store.db.execute('INSERT INTO ingested_batches VALUES(?)', (tag,))
            target.unlink()
            done.add(tag)
        progress = {'stage': 'refresh_question_calculations', 'completed_batches': len(done),
                    'total_batches': len(expected), 'time': time.time(), 'jev_started': False}
        (folder / 'progress.json').write_text(json.dumps(progress))
        s3.put_object(Bucket=bucket, Key=f'{fleet["base"]}/progress.json', Body=json.dumps(progress).encode())
        print(progress, flush=True)
        if len(done) < len(expected):
            time.sleep(15)
    for store in stores.values():
        store.db.close()
    (folder / 'calculated.json').write_text(json.dumps(progress))
    return progress


def monitor(s3, bucket, prefix, folder):
    """Publish the coordinator stage beside the workers' live calculation logs."""
    folder = Path(folder)
    while True:
        try:
            progress = json.loads((folder / 'progress.json').read_text())
            if (folder / 'fleet.json').exists():
                progress['logs_prefix'] = json.loads((folder / 'fleet.json').read_text())['base'] + '/logs/'
            progress['observed_at'] = time.time()
            s3.put_object(Bucket=bucket, Key=f'{prefix}/progress.json', Body=json.dumps(progress).encode())
            log = Path('/var/log/question-index.log')
            if log.exists():
                s3.put_object(Bucket=bucket, Key=f'{prefix}/index.log', Body=log.read_bytes()[-262144:])
        except Exception as error:
            print({'monitor_error': str(error)}, flush=True)
        time.sleep(15)


def _one(task):
    tag = task['tag']
    source, output, log = (Path(tag + suffix) for suffix in ('.input.gz', '.output.gz', '.log'))
    if fetch(task['existing'], output, missing_ok=True):
        output.unlink()
        return tag, 'preserved'
    fetch(task['input'], source)
    stop = threading.Event()
    log.write_text(f'calculating {tag}\n')
    failures = []

    def report():
        while not stop.wait(15):
            try:
                put(task['log'], log.read_bytes())
            except Exception as error:
                failures.append(type(error).__name__)

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    try:
        with log.open('a', buffering=1) as fh, contextlib.redirect_stdout(fh):
            count = run_batch(_FC, source, output)
            print({'complete': tag, 'calculations': count}, flush=True)
        put(task['output'], output.read_bytes())
        return tag, count
    finally:
        stop.set()
        reporter.join()
        put(task['log'], log.read_bytes())
        if failures:
            print({'task': tag, 'transient_log_uploads': failures}, flush=True)
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def worker(job, manifest_url, cores=None):
    global _FC, _INFO
    from app.disputes import pool

    fetch(manifest_url, 'refresh-manifest.json')
    _INFO = json.loads(Path('refresh-manifest.json').read_text())
    if _INFO['version'] != version():
        raise RuntimeError('Refresh worker differs from prepared calculation code')
    fetch(_INFO['control'], 'refresh-control.pkl')
    with open('refresh-control.pkl', 'rb') as fh:
        _FC = pool.forecaster(_INFO['run'], pickle.load(fh))
    tasks = _INFO['jobs'][job]
    cores = cores or (os.cpu_count() or 1)
    if hasattr(os, 'sysconf'):
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        cores = min(cores, max(1, memory // (2 * 1024**3)))
    print({'job': job, 'cores': cores, 'batches': len(tasks), 'jev_started': False}, flush=True)
    if cores == 1:
        for task in tasks:
            print({'completed': _one(task)}, flush=True)
    else:
        with multiprocessing.get_context('fork').Pool(cores) as processes:
            for done in processes.imap_unordered(_one, tasks, chunksize=1):
                print({'completed': done}, flush=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'worker':
        worker(int(sys.argv[2]), os.environ['REFRESH_MANIFEST_URL'],
               int(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif sys.argv[1] == 'collect':
        import boto3
        collect(boto3.client('s3'), sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'monitor':
        import boto3
        monitor(boto3.client('s3'), *sys.argv[2:])
    else:
        raise SystemExit('usage: python -m tools.question_refresh_fleet worker JOB [CORES]')

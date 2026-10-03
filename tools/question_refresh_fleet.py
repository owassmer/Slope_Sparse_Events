"""Distribute globally unique refresh requests; retain engine caches across batches."""
from __future__ import annotations

import concurrent.futures
import contextlib
import gzip
import hashlib
import http.client
import json
import multiprocessing
import os
import pickle
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
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
    populations = json.loads((folder / 'populations.json').read_text())
    if populations['histories'] != prepared['histories']:
        raise ValueError('Required draw populations do not cover the saved preparation')
    costs = json.loads((folder / 'balanced.json').read_text())
    inputs = []
    identity = hashlib.sha256(Path(control).read_bytes())
    for instance, item in sorted(prepared['instances'].items()):
        for source in sorted((folder / item['folder'] / 'balanced').glob('*.pkl.gz')):
            tag = f'{item["folder"]}-{source.name.removesuffix(".pkl.gz")}'
            identity.update(pickle.dumps((instance, tag), protocol=5))
            identity.update(hashlib.sha256(source.read_bytes()).digest())
            inputs.append((source, instance, tag))
    preparation = identity.hexdigest()
    base = f'{prefix}/{version()}/{preparation[:24]}'

    def url(operation, key, **params):
        return s3.generate_presigned_url(operation, Params={'Bucket': bucket, 'Key': key, **params}, ExpiresIn=21600)

    control_key = f'{base}/control.pkl'
    s3.upload_file(str(control), bucket, control_key)
    info = {'run': prepared['run'], 'version': version(), 'preparation': preparation,
            'control': url('get_object', control_key), 'github_jobs': github_jobs,
            'workers': github_jobs + aws_jobs, 'tasks': [],
            'pending': url('get_object', f'{base}/pending.json')}
    transfers = []
    for source, instance, tag in sorted(inputs, key=lambda x: (-costs[x[2]], x[2])):
        key = f'{base}/inputs/{tag}.pkl.gz'
        transfers.append((source, key))
        info['tasks'].append({'tag': tag, 'instance': instance, 'input': url('get_object', key),
                              'output': url('put_object', f'{base}/outputs/{tag}.pkl.gz'),
                              'existing': url('get_object', f'{base}/outputs/{tag}.pkl.gz'),
                              'claim': url('put_object', f'{base}/claims/{tag}.json', IfNoneMatch='*'),
                              'release': url('delete_object', f'{base}/claims/{tag}.json'),
                              'log': url('put_object', f'{base}/logs/{tag}.log')})
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(s3.upload_file, str(source), bucket, key) for source, key in transfers]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    manifest = f'{base}/manifest.json.gz'
    s3.put_object(Bucket=bucket, Key=f'{base}/pending.json',
                  Body=json.dumps({'remaining': len(info['tasks']),
                                   'available': [t['tag'] for t in info['tasks']]}).encode())
    s3.put_object(Bucket=bucket, Key=manifest, Body=gzip.compress(json.dumps(info).encode(), compresslevel=1))
    (folder / 'fleet.json').write_text(json.dumps({'base': base, 'tasks': len(inputs), 'version': version(),
                                                'preparation': preparation}))
    return url('get_object', manifest)


def downloaded_outputs(s3, bucket, keys, folder, workers=8):
    """Overlap transfer latency with ingestion, with a bounded disk buffer."""
    keys = iter(keys)
    with tempfile.TemporaryDirectory(prefix='collect-', dir=folder) as temporary, \
            concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {}

        def submit():
            key = next(keys, None)
            if key is None:
                return
            target = Path(temporary) / key.rsplit('/', 1)[-1]
            future = executor.submit(s3.download_file, bucket, key, str(target))
            pending[future] = key, target

        for _ in range(workers * 2):
            submit()
        while pending:
            ready, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in ready:
                key, target = pending.pop(future)
                future.result()
                yield key, target
                target.unlink()
                submit()


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
        for source in (folder / item['folder'] / 'balanced').glob('*.pkl.gz'):
            expected[f'{item["folder"]}-{source.name.removesuffix(".pkl.gz")}'] = iid
    done = {tag for store in stores.values() for tag, in store.db.execute('SELECT tag FROM ingested_batches')}
    if not done <= expected.keys() or len(expected) != fleet['tasks']:
        raise ValueError('Refresh result assignment differs from preparation')
    prefix = fleet['base'] + '/outputs/'
    last_progress = 0

    def report():
        nonlocal last_progress
        last_progress = time.time()
        progress = {'stage': 'refresh_question_calculations', 'completed_batches': len(done),
                    'total_batches': len(expected), 'time': last_progress, 'jev_started': False}
        (folder / 'progress.json').write_text(json.dumps(progress))
        s3.put_object(Bucket=bucket, Key=f'{fleet["base"]}/progress.json', Body=json.dumps(progress).encode())
        print(progress, flush=True)

    while len(done) < len(expected):
        available = {o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix)
                     for o in page.get('Contents', [])}
        waiting = []
        for key in sorted(available):
            tag = key.removeprefix(prefix).removesuffix('.pkl.gz')
            if tag not in expected:
                raise ValueError(f'Unassigned refresh output {tag}')
            if tag in done:
                continue
            waiting.append(key)
        with contextlib.closing(downloaded_outputs(s3, bucket, waiting, folder)) as downloads:
            for key, target in downloads:
                tag = key.removeprefix(prefix).removesuffix('.pkl.gz')
                store = stores[expected[tag]]
                store.ingest(target, expected[tag])
                with store.db:
                    store.db.execute('INSERT INTO ingested_batches VALUES(?)', (tag,))
                done.add(tag)
                if time.time() - last_progress >= 10:
                    report()
        active = release_stale(s3, bucket, fleet['base'], done)
        s3.put_object(Bucket=bucket, Key=f'{fleet["base"]}/pending.json',
                      Body=json.dumps({'remaining': len(expected) - len(done),
                                       'available': sorted(expected.keys() - done - active)}).encode())
        report()
        if len(done) < len(expected):
            time.sleep(15)
    progress = {'stage': 'refresh_question_calculations', 'completed_batches': len(done),
                'total_batches': len(expected), 'time': time.time(), 'jev_started': False}
    s3.put_object(Bucket=bucket, Key=f'{fleet["base"]}/pending.json',
                  Body=json.dumps({'remaining': 0, 'available': []}).encode())
    (folder / 'progress.json').write_text(json.dumps(progress))
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


def release_stale(s3, bucket, base, done, age=300):
    """Recover dead processes without taking a batch away from a live heartbeat."""
    from botocore.exceptions import ClientError

    active = set()
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'{base}/claims/'):
        for item in page.get('Contents', []):
            tag = item['Key'].rsplit('/', 1)[-1].removesuffix('.json')
            if tag in done:
                continue
            active.add(tag)
            if time.time() - item['LastModified'].timestamp() < age:
                continue
            try:
                log = s3.head_object(Bucket=bucket, Key=f'{base}/logs/{tag}.log')
                if time.time() - log['LastModified'].timestamp() < age:
                    continue
            except ClientError as error:
                if error.response['Error']['Code'] not in ('404', 'NoSuchKey'):
                    raise
            # Conditional deletion cannot remove a replacement owner's claim.
            try:
                s3.delete_object(Bucket=bucket, Key=item['Key'], IfMatch=item['ETag'])
                active.discard(tag)
            except ClientError as error:
                if error.response['Error']['Code'] not in ('PreconditionFailed', 'NoSuchKey', '404'):
                    raise

    return active


def queue_request(request, *, json_body=False):
    """Retry transient queue transport failures without weakening conditional writes."""
    for attempt in range(5):
        try:
            response = urllib.request.urlopen(request, timeout=30)
            if json_body:
                with response:
                    return json.load(response)
            return response
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            error.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.RemoteDisconnected, http.client.IncompleteRead):
            if attempt == 4:
                raise
        time.sleep(2 ** attempt)


def claim(task):
    body = json.dumps({'owner': uuid.uuid4().hex, 'time': time.time()}).encode()
    request = urllib.request.Request(task['claim'], data=body, method='PUT', headers={'If-None-Match': '*'})
    try:
        with queue_request(request) as response:
            return response.headers['ETag']
    except urllib.error.HTTPError as error:
        if error.code in (409, 412):
            return None
        raise


def release(task, etag):
    request = urllib.request.Request(task['release'], method='DELETE', headers={'If-Match': etag})
    try:
        with queue_request(request):
            pass
    except urllib.error.HTTPError as error:
        if error.code not in (404, 412):
            raise


def _consume(slot):
    """Every free process can claim work from any runner's preferred range."""
    tasks = _INFO['tasks']
    workers = _INFO['workers']
    # Start near this runner's preferred range; all tasks remain claimable.
    start = len(tasks) * slot // workers % max(1, len(tasks))
    ordered = tasks[start:] + tasks[:start]
    while True:
        state = queue_request(_INFO['pending'], json_body=True)
        if not state['remaining']:
            return
        pending = set(state['available'])
        for task in ordered:
            if task['tag'] not in pending:
                continue
            etag = claim(task)
            if etag is None:
                continue
            try:
                print({'completed': _one(task)}, flush=True)
                break
            except Exception:
                release(task, etag)
                raise
        else:
            # Busy batches stay owned; the collector recovers a dead process.
            time.sleep(15)


def _consume_logged(slot):
    try:
        return _consume(slot)
    except Exception as error:
        # HTTPError contains an unpicklable response. Preserve its traceback in
        # the worker log before sending a simple failure across the process pool.
        traceback.print_exc()
        raise RuntimeError(f'Worker failed: {type(error).__name__}') from None


def _one(task):
    tag = task['tag']
    source, output, log = (Path(tag + suffix) for suffix in ('.input.gz', '.output.gz', '.log'))
    if fetch(task['existing'], output, missing_ok=True):
        output.unlink()
        return tag, 'preserved'
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
        put(task['log'], log.read_bytes())
        fetch(task['input'], source)
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
    _INFO = json.loads(gzip.decompress(Path('refresh-manifest.json').read_bytes()))
    if _INFO['version'] != version():
        raise RuntimeError('Refresh worker differs from prepared calculation code')
    fetch(_INFO['control'], 'refresh-control.pkl')
    with open('refresh-control.pkl', 'rb') as fh:
        _FC = pool.forecaster(_INFO['run'], pickle.load(fh))
    tasks = _INFO['tasks']
    cores = cores or (os.cpu_count() or 1)
    if hasattr(os, 'sysconf'):
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        cores = min(cores, max(1, memory // (2 * 1024**3)))
    print({'job': job, 'cores': cores, 'batches': len(tasks), 'jev_started': False}, flush=True)
    if cores == 1:
        _consume_logged(job)
    else:
        with multiprocessing.get_context('fork').Pool(cores) as processes:
            # A process takes a new batch as soon as its previous one is published.
            for _ in processes.imap_unordered(_consume_logged, [job] * cores, chunksize=1):
                pass


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

"""Run existing whole walk shards from an S3 queue; publish one complete result per shard.

AWS and GitHub workers use the same queue. Each attempt owns its output prefix. A conditional
completion write chooses one complete attempt, so retries never combine duplicate path events.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

RUN = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'
REPO = 'owassmer/Slope_Sparse_Events'


def read(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    except ClientError as e:
        if e.response['Error']['Code'] in ('NoSuchKey', '404'):
            return None
        raise


def create(s3, bucket, key, value):
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(value).encode(), IfNoneMatch='*')
        return True
    except ClientError as e:
        if e.response['Error']['Code'] in ('PreconditionFailed', 'ConditionalRequestConflict'):
            return False
        raise


def publish(s3, bucket, prefix, job, attempt, split, log):
    dest = f'{prefix}/attempts/{job}/{attempt}'
    for name in ['control', *(f'rows{i}' for i in range(16))]:
        path = split / name
        with tempfile.NamedTemporaryFile(suffix='.tgz') as f:
            with tarfile.open(fileobj=f, mode='w:gz', compresslevel=1) as tar:
                tar.add(path, arcname='.')
            f.flush()
            s3.upload_file(f.name, bucket, f'{dest}/{name}.tgz')
    s3.upload_file(str(log), bucket, f'{dest}/walk.log')
    create(s3, bucket, f'{prefix}/done/{job}.json', {'job': job, 'source': dest, 'finished': time.time()})
    print(f'published shard {job}: {dest}', flush=True)


def worker(bucket, prefix, slots, minutes):
    s3 = boto3.client('s3')
    config = read(s3, bucket, f'{prefix}/config.json')
    stop = time.monotonic() + 60 * minutes
    checkpoint_stop = threading.Event()
    active = {}
    active_lock = threading.Lock()

    def checkpoint():
        while not checkpoint_stop.wait(180):
            with active_lock:
                paths = list(active.items())
            for attempt, (job, work) in paths:
                try:
                    for path in (work / 'out').glob('part*.stream'):
                        s3.upload_file(str(path), bucket, f'{prefix}/recovery/{job}/{attempt}/{path.name}')
                    s3.put_object(Bucket=bucket, Key=f'{prefix}/heartbeat/{job}.json',
                                  Body=json.dumps({'attempt': attempt, 'time': time.time()}).encode())
                except (OSError, ClientError) as e:
                    print(f'checkpoint {job}: {e}', flush=True)

    thread = threading.Thread(target=checkpoint, daemon=True)
    thread.start()

    def slot():
        # boto3 clients are thread safe; subprocesses keep the walk's monkey patches isolated.
        for job in config['jobs']:
            if time.monotonic() >= stop:
                break
            if read(s3, bucket, f'{prefix}/done/{job}.json'):
                continue
            attempt = uuid.uuid4().hex
            if not create(s3, bucket, f'{prefix}/claims/{job}.json',
                          {'attempt': attempt, 'started': time.time(), 'host': os.uname().nodename}):
                continue
            work = Path(tempfile.mkdtemp(prefix=f'walk-{job}-'))
            with active_lock:
                active[attempt] = (job, work)
            print(f'start shard {job}: {attempt}', flush=True)
            try:
                env = {**os.environ, 'SLOPE_JEV_CACHE_ONLY': '1', 'SLOPE_WALK_CUT': '10',
                       'SLOPE_WALK_MINUTES': '0', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
                env.pop('CLAUDE_CODE_OAUTH_TOKEN', None)
                log = work / 'walk.log'
                with log.open('wb') as fh:
                    subprocess.run(['.venv/bin/python', '-m', 'app.disputes.parallel', RUN,
                                    str(job), '100', '4', str(work / 'out')], env=env, stdout=fh,
                                   stderr=subprocess.STDOUT, check=True, timeout=max(60, stop-time.monotonic()))
                    subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'split',
                                    str(work / 'out'), str(work / 'split')], env=env, stdout=fh,
                                   stderr=subprocess.STDOUT, check=True)
                publish(s3, bucket, prefix, job, attempt, work / 'split', log)
            except Exception:
                # Retain recovery streams before releasing the claim; a retry walks a whole shard.
                for path in work.rglob('*'):
                    if path.is_file():
                        s3.upload_file(str(path), bucket, f'{prefix}/failed/{job}/{attempt}/{path.relative_to(work)}')
                s3.delete_object(Bucket=bucket, Key=f'{prefix}/claims/{job}.json')
                raise
            finally:
                with active_lock:
                    active.pop(attempt, None)
                shutil.rmtree(work)
    with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as ex:
        futures = [ex.submit(slot) for _ in range(slots)]
        for f in futures:
            f.result()
    checkpoint_stop.set()
    thread.join()


def export_github(bucket, prefix, run, minutes):
    s3 = boto3.client('s3')
    stop = time.monotonic() + 60 * minutes
    while time.monotonic() < stop:
        jobs = json.loads(subprocess.check_output(['gh', 'api',
            f'repos/{REPO}/actions/runs/{run}/jobs?per_page=100']))['jobs']
        for j in jobs:
            if j['conclusion'] != 'success':
                continue
            job = int(j['name'].split('(')[1].split(')')[0])
            if read(s3, bucket, f'{prefix}/done/{job}.json'):
                continue
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                subprocess.run(['gh', 'run', 'download', str(run), '-R', REPO,
                                '-p', f'*-j{job}-w0', '-D', folder], check=True)
                split = root / 'split'
                for name, artifact in [('control', f'control-j{job}-w0'),
                                      *((f'rows{i}', f'rows-b{i}-j{job}-w0') for i in range(16))]:
                    target = split / name
                    target.mkdir(parents=True)
                    for part in (root / artifact).rglob('part*.pkl'):
                        shutil.copy2(part, target / part.name)
                log = next((root / f'control-j{job}-w0').rglob('walk.log'))
                publish(s3, bucket, prefix, job, f'github-{run}', split, log)
        done = s3.list_objects_v2(Bucket=bucket, Prefix=f'{prefix}/done/').get('KeyCount', 0)
        print(f'completed {done}/100 shards', flush=True)
        if done == 100:
            return
        time.sleep(45)



def fetch_group(s3, bucket, source, name, target):
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.tgz') as f:
        s3.download_file(bucket, f'{source}/{name}.tgz', f.name)
        with tarfile.open(f.name) as tar:
            tar.extractall(target, filter='data')


def upload_group(s3, bucket, key, path):
    with tempfile.NamedTemporaryFile(suffix='.tgz') as f:
        with tarfile.open(fileobj=f, mode='w:gz', compresslevel=1) as tar:
            tar.add(path, arcname='.')
        f.flush()
        s3.upload_file(f.name, bucket, key)


def pool(bucket, prefix, slots):
    """Pool exactly one selected attempt per shard; keep all large intermediates in AWS."""
    s3 = boto3.client('s3')
    selected = [read(s3, bucket, f'{prefix}/done/{j}.json') for j in range(100)]
    if any(x is None for x in selected):
        raise RuntimeError('pool requires all 100 shard outputs')
    root = Path('/opt/slope-pool')
    root.mkdir(exist_ok=True)
    (root / 'selection.json').write_text(json.dumps(selected, indent=2))
    s3.upload_file(str(root / 'selection.json'), bucket, f'{prefix}/pool/selection.json')
    env = {**os.environ, 'SLOPE_JEV_CACHE_ONLY': '1', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    env.pop('CLAUDE_CODE_OAUTH_TOKEN', None)

    def download(name, target):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(fetch_group, s3, bucket, x['source'], name, target / str(x['job']))
                       for x in selected]
            for f in futures:
                f.result()

    download('control', root / 'control')
    subprocess.run(['.venv/bin/python', '-m', 'app.disputes.parallel', 'spill',
                    str(root / 'control'), str(root / 'walk_roots.pkl')], check=True, env=env)
    import pickle
    with (root / 'walk_roots.pkl').open('rb') as f:
        missing = pickle.load(f)
    if missing:
        s3.upload_file(str(root / 'walk_roots.pkl'), bucket, f'{prefix}/pool/walk_roots.pkl')
        raise RuntimeError(f'{len(missing)} segments require recovery; no partial model will be published')
    subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'control',
                    str(root / 'control'), str(root / 'ctl')], check=True, env=env)
    with (root / 'ctl/control.pkl').open('rb') as f:
        ctl = pickle.load(f)
    if ctl['raised']:
        raise RuntimeError(f"{len(ctl['raised'])} offering continuations require a further walk")
    s3.upload_file(str(root / 'ctl/control.pkl'), bucket, f'{prefix}/pool/control.pkl')
    upload_group(s3, bucket, f'{prefix}/pool/paths.tgz', root / 'ctl/paths')

    def facts(b):
        folder = root / f'rows{b}'
        download(f'rows{b}', folder)
        out = root / f'states{b}'
        subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'facts', RUN, str(folder),
                        str(root / 'ctl/control.pkl'), str(b), str(out)], check=True, env=env)
        upload_group(s3, bucket, f'{prefix}/pool/states{b}.tgz', out)
        shutil.rmtree(folder)
        print(f'facts bucket {b} published', flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as ex:
        futures = [ex.submit(facts, b) for b in range(16)]
        for f in futures:
            f.result()
    create(s3, bucket, f'{prefix}/pool/ready.json',
           {'finished': time.time(), 'paths': ctl['paths'], 'walked': ctl['walked'], 'nodes': len(ctl['nodes'])})


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['worker', 'export', 'pool'])
    p.add_argument('bucket')
    p.add_argument('prefix')
    p.add_argument('--slots', type=int, default=1)
    p.add_argument('--minutes', type=int, default=300)
    p.add_argument('--run', default='36781427817')
    a = p.parse_args()
    if a.mode == 'worker':
        worker(a.bucket, a.prefix, a.slots, a.minutes)
    elif a.mode == 'export':
        export_github(a.bucket, a.prefix, a.run, a.minutes)
    else:
        pool(a.bucket, a.prefix, a.slots)

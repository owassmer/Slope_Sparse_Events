"""Subdivide unfinished walk segments while retaining the existing shard checkpoints."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pickle
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from cloud_walk import RUN, create, fetch_group, publish, read, upload_group

PARTITIONS = 24
DEPTH = 4


def objects(s3, bucket, prefix):
    return [o for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix)
            for o in page.get('Contents', [])]


def prepare(bucket, prefix, job):
    from app.disputes import parallel, pool

    s3 = boto3.client('s3')
    queue = prefix + '/refine-v1'
    if read(s3, bucket, f'{prefix}/done/{job}.json'):
        print(f'shard {job} already complete', flush=True)
        return
    if read(s3, bucket, f'{queue}/plans/{job}.json'):
        return
    with tempfile.TemporaryDirectory(prefix=f'refine-prepare-{job}-') as tmp:
        root = Path(tmp)
        claim = read(s3, bucket, f'{prefix}/claims/{job}.json')
        recovery = f"{prefix}/recovery/{job}/{claim['attempt']}" if claim else None
        files = objects(s3, bucket, recovery + '/') if recovery else []
        if not files:
            config = read(s3, bucket, f'{prefix}/config.json')
            recovery = config.get('recoveries', {}).get(str(job))
            files = objects(s3, bucket, recovery + '/') if recovery else []
        if not files:
            raise RuntimeError(f'no recovery checkpoint for {job}; refusing to restart whole shard')
        parts = root / 'parts'
        parts.mkdir()
        for obj in files:
            if obj['Key'].endswith('.stream') and obj['Size']:
                s3.download_file(bucket, obj['Key'], str(parts / Path(obj['Key']).name))
        restored = parallel.load_parts(str(parts))
        done = {key for p in restored for key in p['done']}
        template = read(s3, bucket, f'{prefix}/done/8.json')
        fetch_group(s3, bucket, template['source'], 'control', root / 'topology')
        with next((root / 'topology').glob('part*.pkl')).open('rb') as f:
            topology = pickle.load(f)
        expected = {(clock, 0, number) for number, clock, owner in topology['segs'] if owner % 100 == job}
        if not done <= expected:
            raise RuntimeError(f'checkpoint contains foreign segments for {job}')
        missing = sorted(expected - done)
        pool.split(str(parts), str(root / 'split'))
        # Base checkpoints are immutable for this refinement; later original completions
        # still win the normal conditional shard publication.
        publish(s3, bucket, queue + '/base', job, 'checkpoint', root / 'split', Path('/dev/null'))
        plan = {'job': job, 'roots': missing, 'saved': len(done), 'partitions': PARTITIONS,
                'depth': DEPTH, 'recovery': recovery}
        create(s3, bucket, f'{queue}/plans/{job}.json', plan)
        print(json.dumps(plan), flush=True)


def task(bucket, prefix, plan, index):
    s3 = boto3.client('s3')
    queue, job = prefix + '/refine-v1', plan['job']
    ident = f'{job}-{index}'
    if read(s3, bucket, f'{prefix}/done/{job}.json') or read(s3, bucket, f'{queue}/done/{ident}.json'):
        return
    claim_key = f'{queue}/claims/{ident}.json'
    previous = read(s3, bucket, claim_key)
    if previous:
        if time.time() - previous['started'] < 300:
            return
        # An expired attempt may still finish: it has a separate immutable output
        # prefix and can win only the normal conditional completion write.
        response = s3.get_object(Bucket=bucket, Key=claim_key)
        if time.time() - json.loads(response['Body'].read())['started'] < 300:
            return
        try:
            s3.delete_object(Bucket=bucket, Key=claim_key, IfMatch=response['ETag'])
        except ClientError as error:
            if error.response['Error']['Code'] in ('PreconditionFailed', 'NoSuchKey'):
                return
            raise
    attempt = uuid.uuid4().hex
    claim = {'started': time.time(), 'host': os.uname().nodename, 'attempt': attempt}
    if not create(s3, bucket, claim_key, claim):
        return
    with tempfile.TemporaryDirectory(prefix=f'refine-{ident}-') as tmp:
        root = Path(tmp)
        roots = root / 'roots.pkl'
        roots.write_bytes(pickle.dumps([tuple(x) for x in plan['roots']]))
        env = {**os.environ, 'SLOPE_JEV_CACHE_ONLY': '1', 'SLOPE_WALK_CUT': '10',
               'SLOPE_WALK_ROOTS': str(roots), 'SLOPE_WALK_MINUTES': '0',
               'SLOPE_WALK_REFINE': f"{index}/{plan['partitions']}/{plan['depth']}",
               'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
        env.pop('CLAUDE_CODE_OAUTH_TOKEN', None)
        log = root / 'walk.log'
        command = ['.venv/bin/python', '-m', 'app.disputes.parallel', RUN, '0', '1', '1',
                   str(root / 'out'), str(100000 + job * 100 + index)]
        print(f'start subdivision {ident}: {len(plan["roots"])} parent segments', flush=True)
        process = None
        stopped = threading.Event()
        errors = []

        def heartbeat():
            while not stopped.wait(15):
                try:
                    if log.exists():
                        s3.upload_file(str(log), bucket, f'{queue}/live/{ident}.log')
                    response = s3.get_object(Bucket=bucket, Key=claim_key)
                    owner = json.loads(response['Body'].read())
                    if owner.get('attempt') != attempt:
                        raise RuntimeError(f'subdivision {ident} lease was replaced')
                    claim['started'] = time.time()
                    s3.put_object(Bucket=bucket, Key=claim_key, Body=json.dumps(claim).encode(),
                                  IfMatch=response['ETag'])
                except Exception as error:
                    errors.append(error)
                    return
        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        try:
            with log.open('wb') as f:
                process = subprocess.Popen(command, env=env, stdout=f, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                while process.poll() is None:
                    time.sleep(1)
                    if errors:
                        raise errors[0]
                if process.returncode:
                    raise RuntimeError(f'subdivision {ident} exited {process.returncode}')
            subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'split',
                            str(root / 'out'), str(root / 'split')], env=env, check=True)
            if errors:
                raise errors[0]
            publish(s3, bucket, queue, ident, attempt, root / 'split', log)
        finally:
            stopped.set()
            pulse.join()
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            try:
                response = s3.get_object(Bucket=bucket, Key=claim_key)
                owner = json.loads(response['Body'].read())
                if owner.get('attempt') == attempt:
                    s3.delete_object(Bucket=bucket, Key=claim_key, IfMatch=response['ETag'])
            except ClientError as error:
                if error.response['Error']['Code'] not in ('PreconditionFailed', 'NoSuchKey'):
                    raise


def worker(bucket, prefix, cores, first, last):
    s3 = boto3.client('s3')
    plans = [read(s3, bucket, o['Key']) for o in objects(s3, bucket, prefix + '/refine-v1/plans/')]
    # Larger estimated tasks start first; every free core claims from the same queue.
    tasks = sorted(((p, i) for p in plans if p['roots']
                    for i in range(first, min(last, p['partitions']))),
                   key=lambda x: x[0].get('estimated_histories', 0) / x[0]['partitions'], reverse=True)
    fresh = bool(plans) and all(p.get('fresh') for p in plans)
    while True:
        with concurrent.futures.ThreadPoolExecutor(max_workers=cores) as ex:
            futures = [ex.submit(task, bucket, prefix, p, i) for p, i in tasks]
            for f in concurrent.futures.as_completed(futures):
                try:
                    f.result()
                except Exception as error:
                    if not fresh:
                        raise
                    print(f'partition retry required: {error}', flush=True)
        if not fresh:
            return
        done = {Path(o['Key']).stem for o in objects(s3, bucket, prefix + '/refine-v1/done/')}
        tasks = [(p, i) for p, i in tasks if f"{p['job']}-{i}" not in done]
        if not tasks:
            return
        print(f'waiting/retrying {len(tasks)} unfinished partitions', flush=True)
        time.sleep(15)


def assemble(bucket, prefix, job):
    from app.disputes import parallel

    s3 = boto3.client('s3')
    queue = prefix + '/refine-v1'
    if read(s3, bucket, f'{prefix}/done/{job}.json'):
        return True
    plan = read(s3, bucket, f'{queue}/plans/{job}.json')
    if plan is None:
        return False
    selected = [read(s3, bucket, f'{queue}/base/done/{job}.json')]
    if plan['roots']:
        selected += [read(s3, bucket, f'{queue}/done/{job}-{i}.json') for i in range(plan['partitions'])]
    if any(x is None for x in selected):
        return False
    with tempfile.TemporaryDirectory(prefix=f'refine-assemble-{job}-') as tmp:
        root = Path(tmp)
        dest = f'{prefix}/attempts/{job}/refined-v1'
        for group in ['control', *(f'rows{i}' for i in range(16))]:
            folder = root / group
            for item in selected:
                fetch_group(s3, bucket, item['source'], group, folder)
            if group == 'control':
                parts = parallel.load_parts(str(folder))
                if not any(p['complete'] for p in parts):
                    template = read(s3, bucket, f'{prefix}/done/8.json')
                    fetch_group(s3, bucket, template['source'], 'control', root / 'topology')
                    with next((root / 'topology').glob('part*.pkl')).open('rb') as f:
                        topology = pickle.load(f)
                    parts.append(topology)  # coverage only; do not copy shard 8's events
                missing = parallel.missing_segments(parts)
                full = next(p for p in parts if p['complete'])
                expected = {tuple(k) for k in plan['roots']} if plan.get('fresh') else {
                    (c, 0, n) for n, c, owner in full['segs'] if owner % 100 == job}
                if expected & set(missing):
                    raise RuntimeError(f'incomplete refined shard {job}')
            if plan.get('fresh') and job != 0:
                (folder / 'part0.pkl').unlink()  # publish the shared top only in group zero
            upload_group(s3, bucket, f'{dest}/{group}.tgz', folder)
            shutil.rmtree(folder)
        create(s3, bucket, f'{prefix}/done/{job}.json',
               {'job': job, 'source': dest, 'finished': time.time(), 'refined': True})
        print(f'assembled shard {job}', flush=True)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'worker', 'assemble'])
    parser.add_argument('bucket')
    parser.add_argument('prefix')
    parser.add_argument('--job', type=int)
    parser.add_argument('--cores', type=int, default=4)
    parser.add_argument('--first', type=int, default=0)
    parser.add_argument('--last', type=int, default=PARTITIONS)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.bucket, args.prefix, args.job)
    elif args.action == 'worker':
        worker(args.bucket, args.prefix, args.cores, args.first, args.last)
    else:
        while not assemble(args.bucket, args.prefix, args.job):
            time.sleep(30)

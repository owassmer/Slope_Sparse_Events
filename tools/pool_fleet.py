"""Distribute independent Jev state construction after global row ordering/deduplication.

AWS prepares each original bucket once. GitHub schedules 240 balanced pieces on up to eighty runners; AWS consumes 16 pieces. Only signed object URLs reach GitHub.
"""
from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.disputes import pool

PARTITIONS = 16
_FC = None



def state_version():
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(b''.join((root / 'app/disputes' / name).read_bytes()
                                   for name in ('state14.py', 'forecast.py', 'pool.py'))).hexdigest()[:16]

def balanced_questions(keys, facts, count):
    import heapq
    groups = [[] for _ in range(count)]
    loads = [(0, i) for i in range(count)]
    heapq.heapify(loads)
    for key in sorted(keys, key=lambda k: (-len(facts.get(k, ())), k)):
        cost, i = heapq.heappop(loads)
        groups[i].append(key)
        heapq.heappush(loads, (cost + max(1, len(facts.get(key, ()))), i))
    return groups


def _states(keys):
    return pool.question_states(_FC, keys)


def combine(results):
    out = {'states': {}, 'errors': {}, 'differ': [], 'never_live': []}
    seen = set()
    for value in results:
        keys = set(value['states']) | set(value['errors']) | set(value['never_live'])
        if seen & keys:
            raise RuntimeError('overlapping question partitions')
        seen.update(keys)
        for name in ('states', 'errors'):
            out[name].update(value[name])
        for name in ('differ', 'never_live'):
            out[name].extend(value[name])
    return out


def prepare(folder, ctl_file, b, out):
    with open(ctl_file, 'rb') as fh:
        ctl = pickle.load(fh)
    facts = pool.bucket_facts(folder, ctl)
    mine = sorted(k for k in ctl['nodes'] if pool.bucket(k) == b and k not in ctl['classed'])
    Path(out).mkdir(parents=True, exist_ok=True)
    for p, keys in enumerate(balanced_questions(mine, facts, PARTITIONS)):
        with gzip.open(Path(out) / f'{b}-{p}.pkl.gz', 'wb', compresslevel=1) as fh:
            pickle.dump({'keys': keys, 'facts': {k: facts[k] for k in keys if k in facts}}, fh,
                        protocol=pickle.HIGHEST_PROTOCOL)
    print(f'prepared bucket {b}: {len(mine)} questions in {PARTITIONS} partitions', flush=True)


def build(run, ctl_file, bundle, out, cores):
    global _FC
    with open(ctl_file, 'rb') as fh:
        ctl = pickle.load(fh)
    with gzip.open(bundle, 'rb') as fh:
        data = pickle.load(fh)
    _FC = pool.forecaster(run, ctl)
    _FC.facts = data['facts']
    # More chunks than cores balance question-state costs while sharing immutable rows through fork.
    keys = sorted(data['keys'], key=lambda k: len(_FC.facts.get(k, ())), reverse=True)
    chunks = balanced_questions(keys, _FC.facts, min(len(keys), cores * 8) or 1)
    if cores == 1:
        result = combine(map(_states, chunks))
    else:
        with multiprocessing.get_context('fork').Pool(cores) as workers:
            result = combine(workers.imap_unordered(_states, chunks, chunksize=1))
    expected = set(data['keys'])
    if result['errors'] or result['differ'] or set(result['states']) & set(result['never_live']) \
            or set(result['states']) | set(result['never_live']) != expected:
        raise RuntimeError(f'question partition invalid: {len(result["errors"])} errors, '
                           f'{len(result["states"])} states, {len(result["never_live"])} never live, '
                           f'{len(expected)} expected; examples: {list(result["errors"].items())[:3]}')
    with gzip.open(out, 'wt', compresslevel=1) as fh:
        json.dump(result, fh, default=str)
    print(f'built {len(expected)} questions on {cores} cores', flush=True)


def manifest(s3, bucket, prefix, identity):
    base = f'{prefix}/pool/fleet-v3'
    outputs = f'{base}/outputs/{state_version()}'

    def url(operation, key):
        return s3.generate_presigned_url(operation, Params={'Bucket': bucket, 'Key': key}, ExpiresIn=21600)

    value = {'identity': identity, 'state_version': state_version(), 'run': 'akoustis_20240514-agent_plus_jev-20260929T052558Z',
             'control': url('get_object', f'{prefix}/pool/control.pkl'), 'tasks': []}
    for b in range(16):
        for p in range(PARTITIONS - 1):
            value['tasks'].append({'bucket': b, 'partition': p,
                                  'input': url('get_object', f'{base}/inputs/{b}-{p}.pkl.gz'),
                                  'output': url('put_object', f'{outputs}/{b}-{p}.json.gz'),
                                  'existing': url('get_object', f'{outputs}/{b}-{p}.json.gz'),
                                  'log': url('put_object', f'{base}/logs/{state_version()}/{b}-{p}.log')})
    key = f'{base}/manifest-{state_version()}.json'
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(value).encode())
    # This pointer stays in private S3 and is passed through a masked GitHub secret by the launcher.
    s3.put_object(Bucket=bucket, Key=f'{base}/manifest-url.txt', Body=url('get_object', key).encode())


def fetch(url, path, wait=False, missing_ok=False):
    deadline = time.monotonic() + (18000 if wait else 120)
    while True:
        try:
            with urllib.request.urlopen(url, timeout=120) as response, open(path, 'wb') as fh:
                import shutil
                shutil.copyfileobj(response, fh)
            return True
        except urllib.error.HTTPError as error:
            if error.code == 404 and missing_ok:
                return False
            retry = error.code in (429, 500, 502, 503, 504) or (error.code == 404 and wait)
            if not retry or time.monotonic() >= deadline:
                raise RuntimeError(f'input download failed: HTTP {error.code}') from None
            time.sleep(15)
        except (urllib.error.URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError('input download failed after retries') from None
            time.sleep(15)


def put(url, data):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, data=data, method='PUT')
            with urllib.request.urlopen(request, timeout=180) as response:
                response.read()
            return
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise RuntimeError('result upload failed') from None
            time.sleep(5)


def github(worker):
    fetch(os.environ['POOL_MANIFEST_URL'], 'fleet-manifest.json')
    info = json.loads(Path('fleet-manifest.json').read_text())
    if info['state_version'] != state_version():
        raise RuntimeError('worker question-state code differs from coordinator')
    fetch(info['control'], 'fleet-control.pkl', wait=True)
    for task in [info['tasks'][worker]]:
        tag = f'{task["bucket"]}-{task["partition"]}'
        if fetch(task['existing'], f'{tag}.json.gz', missing_ok=True):
            print(f'reusing completed question partition {tag}', flush=True)
            continue
        print(f'waiting for question partition {tag}', flush=True)
        fetch(task['input'], f'{tag}.pkl.gz', wait=True)
        log = Path(f'{tag}.log')
        try:
            with log.open('w') as fh:
                subprocess.run([sys.executable, __file__, 'build', info['run'], 'fleet-control.pkl',
                                f'{tag}.pkl.gz', f'{tag}.json.gz', str(os.cpu_count() or 4)],
                               stdout=fh, stderr=subprocess.STDOUT, check=True)
            put(task['output'], Path(f'{tag}.json.gz').read_bytes())
        finally:
            put(task['log'], log.read_bytes())
        Path(f'{tag}.pkl.gz').unlink()
        print(f'published question partition {tag}: {log.read_text()}', flush=True)


def coordinate(s3, bucket, prefix, root, download, env):
    """Called by the personal AWS pool coordinator after globally merged control is published."""
    from cloud_walk import read, upload_group

    from app.analysis.pooled import save

    base = f'{prefix}/pool/fleet-v3'
    outputs = f'{base}/outputs/{state_version()}'
    ctl_file = root / 'ctl/control.pkl'
    run = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'
    done = set()
    identity = hashlib.sha256((root / 'inputs.json').read_bytes()).hexdigest()
    manifest(s3, bucket, prefix, identity)

    def one_bucket(b):
        from botocore.exceptions import ClientError

        try:
            s3.head_object(Bucket=bucket, Key=f'{outputs}/{b}-{PARTITIONS - 1}.json.gz')
            return b
        except ClientError as error:
            if error.response['Error']['Code'] not in ('404', 'NoSuchKey'):
                raise
        folder, bundles = root / f'rows{b}', root / 'fleet-inputs'
        if read(s3, bucket, f'{base}/prepared/{b}.json') is None:
            download(f'rows{b}', folder)
            subprocess.run(['.venv/bin/python', __file__, 'prepare', str(folder), str(ctl_file), str(b),
                            str(bundles)], env=env, check=True)
            for p in range(PARTITIONS):
                name = f'{b}-{p}.pkl.gz'
                s3.upload_file(str(bundles / name), bucket, f'{base}/inputs/{name}')
            s3.put_object(Bucket=bucket, Key=f'{base}/prepared/{b}.json', Body=b'{}')
            import shutil
            shutil.rmtree(folder)
        else:
            bundles.mkdir(exist_ok=True)
            name = f'{b}-{PARTITIONS - 1}.pkl.gz'
            s3.download_file(bucket, f'{base}/inputs/{name}', str(bundles / name))
        # Four concurrent buckets, four cores each: use all 16 AWS cores as preparations finish.
        output = root / f'fleet-{b}-{PARTITIONS - 1}.json.gz'
        subprocess.run(['.venv/bin/python', __file__, 'build', run, str(ctl_file), str(bundles / f'{b}-{PARTITIONS - 1}.pkl.gz'),
                        str(output), '4'], env=env, check=True)
        s3.upload_file(str(output), bucket, f'{outputs}/{b}-{PARTITIONS - 1}.json.gz')
        for p in range(PARTITIONS):
            (bundles / f'{b}-{p}.pkl.gz').unlink(missing_ok=True)
        return b

    def collect(b):
        values = []
        for p in range(PARTITIONS):
            obj = s3.get_object(Bucket=bucket, Key=f'{outputs}/{b}-{p}.json.gz')
            values.append(json.loads(gzip.decompress(obj['Body'].read())))
        result = combine(values)
        with ctl_file.open('rb') as fh:
            ctl = pickle.load(fh)
        expected = {k for k in ctl['nodes'] if pool.bucket(k) == b and k not in ctl['classed']}
        if result['errors'] or result['differ'] or set(result['states']) & set(result['never_live']) \
                or set(result['states']) | set(result['never_live']) != expected:
            raise RuntimeError(f'incomplete or invalid bucket {b}')
        out = root / f'states{b}'
        out.mkdir(exist_ok=True)
        with gzip.open(out / f'states{b}.json.gz', 'wt') as fh:
            json.dump(result, fh)
        upload_group(s3, bucket, f'{prefix}/pool/states{b}.tgz', out)
        s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/states{b}-ready.json',
                      Body=json.dumps({'finished': time.time()}).encode())
        print(f'fleet bucket {b} complete: {len(expected)} questions', flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(one_bucket, b) for b in range(16)]
        while len(done) < 16:
            for future in futures:
                if future.done():
                    future.result()
            keys = set()
            for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'{outputs}/'):
                keys.update(x['Key'] for x in page.get('Contents', []))
            for b in range(16):
                if b not in done and all(f'{outputs}/{b}-{p}.json.gz' in keys for p in range(PARTITIONS)):
                    collect(b)
                    done.add(b)
            progress = {'stage': 'build_question_states', 'time': time.time(), 'partitions': len(keys),
                        'total_partitions': 16 * PARTITIONS, 'buckets_complete': len(done), 'github_runners': 40,
                        'aws_cores': 16}
            s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/progress.json', Body=json.dumps(progress).encode())
            save(root / 'fleet-progress.json', progress)
            if len(done) < 16:
                time.sleep(15)


if __name__ == '__main__':
    mode, *args = sys.argv[1:]
    if mode == 'prepare':
        prepare(args[0], args[1], int(args[2]), args[3])
    elif mode == 'build':
        build(*args[:4], int(args[4]))
    elif mode == 'github':
        github(int(args[0]))
    else:
        raise SystemExit(mode)

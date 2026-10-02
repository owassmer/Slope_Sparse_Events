"""Reduce disjoint indexed path ranges on GitHub and AWS using the existing arithmetic."""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import pickle
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from app.analysis import reduce
from app.analysis.pooled import binding, digest, save
from app.config import RECORDED
from app.disputes import pool
from tools.merge_fleet import archive
from tools.pool_fleet import fetch, put, run_logged

GITHUB_JOBS, AWS_JOBS, PROCS = 160, 8, 4


def version():
    root = Path(__file__).resolve().parents[1]
    files = [Path(__file__), *sorted((root / 'app/analysis').glob('*.py')),
             *sorted((root / 'app/finance').glob('*.py')), *sorted((root / 'app/disputes').glob('*.py'))]
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def pack_job(ctl_dir, target, ctl, blocks, job):
    """Only the assigned ranges cross the network; block numbers and within-block order remain fixed."""
    target = Path(target)
    (target / 'paths').mkdir(parents=True, exist_ok=True)
    ranges = {}
    for k in range(job * PROCS, (job + 1) * PROCS):
        ranges[k] = []
        for i, (f, lo, hi) in enumerate(blocks[k]):
            name = f'block{k}-slice{i}.pkl'
            paths = pool.read_paths(str(Path(ctl_dir) / 'paths' / f), lo, hi)
            pool.write_paths(str(target / 'paths' / name), paths)
            ranges[k].append((name, 0, hi - lo))
    (target / 'control.pkl').write_bytes(pickle.dumps(ctl, protocol=pickle.HIGHEST_PROTOCOL))
    (target / 'blocks.pkl').write_bytes(pickle.dumps(ranges, protocol=pickle.HIGHEST_PROTOCOL))


def expected(job):
    return {f'{kind}{k}.pkl' for k in range(job * PROCS, (job + 1) * PROCS) for kind in ('tab', 'stress')}


def complete(folder, job):
    marker = folder / 'complete.json'
    if not marker.exists():
        return False
    actual = {p.name for p in folder.glob('*.pkl')}
    if actual != expected(job):
        return False
    return json.loads(marker.read_text()) == {name: digest(folder / name) for name in sorted(actual)}


def worker(run_id, source, answers, job, jobs, out):
    if json.loads((Path(source) / 'binding.json').read_text()) != binding(run_id, RECORDED):
        raise RuntimeError('financial worker investigation or model differs from saved pool')
    with (Path(source) / 'blocks.pkl').open('rb') as fh:
        blocks = pickle.load(fh)
    reduce.job(run_id, source, answers, job, jobs, PROCS, out, block_ranges=blocks)


def github(job):
    fetch(os.environ['REDUCE_MANIFEST_URL'], 'reduce-manifest.json')
    info = json.loads(Path('reduce-manifest.json').read_text())
    if info['version'] != version():
        raise RuntimeError('financial worker code differs from coordinator')
    task = info['tasks'][job]
    if fetch(task['existing'], 'existing.tgz', missing_ok=True):
        return
    fetch(task['input'], 'input.tgz', wait=True)
    with tarfile.open('input.tgz') as tf:
        tf.extractall('input', filter='data')
    run_logged([sys.executable, __file__, 'local', info['run'], 'input', 'input/answers.json',
                str(job), str(info['jobs']), 'output'], Path('reduce.log'), task['log'])
    archive('output', 'output.tgz', sorted(expected(job)))
    put(task['output'], Path('output.tgz').read_bytes())
    print(f'Reduction job {job} saved', flush=True)


def coordinate(run_id, directory, answers, identity, progress):
    import shutil
    import tempfile

    import boto3

    directory = Path(directory)
    jobs = GITHUB_JOBS + AWS_JOBS
    work = directory / 'reduction-fleet' / identity / version()
    work.mkdir(parents=True, exist_ok=True)
    with (directory / 'ctl/control.pkl').open('rb') as fh:
        ctl = pickle.load(fh)
    blocks = reduce._blocks(ctl['part_cost'], jobs * PROCS)
    s3 = boto3.client('s3')
    bucket, prefix = os.environ['SLOPE_POOL_BUCKET'], os.environ['SLOPE_POOL_PREFIX']
    remote = f'{prefix}/pool/reduction/{identity}/{version()}'
    def url(op, key):
        return s3.generate_presigned_url(op, Params={'Bucket': bucket, 'Key': key}, ExpiresIn=21600)
    tasks = [{'input': url('get_object', f'{remote}/inputs/{j}.tgz'),
              'output': url('put_object', f'{remote}/outputs/{j}.tgz'),
              'existing': url('get_object', f'{remote}/outputs/{j}.tgz'),
              'log': url('put_object', f'{prefix}/pool/reduce-logs/{j}.log')}
             for j in range(GITHUB_JOBS)]
    manifest = {'version': version(), 'run': run_id, 'jobs': jobs, 'tasks': tasks}
    key = f'{remote}/manifest.json'
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(manifest).encode())
    s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/reduce-manifest-url.txt', Body=url('get_object', key).encode())
    def one(j):
        target = work / f'job{j}'
        if complete(target, j):
            return j
        with tempfile.TemporaryDirectory(dir=work) as tmp:
            base = Path(tmp) / 'input'
            pack_job(directory / 'ctl', base, ctl, blocks, j)
            shutil.copyfile(answers, base / 'answers.json')
            shutil.copyfile(directory / 'binding.json', base / 'binding.json')
            if j < GITHUB_JOBS:
                file = Path(tmp) / 'input.tgz'
                archive(base, file, [p.name for p in base.iterdir()])
                s3.upload_file(str(file), bucket, f'{remote}/inputs/{j}.tgz')
            else:
                subprocess.run([sys.executable, __file__, 'local', run_id, str(base), str(base / 'answers.json'),
                                str(j), str(jobs), str(target)], check=True)
                save(target / 'complete.json', {name: digest(target / name) for name in sorted(expected(j))})
        return j
    done = {j for j in range(jobs) if complete(work / f'job{j}', j)}
    with cf.ThreadPoolExecutor(max_workers=4) as uploads, cf.ThreadPoolExecutor(max_workers=4) as aws:
        uploads_pending = [uploads.submit(one, j) for j in range(GITHUB_JOBS) if j not in done]
        local = [aws.submit(one, j) for j in range(GITHUB_JOBS, jobs) if j not in done]
        while len(done) < jobs:
            for f in uploads_pending:
                if f.done():
                    f.result()
            for f in local:
                if f.done():
                    done.add(f.result())
            keys = {o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'{remote}/outputs/')
                    for o in page.get('Contents', [])}
            for j in range(GITHUB_JOBS):
                key = f'{remote}/outputs/{j}.tgz'
                if j in done or key not in keys:
                    continue
                with tempfile.TemporaryDirectory(dir=work) as tmp:
                    file = Path(tmp) / 'output.tgz'
                    s3.download_file(bucket, key, str(file))
                    with tarfile.open(file) as tf:
                        tf.extractall(Path(tmp) / 'out', filter='data')
                    folder = Path(tmp) / 'out'
                    if {p.name for p in folder.iterdir()} != expected(j):
                        raise RuntimeError(f'Wrong reduction block coverage in job {j}')
                    target = work / f'job{j}'
                    target.mkdir(exist_ok=True)
                    for f in folder.iterdir():
                        f.replace(target / f.name)
                    save(target / 'complete.json', {name: digest(target / name) for name in sorted(expected(j))})
                done.add(j)
            state = {'stage': 'financial_reduction', 'time': time.time(), 'jobs_complete': len(done), 'total_jobs': jobs}
            s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/progress.json', Body=json.dumps(state).encode())
            progress(f'financial_reduction_{len(done)}_of_{jobs}')
            if len(done) < jobs:
                time.sleep(15)
    return work, jobs


if __name__ == '__main__':
    if sys.argv[1] == 'github':
        github(int(sys.argv[2]))
    elif sys.argv[1] == 'local':
        run, source, answers, job, jobs, out = sys.argv[2:]
        worker(run, source, answers, int(job), int(jobs), out)

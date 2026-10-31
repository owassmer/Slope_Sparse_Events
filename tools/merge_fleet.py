"""Distribute existing whole equivalence groups; retain their original home and event order."""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import hashlib
import json
import os
import pickle
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

from app.disputes import pool
from tools.pool_fleet import fetch, put

GITHUB_JOBS = 80
AWS_JOBS = 16
_ADMISSION = threading.Condition()
_MEMORY_USED = 0
_MEMORY_BUDGET = None


def version():
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(Path(__file__).read_bytes() + (root / 'app/disputes/pool.py').read_bytes()
                          + (root / 'app/disputes/forecast.py').read_bytes()).hexdigest()[:16]


def completed_count(path):
    """The final offset proves that the index covers the entire, already-written path file."""
    index = Path(str(path) + '.idx')
    if not index.exists() or not path.exists():
        return None
    raw = index.read_bytes()
    if not raw or len(raw) % 8:
        return None
    offsets = struct.unpack(f'<{len(raw)//8}Q', raw)
    if offsets[0] != 0 or offsets[-1] != path.stat().st_size or any(a >= b for a, b in zip(offsets, offsets[1:], strict=False)):
        return None
    return len(offsets) - 1


def build_home(source, branches_file, out):
    from app.disputes.forecast import merge_equivalent

    with gzip.open(branches_file, 'rb') as fh:
        branches = pickle.load(fh)
    groups = defaultdict(list)
    with gzip.open(source, 'rb') as fh:
        header = pickle.load(fh)
        while True:
            try:
                records = pickle.load(fh)
            except EOFError:
                break
            for event, key, path in records:
                groups[key].append((event, path))
    if {k: len(v) for k, v in groups.items()} != header['expected']:
        raise RuntimeError('group members differ from the prepared assignment')
    rows = []
    for key, group in groups.items():
        group.sort(key=lambda item: item[0])
        first = group[0][0]
        members = [p for _, p in group]
        rows.extend((first, path) for path in merge_equivalent(members, [key] * len(members), branches))
    rows.sort(key=lambda item: item[0])
    Path(out).mkdir(exist_ok=True, parents=True)
    pool.write_paths(str(Path(out) / header['home']), [p for _, p in rows])
    return len(rows)


def prepare(out, meta, branches):
    out = Path(out)
    base = out / 'merge-fleet'
    base.mkdir(exist_ok=True)
    saved = base / 'prepared.pkl'
    if saved.exists():
        with saved.open('rb') as fh:
            result = pickle.load(fh)
        if result['version'] != version():
            raise RuntimeError('prepared grouping belongs to another implementation')
        return result
    meta.sort(key=lambda m: m[0])
    homes, expected, routes = {}, defaultdict(dict), defaultdict(list)
    for event, key, source, index in meta:
        home = homes.setdefault(key, source)
        expected[home][key] = expected[home].get(key, 0) + 1
        routes[source].append((index, home, event, key))
    all_homes = sorted(routes)
    reused = {f: n for f in all_homes if (n := completed_count(out / 'paths' / f)) is not None}
    sizes, locks = defaultdict(int), {f: threading.Lock() for f in all_homes}
    for f in all_homes:
        if f not in reused:
            with gzip.open(base / (f + '.gz'), 'wb', compresslevel=1) as fh:
                pickle.dump({'home': f, 'expected': expected[f]}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    with gzip.open(base / 'branches.pkl.gz', 'wb', compresslevel=1) as fh:
        pickle.dump(branches, fh, protocol=pickle.HIGHEST_PROTOCOL)

    def append(home, records):
        raw = pickle.dumps(records, protocol=pickle.HIGHEST_PROTOCOL)
        compressed = gzip.compress(raw, compresslevel=1)
        with locks[home]:
            with (base / (home + '.gz')).open('ab') as fh:
                fh.write(compressed)
            sizes[home] += len(raw)

    pending = set()
    with cf.ThreadPoolExecutor(max_workers=16) as workers:
        for number, f in enumerate(all_homes, 1):
            with (out / 'walked' / f).open('rb') as fh:
                paths = pickle.load(fh)
            assignments = routes.pop(f)
            if sorted(i for i, *_ in assignments) != list(range(len(paths))):
                raise RuntimeError(f'path coverage differs for {f}')
            blocks = defaultdict(list)
            for i, home, event, key in assignments:
                if home not in reused:
                    blocks[home].append((event, key, paths[i]))
            for home, records in blocks.items():
                pending.add(workers.submit(append, home, records))
                if len(pending) >= 16:
                    finished, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
                    for future in finished:
                        future.result()
            print(f'grouping inputs: {number}/{len(all_homes)} source parts routed', flush=True)
        for future in pending:
            future.result()
    jobs = [[] for _ in range(GITHUB_JOBS + AWS_JOBS)]
    loads = [0.0] * len(jobs)
    for f in sorted((f for f in all_homes if f not in reused), key=lambda f: -sum(expected[f].values())):
        eligible = range(GITHUB_JOBS, len(jobs)) if sizes[f] * 6 > 6 * 1024**3 else range(len(jobs))
        j = min(eligible, key=lambda j: loads[j] / (1 if j < GITHUB_JOBS else .25))
        jobs[j].append(f)
        loads[j] += max(1, sum(expected[f].values()))
    result = {'version': version(), 'homes': all_homes, 'reused': reused, 'jobs': jobs, 'sizes': dict(sizes)}
    temporary = saved.with_suffix('.tmp')
    temporary.write_bytes(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
    temporary.replace(saved)
    return result


def run_job(base, homes, sizes, cores, out, report=None):
    """Each child loads only its own home; admission considers both cores and input size."""
    global _MEMORY_USED, _MEMORY_BUDGET
    base, out = Path(base), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    budget = max(512 * 1024**2, int(available * .65))
    with _ADMISSION:
        if _MEMORY_BUDGET is None:
            _MEMORY_BUDGET = budget
    budget = _MEMORY_BUDGET

    def one(home):
        global _MEMORY_USED
        if completed_count(out / home) is not None:
            return
        estimate = max(256 * 1024**2, sizes.get(home, 0) * 6)
        if estimate > budget:
            raise RuntimeError(f"{home} exceeds this worker memory budget")
        with _ADMISSION:
            _ADMISSION.wait_for(lambda: _MEMORY_USED + estimate <= budget)
            _MEMORY_USED += estimate
        try:
            subprocess.run([sys.executable, __file__, 'home', str(base / (home + '.gz')),
                            str(base / 'branches.pkl.gz'), str(out)], check=True)
            message = f'grouping output: {home}, {completed_count(out / home)} paths saved'
            print(message, flush=True)
            if report:
                report(message)
        finally:
            with _ADMISSION:
                _MEMORY_USED -= estimate
                _ADMISSION.notify_all()
    with cf.ThreadPoolExecutor(max_workers=cores) as workers:
        list(workers.map(one, sorted(homes, key=lambda f: -sizes.get(f, 0))))


def archive(folder, target, names):
    with tarfile.open(target, 'w:gz', compresslevel=1) as tf:
        for name in names:
            tf.add(Path(folder) / name, arcname=name)


def github(job):
    fetch(os.environ['MERGE_MANIFEST_URL'], 'merge-manifest.json')
    info = json.loads(Path('merge-manifest.json').read_text())
    if info['version'] != version():
        raise RuntimeError('grouping worker code differs from coordinator')
    task = info['tasks'][job]
    if fetch(task['existing'], 'existing.tgz', missing_ok=True):
        print('Reusing saved grouping job', job, flush=True)
        return
    fetch(task['input'], 'input.tgz', wait=True)
    Path('merge-input').mkdir(exist_ok=True)
    with tarfile.open('input.tgz') as tf:
        tf.extractall('merge-input', filter='data')
    lines = []
    def report(message):
        lines.append(f'{time.time():.0f} {message}')
        put(task['log'], ('\n'.join(lines) + '\n').encode())
    report('Grouping started')
    run_job('merge-input', task['homes'], info['sizes'], os.cpu_count() or 1, 'merge-output', report)
    names = [name for f in task['homes'] for name in (f, f + '.idx')]
    archive('merge-output', 'output.tgz', names)
    put(task['output'], Path('output.tgz').read_bytes())
    print('Grouping job saved', job, flush=True)


def run(out, meta, branches):
    import boto3

    out = Path(out)
    spec = prepare(out, meta, branches)
    # Workers are fresh subprocesses; no fork inherits the coordinator's global path tables.
    s3 = boto3.client('s3')
    bucket, prefix = os.environ['SLOPE_POOL_BUCKET'], os.environ['SLOPE_POOL_PREFIX']
    base = out / 'merge-fleet'
    remote = f'{prefix}/pool/merge-fleet/{version()}'
    counts = dict(spec['reused'])
    done = set()

    def url(op, key):
        return s3.generate_presigned_url(op, Params={'Bucket': bucket, 'Key': key}, ExpiresIn=21600)

    tasks = [{'homes': homes, 'input': url('get_object', f'{remote}/inputs/{j}.tgz'),
              'output': url('put_object', f'{remote}/outputs/{j}.tgz'),
              'existing': url('get_object', f'{remote}/outputs/{j}.tgz'),
              'log': url('put_object', f'{prefix}/pool/merge-logs/{j}.log')}
             for j, homes in enumerate(spec['jobs'][:GITHUB_JOBS])]
    manifest = {'version': version(), 'tasks': tasks, 'sizes': spec['sizes']}
    key = f'{remote}/manifest.json'
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(manifest).encode())
    s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/merge-manifest-url.txt', Body=url('get_object', key).encode())

    def upload(j):
        target = base / f'job{j}.tgz'
        archive(base, target, ['branches.pkl.gz'] + [f + '.gz' for f in spec['jobs'][j]])
        s3.upload_file(str(target), bucket, f'{remote}/inputs/{j}.tgz')
        target.unlink()

    def local(j):
        target = base / f'aws{j}'
        run_job(base, spec['jobs'][j], spec['sizes'], 1, target)
        return j, target

    with cf.ThreadPoolExecutor(max_workers=8) as uploads, cf.ThreadPoolExecutor(max_workers=AWS_JOBS) as aws:
        transfers = [uploads.submit(upload, j) for j in range(GITHUB_JOBS)]
        local_jobs = [aws.submit(local, j) for j in range(GITHUB_JOBS, len(spec['jobs']))]
        while len(done) < len(spec['jobs']):
            for future in transfers:
                if future.done():
                    future.result()
            for future in local_jobs:
                if future.done():
                    j, folder = future.result()
                    if j not in done:
                        adopt(folder, out, spec['jobs'][j], counts)
                        done.add(j)
            keys = {o['Key'] for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'{remote}/outputs/')
                    for o in page.get('Contents', [])}
            for j in range(GITHUB_JOBS):
                key = f'{remote}/outputs/{j}.tgz'
                if j in done or key not in keys:
                    continue
                with tempfile.TemporaryDirectory(dir=base) as tmp:
                    target = Path(tmp) / 'out.tgz'
                    s3.download_file(bucket, key, str(target))
                    with tarfile.open(target) as tf:
                        tf.extractall(tmp, filter='data')
                    target.unlink()
                    adopt(Path(tmp), out, spec['jobs'][j], counts)
                done.add(j)
            progress = {'stage': 'group_equivalent_paths', 'time': time.time(), 'jobs_complete': len(done),
                        'total_jobs': len(spec['jobs']), 'completed_output_parts': len(counts),
                        'total_parts': len(spec['homes']), 'paths_in_completed_outputs': sum(counts.values())}
            s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/progress.json', Body=json.dumps(progress).encode())
            print(json.dumps(progress), flush=True)
            if len(done) < len(spec['jobs']):
                time.sleep(15)
    if set(counts) != set(spec['homes']):
        raise RuntimeError('grouping output coverage differs')
    return counts


def adopt(folder, out, homes, counts):
    expected = {name for f in homes for name in (f, f + '.idx')}
    if {p.name for p in folder.iterdir()} != expected:
        raise RuntimeError('foreign or missing grouping outputs')
    for f in homes:
        count = completed_count(folder / f)
        if count is None:
            raise RuntimeError(f'incomplete grouping output {f}')
        for name in (f, f + '.idx'):
            target = out / 'paths' / name
            temporary = target.with_name(target.name + '.tmp')
            shutil.copyfile(folder / name, temporary)
            temporary.replace(target)
        counts[f] = count


if __name__ == '__main__':
    mode, *args = sys.argv[1:]
    if mode == 'home':
        build_home(*args)
    elif mode == 'github':
        github(int(args[0]))
    else:
        raise SystemExit(mode)

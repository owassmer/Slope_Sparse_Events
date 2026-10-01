"""Run existing whole walk shards from an S3 queue; publish one complete result per shard.

AWS and GitHub workers use the same queue. Each attempt owns its output prefix. A conditional
completion write chooses one complete attempt, so retries never combine duplicate path events.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pickle
import shutil
import signal
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


def run_process(command, env, log, stop):
    # Kill the forked walker as well as its parent when its time budget expires.
    with subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                          start_new_session=True) as process:
        try:
            code = process.wait(timeout=max(1, stop - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


class WalkCores:
    """Share runner cores across shards; reserve another shard only when a core frees up."""

    def __init__(self, cores, stop):
        self.cores, self.stop = cores, stop
        self.pending = 0
        self.condition = threading.Condition()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=cores)

    def reserve(self):
        with self.condition:
            while self.pending >= self.cores:
                remaining = self.stop - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(timeout=remaining)
            if time.monotonic() >= self.stop:
                return False
            self.pending += 4
            return True

    def release(self, count=1):
        with self.condition:
            self.pending -= count
            self.condition.notify_all()

    def run(self, command, env, log):
        try:
            run_process(command, env, log, self.stop)
        finally:
            self.release()

    def walk(self, job, jobs, base, out, env, log):
        # Keep the original four part numbers and shared O_EXCL unit claims. Each
        # invocation runs one child, so its core is reusable before the shard ends.
        futures = [self.executor.submit(self.run,
                   ['.venv/bin/python', '-m', 'app.disputes.parallel', RUN,
                    str(job), str(jobs), '1', str(out), str(base + 3 * job + child)], env, log)
                   for child in range(4)]
        concurrent.futures.wait(futures)
        for future in futures:
            future.result()


def worker(bucket, prefix, slots, minutes, cores=0):
    s3 = boto3.client('s3')
    config = read(s3, bucket, f'{prefix}/config.json')
    stop = time.monotonic() + 60 * minutes
    cpu = WalkCores(cores, stop) if cores else None
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
    from cloud_logs import follow
    threading.Thread(target=follow, args=(bucket, prefix), daemon=True).start()
    print(f'worker resources: {len(os.sched_getaffinity(0))} CPUs, {slots} shard slots, '
          f'{cores or slots * 4} walk processes; shared cores={bool(cpu)}', flush=True)

    def slot():
        # boto3 clients are thread safe; subprocesses keep the walk's monkey patches isolated.
        for job in config['jobs']:
            if time.monotonic() >= stop:
                break
            if read(s3, bucket, f'{prefix}/done/{job}.json') or read(s3, bucket, f'{prefix}/claims/{job}.json'):
                continue
            if cpu and not cpu.reserve():
                break
            attempt = uuid.uuid4().hex
            if not create(s3, bucket, f'{prefix}/claims/{job}.json',
                          {'attempt': attempt, 'started': time.time(), 'host': os.uname().nodename,
                           'github_run': os.environ.get('GITHUB_RUN_ID')}):
                if cpu:
                    cpu.release(4)
                continue
            work = Path(tempfile.mkdtemp(prefix=f'walk-{job}-'))
            with active_lock:
                active[attempt] = (job, work)
            print(f'start shard {job}: {attempt}', flush=True)
            submitted = False
            try:
                env = {**os.environ, 'SLOPE_JEV_CACHE_ONLY': '1', 'SLOPE_WALK_CUT': '10',
                       'SLOPE_WALK_MINUTES': '0', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
                env.pop('CLAUDE_CODE_OAUTH_TOKEN', None)
                log = work / 'walk.log'
                job_index, jobs, base = job, 100, 0
                current = read(s3, bucket, f'{prefix}/config.json')
                recovery = current.get('recoveries', {}).get(str(job))
                if recovery:
                    out = work / 'out'
                    out.mkdir()
                    restored = set()
                    largest = -1
                    for obj in s3.list_objects_v2(Bucket=bucket, Prefix=recovery + '/').get('Contents', []):
                        name = Path(obj['Key']).name
                        if not name.startswith('part') or not name.endswith('.stream') or not obj['Size']:
                            continue
                        target = out / name
                        s3.download_file(bucket, obj['Key'], str(target))
                        with target.open('rb') as src:
                            try:
                                pickle.load(src)  # stream header
                            except (EOFError, pickle.UnpicklingError):
                                target.unlink()
                                continue
                            while True:
                                try:
                                    record = pickle.load(src)
                                    restored.update(record['done'])
                                except (EOFError, pickle.UnpicklingError):
                                    break
                        largest = max(largest, int(target.stem[4:]))
                    template = read(s3, bucket, f'{prefix}/done/8.json')
                    if template is None:
                        raise RuntimeError('recovery needs the completed shard 8 topology')
                    fetch_group(s3, bucket, template['source'], 'control', work / 'topology')
                    with next((work / 'topology').glob('part*.pkl')).open('rb') as src:
                        topology = pickle.load(src)
                    expected = {(clock, 0, number) for number, clock, owner in topology['segs'] if owner % 100 == job}
                    remaining = sorted(expected - restored)
                    roots = work / 'roots.pkl'
                    roots.write_bytes(pickle.dumps(remaining))
                    env['SLOPE_WALK_ROOTS'] = str(roots)
                    job_index, jobs = 0, 1
                    base = (largest // 400 + 1) * 400 + job * 4
                    print(f'recover shard {job}: {len(restored)} segments saved, {len(remaining)} remain', flush=True)
                with log.open('ab') as fh:
                    submitted = True
                    if cpu:
                        cpu.walk(job_index, jobs, base, work / 'out', env, fh)
                    else:
                        run_process(['.venv/bin/python', '-m', 'app.disputes.parallel', RUN,
                                     str(job_index), str(jobs), '4', str(work / 'out'), str(base)], env, fh, stop)
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
                if cpu and not submitted:
                    cpu.release(4)
                with active_lock:
                    active.pop(attempt, None)
                shutil.rmtree(work)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as ex:
            futures = [ex.submit(slot) for _ in range(slots)]
            for f in futures:
                f.result()
    finally:
        if cpu:
            cpu.executor.shutdown(wait=True)
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


def pool(bucket, prefix, slots, *, source_bucket=None, source_s3=None):
    """Pool one canonical result per shard, checkpointing stages in the destination account."""
    from collections import Counter

    from app.analysis.pooled import binding, save
    from app.config import RECORDED

    s3 = boto3.client('s3')
    source_s3 = source_s3 or s3
    source_bucket = source_bucket or bucket
    selected = [read(source_s3, source_bucket, f'{prefix}/done/{j}.json') for j in range(100)]
    if any(x is None for x in selected):
        raise RuntimeError('pool requires all 100 shard outputs')
    root = Path('/opt/slope-pool')
    root.mkdir(exist_ok=True)
    inputs = {'source_bucket': source_bucket, 'selected': selected, 'binding': binding(RUN, RECORDED)}
    input_key = f'{prefix}/pool/inputs.json'
    create(s3, bucket, input_key, inputs)
    if read(s3, bucket, input_key) != inputs:
        raise RuntimeError('pool destination belongs to a different shard selection or input binding')
    local_inputs = root / 'inputs.json'
    if local_inputs.exists() and json.loads(local_inputs.read_text()) != inputs:
        raise RuntimeError('local pool belongs to a different shard selection or input binding')
    save(local_inputs, inputs)
    save(root / 'selection.json', selected)
    save(root / 'binding.json', inputs['binding'])
    for name in ('selection.json', 'binding.json'):
        s3.upload_file(str(root / name), bucket, f'{prefix}/pool/{name}')
    env = {**os.environ, 'SLOPE_JEV_CACHE_ONLY': '1', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    env.pop('CLAUDE_CODE_OAUTH_TOKEN', None)

    def progress(stage, **details):
        value = {'stage': stage, 'time': time.time(), **details}
        s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/progress.json', Body=json.dumps(value).encode())
        print(json.dumps(value), flush=True)

    def download(name, target):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(fetch_group, source_s3, source_bucket, x['source'], name, target / str(x['job']))
                       for x in selected]
            for f in futures:
                f.result()

    try:
        if read(s3, bucket, f'{prefix}/pool/control-ready.json'):
            progress('restore_control')
            (root / 'ctl').mkdir(exist_ok=True)
            s3.download_file(bucket, f'{prefix}/pool/control.pkl', str(root / 'ctl/control.pkl'))
            fetch_group(s3, bucket, f'{prefix}/pool', 'paths', root / 'ctl/paths')
        else:
            progress('download_control', shards=100)
            download('control', root / 'control')
            progress('verify_segment_coverage')
            subprocess.run(['.venv/bin/python', '-m', 'app.disputes.parallel', 'spill',
                            str(root / 'control'), str(root / 'walk_roots.pkl')], check=True, env=env)
            with (root / 'walk_roots.pkl').open('rb') as f:
                missing = pickle.load(f)
            if missing:
                s3.upload_file(str(root / 'walk_roots.pkl'), bucket, f'{prefix}/pool/walk_roots.pkl')
                raise RuntimeError(f'{len(missing)} segments require recovery; no partial model will be published')
            progress('merge_paths_and_questions')
            subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'control',
                            str(root / 'control'), str(root / 'ctl')], check=True, env=env)
            with (root / 'ctl/control.pkl').open('rb') as f:
                ctl = pickle.load(f)
            summary = {k: ctl[k] for k in ('walked', 'paths', 'segments', 'parts')}
            summary.update(shards=100, missing_segments=0, question_nodes=len(ctl['nodes']),
                           classed_parent_nodes=len(ctl['classed']),
                           question_types=dict(Counter(n.node for n in ctl['nodes'].values())),
                           offering_continuations=len(ctl['raised']))
            save(root / 'walk-summary.json', summary)
            s3.upload_file(str(root / 'walk-summary.json'), bucket, f'{prefix}/pool/walk-summary.json')
            if ctl['raised']:
                raise RuntimeError(f"{len(ctl['raised'])} offering continuations require a further walk")
            s3.upload_file(str(root / 'ctl/control.pkl'), bucket, f'{prefix}/pool/control.pkl')
            upload_group(s3, bucket, f'{prefix}/pool/paths.tgz', root / 'ctl/paths')
            create(s3, bucket, f'{prefix}/pool/control-ready.json', summary)
            shutil.rmtree(root / 'control')
        with (root / 'ctl/control.pkl').open('rb') as f:
            ctl = pickle.load(f)

        def facts(b):
            out = root / f'states{b}'
            if read(s3, bucket, f'{prefix}/pool/states{b}-ready.json'):
                fetch_group(s3, bucket, f'{prefix}/pool', f'states{b}', out)
                return
            folder = root / f'rows{b}'
            download(f'rows{b}', folder)
            subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'facts', RUN, str(folder),
                            str(root / 'ctl/control.pkl'), str(b), str(out)], check=True, env=env)
            upload_group(s3, bucket, f'{prefix}/pool/states{b}.tgz', out)
            import gzip
            with gzip.open(out / f'states{b}.json.gz', 'rt') as f:
                states = json.load(f)
            if states['errors'] or states.get('differ'):
                raise RuntimeError(f'facts bucket {b} contains failed or inconsistent question states')
            create(s3, bucket, f'{prefix}/pool/states{b}-ready.json', {'finished': time.time()})
            shutil.rmtree(folder)
            print(f'facts bucket {b} published', flush=True)

        progress('build_question_states', buckets=16, concurrent_buckets=slots)
        with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as ex:
            futures = [ex.submit(facts, b) for b in range(16)]
            for f in futures:
                f.result()
        progress('verify_question_coverage')
        subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'judge', RUN, str(root),
                        str(root / 'ctl/control.pkl')], check=True, env={**env, 'SLOPE_JUDGE_COUNT': '1'})
        create(s3, bucket, f'{prefix}/pool/ready.json',
               {'finished': time.time(), 'paths': ctl['paths'], 'walked': ctl['walked'], 'nodes': len(ctl['nodes'])})
        progress('complete', paths=ctl['paths'], nodes=len(ctl['nodes']))
    except BaseException as error:
        progress('failed', error=str(error))
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['worker', 'export', 'pool'])
    p.add_argument('bucket')
    p.add_argument('prefix')
    p.add_argument('--slots', type=int, default=1)
    p.add_argument('--minutes', type=int, default=300)
    p.add_argument('--cores', type=int, default=0, help='share this many cores across active shards')
    p.add_argument('--run', default='36781427817')
    a = p.parse_args()
    if a.mode == 'worker':
        worker(a.bucket, a.prefix, a.slots, a.minutes, a.cores)
    elif a.mode == 'export':
        export_github(a.bucket, a.prefix, a.run, a.minutes)
    else:
        pool(a.bucket, a.prefix, a.slots)

"""Continue a saved pool through forecasts, resumable reduction and the analysis page."""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path
from urllib.parse import urlparse

from app.agent.run_store import RunStore
from app.config import CASES_DIR, CONTRACTS
from app.disputes.rules import load_model


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def binding(run_id: str, root: Path) -> dict:
    store = RunStore(run_id, root=root)
    if not store.locked:
        raise ValueError('A saved pool requires a locked investigation')
    live = [d for d in store.graph['disputes'].values() if d.status != 'superseded']
    if (len(live) != 1 or live[0].stage != 'liability_pending' or live[0].borrower_role != 'debtor'
            or live[0].status not in ('interpreted', 'resolved')):
        raise ValueError('Pooled analysis requires one interpreted pending debtor claim; other disputes need the local flow')
    snapshot = store.events[0].payload['snapshot_id']
    return {'run_id': run_id, 'chain_head': store.head, 'snapshot_id': snapshot,
            'variant': os.environ.get('SLOPE_VARIANT') or 'central',
            'model': load_model(snapshot),
            'files': {name: digest(CASES_DIR / snapshot / name) if (CASES_DIR / snapshot / name).exists() else None
                      for name in ('bank_feed.json', 'run_inputs.json', 'scenario.json')},
            'questions': digest(CONTRACTS / 'question_registry.json'),
            'run': digest(root / run_id / 'run.json')}


def save(path: Path, value: dict) -> None:
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(value, sort_keys=True, default=str) + '\n')
    pending.replace(path)


def fetch(uri: str, directory: Path, expected: dict) -> Path:
    """Materialize a completed S3 pool on the execution host, reusing completed downloads."""
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError('S3 adoption requires: uv run --with boto3 slope analyze-case ...') from exc
    location = urlparse(uri)
    if location.scheme != 's3' or not location.netloc or not location.path.strip('/'):
        raise ValueError('Expected s3://bucket/walk-prefix/pool')
    s3 = boto3.client('s3')
    bucket, prefix = location.netloc, location.path.strip('/')
    # The ready marker is published only after all states and the input binding are durable.
    s3.head_object(Bucket=bucket, Key=f'{prefix}/ready.json')
    source_binding = json.loads(s3.get_object(Bucket=bucket, Key=f'{prefix}/binding.json')['Body'].read())
    if source_binding != expected:
        raise ValueError('Saved pool does not match the investigation, case inputs or model')
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / 'binding.json', source_binding)
    for name in ['control.pkl', *(f'states{i}.tgz' for i in range(16)), 'paths.tgz']:
        key = f'{prefix}/{name}'
        meta = s3.head_object(Bucket=bucket, Key=key)
        marker = directory / f'.{name}.download.json'
        identity = {'uri': uri, 'etag': meta['ETag']}
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        files = previous.get('files', {})
        if previous.get('source') == identity and files and all(
                (directory / name).is_file() and digest(directory / name) == sha for name, sha in files.items()):
            continue
        if meta['ContentLength'] + 128 * 1024**2 > shutil.disk_usage(directory).free:
            raise RuntimeError(f'Insufficient disk for {name}; run saved-pool analysis on the remote execution host')
        pending = directory / f'.{name}.download'
        s3.download_file(bucket, key, str(pending))
        if name.endswith('.tgz'):
            target = directory / ('ctl/paths' if name == 'paths.tgz' else name.removesuffix('.tgz'))
            with tarfile.open(pending) as archive:
                size = sum(member.size for member in archive.getmembers())
                if size + 128 * 1024**2 > shutil.disk_usage(directory).free:
                    pending.unlink()
                    raise RuntimeError(f'Insufficient disk to unpack {name} on this execution host')
                if target.exists():
                    shutil.rmtree(target)
                archive.extractall(target, filter='data')
            pending.unlink()
        else:
            target = directory / ('ctl/control.pkl' if name == 'control.pkl' else name)
            target.parent.mkdir(parents=True, exist_ok=True)
            pending.replace(target)
        downloaded = sorted(target.rglob('*')) if target.is_dir() else [target]
        save(marker, {'source': identity, 'files': {str(p.relative_to(directory)): digest(p)
                                                  for p in downloaded if p.is_file()}})
    return directory


def build(run_id: str, root: Path, directory: Path | str, *, processes: int, progress) -> list[str]:
    """The directory contains binding.json, ctl/{control.pkl,paths/}, and states*/states*.json.gz.

    Run on the machine holding the paths. A process lock and completed-stage hashes allow interruption and
    retry without accepting partial reductions or silently reusing tables after judgments change.
    """
    from app.analysis import reduce, tables_page
    from app.disputes import pool

    if processes < 1:
        raise ValueError('Reduction processes must be positive')
    if os.environ.get('SLOPE_VARIANT'):
        raise ValueError('Unset SLOPE_VARIANT for the coordinated central-scenario flow')
    out = root / run_id
    with (out / '.pooled.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('A pooled invocation is already running for this investigation') from exc
        if str(directory).startswith('s3://'):
            from app.config import VAR
            progress('download_pool')
            directory = fetch(str(directory), VAR / 'pooled' / run_id, binding(run_id, root))
        directory = Path(directory)
        progress('validate_pool')
        if json.loads((directory / 'binding.json').read_text()) != binding(run_id, root):
            raise ValueError('Saved pool does not match the investigation, case inputs or model')
        control = directory / 'ctl/control.pkl'
        # Validate every situation even when forecasts are already saved.
        pool.judge(run_id, str(directory), str(control), count_only=True, root=root)
        paths = directory / 'ctl/paths'
        with control.open('rb') as stream:
            import pickle
            ctl = pickle.load(stream)
        for name, (count, _seconds) in ctl.get('part_cost', {}).items():
            if count and not (paths / name).is_file():
                raise ValueError(f'Saved pool path file is missing: {name}')
        inputs = {'paths': {str(p.relative_to(paths)): digest(p) for p in sorted(paths.rglob('*')) if p.is_file()},
                  'control': digest(control), 'binding': digest(directory / 'binding.json'),
                  'states': {str(p.relative_to(directory)): digest(p)
                             for p in sorted(directory.rglob('states*.json.gz'))}}
        journal = directory / 'analysis-stages.json'
        saved = json.loads(journal.read_text()) if journal.exists() else {}
        if saved.get('inputs') != inputs:
            saved = {'inputs': inputs}
        answers = out / 'tree_answers.json'
        judgments = out / 'tree_judgments.json.gz'
        progress('jev_forecasts')
        forecast_files = [answers, judgments]
        if saved.get('forecasts') != {p.name: digest(p) if p.exists() else None for p in forecast_files}:
            pool.judge(run_id, str(directory), str(control), root=root)
            saved = {'inputs': inputs, 'forecasts': {p.name: digest(p) for p in forecast_files}}
            save(journal, saved)
        progress('financial_analysis')
        # One job per process-sized batch bounds memory while retaining completed batches on retry.
        # Jobs stay fixed across retries; changing process count starts a different reduction directory.
        jobs = 16
        work = directory / f'reduction-{processes}'
        # A financial-code correction must not inherit completed reductions from older code.
        from app.config import ROOT
        code = {str(p.relative_to(ROOT)): digest(p)
                for folder in ('analysis', 'finance', 'disputes') for p in sorted((ROOT / 'app' / folder).glob('*.py'))}
        identity = hashlib.sha256(json.dumps({'inputs': inputs, 'answers': digest(answers), 'code': code},
                                             sort_keys=True).encode()).hexdigest()
        work = work / identity
        for j in range(jobs):
            block = work / f'job{j}'
            marker = block / 'complete.json'
            expected = [block / f'{kind}{k}.pkl' for k in range(j * processes, (j + 1) * processes)
                        for kind in ('tab', 'stress')]
            complete = json.loads(marker.read_text()) if marker.exists() else {}
            if complete != {p.name: digest(p) if p.exists() else None for p in expected}:
                progress(f'reduction_{j + 1}_of_{jobs}')
                reduce.job(run_id, str(directory / 'ctl'), str(answers), j, jobs, processes, str(block), root=root)
                save(marker, {p.name: digest(p) for p in expected})
        progress('merge_financials')
        merged = work / 'merged'
        # Merge only the completed job directories; never feed a previous merged tables.pkl back into itself.
        inputs_dir = work / 'blocks'
        inputs_dir.mkdir(parents=True, exist_ok=True)
        for j in range(jobs):
            for p in (work / f'job{j}').glob('*.pkl'):
                link = inputs_dir / p.name
                if not link.exists():
                    link.symlink_to(p.resolve())
        reduce.merge(str(inputs_dir), str(merged))
        progress('page')
        payload = tables_page.build(run_id, str(merged / 'tables.pkl'), str(control),
                                    str(merged / 'stress.pkl'), root=root)
        save(out / 'page.json', payload)
        with (out / 'daily.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            keys = tuple(payload['event']['daily'])
            writer.writerow(['date', 'view', *keys])
            for view in ('bank', 'event'):
                daily = payload[view]['daily']
                for t, day in enumerate(payload['dates']):
                    writer.writerow([day, view, *(daily[k][t] for k in keys)])
        return ['page.json', 'daily.csv', 'tree_answers.json', 'tree_judgments.json.gz']


if __name__ == '__main__':
    import argparse

    from app.config import RECORDED

    parser = argparse.ArgumentParser(description='Record the input binding when producing a saved pool')
    parser.add_argument('run_id')
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    save(args.directory / 'binding.json', binding(args.run_id, RECORDED))

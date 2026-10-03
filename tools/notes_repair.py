"""Recover original note-question paths for a bounded saved-walk correction.

Runs on standard GitHub runners. Source reads and destination writes use scoped,
short-lived signed URLs; no cloud credentials or Jev calls reach the runner.
"""
from __future__ import annotations

import concurrent.futures
import gzip
import json
import os
import pickle
import sys
import tarfile
import time
from pathlib import Path

from app.disputes import parallel, pool
from app.disputes.forecast import atoms
from tools.pool_fleet import fetch, put

KEY = 'dispute_002:holders_involuntary|judgment_ruling|motions_pending'
_CTL = None
_FC = None
_REUSE = None


def rebuild(path):
    """Rebuild notes rows/classes from saved histories; do not enumerate paths."""
    from dataclasses import replace

    from app.disputes import notes
    from app.disputes.forecast import class_entry, pack_row, path_mask

    global _FC
    if _FC is None:
        _FC = pool.forecaster('akoustis_20240514-agent_plus_jev-20260929T052558Z', _CTL)
    fc = _FC
    part = parallel.read_part(str(path))
    for _key, kind, value, _cond in part['events']:
        if kind == 'node':
            fc.nodes.setdefault(value[0], value[1])
    before = set(fc.nodes)
    disputes = {d.instance_id: d for d in fc.disputes}
    number = int(path.stem[4:])
    dest = Path('var/notes-repair/rebuilt')
    dest.mkdir(parents=True, exist_ok=True)
    paths = rows = 0
    started = time.monotonic()
    pending_rows = []

    def keep(key, prefix, row):
        pending_rows.append((key, fc.late_key(key, prefix, row), pack_row(row)))

    fc._keep_late = keep
    with gzip.open(dest / f'part{number}.pkl.gz', 'wb', compresslevel=1) as output:
        for ekey, kind, value, cond in part['events']:
            if kind != 'path' or (_REUSE is not None and ekey[:3] not in _REUSE):
                continue
            p, equivalence, watches = value
            keys = sorted({k for edge, _ in p.edges for k in atoms(edge)
                           if k in fc.nodes and fc.nodes[k].node in notes.NAMES})
            if not keys:
                continue
            pending_rows.clear()
            mask = path_mask(p, fc.draws.n)
            classes = [c for c in p.classes if c[0] not in keys]
            for key in keys:
                cls = notes.record(fc, disputes[p.instance_id], p.steps, key, mask)
                classes.append(class_entry(key, cls, mask))
            changed = replace(p, classes=tuple(classes))
            pickle.dump((ekey, (changed, equivalence, watches), tuple(pending_rows), cond), output, protocol=5)
            paths += 1
            rows += len(pending_rows)
            if paths % 100 == 0:
                print({'part': number, 'paths': paths, 'rows': rows,
                       'seconds': round(time.monotonic() - started, 1)}, flush=True)
    new_nodes = {k: n for k, n in fc.nodes.items() if k not in before}
    with gzip.open(dest / f'part{number}-nodes.pkl.gz', 'wb', compresslevel=1) as output:
        pickle.dump({'nodes': new_nodes, 'classed': fc.classed - _CTL['classed']}, output, protocol=5)
    result = {'part': number, 'paths': paths, 'rows': rows, 'new_nodes': len(new_nodes), 'complete': True}
    (dest / f'part{number}.json').write_text(json.dumps(result))
    return result


def scope(path):
    """Locate changed continuations, including questions hidden by old local merges."""
    from collections import defaultdict

    part = parallel.read_part(str(path))
    segments = defaultdict(lambda: {'paths': 0, 'notes': 0, 'ripe_filing': 0})
    total = 0
    for ekey, kind, value, _cond in part['events']:
        if ekey[1] != 0 or kind != 'path':
            continue
        p = value[0]
        row = segments[ekey[:3]]
        row['paths'] += 1
        total += 1
        row['notes'] += int(any(s[0] in ('judgment_default', 'delisting_notes', 'nonpayment') for s in p.steps))
        row['ripe_filing'] += int(any(s[0] == 'judgment_response' and s[1] == 'ripe'
                                     and s[2].split('=')[-1] == 'file' for s in p.steps))
    return {'part': int(path.stem[4:]), 'paths': total,
            'segments': [(key, value) for key, value in sorted(segments.items())]}


def validate(path):
    """Replay the failed question on original histories, before either answer books."""
    from app.disputes import notes
    from app.disputes.forecast import path_mask

    global _FC
    if _FC is None:
        _FC = pool.forecaster('akoustis_20240514-agent_plus_jev-20260929T052558Z', _CTL)
    fc = _FC
    d = next(d for d in fc.disputes if d.instance_id == fc.nodes[KEY].instance_id)
    count = live = draws = 0
    classes, segments = set(), set()
    with gzip.open(path, 'rb') as stream:
        rows = pickle.load(stream)
    for ekey, (p, _eq, _watch) in rows:
        cls = notes.record(fc, d, p.steps, KEY, path_mask(p, fc.draws.n))
        on = cls != ''
        count += 1
        live += int(on.any())
        draws += int(on.sum())
        classes.update(cls[on])
        segments.add(ekey[:3])
        # These are validation rows, never production pool inputs.
        fc.facts.clear()
        fc._late_seen.clear()
    return {'part': path.name, 'paths': count, 'live_paths': live, 'live_draws': draws,
            'classes': sorted(classes), 'segments': sorted(segments)}


def extract(path):
    part = parallel.read_part(str(path))
    number = int(path.stem[4:])
    holds = pool._holds(_CTL['reads'])
    rows = []
    paths = 0
    for ekey, kind, value, cond in sorted(part['events'], key=lambda e: e[0]):
        if kind != 'path' or not all(holds(c) for c in cond):
            continue
        if ekey[1] == 1 and number != _CTL['top_owner']:
            continue
        q = value[0]
        paths += 1
        if any(KEY in k and KEY in atoms(k) for k, _ in q.edges):
            # Keep original equivalence key, watched regions, and global ordering.
            # A merged representative cannot recover every source prefix.
            rows.append((ekey, value))
    out = path.with_suffix('.affected.pkl.gz')
    with gzip.open(out, 'wb', compresslevel=1) as stream:
        pickle.dump(rows, stream, protocol=5)
    return {'part': number, 'paths': paths, 'affected': len(rows), 'file': str(out)}


def github(worker):
    import multiprocessing

    root = Path('var/notes-repair')
    root.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_REPAIR_MANIFEST_URL'], root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    global _REUSE
    _REUSE = {tuple(key) for key in manifest['reuse']} if 'reuse' in manifest else None
    task = manifest['tasks'][worker]
    fetch(manifest['control'], root / 'control.pkl')
    global _CTL
    with (root / 'control.pkl').open('rb') as stream:
        _CTL = pickle.load(stream)

    if manifest.get('mode') == 'validate':
        archive = root / 'raw.tgz'
        fetch(task['input'], archive)
        with tarfile.open(archive) as tar:
            tar.extractall(root / 'raw', filter='data')
        with multiprocessing.get_context('fork').Pool(os.cpu_count()) as workers:
            results = list(workers.imap_unordered(validate, (root / 'raw').glob('*.pkl.gz')))
        report = {'worker': worker, 'cores': os.cpu_count(), 'parts': results,
                  **{k: sum(r[k] for r in results) for k in ('paths', 'live_paths', 'live_draws')}}
        put(task['output'], json.dumps(report).encode())
        print({k: v for k, v in report.items() if k != 'parts'}, flush=True)
        return

    def source(item):
        archive = root / f"source-{item['job']}.tgz"
        folder = root / f"source-{item['job']}"
        folder.mkdir(exist_ok=True)
        fetch(item['url'], archive)
        with tarfile.open(archive) as tar:
            tar.extractall(folder, filter='data')
        archive.unlink()
        return list(folder.glob('part*.pkl'))

    files = []
    with concurrent.futures.ThreadPoolExecutor(4) as downloads:
        for group in downloads.map(source, task['sources']):
            files.extend(group)
    results = []
    if manifest.get('mode') == 'rebuild':
        with multiprocessing.get_context('fork').Pool(os.cpu_count()) as workers:
            for result in workers.imap_unordered(rebuild, files):
                results.append(result)
                print(result, flush=True)
        report = {'worker': worker, 'cores': os.cpu_count(), 'parts': results,
                  'paths': sum(r['paths'] for r in results), 'rows': sum(r['rows'] for r in results)}
        (root / 'rebuilt' / 'report.json').write_text(json.dumps(report))
        print({k: v for k, v in report.items() if k != 'parts'}, flush=True)
        return
    with multiprocessing.get_context('fork').Pool(os.cpu_count()) as workers:
        for result in workers.imap_unordered(scope if manifest.get('mode') == 'scope' else extract, files):
            results.append(result)
            print({k: v for k, v in result.items() if k not in ('file', 'segments')}, flush=True)
    report = {'worker': worker, 'cores': os.cpu_count(), 'parts': results,
              'paths': sum(r['paths'] for r in results), 'affected': sum(r.get('affected', 0) for r in results)}
    (root / 'report.json').write_text(json.dumps(report))
    output = root / 'result.tgz'
    with tarfile.open(output, 'w:gz', compresslevel=1) as tar:
        tar.add(root / 'report.json', arcname='report.json')
        for result in results:
            if 'file' in result:
                tar.add(result['file'], arcname=Path(result['file']).name)
    put(task['output'], output.read_bytes())
    print({k: v for k, v in report.items() if k != 'parts'}, flush=True)


if __name__ == '__main__':
    github(int(sys.argv[1]))

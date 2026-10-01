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
from pathlib import Path

from app.disputes import parallel, pool
from app.disputes.forecast import atoms
from tools.pool_fleet import fetch, put

KEY = 'dispute_002:holders_involuntary|judgment_ruling|motions_pending'
_CTL = None


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
    task = manifest['tasks'][worker]
    fetch(manifest['control'], root / 'control.pkl')
    global _CTL
    with (root / 'control.pkl').open('rb') as stream:
        _CTL = pickle.load(stream)

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
    with multiprocessing.get_context('fork').Pool(os.cpu_count()) as workers:
        for result in workers.imap_unordered(extract, files):
            results.append(result)
            print({k: v for k, v in result.items() if k != 'file'}, flush=True)
    report = {'worker': worker, 'cores': os.cpu_count(), 'parts': results,
              'paths': sum(r['paths'] for r in results), 'affected': sum(r['affected'] for r in results)}
    (root / 'report.json').write_text(json.dumps(report))
    output = root / 'result.tgz'
    with tarfile.open(output, 'w:gz', compresslevel=1) as tar:
        tar.add(root / 'report.json', arcname='report.json')
        for result in results:
            tar.add(result['file'], arcname=Path(result['file']).name)
    put(task['output'], output.read_bytes())
    print({k: v for k, v in report.items() if k != 'parts'}, flush=True)


if __name__ == '__main__':
    github(int(sys.argv[1]))

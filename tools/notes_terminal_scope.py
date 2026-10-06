"""Read saved terminal histories to locate unanswered decisions before their filing."""
from __future__ import annotations

import gzip
import json
import multiprocessing
import os
import pickle
import sys
import tarfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

from app.analysis.events import BIG
from app.disputes import pool
from app.disputes.forecast import _Walk
from tools.pool_fleet import fetch

_ROOT = None
_WALKS = None


def scan(path):
    with gzip.open(path, 'rb') as stream:
        rows = pickle.load(stream)
    affected, counts = [], Counter()
    for event, instance, steps, condition, probes in rows:
        walk = _WALKS[instance]
        mask = walk.mask_of(steps)
        hits = []
        for probe in probes:
            trace = walk._trace(steps + (probe,))
            live = (trace.day[-1] < walk.N) & (trace.day[-1] < np.where(trace.petition < 0, BIG, trace.petition))
            if mask is not None:
                live &= mask
            if live.any():
                hits.append((probe, np.flatnonzero(live).tolist()))
                counts[probe[0]] += 1
        if hits:
            affected.append((event, instance, steps, condition, hits))
    summary = {'file': path.name, 'screened': len(rows), 'affected': len(affected), 'questions': dict(counts)}
    target = _ROOT / path.name
    with gzip.open(target.with_suffix('.tmp'), 'wb', compresslevel=1) as stream:
        pickle.dump({'summary': summary, 'affected': affected}, stream)
    target.with_suffix('.tmp').replace(target)
    return summary


def main(worker):
    global _ROOT, _WALKS
    root = Path('var/notes-terminal-scope')
    _ROOT = root / 'results'
    _ROOT.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_TERMINAL_SCOPE_MANIFEST_URL'], root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    fetch(manifest['control'], root / 'control.pkl')
    with (root / 'control.pkl').open('rb') as stream:
        control = pickle.load(stream)
    fc = pool.forecaster('akoustis_20240514-agent_plus_jev-20260929T052558Z', control)
    fc._raise_open = set(control['raised'])
    _WALKS = {d.instance_id: _Walk(fc, d) for d in fc.disputes}
    task = manifest['tasks'][worker]
    fetch(task['url'], root / 'input.tgz')
    with tarfile.open(root / 'input.tgz') as archive:
        archive.extractall(root / 'input', filter='data')
    files = sorted((root / 'input').glob('*.pkl.gz'))
    if [p.name for p in files] != sorted(task['files']):
        raise ValueError('Downloaded inventory differs from assigned saved histories')
    reports, start = [], time.monotonic()
    with multiprocessing.get_context('fork').Pool(os.cpu_count(), maxtasksperchild=20) as workers:
        for report in workers.imap_unordered(scan, files):
            reports.append(report)
            print({**report, 'seconds': round(time.monotonic() - start, 1)}, flush=True)
    if sorted(r['file'] for r in reports) != sorted(task['files']):
        raise ValueError('Incomplete saved-history scan')
    (_ROOT / 'report.json').write_text(json.dumps({'worker': worker, 'complete': True, 'reports': reports}))


if __name__ == '__main__':
    main(int(sys.argv[1]))

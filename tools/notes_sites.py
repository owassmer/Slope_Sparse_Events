"""Locate the original repair sites in saved raw histories, without walking a new tree."""
from __future__ import annotations

import concurrent.futures
import gzip
import json
import multiprocessing
import os
import pickle
import sys
import tarfile
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.disputes import parallel, pool
from app.disputes.forecast import _Walk
from tools.notes_scope import merged_holders
from tools.pool_fleet import fetch


def scan(path, walks, index=None, top_owner=None):
    part = parallel.read_part(str(path))
    sites = {}
    counts = Counter()

    @lru_cache(maxsize=8192)
    def stopped(instance, steps):
        return bool((walks[instance]._trace(steps, True).petition >= 0).all())

    for event, kind, value, condition in part['events']:
        if kind != 'path' or (top_owner is not None and event[1] == 1 and int(path.stem[4:]) != top_owner):
            continue
        p, _equivalence, _watches = value
        counts['raw_paths'] += 1
        if index is not None:
            pickle.dump((event, p.instance_id, p.steps, condition), index, protocol=5)
        candidates = []
        merged = set().union(*(merged_holders(edge) for edge, _ in p.edges))
        for i, (node, context, branch) in enumerate(p.steps):
            if node == 'judgment_default':
                if branch in ('yes', 'holders_file') and stopped(p.instance_id, p.steps[:i + 1]):
                    candidates.append((i, 'notes_petition', context, 'prefix_terminal'))
                if branch == 'accelerated' and f'judgment_{context}' in merged:
                    candidates.append((i, 'notes_petition', context, 'digest_merge'))
            elif node == 'judgment_response' and context == 'ripe' and branch.split('=')[-1] == 'file':
                candidates.append((i, 'a4', context, 'ripe_filing'))
        counts['candidate_paths'] += bool(candidates)
        for i, method, phase, reason in candidates:
            key = (p.instance_id, p.steps[:i], method, phase)
            row = sites.setdefault(key, {'reasons': set(), 'events': [], 'conditional_events': 0, 'watches': set()})
            row['reasons'].add(reason)
            row['watches'].update(_watches)
            row['events'].append(event)
            row['conditional_events'] += bool(condition)
    return {'part': path.name, 'counts': dict(counts), 'sites': sites}


_FC = None
_WALKS = None
_TOP_OWNER = None


def scan_file(item):
    job, path = item
    with gzip.open(path.parent.parent / f"index-{job}-{path.stem}.pkl.gz", "wb", compresslevel=1) as index:
        result = scan(path, _WALKS, index, _TOP_OWNER)
    dest = path.parent.parent / f"sites-{job}-{path.stem}.pkl.gz"
    with gzip.open(dest, "wb", compresslevel=1) as stream:
        pickle.dump(result, stream, protocol=5)
    path.unlink()
    return {"source": job, "part": path.name, **result["counts"], "sites": len(result["sites"])}


def main(worker):
    root = Path('var/notes-sites')
    root.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_REPAIR_MANIFEST_URL'], root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    fetch(manifest['control'], root / 'control.pkl')
    with (root / 'control.pkl').open('rb') as stream:
        control = pickle.load(stream)
    global _FC, _WALKS, _TOP_OWNER
    _TOP_OWNER = control['top_owner']
    _FC = pool.forecaster('akoustis_20240514-agent_plus_jev-20260929T052558Z', control)
    _WALKS = {d.instance_id: _Walk(_FC, d) for d in _FC.disputes}
    started = time.monotonic()

    def download(source):
        archive = root / f"source-{source['job']}.tgz"
        fetch(source['url'], archive)
        folder = root / f"input-{source['job']}"
        folder.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(folder, filter='data')
        archive.unlink()
        paths = sorted(folder.glob('part*.pkl'))
        if not paths:
            raise ValueError(f"Source {source['job']} contains no raw parts")
        return [(source['job'], path) for path in paths]

    files = []
    with concurrent.futures.ThreadPoolExecutor(4) as downloads:
        for items in downloads.map(download, manifest['tasks'][worker]['sources']):
            files.extend(items)
    inventory = [(job, path.name) for job, path in files]
    if len(set(inventory)) != len(inventory):
        raise ValueError('Duplicate source part assignment')
    (root / 'expected.json').write_text(json.dumps({'worker': worker, 'parts': inventory}))
    completed = []
    with multiprocessing.get_context('fork').Pool(os.cpu_count()) as workers:
        for report in workers.imap_unordered(scan_file, files):
            completed.append((report['source'], report['part']))
            print({**report, 'seconds': round(time.monotonic() - started, 1)}, flush=True)
    if sorted(completed) != sorted(inventory):
        raise ValueError('Incomplete raw part scan')
    (root / 'complete.json').write_text(json.dumps({'worker': worker, 'complete': True, 'parts': inventory}))


if __name__ == '__main__':
    main(int(sys.argv[1]))

"""Sum saved path probabilities without rerunning financial calculations or calling Jev."""
from __future__ import annotations

import concurrent.futures
import gzip
import json
import multiprocessing
import os
import pickle
import sys
from pathlib import Path

import numpy as np

from app.disputes.forecast import Dist, class_firsts, expand_classes, path_mask, path_probability
from tools.path_archives import materialize
from tools.pool_fleet import fetch, put

_CONTEXT = None


def distributions(nodes, trials=3):
    rng = np.random.default_rng(20261003)
    return [Dist({k: dict(zip(node.branches, rng.dirichlet(np.ones(len(node.branches))), strict=True))
                  for k, node in sorted(nodes.items())}) for _ in range(trials)]


def totals(paths, nodes, draws, dead, dists, first):
    """A partition contributes partial mass; only their complete sum must equal one."""
    result = {}
    count = 0
    for path in paths:
        count += 1
        total = result.setdefault(path.instance_id, np.zeros((len(dists), draws)))
        for expanded in expand_classes([path], nodes, draws, dead, first):
            mask = path_mask(expanded, draws)
            weights = np.array([path_probability(expanded.edges, dist) for dist in dists])
            total += weights[:, None] * (1 if mask is None else mask)
    return count, result


def _file(task):
    name, expected = task
    nodes, draws, dead = _CONTEXT
    dists = distributions(nodes)
    first = class_firsts(nodes, dead)
    def paths():
        with open(name, 'rb') as fh:
            for _ in range(expected):
                yield pickle.load(fh)
            if fh.read(1):
                raise ValueError('Trailing paths beyond catalog count')
    count, result = totals(paths(), nodes, draws, dead, dists, first)
    print(f'Checked {count} saved paths in {Path(name).name}', flush=True)
    return count, result


def github(job):
    global _CONTEXT
    fetch(os.environ['MASS_MANIFEST_URL'], 'mass-manifest.json')
    manifest = json.loads(Path('mass-manifest.json').read_text())
    if job in manifest.get('completed', []):
        print(f'Job {job} already has a validated saved probability sum', flush=True)
        return
    task = manifest['tasks'][job]
    fetch(manifest['control'], 'control.pkl')
    import hashlib
    if hashlib.sha256(Path('control.pkl').read_bytes()).hexdigest() != manifest['control_sha256']:
        raise ValueError('Probability check control differs')
    control = pickle.loads(Path('control.pkl').read_bytes())
    materialize(task['archives'], Path('paths'))
    _CONTEXT = control['nodes'], manifest['draws'], frozenset(manifest['dead'])
    count, combined = 0, {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=os.cpu_count() or 1,
            mp_context=multiprocessing.get_context('fork')) as workers:
        for n, values in workers.map(_file, [(str(Path('paths') / f), n) for f, n in task['files'].items()]):
            count += n
            for iid, value in values.items():
                combined.setdefault(iid, np.zeros_like(value))[:] += value
    if count != sum(task['files'].values()):
        raise ValueError('Probability check path coverage differs')
    result = {'identity': manifest['identity'], 'job': job, 'files': task['files'], 'paths': count,
              'mass': {iid: value.tolist() for iid, value in combined.items()}, 'jev_started': False}
    put(task['output'], gzip.compress(json.dumps(result).encode()))
    print(f'Saved probability sums for {count} paths; global summation is still required', flush=True)


if __name__ == '__main__':
    github(int(sys.argv[1]))

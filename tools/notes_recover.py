"""Recover the exact selected continuation branches; preserve outputs per completed unit."""
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

import numpy as np

from app.disputes import parallel, pool
from app.disputes.forecast import Dist, _Walk, atoms, expand_classes, pack_row, path_mask, path_probability
from tools.notes_resume import capture
from tools.pool_fleet import fetch

_CONTROL = None
_PLAN = None
_ROOT = None
RUN = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'


def recover(number, unit, control):
    outputs = []
    for prefix, method, phase in unit['targets']:
        fc = pool.forecaster(RUN, control)
        fc._raise_open = set(control['raised'])
        dispute = next(d for d in fc.disputes if d.instance_id == unit['instance'])
        walk = _Walk(fc, dispute)
        if walk.reading() != 'entered':
            raise ValueError('This recovery manifest binds the entered-judgment reading')
        continuation = capture(walk, prefix, method, phase)
        parent = continuation.state
        if walk.out:
            raise ValueError('Prefix capture unexpectedly emitted paths')
        fc.facts.clear()
        fc._late_seen.clear()
        rows = []
        record = fc.record

        def recorded(keys, trace, record=record, rows=rows, fc=fc):
            result = record(keys, trace)
            rows.extend(('rec', key, fc._rec_at, None, blob) for key, blob in result)
            return result

        def late(key, before, row, rows=rows, fc=fc):
            rows.append(('late', key, before, fc.late_key(key, before, row), pack_row(row)))

        fc.record = recorded
        fc._keep_late = late
        continuation.run()
        if not walk.out:
            raise ValueError('Selected continuation emitted no descendants')
        if fc._raise_more - fc._raise_open:
            raise ValueError('Continuation opens additional financing prefixes; expand recovery before adoption')
        conditional = [replace(p, edges=p.edges[len(parent.edges):]) for p in walk.out]
        expanded = expand_classes(conditional, fc.nodes, fc.draws.n)
        keys = {key for p in expanded for edge, _ in p.edges for key in atoms(edge)}
        expected = walk.mask_of(parent.steps)
        expected = np.ones(fc.draws.n) if expected is None else expected.astype(float)
        rng = np.random.default_rng(number)
        max_error = 0.0
        for _ in range(3):
            dist = Dist({key: dict(zip(fc.nodes[key].branches,
                                      rng.dirichlet(np.ones(len(fc.nodes[key].branches))), strict=True))
                         for key in sorted(keys)})
            total = np.zeros(fc.draws.n)
            for path in expanded:
                mask = path_mask(path, fc.draws.n)
                total += path_probability(path.edges, dist) * (1 if mask is None else mask)
            error = float(np.abs(total - expected).max())
            max_error = max(max_error, error)
            if error > 1e-10:
                raise ValueError(f'Conditional descendant probability differs from incoming mask: {error}')
        fields = {}
        for name in parallel.FIELDS:
            values, original = getattr(fc, name), control[name]
            if any(value != original[key] for key, value in values.items() if key in original):
                raise ValueError(f'Original {name} metadata changed during recovery')
            fields[name] = {key: value for key, value in values.items() if key not in original}
        outputs.append({'parent': parent, 'paths': walk.out, 'equivalence': walk.keys, 'rows': rows,
                        'nodes': {k: v for k, v in fc.nodes.items() if k not in control['nodes']},
                        'fields': fields,
                        'sets': {name: set(getattr(fc, name)) - control[name] for name in parallel.SETS},
                        'max_probability_error': max_error, 'ev_range': getattr(fc, 'ev_range', None)})
    return {'unit': number, 'old_events': unit['old_events'], 'outputs': outputs}


def one(number):
    started = time.monotonic()
    try:
        result = recover(number, _PLAN['units'][number], _CONTROL)
        path = _ROOT / f'unit-{number}.pkl.gz'
        with gzip.open(path.with_suffix('.tmp'), 'wb', compresslevel=1) as stream:
            pickle.dump(result, stream, protocol=5)
        path.with_suffix('.tmp').replace(path)
        return {'unit': number, 'paths': sum(len(o['paths']) for o in result['outputs']),
                'seconds': round(time.monotonic() - started, 2), 'complete': True}
    except Exception:
        error = traceback.format_exc()
        (_ROOT / f'unit-{number}.error.txt').write_text(error)
        return {'unit': number, 'seconds': round(time.monotonic() - started, 2), 'complete': False,
                'error': error.splitlines()[-1]}


def main(worker):
    global _CONTROL, _PLAN, _ROOT
    _ROOT = Path('var/notes-recovery')
    _ROOT.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_RECOVERY_MANIFEST_URL'], _ROOT / 'manifest.json')
    manifest = json.loads((_ROOT / 'manifest.json').read_text())
    fetch(manifest['control'], _ROOT / 'control.pkl')
    fetch(manifest['plan'], _ROOT / 'plan.pkl')
    with (_ROOT / 'control.pkl').open('rb') as stream:
        _CONTROL = pickle.load(stream)
    data = (_ROOT / 'plan.pkl').read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest['plan_sha256']:
        raise ValueError('Recovery plan checksum mismatch')
    _PLAN = pickle.loads(data)
    if sorted(i for task in manifest['tasks'] for i in task['units']) != list(range(len(_PLAN['units']))):
        raise ValueError('Recovery assignments do not cover the exact plan once')
    assigned = manifest['tasks'][worker]['units']
    reports = []
    with multiprocessing.get_context('fork').Pool(os.cpu_count(), maxtasksperchild=30) as workers:
        for report in workers.imap_unordered(one, assigned):
            reports.append(report)
            print(report, flush=True)
    complete = len(reports) == len(assigned) and all(r['complete'] for r in reports)
    (_ROOT / 'report.json').write_text(json.dumps({'worker': worker, 'assigned': assigned,
                                                  'reports': reports, 'complete': complete,
                                                  'plan_sha256': manifest['plan_sha256']}))
    if not complete:
        raise SystemExit('Some recovery units failed; completed unit artifacts are preserved')


if __name__ == '__main__':
    main(int(sys.argv[1]))

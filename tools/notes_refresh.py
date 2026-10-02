"""Refresh saved descendants through native question consumers, with no unsaved walking."""
from __future__ import annotations

import functools
import inspect
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from app.disputes import pool
from app.disputes.forecast import _S, _Walk, atoms, expand_classes, pack_row, path_mask
from tools.notes_recover import RUN
from tools.notes_resume import capture


def refresh(number, unit, saved, control):
    """Keep saved coverage explicit: unknown branches are returned, never silently dropped."""
    outputs, discrepancies = [], []
    for target, source in zip(unit['targets'], saved['outputs'], strict=True):
        prefix, method, phase = target
        fc = pool.forecaster(RUN, control)
        fc._raise_open = set(control['raised'])
        dispute = next(d for d in fc.disputes if d.instance_id == unit['instance'])
        walk = _Walk(fc, dispute)
        continuation = capture(walk, prefix, method, phase, legacy=True)
        parent = continuation.state
        # Keep the complete saved sibling population, including no-event watch branches.
        allowed = {p.steps[:i] for p in source['paths'] for i in range(len(prefix), len(p.steps) + 1)}
        missing = set()
        rows = []
        record = fc.record

        def recorded(keys, trace, record=record, rows=rows, fc=fc):
            result = record(keys, trace)
            rows.extend(('rec', key, fc._rec_at, None, blob) for key, blob in result)
            return result

        def late(key, before, row, rows=rows, fc=fc):
            rows.append(('late', key, before, fc.late_key(key, before, row), pack_row(row)))

        fc.record, fc._keep_late = recorded, late
        fc.facts.clear()
        fc._late_seen.clear()

        def bounded(function, walk=walk, allowed=allowed, missing=missing):
            @functools.wraps(function)
            def call(self, state, *args, **kwargs):
                if self is walk and isinstance(state, _S) and state.steps not in allowed:
                    mask = self.mask_of(state.steps)
                    if mask is None or mask.any():
                        missing.add(state.steps)
                    return None
                return function(self, state, *args, **kwargs)
            return call

        with ExitStack() as stack:
            for name, function in list(vars(_Walk).items()):
                if not callable(function) or name.startswith('__'):
                    continue
                signature = inspect.signature(function)
                parameters = list(signature.parameters)
                if len(parameters) > 1 and parameters[1] == 's' and signature.return_annotation in ('None', None):
                    stack.enter_context(patch.object(_Walk, name, bounded(function)))
            continuation.run()
        # Matching is by the complete decision history and population, not output order.
        original = {(p.steps, p.mask, p.outcome): eq for p, eq in
                    zip(source['paths'], source['equivalence'], strict=True)}
        current = {(p.steps, p.mask, p.outcome): eq for p, eq in zip(walk.out, walk.keys, strict=True)}
        altered = [key for key in original.keys() & current.keys() if original[key] != current[key]]
        report = {'target_steps': len(prefix), 'saved': len(source['paths']), 'refreshed': len(walk.out),
                  'unreached': len(original.keys() - current.keys()), 'new_leaves': len(current.keys() - original.keys()),
                  'changed_finances': len(altered), 'unknown_prefixes': len(missing)}
        discrepancies.append({**report, 'missing': sorted(missing)})
        # This is an adoption gate. Pruning cannot be used to claim mass conservation.
        if missing or altered or original.keys() != current.keys():
            continue
        conditional = [replace(p, edges=p.edges[len(parent.edges):]) for p in walk.out]
        expanded = expand_classes(conditional, fc.nodes, fc.draws.n)
        from app.disputes.forecast import Dist, path_probability
        keys = {k for p in expanded for edge, _ in p.edges for k in atoms(edge)}
        expected = walk.mask_of(parent.steps)
        expected = np.ones(fc.draws.n) if expected is None else expected.astype(float)
        rng = np.random.default_rng(number)
        maximum = 0.0
        for _ in range(3):
            dist = Dist({k: dict(zip(fc.nodes[k].branches, rng.dirichlet(np.ones(len(fc.nodes[k].branches))),
                                     strict=True)) for k in sorted(keys)})
            total = np.zeros(fc.draws.n)
            for p in expanded:
                mask = path_mask(p, fc.draws.n)
                total += path_probability(p.edges, dist) * (1 if mask is None else mask)
            maximum = max(maximum, float(np.abs(total - expected).max()))
        report['max_probability_error'] = maximum
        if maximum > 1e-10:
            raise ValueError(f'Refreshed unit {number} fails conditional conservation: {maximum}')
        outputs.append({**source, 'paths': walk.out, 'equivalence': walk.keys, 'rows': rows,
                        'nodes': {k: v for k, v in fc.nodes.items() if k not in control['nodes']},
                        'max_probability_error': maximum})
    return {'unit': number, 'outputs': outputs, 'reports': discrepancies,
            'complete': len(outputs) == len(unit['targets']), 'production_ready': False}


def main(number):
    import gzip
    import hashlib
    import json
    import os
    import pickle
    import time
    from pathlib import Path

    from tools.pool_fleet import fetch

    root = Path('var/notes-refresh')
    root.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_ASSEMBLY_MANIFEST_URL'], root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    for key, filename in [('plan', 'plan.pkl'), ('control', 'control.pkl')]:
        fetch(manifest[key], root / filename)
    raw = (root / 'plan.pkl').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['plan_sha256']:
        raise ValueError('Replacement plan checksum mismatch')
    plan = pickle.loads(raw)
    with (root / 'control.pkl').open('rb') as stream:
        control = pickle.load(stream)
    fetch(manifest['samples'][str(number)], root / 'unit.pkl.gz')
    with gzip.open(root / 'unit.pkl.gz', 'rb') as stream:
        saved = pickle.load(stream)
    started = time.monotonic()
    result = refresh(number, plan['units'][number], saved, control)
    with gzip.open(root / 'refreshed.pkl.gz', 'wb', compresslevel=1) as stream:
        pickle.dump(result, stream, protocol=5)
    report = {k: v for k, v in result.items() if k != 'outputs'}
    report['seconds'] = time.monotonic() - started
    (root / 'report.json').write_text(json.dumps(report))
    print(json.dumps({**report, 'reports': [{k: v for k, v in r.items() if k != 'missing'}
                                         for r in report['reports']]}), flush=True)
    if not result['complete']:
        raise SystemExit('Saved coverage differs; explicit reconciliation required')


if __name__ == '__main__':
    import sys
    main(int(sys.argv[1]))

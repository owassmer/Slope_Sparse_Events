"""Refresh saved descendants through native question consumers, with no unsaved walking."""
from __future__ import annotations

import functools
import inspect
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from app.disputes import parallel, pool
from app.disputes.forecast import _S, _Walk, atoms, expand_classes, pack_row, path_mask
from tools.notes_recover import RUN
from tools.notes_resume import capture


def history(steps):
    # Matching only: native masks and edges remain untouched. Infeasible no has
    # no settlement probability, while feasible yes/no retain their native edges.
    from app.analysis.events import plain
    return tuple((n, c, plain(b) if n == 'settle' else b) for n, c, b in steps)


def correspondence(walk, source):
    """Compare saved/new populations and finances under native settlement feasibility."""
    from app.analysis.events import event_trace, plain

    fc = walk.fc
    old, new = defaultdict(list), defaultdict(list)
    for path, eq in zip(source['paths'], source['equivalence'], strict=True):
        old[(history(path.steps), path.outcome)].append((path, eq))
    for path, eq in zip(walk.out, walk.keys, strict=True):
        new[(history(path.steps), path.outcome)].append((path, eq))
    uncovered = changed = 0
    for key in old.keys() | new.keys():
        expected, got = np.zeros(fc.draws.n, dtype=int), np.zeros(fc.draws.n, dtype=int)
        for path, _eq in old[key]:
            mask = path_mask(path, fc.draws.n)
            mask = np.ones(fc.draws.n, dtype=bool) if mask is None else mask.copy()
            for i, (node, ctx, branch) in enumerate(path.steps):
                if node == 'settle' and plain(branch) == 'yes' and not branch.startswith('@'):
                    mask &= walk.walk_groups(path.steps[:i] + ((node, ctx, 'no'),)) == 1
            expected += mask
        for path, eq in new[key]:
            mask = path_mask(path, fc.draws.n)
            mask = np.ones(fc.draws.n, dtype=bool) if mask is None else mask
            got += mask
            if not old[key]:
                continue
            same = next((saved_eq for saved, saved_eq in old[key]
                         if saved.steps == path.steps and saved.mask == path.mask), None)
            if same is not None:
                changed += same != eq
            else:
                # Regrouping changes the digest's mask. Re-evaluate the saved
                # financial history on this exact native population for comparison.
                saved = old[key][0][0]
                tr = event_trace(walk.d, saved, fc.setup, fc.m, fc.draws, fc.sens,
                                 rows=tuple(mask for _ in saved.steps))
                comparable = walk.equivalence(_S(steps=saved.steps), saved.outcome, tr, mask)
                changed += comparable != eq
        uncovered += int(np.count_nonzero(expected != got))
    return uncovered, changed


def refresh(number, unit, saved, control, *, complete_missing=False):
    """Keep saved coverage explicit: unknown branches are returned, never silently dropped."""
    outputs, discrepancies = [], []
    for target, source in zip(unit['targets'], saved['outputs'], strict=True):
        prefix, method, phase = target
        fc = pool.forecaster(RUN, control)
        fc._raise_open = set(control['raised'])
        dispute = next(d for d in fc.disputes if d.instance_id == unit['instance'])
        historical = capture(_Walk(fc, dispute), prefix, method, phase, legacy=True)
        incoming = historical.incoming_mask
        del historical
        fc = pool.forecaster(RUN, control)
        fc._raise_open = set(control['raised'])
        dispute = next(d for d in fc.disputes if d.instance_id == unit['instance'])
        walk = _Walk(fc, dispute)
        rows = []
        record = fc.record

        def recorded(keys, trace, record=record, rows=rows, fc=fc):
            result = record(keys, trace)
            rows.extend(('rec', key, fc._rec_at, None, blob) for key, blob in result)
            return result

        def late(key, before, row, rows=rows, fc=fc):
            rows.append(('late', key, before, fc.late_key(key, before, row), pack_row(row)))

        fc.record, fc._keep_late = recorded, late

        try:
            continuation = capture(walk, prefix, method, phase, legacy=False)
        except ValueError as error:
            discrepancies.append({'target_steps': len(prefix), 'incoming_compatible': False, 'reason': str(error)})
            continue
        parent = continuation.state
        native_mask = walk.mask_of(parent.steps)
        old = np.ones(fc.draws.n, dtype=bool) if incoming is None else incoming
        new = np.ones(fc.draws.n, dtype=bool) if native_mask is None else native_mask
        if not np.array_equal(old, new):
            discrepancies.append({'target_steps': len(prefix), 'incoming_compatible': False,
                                  'changed_draws': int(np.count_nonzero(old != new))})
            continue
        if walk.out:
            raise ValueError("Prefix capture unexpectedly emitted paths")
        # Keep the complete saved sibling population, including no-event watch branches.
        allowed = {history(p.steps[:i]) for p in source['paths'] for i in range(len(prefix), len(p.steps) + 1)}
        missing = set()
        completing = []
        def bounded(function, walk=walk, allowed=allowed, missing=missing, completing=completing):
            @functools.wraps(function)
            def call(self, state, *args, **kwargs):
                if self is walk and isinstance(state, _S) and history(state.steps) not in allowed:
                    mask = self.mask_of(state.steps)
                    if mask is not None and not mask.any():
                        return None
                    if not any(state.steps[:len(root)] == root for root in completing):
                        missing.add(state.steps)
                        if complete_missing:
                            completing.append(state.steps)
                    if not complete_missing:
                        return None
                return function(self, state, *args, **kwargs)
            return call

        emitted = _Walk.emit

        def progress(self, s, outcome, emitted=emitted, walk=walk):
            result = emitted(self, s, outcome)
            if self is walk and len(self.out) % 100 == 0:
                print({'unit': number, 'saved_histories_refreshed': len(self.out)}, flush=True)
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(_Walk, 'emit', progress))
            for name, function in list(vars(_Walk).items()):
                if not callable(function) or name.startswith('__'):
                    continue
                signature = inspect.signature(function)
                parameters = list(signature.parameters)
                if len(parameters) > 1 and parameters[1] == 's' and signature.return_annotation in ('None', None):
                    stack.enter_context(patch.object(_Walk, name, bounded(function)))
            continuation.run()
        uncovered, altered = correspondence(walk, source)
        if fc._raise_more - fc._raise_open:
            raise ValueError('Refresh opens additional financing prefixes')
        report = {'incoming_compatible': True, 'target_steps': len(prefix), 'saved': len(source['paths']), 'refreshed': len(walk.out),
                  'unmatched_history_draws': uncovered,
                  'changed_finances': altered, 'unknown_prefixes': len(missing),
                  'completed_missing_branches': bool(complete_missing and missing)}
        report['missing'] = sorted(missing)
        discrepancies.append(report)
        # This is an adoption gate. Pruning cannot be used to claim mass conservation.
        if altered or (not complete_missing and (missing or uncovered)):
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
        fields = {}
        for name in parallel.FIELDS:
            values, before = getattr(fc, name), control[name]
            if any(value != before[key] for key, value in values.items() if key in before):
                raise ValueError(f'Original {name} changed during refresh')
            fields[name] = {k: v for k, v in values.items() if k not in before}
        outputs.append({**source, 'ev_range': getattr(fc, 'ev_range', None), 'parent': parent, 'fields': fields,
                        'sets': {name: set(getattr(fc, name)) - control[name] for name in parallel.SETS},
                        'paths': walk.out, 'equivalence': walk.keys, 'rows': rows,
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
    result = refresh(number, plan['units'][number], saved, control,
                     complete_missing=os.environ.get('NOTES_COMPLETE_MISSING') == '1')
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

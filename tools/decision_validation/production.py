"""Validate the integrated engine against preserved candidate outputs; never adopt them."""
from __future__ import annotations

import gzip
import json
import os
import pickle
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

from app.disputes import forecast as F
from tools import notes_recover as R
from tools.notes_resume import capture

ROOT = Path('var/decision-production')


def restore():
    raw = Fernet(os.environ['DECISION_VALIDATION_KEY'].encode()).decrypt(
        Path('tools/decision_validation/inputs.enc').read_bytes())
    inputs = pickle.loads(gzip.decompress(raw))
    return pickle.loads(inputs['plan']), pickle.loads(inputs['control'])


def validate_unit(number):
    plan, control = restore()
    result = R.recover(number, plan['units'][number], control)
    with gzip.open(Path('var/validated-reference') / f'unit-{number}.pkl.gz', 'rb') as stream:
        reference = pickle.load(stream)
    assert len(result['outputs']) == len(reference['outputs'])
    for actual, expected in zip(result['outputs'], reference['outputs'], strict=True):
        assert actual['parent'] == expected['parent'], 'Saved incoming state changed'
        assert actual['paths'] == expected['paths'], 'Histories, probability edges or classes changed'
        assert actual['equivalence'] == expected['equivalence'], 'Financial equivalence changed'
    with gzip.open(ROOT / f'unit-{number}.pkl.gz', 'wb', compresslevel=1) as stream:
        pickle.dump(result, stream, protocol=5)
    return {'unit': number, 'paths': sum(len(o['paths']) for o in result['outputs']),
            'candidate_parity': True,
            'max_error': max(o['max_probability_error'] for o in result['outputs'])}


def validate_boundary():
    sys.path.insert(0, 'tests')
    import test_notes_decision as fixture

    def selected():
        fc, _, walk, _ = fixture.case.__wrapped__()
        original = fc.verdict_classes
        fc.verdict_classes = lambda d: {fixture.MERGE_STEPS[1][2]: original(d)[fixture.MERGE_STEPS[1][2]]}
        return fc, walk

    _, walk = selected()
    saved = capture(walk, fixture.MERGE_STEPS[:14], 'notes_petition', 'ruling', legacy=True)
    try:
        saved.run()
    except ValueError as exc:
        assert 'incoming recovery population' in str(exc)
    else:
        raise AssertionError('Incompatible old boundary accepted')
    fc, walk = selected()
    saved = capture(walk, fixture.MERGE_STEPS[:10], 'ripe_i1', legacy=True)
    assert not walk.out
    saved.run()
    conditional = [replace(p, edges=p.edges[len(saved.state.edges):]) for p in walk.out]
    paths = F.expand_classes(conditional, fc.nodes, fc.draws.n)
    incoming = walk.mask_of(saved.state.steps)
    expected = np.ones(fc.draws.n) if incoming is None else incoming.astype(float)
    keys = sorted({k for p in paths for edge, _ in p.edges for k in F.atoms(edge)})
    rng, maximum = np.random.default_rng(291), 0.0
    for _ in range(3):
        dist = F.Dist({k: dict(zip(fc.nodes[k].branches,
                                rng.dirichlet(np.ones(len(fc.nodes[k].branches))), strict=True)) for k in keys})
        total = np.zeros(fc.draws.n)
        for p in paths:
            mask = F.path_mask(p, fc.draws.n)
            total += F.path_probability(p.edges, dist) * (1 if mask is None else mask)
        maximum = max(maximum, float(abs(total - expected).max()))
        np.testing.assert_allclose(total, expected, atol=1e-10, rtol=0)
    assert len(walk.out) == 306
    return {'old_boundary_rejected': True, 'paths': len(walk.out),
            'incoming_draws': int(expected.sum()), 'max_error': maximum}


if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    print(json.dumps({'started': sys.argv[1]}), flush=True)
    report = validate_boundary() if sys.argv[1] == 'boundary' else validate_unit(int(sys.argv[1]))
    report.update(complete=True, seconds=round(time.monotonic() - started, 2))
    (ROOT / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

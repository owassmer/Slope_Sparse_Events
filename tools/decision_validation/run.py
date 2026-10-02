"""Isolated proposal validation; never adopts outputs or requests Jev judgments."""
from __future__ import annotations

import gzip
import inspect
import json
import os
import pickle
import sys
import textwrap
import time
import traceback
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.fernet import Fernet

from app.disputes import forecast as F
from tools import notes_recover as R
from tools.decision_validation import context_candidate as C
from tools.decision_validation.load_candidate import _resume, patches

ROOT = Path('var/decision-validation')


def restore():
    raw = Fernet(os.environ['DECISION_VALIDATION_KEY'].encode()).decrypt(
        Path('tools/decision_validation/inputs.enc').read_bytes())
    inputs = pickle.loads(gzip.decompress(raw))
    return pickle.loads(inputs['plan']), pickle.loads(inputs['control'])


def context_patches():
    return [patch.object(F._Walk, 'node', C.newnode),
            patch.object(F._Walk, 'situation', C.situation),
            patch.object(F.Forecaster, '_split', C.split)]


def continuation_patch():
    source = textwrap.dedent(inspect.getsource(F._Walk.settle))
    source = source.replace(
        'self.tail(self.take(s, ("settle", interval, "@1=yes"), (k, "yes"), (a3, q4)), "settled")',
        'then_no(self.take(s, ("settle", interval, "@1=yes"), (k, "yes"), (a3, q4)))')
    source = source.replace('lambda y: self.tail(y, "settled"), then_no)', 'then_no, then_no)')
    namespace = dict(F.__dict__)
    exec(compile(source, '<settlement-continuation-experiment>', 'exec'), namespace)
    return patch.object(F._Walk, 'settle', namespace['settle'])


def decision_first(walk, state):
    decision = walk._trace(state.steps + (("enforce", "post", "none"),)).day[-1]
    window = walk._trace(state.steps + (("settle", "I3", "no"),)).day[-1]
    both = (decision < walk.N) & (window < walk.N)
    return bool((both & (decision < window)).any())


def run_unit(number, mode):
    plan, control = restore()
    started = time.monotonic()
    emitted = 0
    emit = F._Walk.emit

    def progress(walk, *args, **kwargs):
        nonlocal emitted
        result = emit(walk, *args, **kwargs)
        emitted += 1
        if emitted % 100 == 0:
            print(json.dumps({'unit': number, 'mode': mode, 'histories_emitted': emitted,
                              'seconds': round(time.monotonic() - started, 1)}), flush=True)
        return result

    def capture(*args, **kwargs):
        saved = _resume.capture(*args, **kwargs)

        def run():
            with ExitStack() as stack:
                for item in context_patches():
                    stack.enter_context(item)
                if mode == 'continuation':
                    stack.enter_context(continuation_patch())
                if mode == 'ordered':
                    stack.enter_context(patch.object(F._Walk, 'levy_first', decision_first))
                return saved.run()
        return SimpleNamespace(state=saved.state, run=run)

    print(json.dumps({'unit': number, 'mode': mode, 'status': 'started'}), flush=True)
    try:
        with ExitStack() as stack:
            for item in patches():
                stack.enter_context(item)
            if mode in ('scoped', 'ordered'):
                from tools.decision_validation.scoped_appeal import patches as scoped_patches
                for item in scoped_patches():
                    stack.enter_context(item)
            stack.enter_context(patch.object(R, 'capture', capture))
            stack.enter_context(patch.object(F._Walk, 'emit', progress))
            result = R.recover(number, plan['units'][number], control)
        with gzip.open(ROOT / f'unit-{number}.pkl.gz', 'wb', compresslevel=1) as stream:
            pickle.dump(result, stream, protocol=5)
        report = {'unit': number, 'mode': mode, 'complete': True,
                  'paths': sum(len(o['paths']) for o in result['outputs']),
                  'max_error': max(o['max_probability_error'] for o in result['outputs'])}
    except Exception as exc:
        (ROOT / 'error.txt').write_text(traceback.format_exc())
        tb = exc.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_name == 'recover' and 'total' in tb.tb_frame.f_locals:
                values = tb.tb_frame.f_locals
                saved = {k: values[k] for k in ('parent', 'conditional', 'expanded', 'expected', 'total', 'dist', 'keys')}
                saved.update(paths=values['walk'].out, nodes=values['fc'].nodes,
                             classes=values['fc']._qcls, canonical=values['fc']._qcanon)
                with gzip.open(ROOT / f'unit-{number}-failed.pkl.gz', 'wb', compresslevel=1) as stream:
                    pickle.dump(saved, stream, protocol=5)
            tb = tb.tb_next
        report = {'unit': number, 'mode': mode, 'complete': False, 'error': str(exc)}
    report['seconds'] = round(time.monotonic() - started, 2)
    (ROOT / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    return 0 if report['complete'] else 1


def focused():
    import pytest

    sys.path.insert(0, 'tests')
    with ExitStack() as stack:
        for item in patches():
            stack.enter_context(item)
        return pytest.main(['-q', 'tools/decision_validation/candidate/test_question_petition.py',
                            'tests/test_appeal_state.py', 'tests/test_jev_states.py',
                            'tests/test_notes_decision.py', f'--junitxml={ROOT}/focused.xml'])



def boundary_replay():
    import numpy as np
    from dataclasses import replace
    sys.path.insert(0, 'tests')
    import test_notes_decision as fixture
    from tools.decision_validation.scoped_appeal import patches as scoped_patches
    started = time.monotonic()
    with ExitStack() as stack:
        for item in patches() + scoped_patches():
            stack.enter_context(item)
        fc, dispute, walk, key = fixture.case.__wrapped__()
        verdict_classes = fc.verdict_classes
        fc.verdict_classes = lambda d, fn=verdict_classes: {fixture.MERGE_STEPS[1][2]: fn(d)[fixture.MERGE_STEPS[1][2]]}
        saved = _resume.capture(walk, fixture.MERGE_STEPS[:14], 'notes_petition', 'ruling', legacy=True)
        try:
            saved.run()
        except ValueError as exc:
            assert 'incoming recovery population' in str(exc)
        else:
            raise AssertionError('The incompatible old boundary was not rejected')
        fc, dispute, walk, key = fixture.case.__wrapped__()
        verdict_classes = fc.verdict_classes
        fc.verdict_classes = lambda d, fn=verdict_classes: {fixture.MERGE_STEPS[1][2]: fn(d)[fixture.MERGE_STEPS[1][2]]}
        saved = _resume.capture(walk, fixture.MERGE_STEPS[:10], 'ripe_i1', legacy=True)
        parent = saved.state
        for item in context_patches():
            stack.enter_context(item)
        stack.enter_context(patch.object(F._Walk, 'levy_first', decision_first))
        original_emit = F._Walk.emit
        count = 0
        def emit(self, *args, **kwargs):
            nonlocal count
            result = original_emit(self, *args, **kwargs)
            count += 1
            if count % 100 == 0:
                print(json.dumps({'histories_emitted': count, 'seconds': round(time.monotonic()-started,1)}), flush=True)
            return result
        stack.enter_context(patch.object(F._Walk, 'emit', emit))
        saved.run()
        paths = F.expand_classes([replace(p, edges=p.edges[len(parent.edges):]) for p in walk.out], fc.nodes, fc.draws.n)
        expected = walk.mask_of(parent.steps)
        expected = np.ones(fc.draws.n) if expected is None else expected.astype(float)
        keys = {k for p in paths for edge, branch in p.edges for k in F.atoms(edge)}
        rng = np.random.default_rng(291)
        maximum = 0.0
        for _ in range(3):
            dist = F.Dist({k: dict(zip(fc.nodes[k].branches, rng.dirichlet(np.ones(len(fc.nodes[k].branches))), strict=True)) for k in sorted(keys)})
            total = np.zeros(fc.draws.n)
            for p in paths:
                mask = F.path_mask(p, fc.draws.n)
                total += F.path_probability(p.edges, dist) * (1 if mask is None else mask)
            maximum = max(maximum, float(abs(total-expected).max()))
            np.testing.assert_allclose(total, expected, atol=1e-10, rtol=0)
    report = {'old_boundary_rejected': True, 'new_prefix_length': 10, 'paths': len(walk.out),
              'incoming_draws': int(expected.sum()), 'max_error': maximum,
              'seconds': round(time.monotonic()-started,2)}
    (ROOT/'boundary-replay.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    return 0


def boundary():
    """Compare the saved fixture's eligibility under the two petition snapshots."""
    import numpy as np
    from dataclasses import replace
    sys.path.insert(0, 'tests')
    import test_notes_decision as fixture
    prefix = fixture.MERGE_STEPS[:14]
    results = {}
    with ExitStack() as stack:
        for item in patches():
            stack.enter_context(item)
        corrected = F._Prefix.of.__func__

        def historical(cls, trace, *args, **kwargs):
            result = corrected(cls, trace, *args, **kwargs)
            petition = trace.events.petition.copy()
            if trace.rows is not None:
                widened = np.zeros(len(result.petition), dtype=petition.dtype)
                widened[trace.rows] = petition
                petition = widened
            return replace(result, petition=petition)

        for mode in ('legacy', 'corrected'):
            fc, d, walk, key = fixture.case.__wrapped__()
            rows = []
            with patch.object(F._Prefix, 'of', classmethod(historical if mode == 'legacy' else corrected)):
                for i, (node, ctx, answer) in enumerate(prefix):
                    if not answer.startswith('@'):
                        continue
                    probe = prefix[:i] + ((node, ctx, walk.quiet_of(node)),)
                    tr = walk._raw(probe)
                    mask = walk.mask_of(prefix[:i + 1])
                    prior = walk.mask_of(prefix[:i])
                    rows.append({'index': i, 'step': prefix[i], 'day': tr.day[-1],
                                 'petition': tr.petition, 'group': tr.groups,
                                 'eligible_group': walk.walk_groups(probe), 'mask': mask,
                                 'prior': np.ones(fc.draws.n, dtype=bool) if prior is None else prior})
            results[mode] = rows
    report = []
    for old, new in zip(results['legacy'], results['corrected'], strict=True):
        np.testing.assert_array_equal(old['day'], new['day'])
        np.testing.assert_array_equal(old['group'], new['group'])
        changed = np.flatnonzero(old['mask'] != new['mask'])
        report.append({'index': old['index'], 'step': old['step'],
                       'legacy_count': int(old['mask'].sum()), 'corrected_count': int(new['mask'].sum()),
                       'changed_draws': changed.tolist(),
                       'examples': [{'draw': int(i), 'day': int(new['day'][i]),
                                     'final_petition': int(old['petition'][i]),
                                     'before_question_petition': int(new['petition'][i]),
                                     'legacy_eligible_group': int(old['eligible_group'][i]),
                                     'corrected_eligible_group': int(new['eligible_group'][i]),
                                     'legacy_prior': bool(old['prior'][i]),
                                     'corrected_prior': bool(new['prior'][i])} for i in changed[:5]]})
    (ROOT / 'boundary.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    return 0

if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(boundary_replay() if sys.argv[1] == 'boundary_replay' else boundary() if sys.argv[1] == 'boundary' else focused() if sys.argv[1] == 'focused' else run_unit(int(sys.argv[2]), sys.argv[1]))

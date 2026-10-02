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
                return saved.run()
        return SimpleNamespace(state=saved.state, run=run)

    print(json.dumps({'unit': number, 'mode': mode, 'status': 'started'}), flush=True)
    try:
        with ExitStack() as stack:
            for item in patches():
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


if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(focused() if sys.argv[1] == 'focused' else run_unit(int(sys.argv[2]), sys.argv[1]))

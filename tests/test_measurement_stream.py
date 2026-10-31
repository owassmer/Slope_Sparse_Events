"""Partial diagnostic results remain useful when the runner stops a walk."""
import json
import os
import signal
from types import SimpleNamespace

import pytest

from tools.measurement_stream import MeasurementStream, emitted_histories


def test_stream_stop_preserves_counts_and_unknowns(tmp_path):
    target = tmp_path / 'draw.json'
    with pytest.raises(SystemExit), MeasurementStream(target, {'native_row': 0}) as stream:
        stream.append(dict(checks=[dict(row=0, questions_checked={'offer': 2}, violations=[
            dict(node='offer', kind='future_conditioning', date='2024-05-22')])]))
        stream.append(dict(checks=[dict(row=0, violations=[], check_error='missing record')]))
        signal.raise_signal(signal.SIGALRM)
        periodic = json.loads(target.read_text())
        assert periodic['status'] == 'running'
        assert periodic['histories'] == 2
        os.kill(os.getpid(), signal.SIGTERM)
    result = json.loads(target.read_text())
    assert result['status'] == 'stopped'
    assert result['by_question_node'] == {'offer': 1}
    assert result['questions_checked'] == {'offer': 2}
    assert result['examples']['offer']['date'] == '2024-05-22'
    assert result['unchecked_history_draws'] == 1
    assert len(target.with_suffix('.jsonl').read_text().splitlines()) == 2


def test_emit_callback_after_recording_and_final_summary(tmp_path):
    walk = SimpleNamespace(out=[], recorded=False)
    def emit(path):
        walk.out.append(path)
        walk.recorded = True
    walk.emit = emit
    target = tmp_path / 'draw.json'
    with MeasurementStream(target, {}) as stream:
        def consume(path):
            assert walk.recorded
            stream.append({'path': path})
        with emitted_histories(walk, consume):
            walk.emit('history')
    assert walk.emit is emit
    result = json.loads(target.read_text())
    assert result['status'] == 'completed'
    assert result['histories'] == 1

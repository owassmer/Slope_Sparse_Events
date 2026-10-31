"""Incomplete or mismatched evidence must never become a completed financial analysis."""
import json

import pytest
from typer.testing import CliRunner

from app import pipeline
from app.agent.run_store import RunStore
from app.cli import cli

SNAPSHOT = 'akoustis_20240514'


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'RECORDED', tmp_path)
    monkeypatch.setattr(pipeline, 'build_snapshot', lambda sid: {'evidence_manifest_hash': 'current'})

    def investigate(snapshot, arm):
        store = RunStore('case-run', root=tmp_path, meta={
            'snapshot_id': snapshot, 'arm': arm, 'evidence_manifest_hash': 'current'})
        store.lock()
        record = {'run_id': 'case-run', 'snapshot_id': snapshot, 'arm': arm, 'status': 'CANDIDATE_READY'}
        (store.dir / 'run.json').write_text(json.dumps(record))
        return record

    monkeypatch.setattr(pipeline, 'investigate', investigate)
    calls = []

    def build(run, root, *, progress):
        calls.append(run)
        for stage in ('engine_context', 'event_situations', 'jev_forecasts', 'financial_analysis', 'page'):
            progress(stage)
        for name in ('analysis.json', 'page.json', 'collections.csv', 'cashflows.csv', 'jev_log.jsonl.gz'):
            (root / run / name).write_text('{}')
        return {'not_modelled': []}

    monkeypatch.setattr(pipeline, 'build', build)
    return tmp_path, calls


def test_one_invocation_connects_the_stages_and_returns_existing_viewer_route(flow):
    root, calls = flow
    result = CliRunner().invoke(cli, ['analyze-case', SNAPSHOT])
    assert result.exit_code == 0, result.output
    assert calls == ['case-run']
    assert 'jev_forecasts' in result.output and 'financial_analysis' in result.output
    assert '/runs/case-run' in result.output
    assert json.loads((root / 'case-run/flow.json').read_text())['status'] == 'complete'


def test_incomplete_investigation_never_calls_the_financial_engine(flow, monkeypatch):
    _, calls = flow
    monkeypatch.setattr(pipeline, 'investigate', lambda *a: {'status': 'INCOMPLETE_REVIEW',
                                                          'incomplete_reasons': ['missing payment evidence']})
    result = CliRunner().invoke(cli, ['analyze-case', SNAPSHOT])
    assert result.exit_code == 1 and 'missing payment evidence' in result.output
    assert not calls


@pytest.mark.parametrize('changed', ['case', 'evidence', 'packet'])
def test_reused_record_must_match_case_evidence_and_locked_log(flow, monkeypatch, changed):
    root, calls = flow
    pipeline.investigate(SNAPSHOT, 'agent_plus_jev')
    if changed == 'case':
        record = root / 'case-run/run.json'
        value = json.loads(record.read_text())
        value['snapshot_id'] = 'akoustis_20240620'
        record.write_text(json.dumps(value))
    elif changed == 'evidence':
        monkeypatch.setattr(pipeline, 'build_snapshot', lambda sid: {'evidence_manifest_hash': 'changed'})
    else:
        packet = root / 'case-run/packet.json'
        value = json.loads(packet.read_text())
        value['chain_head'] = 'tampered'
        packet.write_text(json.dumps(value))
    with pytest.raises(pipeline.FlowError):
        pipeline.analyze_case(SNAPSHOT, run_id='case-run', progress=lambda stage: None)
    assert not calls


def test_failed_forecast_does_not_become_a_completed_analysis(flow, monkeypatch):
    root, _ = flow
    def fail(run, base, *, progress):
        progress('jev_forecasts')
        raise RuntimeError('forecast unavailable')
    monkeypatch.setattr(pipeline, 'build', fail)
    result = CliRunner().invoke(cli, ['analyze-case', SNAPSHOT])
    assert result.exit_code == 1 and 'jev_forecasts: forecast unavailable' in result.output
    status = json.loads((root / 'case-run/flow.json').read_text())
    assert status['status'] == 'failed' and status['stage'] == 'jev_forecasts'
    assert not (root / 'case-run/page.json').exists()


def test_reusing_a_ready_record_does_not_repeat_investigation(flow, monkeypatch):
    _, calls = flow
    pipeline.investigate(SNAPSHOT, 'agent_plus_jev')
    def unexpected(*args):
        raise AssertionError('investigation was repeated')
    monkeypatch.setattr(pipeline, 'investigate', unexpected)
    result = CliRunner().invoke(cli, ['analyze-case', SNAPSHOT, '--run', 'case-run'])
    assert result.exit_code == 0, result.output
    assert calls == ['case-run']


def test_unmodelled_disputes_cannot_be_reported_as_complete(flow, monkeypatch):
    root, _ = flow
    monkeypatch.setattr(pipeline, 'build', lambda *a, **k: {'not_modelled': [{'title': 'Unresolved obligation'}]})
    result = CliRunner().invoke(cli, ['analyze-case', SNAPSHOT])
    assert result.exit_code == 1 and 'Unresolved obligation' in result.output
    assert json.loads((root / 'case-run/flow.json').read_text())['status'] == 'failed'


@pytest.mark.parametrize('saved_evidence, reaches_coverage', [('expanded', True), ('older', False)])
def test_saved_pool_uses_its_bound_evidence_before_any_judging(flow, monkeypatch, saved_evidence, reaches_coverage):
    from app.analysis import pooled
    from app.disputes import pool

    root, calls = flow
    pipeline.investigate(SNAPSHOT, 'agent_plus_jev')
    monkeypatch.setattr(pipeline, 'build_snapshot', lambda sid: {'evidence_manifest_hash': 'expanded'})
    monkeypatch.setattr(pooled, 'binding', lambda *args: {'evidence_manifest_hash': 'expanded'})
    directory = root / 'pool'
    directory.mkdir()
    (directory / 'binding.json').write_text(json.dumps({'evidence_manifest_hash': saved_evidence}))
    checked = []
    def coverage(*args, count_only=False, **kwargs):
        assert count_only
        checked.append(True)
        raise RuntimeError('coverage reached without forecasting')
    monkeypatch.setattr(pool, 'judge', coverage)
    with pytest.raises(pipeline.FlowError, match='coverage reached' if reaches_coverage else 'Saved pool does not match'):
        pipeline.analyze_case(SNAPSHOT, run_id='case-run', pool_dir=directory, progress=lambda stage: None)
    assert bool(checked) == reaches_coverage
    assert not calls

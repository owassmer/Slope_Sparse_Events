"""A bounded fixture through the real financial flow; no live agent/provider calls."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
from fastapi.testclient import TestClient

from app import pipeline
from app.agent import jev
from app.agent.run_store import RunStore
from app.analysis import build as analysis
from app.config import CASES_DIR
from app.disputes.akoustis_pre_d import REVIEW, SETUP, SNAP, judgment
from app.domain.investigation import JevCallRecord, SemanticObservation
from app.evidence import snapshot, store
from app.web import app as web


class FixtureJev:
    """Fixed provider responses, through the actual DisputeProfile and question-state builders."""
    calls = []

    def __init__(self, **kwargs):
        self.run_id = kwargs['run_id']

    async def judge(self, *, profile, question_ids, state, subject_ids, source_content_hashes, criteria=None):
        self.calls.append((profile, state))
        cid = f'fixture-{len(self.calls)}'
        observations = []
        for qid in question_ids:
            q = jev.registry_question(qid)
            options = (criteria or {}).get(qid) or q['prompt'].get('criteria') or {}
            observations.append(SemanticObservation(
                observation_id=f'{cid}-{qid}', call_id=cid, profile=profile, question_id=qid,
                question_version=str(q['version']), primitive=q['primitive'], answer=None,
                noul_value=0.3 if q['primitive'] == 'noul' else None,
                probabilities={k: 1 / len(options) for k in options} if options else None,
                confidence=0.9, subject_ids=subject_ids))
        call = JevCallRecord(call_id=cid, run_id=self.run_id, profile=profile, subject_ids=subject_ids,
                             question_ids=tuple(question_ids), registry_version='fixture', requested_model='fixture',
                             returned_model='fixture', state_sha256='fixture', source_content_hashes=source_content_hashes,
                             attempts_used=0, usage=None, raw_response={}, created_at=datetime.now(UTC).isoformat())
        return call, observations

    def usage_summary(self):
        return {'provider': 'fixture', 'requests': len(self.calls), 'spent_usd': '0'}


def test_record_to_financial_page_through_real_components(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    recorded = tmp_path / 'recorded'
    manifest = snapshot.build_snapshot(SNAP, out_dir=evidence)
    monkeypatch.setattr(pipeline, 'RECORDED', recorded)
    monkeypatch.setattr(pipeline, 'build_snapshot', lambda sid: manifest)
    monkeypatch.setattr(store, 'db_path', lambda sid: evidence / f'{sid}.sqlite')
    monkeypatch.setattr(analysis, 'VAR', tmp_path / 'var')
    monkeypatch.setattr(analysis, 'setup_from_inputs', lambda inputs, review: replace(SETUP, horizon=REVIEW + timedelta(days=14)))
    monkeypatch.setattr(analysis, 'record_item_slots', lambda *a, **k: {})  # no findings in this mechanics fixture
    monkeypatch.setattr(jev, 'JevAdapter', FixtureJev)
    monkeypatch.setenv('SLOPE_WALK_PROCESSES', '1')
    monkeypatch.setenv('SLOPE_RUNS_ROOT', str(recorded))
    FixtureJev.calls = []

    def recorded_agent(snapshot_id, arm):
        inputs = json.loads((CASES_DIR / SNAP / 'run_inputs.json').read_text())
        run = RunStore('flow-fixture', root=recorded, meta={
            'snapshot_id': snapshot_id, 'arm': arm, 'run_inputs': inputs,
            'evidence_manifest_hash': manifest['evidence_manifest_hash']})
        d = judgment(stage='judgment_entered', motions=(), components=(), financing=(),
                     amount=judgment().amount.model_copy(update={'value': 200_000_000}))
        run.put('dispute_instantiated', d)
        run.lock()
        record = {'run_id': run.run_id, 'snapshot_id': snapshot_id, 'arm': arm, 'status': 'CANDIDATE_READY'}
        (run.dir / 'run.json').write_text(json.dumps(record))
        return record

    monkeypatch.setattr(pipeline, 'investigate', recorded_agent)
    stages = []
    result = pipeline.analyze_case(SNAP, progress=stages.append)
    out = recorded / 'flow-fixture'
    data = json.loads((out / 'analysis.json').read_text())
    model = analysis.model_from_json(data['model'])
    assert FixtureJev.calls and model.judgments
    assert np.isclose(model.probs().sum(), 1)
    assert len(data['dates']) == 14
    assert result['status'] == 'complete'
    assert stages == ['evidence', 'investigation', 'validate_record', 'engine_context',
                      'event_situations', 'jev_forecasts', 'financial_analysis', 'page', 'complete']
    assert all((out / f).stat().st_size for f in result['artifacts'])
    page = TestClient(web.app).get(result['page_route'])
    assert page.status_code == 200 and 'page-data' in page.text
    assert '/runs/flow-fixture/investigation' in page.text
    assert 'No Jev answers yet' not in page.text

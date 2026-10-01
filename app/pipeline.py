"""One invocation of the existing investigation, Jev forecast, engine and page flow.

This coordinates local execution, or continues a saved pool on the machine holding its paths.
Launching remote compute and transferring distributed artifacts remain separate execution concerns.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.agent.investigation import investigate
from app.agent.run_store import RunStore
from app.analysis.build import build
from app.config import RECORDED
from app.evidence import SNAPSHOTS
from app.evidence.snapshot import build_snapshot


class FlowError(RuntimeError):
    """The current stage could not produce a valid input for the next stage."""


def analyze_case(snapshot_id: str, *, run_id: str | None = None,
                 pool_dir: Path | str | None = None, processes: int = 4,
                 progress: Callable[[str], None] = print) -> dict:
    """Use a dated case's configured inputs, or an explicitly selected ready investigation."""
    if snapshot_id not in SNAPSHOTS:
        raise FlowError(f"Unknown dated case {snapshot_id!r}; choose from {', '.join(SNAPSHOTS)}")
    if pool_dir is not None and run_id is None:
        raise FlowError('Adopting a saved pool requires --run')
    if run_id is not None and (not run_id or '/' in run_id or '\\' in run_id or '..' in run_id):
        raise FlowError('Invalid recorded run ID')
    state = {'snapshot_id': snapshot_id, 'run_id': run_id, 'status': 'running',
             'started_at': datetime.now(UTC).isoformat(), 'stage': 'evidence'}
    out = None

    def save():
        if out is not None:
            pending = out / 'flow.json.tmp'
            pending.write_text(json.dumps(state, indent=2) + '\n')
            pending.replace(out / 'flow.json')

    def stage(name):
        state['stage'] = name
        save()
        progress(name)

    try:
        stage('evidence')
        manifest = build_snapshot(snapshot_id)
        stage('investigation')
        if run_id is None:
            record = investigate(snapshot_id, 'agent_plus_jev')
            run_id = record.get('run_id')
        else:
            path = RECORDED / run_id / 'run.json'
            record = json.loads(path.read_text())
        state['run_id'] = run_id
        if run_id and (RECORDED / run_id).is_dir():
            out = RECORDED / run_id
            save()
        if record.get('status') != 'CANDIDATE_READY':
            reason = record.get('error') or record.get('failure') or record.get('incomplete_reasons') or record.get('status')
            raise FlowError(f'Investigation is not ready: {reason}')
        if out is None:
            raise FlowError('Investigation did not produce a recorded run')
        stage('validate_record')
        store = RunStore(run_id, root=RECORDED)
        if not store.locked or not store.events:
            raise FlowError('Investigation has no verified locked packet')
        meta = store.events[0].payload
        if record.get('snapshot_id') != snapshot_id or meta.get('snapshot_id') != snapshot_id:
            raise FlowError('Recorded investigation belongs to a different dated case')
        if record.get('arm') != 'agent_plus_jev' or meta.get('arm') != 'agent_plus_jev':
            raise FlowError('This flow requires an agent-plus-Jev investigation')
        if meta.get('evidence_manifest_hash') != manifest['evidence_manifest_hash']:
            raise FlowError('Evidence changed since the investigation; investigate the current snapshot again')
        if pool_dir is not None:
            from app.analysis import pooled
            artifacts = pooled.build(run_id, RECORDED, pool_dir, processes=processes, progress=stage)
        else:
            data = build(run_id, RECORDED, progress=stage)
            if data.get('not_modelled'):
                raise FlowError('Analysis has unresolved disputes: ' + ', '.join(d['title'] for d in data['not_modelled']))
            artifacts = ['analysis.json', 'page.json', 'collections.csv', 'cashflows.csv', 'jev_log.jsonl.gz']
        missing = [name for name in artifacts if not (out / name).is_file()]
        if missing:
            raise FlowError('Analysis did not write: ' + ', '.join(missing))
        state.update(status='complete', stage='complete', finished_at=datetime.now(UTC).isoformat(),
                     artifacts={name: str(out / name) for name in artifacts}, page_route=f'/runs/{run_id}')
        save()
        progress('complete')
        return state
    except (Exception, SystemExit) as exc:
        state.update(status='failed', error=str(exc), finished_at=datetime.now(UTC).isoformat())
        save()
        raise FlowError(f"{state['stage']}: {exc}" + (f' (run {run_id})' if run_id else '')) from exc

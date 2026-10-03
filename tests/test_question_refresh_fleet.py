"""Free processes share work; recovery cannot steal a replacement claim."""
import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from tools import question_refresh_fleet as fleet


def test_free_worker_claims_work_outside_its_preferred_range():
    tasks = [{'tag': str(i)} for i in range(4)]
    state = {'remaining': 1, 'available': ['3']}
    info = {'tasks': tasks, 'workers': 4, 'pending': 'status'}
    calls = []

    def one(task):
        calls.append(task['tag'])
        state.update(remaining=0, available=[])
        return task['tag'], 1

    with patch.object(fleet, '_INFO', info), patch.object(fleet, 'claim', return_value='etag'), \
            patch.object(fleet, '_one', side_effect=one), \
            patch.object(fleet.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(json.dumps(state).encode())):
        fleet._consume(0)
    assert calls == ['3']


def test_stale_recovery_preserves_live_completed_and_replaced_claims():
    from botocore.exceptions import ClientError

    now = datetime.now(UTC)
    old = now - timedelta(seconds=900)
    claims = [{'Key': f'run/claims/{tag}.json', 'ETag': tag + '-owner', 'LastModified': old}
              for tag in ('dead', 'live', 'done', 'replaced')]
    calls = []

    class S3:
        def get_paginator(self, _):
            return self

        def paginate(self, **_):
            return [{'Contents': claims}]

        def head_object(self, **kw):
            return {'LastModified': now if '/live.log' in kw['Key'] else old}

        def delete_object(self, **kw):
            calls.append(kw)
            if '/replaced.json' in kw['Key']:
                raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'DeleteObject')

    assert fleet.release_stale(S3(), 'bucket', 'run', {'done'}) == {'live', 'replaced'}
    assert calls == [
        {'Bucket': 'bucket', 'Key': 'run/claims/dead.json', 'IfMatch': 'dead-owner'},
        {'Bucket': 'bucket', 'Key': 'run/claims/replaced.json', 'IfMatch': 'replaced-owner'},
    ]


def test_collector_restart_after_last_commit_recovers_completion(tmp_path):
    import sqlite3

    root = tmp_path / 'd'
    (root / 'balanced').mkdir(parents=True)
    (root / 'balanced/0.pkl.gz').touch()
    (tmp_path / 'prepared.json').write_text(json.dumps({'instances': {'d': {'folder': 'd'}}}))
    (tmp_path / 'fleet.json').write_text(json.dumps({'base': 'run', 'tasks': 1}))
    with sqlite3.connect(root / 'plan.sqlite') as db:
        db.execute('CREATE TABLE ingested_batches(tag TEXT PRIMARY KEY)')
        db.execute("INSERT INTO ingested_batches VALUES('d-0')")
    published = {}

    class S3:
        def put_object(self, **kw):
            published[kw['Key']] = json.loads(kw['Body'])

    result = fleet.collect(S3(), 'bucket', tmp_path)
    assert result['completed_batches'] == result['total_batches'] == 1
    assert published['run/pending.json'] == {'remaining': 0, 'available': []}
    assert json.loads((tmp_path / 'calculated.json').read_text()) == result

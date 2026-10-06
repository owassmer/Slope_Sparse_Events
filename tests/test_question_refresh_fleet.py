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


def test_downloads_overlap_without_unbounded_prefetch(tmp_path):
    import threading
    from pathlib import Path

    barrier = threading.Barrier(2)
    started = []
    lock = threading.Lock()

    class S3:
        def download_file(self, bucket, key, target):
            with lock:
                started.append(key)
            barrier.wait(timeout=3)
            Path(target).write_text(key)

    with fleet.contextlib.closing(fleet.downloaded_outputs(
            S3(), 'bucket', [f'outputs/{i}' for i in range(10)], tmp_path, workers=2)) as outputs:
        key, target = next(outputs)
        assert target.read_text() == key
        assert len(started) <= 4
        seen = {key}
        for key, target in outputs:
            assert target.read_text() == key
            seen.add(key)
    assert len(seen) == 10
    assert list(tmp_path.iterdir()) == []


def test_collector_ingests_prefetched_outputs_and_rejects_incomplete(tmp_path):
    import gzip
    import pickle
    import sqlite3

    import pytest

    root = tmp_path / 'd'
    (root / 'balanced').mkdir(parents=True)
    for i in range(2):
        (root / f'balanced/{i}.pkl.gz').touch()
    (tmp_path / 'prepared.json').write_text(json.dumps({'instances': {'d': {'folder': 'd'}}}))
    (tmp_path / 'fleet.json').write_text(json.dumps({'base': 'run', 'tasks': 2}))

    class S3:
        malformed = True

        def get_paginator(self, _):
            return self

        def paginate(self, **_):
            return [{'Contents': [{'Key': f'run/outputs/d-{i}.pkl.gz'} for i in range(2)]}]

        def download_file(self, bucket, key, target):
            with gzip.open(target, 'wb') as fh:
                pickle.dump('d', fh)
                pickle.dump(('complete', 1 if self.malformed else 0), fh)

        def put_object(self, **_):
            pass

    s3 = S3()
    with pytest.raises(ValueError, match='Incomplete'):
        fleet.collect(s3, 'bucket', tmp_path)
    with sqlite3.connect(root / 'plan.sqlite') as db:
        assert db.execute('SELECT count(*) FROM ingested_batches').fetchone()[0] == 0
    assert not (tmp_path / 'calculated.json').exists()
    assert not list(tmp_path.glob('collect-*'))
    s3.malformed = False
    with patch.object(fleet, 'release_stale', return_value=set()):
        result = fleet.collect(s3, 'bucket', tmp_path)
    assert result['completed_batches'] == 2
    with sqlite3.connect(root / 'plan.sqlite') as db:
        assert set(db.execute('SELECT tag FROM ingested_batches')) == {('d-0',), ('d-1',)}
    assert not list(tmp_path.glob('collect-*'))


def test_queue_retry_preserves_conditional_claim_and_conflict():
    import http.client
    import urllib.error

    conflict = urllib.error.HTTPError('claim', 412, 'already claimed', {}, io.BytesIO())
    attempts = []

    def open_request(request, **_):
        attempts.append(request)
        if len(attempts) == 1:
            raise http.client.RemoteDisconnected('lost acknowledgement')
        raise conflict

    with patch.object(fleet.urllib.request, 'urlopen', side_effect=open_request), \
            patch.object(fleet.time, 'sleep'):
        assert fleet.claim({'claim': 'https://example.test/claim'}) is None
    assert attempts[0] is attempts[1]
    assert attempts[0].get_header('If-none-match') == '*'
    assert attempts[0].data == attempts[1].data


def test_queue_retry_is_bounded_and_does_not_retry_authorization():
    import urllib.error

    import pytest

    with patch.object(fleet.urllib.request, 'urlopen', side_effect=TimeoutError), \
            patch.object(fleet.time, 'sleep') as sleep:
        with pytest.raises(TimeoutError):
            fleet.queue_request('queue')
        assert sleep.call_count == 4
    with patch.object(fleet.urllib.request, 'urlopen', side_effect=
                      urllib.error.HTTPError('queue', 403, 'forbidden', {}, io.BytesIO())) as request:
        with pytest.raises(urllib.error.HTTPError):
            fleet.queue_request('queue')
        assert request.call_count == 1


def test_worker_logs_original_error_before_serializing_failure(capsys):
    import pickle
    import urllib.error

    import pytest

    with patch.object(fleet, '_consume', side_effect=
                      urllib.error.HTTPError('queue', 403, 'forbidden', {}, io.BytesIO())):
        with pytest.raises(RuntimeError, match='HTTPError') as caught:
            fleet._consume_logged(0)
    pickle.dumps(caught.value)
    assert 'HTTP Error 403' in capsys.readouterr().err


def test_pending_body_disconnect_is_retried_and_closed():
    import http.client

    class Broken(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b'{')

    broken = Broken()
    good = io.BytesIO(b'{"remaining":0,"available":[]}')
    with patch.object(fleet.urllib.request, 'urlopen', side_effect=[broken, good]), \
            patch.object(fleet.time, 'sleep'):
        assert fleet.queue_request('queue', json_body=True) == {'remaining': 0, 'available': []}
    assert broken.closed and good.closed

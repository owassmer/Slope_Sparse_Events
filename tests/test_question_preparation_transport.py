import gzip
import http.client
import pickle

from tools import question_preparation_fleet as preparation


def test_truncated_read_retries_without_returning_partial_rows(monkeypatch):
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self):
            calls.append(1)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b'partial', 20)
            return gzip.compress(pickle.dumps([(7, 'complete row')]))
    monkeypatch.setattr(preparation, '_INFO', {'endpoint': 'https://example.invalid', 'token': 'test'})
    monkeypatch.setattr(preparation.urllib.request, 'urlopen', lambda *a, **kw: Response())
    monkeypatch.setattr(preparation.time, 'sleep', lambda _: None)
    assert preparation.request('/rows', {'ids': [7]}) == [(7, 'complete row')]
    assert len(calls) == 2

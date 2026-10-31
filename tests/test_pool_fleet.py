"""Distribution must not change the ordered financial rows behind a conditional question."""
import gzip
import pickle
from types import SimpleNamespace

from app.disputes import pool
from tools import pool_fleet


def test_whole_question_partitions_preserve_order_deduplication_and_watch_filter(tmp_path):
    keys = [f'q{i}' for i in range(500) if pool.bucket(f'q{i}') == 0]
    ctl = {'nodes': {k: SimpleNamespace() for k in keys}, 'classed': {keys[-1]},
           'reads': {'live': [()]}, 'top_owner': 0}
    control = tmp_path / 'control.pkl'
    control.write_bytes(pickle.dumps(ctl))
    rows = tmp_path / 'rows'
    rows.mkdir()
    a, b = [], []
    for k in keys:
        # The earlier copy is in the later filename: dedupe must follow global event order.
        a.extend([((2, 0), 'late', k, (k, 'prefix'), b'duplicate', ('live',)),
                  ((4, 0), 'rec', k, None, b'last', ()),
                  ((0, 0), 'rec', k, None, b'not-live', ('absent',))])
        b.extend([((1, 0), 'late', k, (k, 'prefix'), b'first', ('live',)),
                  ((3, 0), 'rec', k, None, b'middle', ()),
                  ((0, 1), 'rec', k, None, b'other-top', ())])
    (rows / 'part0.pkl').write_bytes(pickle.dumps(a))
    (rows / 'part1.pkl').write_bytes(pickle.dumps(b))
    out = tmp_path / 'bundles'
    published = []
    def publish(path):
        # Reading through the gzip footer requires a fully closed bundle.
        with gzip.open(path, "rb") as fh:
            published.append(pickle.loads(fh.read()))
    pool_fleet.prepare(str(rows), str(control), 0, str(out), publish=publish)
    assert len(published) == pool_fleet.PARTITIONS
    recovered = {}
    for p in range(pool_fleet.PARTITIONS):
        with gzip.open(out / f'0-{p}.pkl.gz', 'rb') as fh:
            data = pickle.load(fh)
        assert not set(recovered) & set(data['keys'])
        for k in data['keys']:
            recovered[k] = [data['facts'][k].blob(i) for i in range(len(data['facts'][k]))]
    assert set(recovered) == set(keys[:-1])
    assert all(value == [b'first', b'middle', b'last'] for value in recovered.values())

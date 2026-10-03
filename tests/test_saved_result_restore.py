import gzip
import pickle
import sqlite3

import pytest

from tools.question_refresh import Results
from tools.saved_result_restore import adopt_batch, decode_batch


def database():
    db = sqlite3.connect(':memory:')
    Results(db)
    db.executescript('CREATE TABLE bindings(id INTEGER PRIMARY KEY,request INTEGER,question TEXT,prefix INTEGER);'
                     'CREATE TABLE ingested_batches(tag TEXT PRIMARY KEY);')
    db.executemany('INSERT INTO bindings VALUES(?,?,?,?)', [(1, 3, 'q', 7), (2, 4, 'r', 9)])
    db.commit()
    return db


def write(path, bad=False):
    with gzip.open(path, 'wb') as fh:
        for item in ['d', (3, [(1, 'wrong' if bad else 'q', 7, b'row1', ('a', None))]),
                     (4, [(2, 'r', 9, b'row2', ('b', b'codes'))]), ('complete', 2)]:
            pickle.dump(item, fh)


def test_parallel_decode_matches_canonical_ingestion_and_resumes_partial_batch(tmp_path):
    source = tmp_path / 'batch.gz'
    write(source)
    canonical, restored = database(), database()
    Results(canonical).ingest(source, 'd')
    requests, rows = decode_batch(restored, source, 'd')
    adopt_batch(restored, 'partial', requests[:1], rows[:1])
    adopt_batch(restored, 'whole', requests, rows)
    for table in ('results', 'finished_requests'):
        assert list(restored.execute('SELECT * FROM ' + table)) == list(canonical.execute('SELECT * FROM ' + table))


def test_invalid_saved_assignment_never_reaches_writer(tmp_path):
    source = tmp_path / 'batch.gz'
    write(source, bad=True)
    db = database()
    with pytest.raises(ValueError, match='global assignment'):
        decode_batch(db, source, 'd')
    assert db.execute('SELECT COUNT(*) FROM results').fetchone()[0] == 0


def test_failed_adoption_rolls_back_rows_and_markers(tmp_path):
    source = tmp_path / 'batch.gz'
    write(source)
    db = database()
    requests, rows = decode_batch(db, source, 'd')
    db.execute('INSERT INTO ingested_batches VALUES(?)', ('duplicate',))
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        adopt_batch(db, 'duplicate', requests, rows)
    assert db.execute('SELECT COUNT(*) FROM results').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM finished_requests').fetchone()[0] == 0

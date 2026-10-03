"""Parallel decoding for restoring already-computed question batches to one SQLite writer."""
from __future__ import annotations

import gzip
import pickle


def decode_batch(db, output, instance):
    """Apply Results.ingest's saved-assignment contract before returning packed inserts."""
    requests, rows = [], []
    with gzip.open(output, 'rb') as fh:
        if pickle.load(fh) != instance:
            raise ValueError('Result belongs to another dispute')
        while True:
            request, records = pickle.load(fh)
            if request == 'complete':
                if records != len(requests) or fh.read(1):
                    raise ValueError('Incomplete or trailing result records')
                break
            expected = list(db.execute('SELECT id,question,prefix FROM bindings '
                                       'WHERE request=? ORDER BY id', (request,)))
            if [(b, k, p) for b, k, p, _, _ in records] != expected:
                raise ValueError('Calculated bindings differ from their global assignment')
            if request in requests:
                raise ValueError('Repeated request in saved batch')
            requests.append(request)
            rows.extend((request, binding, row, pickle.dumps(classes, protocol=5))
                        for binding, _, _, row, classes in records)
    return requests, rows


def adopt_batch(db, tag, requests, rows):
    """Commit only validated rows, request markers and batch marker together."""
    with db:
        completed = {r for r, in db.execute(
            'SELECT request FROM finished_requests WHERE request IN (' + ','.join('?' for _ in requests) + ')',
            requests)} if requests else set()
        db.executemany('INSERT INTO results VALUES(?,?,?)',
                       ((binding, row, classes) for request, binding, row, classes in rows if request not in completed))
        db.executemany('INSERT INTO finished_requests VALUES(?)', ((r,) for r in requests if r not in completed))
        db.execute('INSERT INTO ingested_batches VALUES(?)', (tag,))

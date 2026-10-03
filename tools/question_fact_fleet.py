"""Distribute unchanged fact serialization over whole conditioning prefixes."""
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import sqlite3
import ssl
import tempfile
import threading
import time
from pathlib import Path

from app.disputes import pool
from tools import question_preparation_fleet as transport
from tools import question_refresh_fleet as queue
from tools.pool_fleet import fetch, put
from tools.question_refresh import Results

PARTITIONS = 224  # Refines the existing 14 prefix partitions without splitting a prefix.
_INFO = None
_CONTROL = None


class RemoteFacts:
    """Page saved rows; keep the production SQL deduplication local to one task."""
    def __init__(self, instance, shard, database):
        self.instance, self.shard = instance, shard
        self.local = database
        self.current = {}
        self.seen = 0

    def executescript(self, sql):
        return self.local.executescript(sql)

    def create_function(self, *args):
        return self.local.create_function(*args)

    def execute(self, sql, args=()):
        if sql.startswith('SELECT binding,prefix,mask FROM fact_assignments'):
            if args != (self.shard,):
                raise ValueError('Wrong fact shard')
            return self.rows()
        if sql.startswith('SELECT b.question,r.row,r.classes FROM results'):
            return transport.Rows([self.current[args[0]]])
        return self.local.execute(sql, args)

    def rows(self):
        after = -1
        while True:
            rows = transport.request('/facts', {'instance': self.instance,
                                                'shard': self.shard, 'after': after})
            if not rows:
                return
            self.current = {b: (key, row, classes) for b, _prefix, _mask, key, row, classes in rows}
            for binding, prefix, mask, _key, _row, _classes in rows:
                if binding <= after or prefix % PARTITIONS != self.shard:
                    raise ValueError('Fact page ownership/order differs')
                after = binding
                self.seen += 1
                yield binding, prefix, mask


def read_page(db, shard, after):
    if type(shard) is not int or not 0 <= shard < PARTITIONS or type(after) is not int or after < -1:
        raise ValueError('Invalid fact page')
    return list(db.execute('SELECT f.binding,f.prefix,f.mask,b.question,r.row,r.classes '
        'FROM fact_assignments f JOIN bindings b ON b.id=f.binding '
        'JOIN results r ON r.binding=f.binding '
        'WHERE f.shard=? AND f.binding>? AND f.prefix % ?=? ORDER BY f.binding LIMIT 128',
        (shard % 14, after, PARTITIONS, shard)))


def build(task, target):
    fc = pool.forecaster(_INFO['run'], _CONTROL)
    fc.grouped.update(k for k in fc.classed if fc.nodes[k].node in
                      ('judgment_response', 'financing_at_floor', 'petition_cash_out'))
    count = 0
    inputs = 0
    with tempfile.TemporaryDirectory(prefix='fact-dedup-') as tmp, gzip.open(target, 'wb', compresslevel=1) as output:
        pickle.dump({'identity': _INFO['identity'], 'shard': task['shard']}, output, protocol=5)
        for iid in _INFO['instances']:
            db = sqlite3.connect(Path(tmp) / 'dedup.sqlite')
            remote = RemoteFacts(iid, task['shard'], db)
            store = Results(remote)
            for binding, question, prefix, blob in store.facts(fc, (task['shard'], PARTITIONS), with_binding=True):
                pickle.dump((iid, binding, question, prefix, blob), output, protocol=5)
                count += 1
            inputs += remote.seen
            db.close()
        if inputs != task['bindings']:
            raise ValueError('Fact input coverage differs')
        pickle.dump({'complete': count, 'bindings': inputs}, output, protocol=5)
    return count


def consume(slot):
    while True:
        pending = queue.queue_request(_INFO['pending'], json_body=True)
        if pending['identity'] != _INFO['identity']:
            raise ValueError('Fact queue identity differs')
        if not pending['remaining']:
            return
        tasks = pending['available']
        start = slot % max(1, len(tasks))
        for task in tasks[start:] + tasks[:start]:
            etag = queue.claim(task)
            if etag is None:
                continue
            stop = threading.Event()
            def heartbeat(stop=stop, task=task):
                while not stop.wait(15):
                    try:
                        put(task['log'], json.dumps({'shard': task['shard'], 'time': time.time()}).encode())
                    except Exception:
                        pass
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory(prefix='facts-') as tmp:
                    target = Path(tmp) / 'facts.gz'
                    count = build(task, target)
                    put(task['output'], target.read_bytes())
                    print({'shard': task['shard'], 'facts': count, 'time': time.time()}, flush=True)
            except Exception as error:
                queue.release(task, etag)
                raise RuntimeError(f'Fact task {task["shard"]}: {type(error).__name__}: {error}') from None
            finally:
                stop.set()
                thread.join()
            break
        else:
            time.sleep(5)


def worker(job):
    global _INFO, _CONTROL
    fetch(os.environ['FACT_MANIFEST_URL'], 'fact-manifest.json')
    _INFO = json.loads(Path('fact-manifest.json').read_text())
    fetch(_INFO['control'], 'fact-control.pkl')
    raw = Path('fact-control.pkl').read_bytes()
    if hashlib.sha256(raw).hexdigest() != _INFO['identity']:
        raise ValueError('Fact control identity differs')
    _CONTROL = pickle.loads(raw)
    transport._INFO = _INFO
    transport._CONTEXT = ssl.create_default_context(cadata=_INFO['certificate'])
    cores = int(os.environ.get('FACT_CORES', os.cpu_count() or 1))
    with multiprocessing.get_context('fork').Pool(cores) as workers:
        list(workers.imap_unordered(consume, range(job * cores, (job + 1) * cores)))


if __name__ == '__main__':
    import sys
    worker(int(sys.argv[1]))

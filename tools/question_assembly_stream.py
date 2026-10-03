"""Consume uploaded assembly bundles while the unchanged producer finishes its manifest."""
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from app.disputes import pool
from tools import question_assembly_fleet as assembly
from tools import question_refresh_fleet as queue
from tools.pool_fleet import fetch, put

_INFO = _CONTROL = None


def version():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def describe(bundle, tag, digest, identity):
    if bundle['tag'] != tag or bundle['identity'] != identity:
        raise ValueError('Uploaded bundle belongs to another assembly')
    return {'tag': tag, 'source': bundle['source'], 'start': bundle['start'],
            'count': len(bundle['paths']), 'input_digest': digest}


def execute(task):
    tag = task['tag']
    with tempfile.TemporaryDirectory(prefix='stream-assembly-') as tmp:
        root = Path(tmp)
        log = root / 'progress.log'
        log.write_text(f'{time.time()} loading {tag}\n')
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(15):
                try:
                    put(task['log'], log.read_bytes())
                except Exception as error:
                    print('heartbeat', type(error).__name__, flush=True)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            put(task['log'], log.read_bytes())
            fetch(task['input'], root / 'input.gz')
            digest = hashlib.sha256((root / 'input.gz').read_bytes()).hexdigest()
            with gzip.open(root / 'input.gz', 'rb') as fh:
                bundle = pickle.load(fh)
            expected = describe(bundle, tag, digest, _INFO['identity'])
            output = root / 'output.tgz'
            if fetch(task['existing'], output, missing_ok=True):
                assembly.validate_output(output, expected, _INFO['identity'])
                return
            bundle['input_digest'] = digest
            fc = pool.forecaster(_INFO['run'], _CONTROL)
            fc.grouped.update(k for k in fc.classed if fc.nodes[k].node in
                              ('judgment_response', 'financing_at_floor', 'petition_cash_out'))
            with log.open('a') as fh:
                print(time.time(), 'rebinding', expected['count'], 'histories', file=fh)
            checkpoint = assembly.rebind_bundle(fc, bundle, tag, root)
            with tarfile.open(output, 'w:gz', compresslevel=1) as tf:
                tf.add(checkpoint, arcname=checkpoint.name)
                tf.add(checkpoint.with_suffix('.pkl'), arcname=checkpoint.with_suffix('.pkl').name)
            assembly.validate_output(output, expected, _INFO['identity'])
            put(task['output'], output.read_bytes())
            with log.open('a') as fh:
                print(time.time(), 'saved', file=fh)
        finally:
            stop.set()
            thread.join()
            put(task['log'], log.read_bytes())


def consume(slot):
    while True:
        state = queue.queue_request(_INFO['pending'], json_body=True)
        if state['identity'] != _INFO['identity']:
            raise ValueError('Queue belongs to another assembly')
        if not state['remaining']:
            return
        tasks = state['available']
        start = len(tasks) * slot // _INFO['slots'] % max(1, len(tasks))
        for task in tasks[start:] + tasks[:start]:
            etag = queue.claim(task)
            if etag is None:
                continue
            try:
                execute(task)
                print({'saved': task['tag']}, flush=True)
            except Exception as error:
                queue.release(task, etag)
                raise RuntimeError(f'Streamed assembly failed: {type(error).__name__}') from None
            break
        else:
            time.sleep(10)


def worker(job):
    global _INFO, _CONTROL
    fetch(os.environ['ASSEMBLY_STREAM_MANIFEST_URL'], 'stream-manifest.json')
    _INFO = json.loads(Path('stream-manifest.json').read_text())
    if _INFO['version'] != assembly.version() or _INFO['stream_version'] != version():
        raise ValueError('Streaming assembly implementation differs')
    fetch(_INFO['control'], 'control.pkl')
    _CONTROL = pickle.loads(Path('control.pkl').read_bytes())
    if hashlib.sha256(Path('control.pkl').read_bytes()).hexdigest() != _INFO['control_sha256']:
        raise ValueError('Assembly control differs')
    cores = os.cpu_count() or 1
    print({'job': job, 'cores': cores, 'streaming': True, 'jev_started': False}, flush=True)
    with multiprocessing.get_context('fork').Pool(cores) as workers:
        list(workers.imap_unordered(consume, range(job * cores, (job + 1) * cores)))


if __name__ == '__main__':
    worker(int(sys.argv[1]))

"""Shared source preparation using a pinned, authenticated read-only record service."""
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import secrets
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools import question_assembly_fleet as assembly
from tools import question_assembly_stream as stream
from tools import question_refresh_fleet as queue
from tools.pool_fleet import fetch, put

_INFO = None
_CONTEXT = None


def request(route, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(_INFO['endpoint'] + route, data=data,
                                 headers={'Authorization': 'Bearer ' + _INFO['token']})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, context=_CONTEXT, timeout=180) as response:
                return pickle.loads(gzip.decompress(response.read()))
        except (OSError, TimeoutError):
            if attempt == 4:
                raise RuntimeError('Read-only preparation service unavailable') from None
            time.sleep(2 ** attempt)


class Rows:
    def __init__(self, values):
        self.values = values

    def fetchone(self):
        return next(iter(self.values), None)

    def __iter__(self):
        return iter(self.values)


class RemoteRecords:
    def __init__(self, ordinal, instance):
        self.instance = instance
        self.consumers = dict(request('/consumers', {'ordinal': ordinal, 'instance': instance}))

    def execute(self, sql, args):
        if sql.startswith('SELECT bindings FROM consumers'):
            row = self.consumers.get(args[1])
            return Rows([] if row is None else [(row,)])
        if not sql.startswith('SELECT b.id,b.question,b.prefix,r.row,r.classes FROM bindings b '):
            raise ValueError('Unexpected preparation query')
        return Rows(request('/rows', {'instance': self.instance, 'ids': list(args)}))


def prepare(task):
    ordinal = task['ordinal']
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(15):
            try:
                put(task['log'], json.dumps({'source': task['source'], 'time': time.time()}).encode())
            except Exception:
                pass
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='source-preparation-') as tmp:
            root = Path(tmp)
            source = root / task['source']
            raw, meta = request('/source', {'ordinal': ordinal})
            source.write_bytes(raw)
            source.with_suffix('.meta').write_bytes(meta)
            dbs = {iid: RemoteRecords(ordinal, iid) for iid in task['instances']}
            records = []
            for number, bundle in enumerate(assembly.bundles(source, dbs)):
                if number >= 100000:
                    raise ValueError('Source exceeds task numbering range')
                tag = str(ordinal * 100000 + number)
                bundle.update(tag=tag, identity=_INFO['identity'])
                target = root / 'input.gz'
                with target.open('wb') as rawfile:
                    with gzip.GzipFile(filename='', fileobj=rawfile, mode='wb', compresslevel=1, mtime=0) as fh:
                        pickle.dump(bundle, fh, protocol=5)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                urls = request('/targets', {'ordinal': ordinal, 'number': number})
                if urls['existing_digest'] is not None:
                    if digest != urls['existing_digest']:
                        raise ValueError('Regenerated bundle differs; preserved input was not overwritten')
                else:
                    put(urls['output'], target.read_bytes())
                records.append(stream.describe(bundle, tag, digest, _INFO['identity']))
                # Existing assembly workers consume each upload immediately.
                print({'source': task['source'], 'bundle': tag, 'histories': len(bundle['paths'])}, flush=True)
            if sum(r['count'] for r in records) != task['count']:
                raise ValueError('Prepared source coverage differs')
            put(task['output'], json.dumps({'identity': _INFO['identity'], 'ordinal': ordinal,
                                            'source': task['source'], 'count': task['count'],
                                            'tasks': records}).encode())
    finally:
        stop.set()
        thread.join()


def consume(slot):
    while True:
        state = queue.queue_request(_INFO['pending'], json_body=True)
        if state['identity'] != _INFO['identity']:
            raise ValueError('Preparation identity differs')
        if not state['remaining']:
            return
        tasks = state['available']
        start = slot % max(1, len(tasks))
        for task in tasks[start:] + tasks[:start]:
            etag = queue.claim(task)
            if etag is None:
                continue
            try:
                prepare(task)
            except Exception as error:
                queue.release(task, etag)
                raise RuntimeError(f'Preparation failed: {type(error).__name__}: {error}') from None
            break
        else:
            time.sleep(5)


def worker(job):
    global _INFO, _CONTEXT
    fetch(os.environ['PREPARATION_MANIFEST_URL'], 'preparation-manifest.json')
    _INFO = json.loads(Path('preparation-manifest.json').read_text())
    if _INFO['version'] != assembly.version():
        raise ValueError('Assembly implementation differs')
    _CONTEXT = ssl.create_default_context(cadata=_INFO['certificate'])
    cores = int(os.environ.get('PREPARATION_CORES', os.cpu_count() or 1))
    print({'job': job, 'cores': cores, 'stage': 'source preparation'}, flush=True)
    with multiprocessing.get_context('fork').Pool(cores) as workers:
        list(workers.imap_unordered(consume, range(job * cores, (job + 1) * cores)))


def serve(config):
    import boto3
    from botocore.config import Config

    cfg = json.loads(Path(config).read_text())
    s3 = boto3.client('s3', region_name=cfg['region'], config=Config(signature_version='s3v4'))
    slots = threading.BoundedSemaphore(48)
    def url(op, key):
        return s3.generate_presigned_url(op, Params={'Bucket': cfg['bucket'], 'Key': key}, ExpiresIn=21600)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + cfg['token']):
                self.send_error(403)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                self.send_error(400)
                return
            with slots:
                try:
                    args = json.loads(self.rfile.read(length))
                    if self.path in ('/source', '/consumers', '/targets'):
                        source = cfg['sources'][int(args['ordinal'])]
                        if source['ordinal'] != int(args['ordinal']):
                            raise ValueError('Source ordinal differs')
                    if self.path == '/source':
                        path = Path(cfg['walked']) / source['source']
                        result = path.read_bytes(), path.with_suffix('.meta').read_bytes()
                    elif self.path in ('/consumers', '/rows'):
                        filename = cfg['databases'][args['instance']]
                        db = sqlite3.connect(f'file:{filename}?mode=ro', uri=True)
                        try:
                            if self.path == '/consumers':
                                result = list(db.execute('SELECT row,bindings FROM consumers WHERE part=? ORDER BY row',
                                                         (source['source'],)))
                            else:
                                ids = args['ids']
                                if not 0 < len(ids) <= 800 or any(type(i) is not int or i < 0 for i in ids):
                                    raise ValueError('Invalid binding request')
                                result = list(db.execute('SELECT b.id,b.question,b.prefix,r.row,r.classes FROM bindings b '
                                    'JOIN results r ON r.binding=b.id WHERE b.id IN (' + ','.join('?' for _ in ids) + ')', ids))
                        finally:
                            db.close()
                    elif self.path == '/targets':
                        number = int(args['number'])
                        if not 0 <= number < 100000:
                            raise ValueError('Invalid bundle number')
                        tag = str(source['ordinal'] * 100000 + number)
                        key = cfg['base'] + '/inputs/' + tag + '.gz'
                        if tag not in cfg['existing']:
                            try:
                                response = s3.get_object(Bucket=cfg['bucket'], Key=key)
                                digest = hashlib.sha256()
                                for chunk in response['Body'].iter_chunks(1024 * 1024):
                                    digest.update(chunk)
                                cfg['existing'][tag] = digest.hexdigest()
                            except s3.exceptions.NoSuchKey:
                                pass
                        result = {'output': url('put_object', key),
                                  'existing_digest': cfg['existing'].get(tag)}
                    else:
                        self.send_error(404)
                        return
                    payload = gzip.compress(pickle.dumps(result, protocol=5), compresslevel=1, mtime=0)
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as error:
                    print('read service', type(error).__name__, flush=True)
                    self.send_error(500)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('0.0.0.0', cfg['port']), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cfg['certfile'], cfg['keyfile'])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == '__main__':
    if sys.argv[1] == 'serve':
        serve(sys.argv[2])
    else:
        worker(int(sys.argv[1]))

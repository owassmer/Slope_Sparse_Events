"""Adopt the committed global support table and coordinate hybrid fact writers."""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import hashlib
import heapq
import json
import os
import pickle
import sqlite3
import subprocess
import time
from pathlib import Path

import boto3
from botocore.config import Config

from app.disputes import pool
from tools import question_refresh_assemble as assembly
from tools.question_fact_fleet import PARTITIONS
from tools.question_refresh_fleet import release_stale


def records(path, identity, shard, expected, instance_order):
    count, previous = 0, None
    with gzip.open(path, 'rb') as fh:
        if pickle.load(fh) != {'identity': identity, 'shard': shard}:
            raise ValueError('Fact output identity differs')
        while True:
            row = pickle.load(fh)
            if isinstance(row, dict):
                if row != {'complete': count, 'bindings': expected} or fh.read(1):
                    raise ValueError('Incomplete fact output')
                return
            iid, binding, _question, prefix, _blob = row
            order = instance_order[iid], binding
            if prefix % PARTITIONS != shard or (previous is not None and order < previous):
                raise ValueError('Fact output ownership/order differs')
            previous = order
            count += 1
            yield row


def main(config):
    cfg = json.loads(Path(config).read_text())
    root = Path(cfg['root'])
    out = root / 'rebound'
    folder = root / 'fact-fleet'
    folder.mkdir(exist_ok=True)
    bucket, base = cfg['bucket'], cfg['base']
    s3 = boto3.client('s3', region_name=cfg['region'], config=Config(signature_version='s3v4'))
    manifest = json.loads(gzip.decompress((root / 'assembly-inputs/manifest.gz').read_bytes()))
    assembly_base = json.loads((root/'assembly-inputs/fleet.json').read_text())['base']
    def publish():
        def upload(name):
            s3.upload_file(str(out/name),bucket,assembly_base+'/assembled/'+name)
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(upload,['control.pkl','merge-meta.pkl','rebound.json']+[f'facts{b}.pkl.gz' for b in range(16)]))
        s3.put_object(Bucket=bucket,Key=assembly_base+'/assembled/complete.json',Body=(out/'rebound.json').read_bytes())
        print('ASSEMBLY_DURABLY_COMPLETE_JEV_HELD',flush=True)
    if (out/'rebound.json').exists():
        publish()
        return
    control = pickle.loads((root / 'control.pkl').read_bytes())
    fc = pool.forecaster(manifest['run'], control)
    fc.grouped.update(k for k in fc.classed if fc.nodes[k].node in
                      ('judgment_response', 'financing_at_floor', 'petition_cash_out'))
    meta, total, retained, cost, coverage = [], 0, 0, {}, {}
    # Reconstruct only in-memory definitions. Never repeat the saved wanted union.
    for task in manifest['tasks']:
        tag = task['tag']
        report = pickle.loads((out / 'walked' / f'part{tag}.refresh').read_bytes())
        if report['identity'] != manifest['identity'] or report['input_digest'] != task['input_digest']:
            raise ValueError('Assembly report identity differs')
        coverage.setdefault(task['source'], []).append((task['start'], task['count']))
        meta.extend(report['meta'])
        total += report['source_count']
        retained += report['retained']
        for key, node in report['nodes'].items():
            if key in fc.nodes and fc.nodes[key] != node:
                raise ValueError('Question definitions differ')
            fc.nodes[key] = node
        for key, group in report['node_group'].items():
            if key in fc.node_group and fc.node_group[key] != group:
                raise ValueError('Answer groups differ')
            fc.node_group[key] = group
        fc.classed |= report['classed']
        cost[f'part{tag}.pkl'] = report['retained'], report['seconds']
    for source, expected in manifest['sources'].items():
        end = 0
        for start, count in sorted(coverage.pop(source, [])):
            if start != end or count <= 0:
                raise ValueError('Source coverage differs')
            end += count
        if end != expected:
            raise ValueError('Source incomplete')
    if coverage or total != control['walked']:
        raise ValueError('History coverage differs')
    print('FACT_METADATA_RESTORED', total, flush=True)
    # The old finalizer creates its child outputs only AFTER assignment commit.
    # Until then it continues useful work; never interrupt/restart the union.
    while not list(out.glob('fact-part*.pkl.gz')):
        if (out / 'rebound.json').exists():
            publish()
            return
        time.sleep(2)
    if (out / 'rebound.json').exists():
        publish()
        return
    subprocess.run(['systemctl', 'stop', 'slope-hybrid-assembly'], check=True)
    databases = cfg['databases']
    counts = {shard: 0 for shard in range(PARTITIONS)}
    for filename in databases.values():
        db = sqlite3.connect(f'file:{filename}?mode=ro', uri=True)
        assigned = db.execute('SELECT count(*) FROM fact_assignments').fetchone()[0]
        wanted = db.execute('SELECT count(*) FROM wanted').fetchone()[0]
        if assigned != wanted:
            raise ValueError('Assignment commit incomplete')
        for shard, count in db.execute('SELECT prefix % ?,count(*) FROM fact_assignments GROUP BY prefix % ?',
                                       (PARTITIONS, PARTITIONS)):
            counts[shard] += count
        db.close()
    control.update(nodes=fc.nodes, node_group=fc.node_group, classed=fc.classed)
    raw = pickle.dumps(control, protocol=5)
    identity = hashlib.sha256(raw).hexdigest()
    (folder / 'control.pkl').write_bytes(raw)
    s3.put_object(Bucket=bucket, Key=base+'/control.pkl', Body=raw)
    def url(op, key, **kw):
        return s3.generate_presigned_url(op, Params={'Bucket': bucket, 'Key': key, **kw}, ExpiresIn=21600)
    tasks = {}
    for shard in range(PARTITIONS):
        tag = str(shard)
        tasks[tag] = {'tag': tag, 'shard': shard, 'bindings': counts[shard],
            'claim': url('put_object',base+'/claims/'+tag+'.json',IfNoneMatch='*'),
            'release': url('delete_object',base+'/claims/'+tag+'.json'),
            'log': url('put_object',base+'/logs/'+tag+'.log'),
            'output': url('put_object',base+'/outputs/'+tag+'.gz')}
    spec = dict(identity=identity, run=manifest['run'], instances=list(databases),
                endpoint=cfg['endpoint'], certificate=Path(cfg['certfile']).read_text(),
                token=cfg['token'], control=url('get_object',base+'/control.pkl'),
                pending=url('get_object',base+'/pending.json'))
    s3.put_object(Bucket=bucket, Key=base+'/manifest.json', Body=json.dumps(spec).encode())
    pointer = url('get_object',base+'/manifest.json')
    (folder/'manifest-url.txt').write_text(pointer)
    service = None
    while True:
        done = {o['Key'].rsplit('/',1)[-1][:-3] for page in s3.get_paginator('list_objects_v2').paginate(
            Bucket=bucket,Prefix=base+'/outputs/') for o in page.get('Contents',[])}
        if not done <= tasks.keys():
            raise ValueError('Unexpected fact output')
        active = release_stale(s3,bucket,base,done)
        pending = tasks.keys()-done
        s3.put_object(Bucket=bucket,Key=base+'/pending.json',Body=json.dumps({'identity':identity,
            'remaining':len(pending),'available':[tasks[k] for k in sorted(pending-active,key=int)]}).encode())
        progress = {'stage':'serialize_question_facts','completed':len(done),'total':PARTITIONS,
                    'active':len(active),'time':time.time(),'jev_started':False,'ready_for_jev':False}
        (out/'progress.json').write_text(json.dumps(progress))
        print(progress,flush=True)
        if service is None:
            subprocess.run(['systemctl','start','slope-fact-records'],check=True)
            service = subprocess.Popen([os.sys.executable,'-m','tools.question_fact_fleet','40'],
                env={**os.environ,'FACT_MANIFEST_URL':pointer,'FACT_CORES':'8'})
            s3.put_object(Bucket=bucket,Key=base+'/manifest-url.txt',Body=pointer.encode())
        if not pending:
            break
        if service.poll() not in (None,0):
            raise RuntimeError('AWS fact support failed')
        time.sleep(10)
    service.wait(timeout=120)
    subprocess.run(['systemctl','stop','slope-fact-records'],check=True)
    def download(shard):
        target=folder/f'{shard}.gz'
        s3.download_file(bucket,base+f'/outputs/{shard}.gz',str(target))
        return target
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        files=list(ex.map(download,range(PARTITIONS)))
    order={iid:i for i,iid in enumerate(databases)}
    def merged(*_args):
        streams=[records(path,identity,shard,counts[shard],order) for shard,path in enumerate(files)]
        for _iid,_binding,question,prefix,blob in heapq.merge(*streams,key=lambda r:(order[r[0]],r[1])):
            yield question,prefix,blob
    assembly.parallel_facts=merged
    assembly.finalize(fc,{},control,meta,total,retained,cost,out,fact_processes=PARTITIONS)
    publish()


if __name__ == '__main__':
    import sys
    main(sys.argv[1])

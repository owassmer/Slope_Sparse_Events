"""Race exact finer partitions against unfinished tail tasks, preserving old winners."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import pickle
import shutil
import tempfile
import threading
import time
from pathlib import Path

import boto3
from cloud_walk import create, fetch_group, read, upload_group
from refine_walk import TaskSuperseded, objects, task
from tail_walk import validate


def collapse(parts, plan, index, factor):
    """Check finer ownership before restoring the original subdivision headers only."""
    roots = {tuple(r) for r in plan['roots']}
    denominator = plan['partitions']
    indexes = {index + denominator * j for j in range(factor)}
    if len(parts) != factor or any(not p['complete'] for p in parts):
        raise ValueError('Incomplete replacement parts')
    first = parts[0]
    if any((p['clock'], p['nseg'], p['segs']) != (first['clock'], first['nseg'], first['segs']) for p in parts):
        raise ValueError('Global topology differs')
    if len({p['k'] for p in parts}) != factor:
        raise ValueError('Duplicate part identifiers')
    if any(set(p['subdivisions']) != roots for p in parts):
        raise ValueError('Root coverage differs')
    combined = {}
    for root in roots:
        subs = [p['subdivisions'][root] for p in parts]
        segments = subs[0]['segments']
        if any(s['segments'] != segments or s['depth'] != plan['depth'] or
               s['partitions'] != denominator * factor for s in subs):
            raise ValueError('Subdivision topology differs')
        if {s['partition'] for s in subs} != indexes:
            raise ValueError('Replacement partition coverage differs')
        for sub in subs:
            wanted = segments[sub['partition']::denominator * factor]
            if tuple(sub['done']) != tuple(wanted):
                raise ValueError('Replacement child coverage differs')
        owned = [c for sub in subs for c in sub['done']]
        wanted = segments[index::denominator]
        if len(set(owned)) != len(owned) or set(owned) != set(wanted):
            raise ValueError('Original child coverage differs')
        combined[root] = {'segments': segments, 'done': tuple(wanted), 'partition': index,
                          'partitions': denominator, 'depth': plan['depth']}
    # Keep every event, dictionary and financial row. Only coverage headers change.
    for p in parts:
        p['subdivisions'] = {}
    parts[0]['subdivisions'] = combined
    validate(parts, plan, index)


def prepare(bucket, source, prefix, factor=8):
    s3 = boto3.client('s3')
    if read(s3, bucket, prefix + '/ready.json'):
        return
    parents = []
    for obj in objects(s3, bucket, source + '/refine-v1/plans/'):
        plan = read(s3, bucket, obj['Key'])
        for index in plan['indexes']:
            ident = f"{plan['job']}-{index}"
            if read(s3, bucket, f'{source}/refine-v1/done/{ident}.json'):
                continue
            children = []
            for j in range(factor):
                new = {**plan, 'job': 2000 + len(parents) * factor + j,
                       'partitions': plan['partitions'] * factor,
                       'indexes': [index + j * plan['partitions']],
                       'superseded_by': f'{source}/refine-v1/done/{ident}.json'}
                children.append(new)
                create(s3, bucket, f"{prefix}/refine-v1/plans/{new['job']}.json", new)
            parents.append({'id': ident, 'plan': plan, 'index': index, 'children': children})
    config = {'source': source, 'parents': parents, 'factor': factor,
              'tasks': len(parents) * factor,
              'roots': sum(len(p['plan']['roots']) * factor for p in parents), 'matched_costs': 0}
    create(s3, bucket, prefix + '/ready.json', config)
    print(json.dumps({'parents': len(parents), 'tasks': config['tasks']}), flush=True)


def assemble(s3, bucket, prefix, config, parent):
    marker = f"{config['source']}/refine-v1/done/{parent['id']}.json"
    if read(s3, bucket, marker):
        return True
    children = [read(s3, bucket, f"{prefix}/refine-v1/done/{p['job']}-{p['indexes'][0]}.json")
                for p in parent['children']]
    if any(c is None for c in children):
        return False
    dest = f"{config['source']}/refine-v1/attempts/{parent['id']}/fanout-v1"
    with tempfile.TemporaryDirectory(prefix='fanout-assemble-') as tmp:
        for group in ['control', *(f'rows{i}' for i in range(16))]:
            folder = Path(tmp) / group
            for child in children:
                fetch_group(s3, bucket, child['source'], group, folder)
            if group == 'control':
                files = sorted(folder.glob('part*.pkl'))
                parts = [pickle.loads(f.read_bytes()) for f in files]
                collapse(parts, parent['plan'], parent['index'], config['factor'])
                for f, p in zip(files, parts, strict=True):
                    f.write_bytes(pickle.dumps(p, protocol=pickle.HIGHEST_PROTOCOL))
            upload_group(s3, bucket, dest + '/' + group + '.tgz', folder)
            shutil.rmtree(folder)
        log = f"Exact eight-way replacement for {parent['id']}; original coverage verified.\n"
        s3.put_object(Bucket=bucket, Key=dest + '/walk.log', Body=log.encode())
        won = create(s3, bucket, marker, {'job': parent['id'], 'source': dest, 'finished': time.time()})
        print(f"replacement {parent['id']}: {'published' if won else 'original already saved'}", flush=True)
    return True


def worker(bucket, prefix, cores):
    s3 = boto3.client('s3')
    config = read(s3, bucket, prefix + '/ready.json')
    locks = {p['id']: threading.Lock() for p in config['parents']}
    assembly_slots = threading.Semaphore(1)

    def run(parent, child):
        marker = f"{config['source']}/refine-v1/done/{parent['id']}.json"
        if read(s3, bucket, marker):
            return
        try:
            task(bucket, prefix, child, child['indexes'][0])
        except TaskSuperseded:
            print(f"original {parent['id']} won; replacement stopped", flush=True)
            return
        with locks[parent['id']], assembly_slots:
            assemble(s3, bucket, prefix, config, parent)

    with concurrent.futures.ThreadPoolExecutor(max_workers=cores) as ex:
        futures = [ex.submit(run, parent, child) for parent in config['parents'] for child in parent['children']]
        for f in concurrent.futures.as_completed(futures):
            f.result()
    print('Finer replacement queue finished', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'worker'])
    parser.add_argument('bucket')
    parser.add_argument('prefix')
    parser.add_argument('--source')
    parser.add_argument('--cores', type=int, default=4)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.bucket, args.source, args.prefix)
    else:
        worker(args.bucket, args.prefix, args.cores)

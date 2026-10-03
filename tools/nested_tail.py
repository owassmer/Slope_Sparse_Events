"""Join a complete second-level split into one original fine-task output."""
from __future__ import annotations

import argparse
import json
import pickle
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import boto3
import numpy as np

from app.disputes import parallel


def combine(parts, part_id):
    if not parts or any(not p['complete'] for p in parts):
        raise ValueError('Incomplete nested outputs')
    first = parts[0]
    for p in parts:
        for field in ('clock', 'nseg', 'segs', 'subdivisions', 'top_nodes'):
            if not parallel._same(p[field], first[field]):
                raise ValueError(f'Nested outputs disagree on {field}')
    expected = {root + child for root, sub in first['subdivisions'].items() for child in sub['done']}
    if any(set(p.get('nested_subdivisions', {})) != expected for p in parts):
        raise ValueError('Missing nested child')
    for key in expected:
        subs = [p['nested_subdivisions'][key] for p in parts]
        sub = subs[0]
        if any((s['segments'], s['depth'], s['partitions']) !=
               (sub['segments'], sub['depth'], sub['partitions']) for s in subs):
            raise ValueError('Nested topology differs')
        indexes = [s['partition'] for s in subs]
        if len(indexes) != sub['partitions'] or set(indexes) != set(range(sub['partitions'])):
            raise ValueError('Nested partition coverage differs')
        for s in subs:
            if tuple(s['done']) != tuple(s['segments'][s['partition']::s['partitions']]):
                raise ValueError('Nested continuation coverage differs')
    if len({p['k'] for p in parts}) != len(parts):
        raise ValueError('Duplicate nested part')
    owner = parallel.top_owner(parts)
    events = sorted((e for p in parts for e in p['events'] if e[0][1] == 0 or p['k'] == owner),
                    key=lambda e: e[0])
    if len({e[0] for e in events}) != len(events):
        raise ValueError('Duplicate nested event')
    ranges = [p['ev_range'] for p in parts if p.get('ev_range') is not None]
    return {**first, 'k': part_id, 'events': events, 'nested_subdivisions': {},
            **{f: parallel._union(parts, f) for f in parallel.FIELDS},
            **{f: set().union(*(p[f] for p in parts)) for f in parallel.SETS},
            'raise_more': set().union(*(p['raise_more'] for p in parts)),
            'walked': sum(p['walked'] for p in parts), 'seconds': max(p['seconds'] for p in parts),
            'rss': max(p['rss'] for p in parts),
            'ev_range': [np.minimum.reduce([r[0] for r in ranges]),
                         np.maximum.reduce([r[1] for r in ranges])] if ranges else None}


def prepare(bucket, source, prefix, identifiers, count=32, depth=4):
    from cloud_walk import create, read
    s3 = boto3.client('s3')
    if read(s3, bucket, prefix + '/ready.json'):
        raise ValueError('Nested queue already exists')
    parents = []
    for ident in identifiers:
        job, index = map(int, ident.split('-'))
        marker = f'{source}/refine-v1/done/{ident}.json'
        if read(s3, bucket, marker):
            continue
        plan = read(s3, bucket, f'{source}/refine-v1/plans/{job}.json')
        children = []
        for j in range(count):
            child = {**plan, 'job': 4000 + len(parents) * count + j, 'indexes': [index],
                     'nested_refinement': [j, count, depth], 'superseded_by': marker}
            create(s3, bucket, f"{prefix}/refine-v1/plans/{child['job']}.json", child)
            children.append(child)
        parents.append({'id': ident, 'plan': plan, 'index': index, 'children': children})
    config = {'source': source, 'parents': parents, 'tasks': count * len(parents),
              'roots': sum(len(c['roots']) for p in parents for c in p['children']), 'matched_costs': 0}
    create(s3, bucket, prefix + '/ready.json', config)
    print(json.dumps({'parents': len(parents), 'tasks': config['tasks']}), flush=True)


def assemble(bucket, prefix):
    from cloud_walk import create, fetch_group, read, upload_group
    s3 = boto3.client('s3')
    config = read(s3, bucket, prefix + '/ready.json')
    pending = []
    for parent in config['parents']:
        marker = f"{config['source']}/refine-v1/done/{parent['id']}.json"
        if read(s3, bucket, marker):
            continue
        children = [read(s3, bucket, f"{prefix}/refine-v1/done/{p['job']}-{parent['index']}.json")
                    for p in parent['children']]
        if any(c is None for c in children):
            pending.append(parent['id'])
            continue
        part_id = 100000 + parent['plan']['job'] * 100 + parent['index']
        dest = f"{config['source']}/refine-v1/attempts/{parent['id']}/nested-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix='nested-assemble-') as tmp:
            root = Path(tmp)
            for group in ['control', *(f'rows{i}' for i in range(16))]:
                folder = root / group
                for child in children:
                    fetch_group(s3, bucket, child['source'], group, folder)
                files = sorted(folder.glob('part*.pkl'))
                if group == 'control':
                    parts = [pickle.loads(f.read_bytes()) for f in files]
                    owner = parallel.top_owner(parts)
                    output = combine(parts, part_id)
                    del parts
                else:
                    output = []
                    for file in files:
                        records = pickle.loads(file.read_bytes())
                        output.extend(r for r in records if r[0][1] == 0 or int(file.stem[4:]) == owner)
                    output.sort(key=lambda r: r[0])
                    if len({r[0] for r in output}) != len(output):
                        # One record event can legitimately target multiple questions.
                        identities = [(r[0], r[2]) for r in output]
                        if len(set(identities)) != len(identities):
                            raise ValueError('Duplicate nested question record')
                for file in files:
                    file.unlink()
                (folder / f'part{part_id}.pkl').write_bytes(pickle.dumps(output, protocol=pickle.HIGHEST_PROTOCOL))
                del output
                upload_group(s3, bucket, dest + '/' + group + '.tgz', folder)
                shutil.rmtree(folder)
            s3.put_object(Bucket=bucket, Key=dest + '/walk.log',
                          Body=f"Nested continuation coverage checked for {parent['id']}.\n".encode())
            create(s3, bucket, marker, {'job': parent['id'], 'source': dest, 'finished': time.time()})
            print(f"Nested replacement {parent['id']} saved", flush=True)
    print(json.dumps({'pending': pending}), flush=True)
    return not pending


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'assemble'])
    parser.add_argument('bucket')
    parser.add_argument('prefix')
    parser.add_argument('--source')
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--count', type=int, default=32)
    parser.add_argument('--depth', type=int, default=4)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.bucket, args.source, args.prefix, args.ids, args.count, args.depth)
    else:
        raise SystemExit(not assemble(args.bucket, args.prefix))

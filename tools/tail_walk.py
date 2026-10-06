"""Split unfinished partitions by their existing native roots; preserve saved siblings."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import boto3
from cloud_walk import create, fetch_group, read, upload_group
from refine_walk import objects


def chunks(plan, partition, pieces=14):
    roots = sorted(plan['roots'])
    if len({tuple(r) for r in roots}) != len(roots):
        raise ValueError('Duplicate native roots')
    return [{**plan, 'roots': roots[i::pieces], 'indexes': [partition],
             'original_job': plan['job'], 'original_partition': partition}
            for i in range(min(pieces, len(roots)))]


def prepare(bucket, original, tail):
    s3 = boto3.client('s3')
    if read(s3, bucket, tail + '/ready.json'):
        return
    plans = []
    parents = []
    for obj in objects(s3, bucket, original + '/refine-v1/plans/'):
        plan = read(s3, bucket, obj['Key'])
        for partition in range(plan['partitions']):
            ident = f"{plan['job']}-{partition}"
            if read(s3, bucket, original + '/refine-v1/done/' + ident + '.json'):
                continue
            children = []
            for sub in chunks(plan, partition):
                sub = {**sub, 'job': 1000 + len(plans)}
                plans.append(sub)
                children.append(f"{sub['job']}-{partition}")
                create(s3, bucket, f"{tail}/refine-v1/plans/{sub['job']}.json", sub)
            parents.append({'id': ident, 'plan': plan, 'partition': partition, 'children': children})
    config = {'tasks': len(plans), 'roots': sum(len(p['roots']) for p in plans),
              'matched_costs': 0, 'original': original, 'parents': parents}
    create(s3, bucket, tail + '/ready.json', config)
    print(json.dumps({'parents': len(parents), 'tasks': len(plans)}))


def validate(parts, plan, partition):
    expected = {tuple(k) for k in plan['roots']}
    seen = set()
    for part in parts:
        if not part['complete']:
            raise ValueError('Incomplete child output')
        for key, sub in part.get('subdivisions', {}).items():
            if key not in expected or key in seen:
                raise ValueError('Foreign or duplicate native root')
            if (sub['partition'], sub['partitions'], sub['depth']) != (partition, plan['partitions'], plan['depth']):
                raise ValueError('Changed original partition')
            wanted = [k for i, k in enumerate(sub['segments']) if i % plan['partitions'] == partition]
            if set(sub['done']) != set(wanted) or len(sub['done']) != len(set(sub['done'])):
                raise ValueError('Child continuation coverage differs')
            seen.add(key)
    if seen != expected:
        raise ValueError('Missing native roots')


def assemble(bucket, tail):
    from app.disputes.parallel import load_parts

    s3 = boto3.client('s3')
    config = read(s3, bucket, tail + '/ready.json')
    original = config['original'] + '/refine-v1'
    saved = 0
    for parent in config['parents']:
        marker = f"{original}/done/{parent['id']}.json"
        if read(s3, bucket, marker):
            saved += 1
            continue
        children = [read(s3, bucket, f'{tail}/refine-v1/done/{ident}.json') for ident in parent['children']]
        if any(c is None for c in children):
            continue
        dest = f"{original}/attempts/{parent['id']}/root-split"
        with tempfile.TemporaryDirectory(prefix='tail-assemble-') as tmp:
            root = Path(tmp)
            for group in ['control', *(f'rows{i}' for i in range(16))]:
                folder = root / group
                for child in children:
                    fetch_group(s3, bucket, child['source'], group, folder)
                if group == 'control':
                    validate(load_parts(str(folder)), parent['plan'], parent['partition'])
                upload_group(s3, bucket, f'{dest}/{group}.tgz', folder)
                import shutil
                shutil.rmtree(folder)
            log = f"Recovered {parent['id']} from {len(children)} disjoint root groups; coverage checked.\n"
            s3.put_object(Bucket=bucket, Key=dest + '/walk.log', Body=log.encode())
            create(s3, bucket, marker, {'job': parent['id'], 'source': dest, 'root_split': True})
            saved += 1
            print(log, end='', flush=True)
    print(f"{saved}/{len(config['parents'])} original partitions saved", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'assemble'])
    parser.add_argument('bucket')
    parser.add_argument('prefix')
    parser.add_argument('--original')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.bucket, args.original, args.prefix)
    else:
        assemble(args.bucket, args.prefix)

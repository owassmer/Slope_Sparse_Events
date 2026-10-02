"""Catalog saved grouping archives and copy assigned indexed bytes at the consuming worker."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import pickle
import struct
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

from tools.merge_fleet import archive, completed_count
from tools.pool_fleet import fetch, put


def entry(size, sha, index, archive_key):
    offsets = struct.unpack(f'<{len(index)//8}Q', index)
    if not offsets or offsets[0] != 0 or offsets[-1] != size or any(a >= b for a, b in zip(offsets, offsets[1:], strict=False)):
        raise ValueError('incomplete path index')
    return {'size': size, 'sha256': sha, 'index': base64.b64encode(index).decode(),
            'count': len(offsets)-1, 'archive': archive_key}


def catalog(file, homes, archive_key):
    files, indices = {}, {}
    with tarfile.open(file, mode='r|gz') as tf:
        for member in tf:
            if member.name not in {n for h in homes for n in (h, h+'.idx')} or not member.isfile():
                raise ValueError(f'Unexpected archive member {member.name}')
            stream = tf.extractfile(member)
            if member.name.endswith('.idx'):
                if member.name[:-4] in indices:
                    raise ValueError('duplicate index')
                indices[member.name[:-4]] = stream.read()
            else:
                if member.name in files:
                    raise ValueError('duplicate path file')
                h = hashlib.sha256()
                while chunk := stream.read(1024**2):
                    h.update(chunk)
                files[member.name] = member.size, h.hexdigest()
    if set(files) != set(homes) or set(indices) != set(homes):
        raise ValueError('archive does not cover its assigned homes')
    return {f: entry(*files[f], indices[f], archive_key) for f in homes}


def github(job):
    fetch(os.environ['CATALOG_MANIFEST_URL'], 'catalog-manifest.json')
    info = json.loads(Path('catalog-manifest.json').read_text())
    task = info['tasks'][job]
    fetch(task['input'], 'grouped.tgz')
    result = catalog('grouped.tgz', task['homes'], task['key'])
    put(task['output'], gzip.compress(json.dumps(result).encode(), compresslevel=1))
    print(f'Cataloged {len(result)} files, {sum(x["count"] for x in result.values())} grouped paths', flush=True)


def complete(out, meta, branches):
    import boto3

    out = Path(out)
    spec = pickle.loads((out/'merge-fleet/prepared.pkl').read_bytes())
    expected_version = os.environ['SLOPE_SAVED_GROUPING_VERSION']
    if spec['version'] != expected_version or set(spec['homes']) != {m[2] for m in meta}:
        raise ValueError('saved grouping assignment differs from selected paths')
    from botocore.config import Config
    s3 = boto3.client('s3', region_name='us-east-2', config=Config(signature_version='s3v4'))
    bucket, prefix = os.environ['SLOPE_POOL_BUCKET'], os.environ['SLOPE_POOL_PREFIX']
    remote = f'{prefix}/pool/merge-fleet/{expected_version}'
    def url(op, key):
        return s3.generate_presigned_url(op, Params={'Bucket': bucket, 'Key': key}, ExpiresIn=21600)
    tasks = [{'homes': homes, 'key': f'{remote}/outputs/{j}.tgz',
              'input': url('get_object', f'{remote}/outputs/{j}.tgz'),
              'output': url('put_object', f'{remote}/catalog/{j}.json.gz')}
             for j, homes in enumerate(spec['jobs'][:80])]
    manifest_key = f'{remote}/catalog-manifest.json'
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps({'tasks': tasks}).encode())
    s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/catalog-manifest-url.txt', Body=url('get_object', manifest_key).encode())
    result = {}
    def local(folder, homes, tag):
        key = f'{remote}/outputs/{tag}.tgz'
        file = out/'merge-fleet'/f'{tag}.tgz'
        archive(folder, file, [n for f in homes for n in (f, f+'.idx')])
        s3.upload_file(str(file), bucket, key)
        file.unlink()
        found = {}
        for f in homes:
            path = folder/f
            if completed_count(path) is None:
                raise ValueError(f'Incomplete local grouping output {f}')
            with path.open('rb') as fh:
                sha = hashlib.file_digest(fh, 'sha256').hexdigest()
            found[f] = entry(path.stat().st_size, sha, Path(str(path)+'.idx').read_bytes(), key)
        return found
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as workers:
        pending = [workers.submit(local, out/'merge-fleet'/f'aws{j}', homes, f'aws{j}')
                   for j, homes in enumerate(spec['jobs']) if j >= 80]
        pending.append(workers.submit(local, out/'paths', list(spec['reused']), 'preserved'))
        done = set()
        while len(done) < 80 or any(not f.done() for f in pending):
            for future in pending:
                if future.done():
                    future.result()
            for j, task in enumerate(tasks):
                if j in done:
                    continue
                try:
                    body = s3.get_object(Bucket=bucket, Key=f'{remote}/catalog/{j}.json.gz')['Body'].read()
                except s3.exceptions.NoSuchKey:
                    continue
                value = json.loads(gzip.decompress(body))
                if set(value) != set(task['homes']) or set(value) & set(result):
                    raise ValueError('catalog ownership differs')
                for x in value.values():
                    if x != entry(x['size'], x['sha256'], base64.b64decode(x['index']), task['key']):
                        raise ValueError('invalid catalog index')
                result.update(value)
                done.add(j)
            status = {'stage':'catalog_saved_grouping','time':time.time(),'jobs_complete':len(done),'total_jobs':80}
            s3.put_object(Bucket=bucket, Key=f'{prefix}/pool/progress.json', Body=json.dumps(status).encode())
            print(json.dumps(status), flush=True)
            if len(done)<80 or any(not f.done() for f in pending):
                time.sleep(15)
        for future in pending:
            value = future.result()
            if set(value) & set(result):
                raise ValueError('overlapping local grouped paths')
            result.update(value)
    if set(result) != set(spec['homes']):
        raise ValueError('incomplete grouped path coverage')
    sources = {'bucket':bucket,'parts':result}
    target = out/'path_sources.json'
    target.with_suffix('.tmp').write_text(json.dumps(sources))
    target.with_suffix('.tmp').replace(target)
    return {f:x['count'] for f,x in result.items()}


def materialize(plan, folder):
    """Stream each needed archive once, writing only assigned byte ranges; validate source hashes."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for task in plan:
        seen = set()
        with urllib.request.urlopen(task['url'], timeout=120) as response, tarfile.open(fileobj=response, mode='r|gz') as tf:
            for member in tf:
                if member.name not in task['members']:
                    continue
                if member.name in seen or not member.isfile():
                    raise ValueError('duplicate or invalid path member')
                seen.add(member.name)
                spec = task['members'][member.name]
                if member.size != spec['size']:
                    raise ValueError('path source size differs')
                outputs = [(x, (folder/x['name']).open('wb')) for x in spec['slices']]
                h, pos = hashlib.sha256(), 0
                try:
                    stream = tf.extractfile(member)
                    while chunk := stream.read(1024**2):
                        h.update(chunk)
                        end = pos+len(chunk)
                        for x, fh in outputs:
                            lo, hi = max(pos,x['begin']), min(end,x['end'])
                            if lo < hi:
                                fh.write(chunk[lo-pos:hi-pos])
                        pos=end
                finally:
                    for _, fh in outputs:
                        fh.close()
                if h.hexdigest() != spec['sha256']:
                    raise ValueError('grouped path source hash differs')
                for x, _ in outputs:
                    (folder/(x['name']+'.idx')).write_bytes(base64.b64decode(x['index']))
        if seen != set(task['members']):
            raise ValueError('assigned path member missing from archive')


if __name__ == '__main__':
    github(int(sys.argv[1]))

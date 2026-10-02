"""Start a fresh native walk with the existing subdivision queue."""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3
from cloud_walk import RUN, create, publish, read


def prepare(bucket, prefix):
    s3 = boto3.client('s3')
    if read(s3, bucket, prefix + '/ready.json'):
        return
    with tempfile.TemporaryDirectory(prefix='fresh-walk-') as tmp:
        root = Path(tmp)
        roots = root / 'empty.pkl'
        roots.write_bytes(pickle.dumps([]))
        env = {**os.environ, 'SLOPE_WALK_ROOTS': str(roots), 'SLOPE_WALK_CUT': '10',
               'SLOPE_WALK_MINUTES': '0', 'SLOPE_JEV_CACHE_ONLY': '1',
               'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
        for name in ('SLOPE_WALK_REFINE', 'SLOPE_WALK_PREFIXES', 'SLOPE_VARIANT'):
            env.pop(name, None)
        log = root / 'topology.log'
        with log.open('w') as stream:
            subprocess.run(['.venv/bin/python', '-m', 'app.disputes.parallel', RUN,
                            '0', '1', '1', str(root / 'out'), '999999'],
                           env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        with (root / 'out/part999999.pkl').open('rb') as stream:
            topology = pickle.load(stream)
        if not topology['complete'] or topology['done'] or topology.get('subdivisions'):
            raise ValueError('Fresh base must contain only the complete native shared top')
        subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'split',
                        str(root / 'out'), str(root / 'split')], env=env, check=True)
        queue = prefix + '/refine-v1'
        publish(s3, bucket, queue + '/base', 0, 'native-top', root / 'split', log)
        base = read(s3, bucket, queue + '/base/done/0.json')
        for job in range(100):
            roots = [(clock, 0, number) for number, clock, owner in topology['segs'] if owner % 100 == job]
            create(s3, bucket, queue + f'/base/done/{job}.json', {**base, 'job': job})
            create(s3, bucket, queue + f'/plans/{job}.json',
                   {'job': job, 'roots': roots, 'saved': 0, 'partitions': 24, 'depth': 4,
                    'recovery': None})
        result = {'jobs': list(range(100)), 'roots': len(topology['segs']),
                  'partitions': 24, 'depth': 4,
                  'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
        create(s3, bucket, prefix + '/config.json', result)
        create(s3, bucket, prefix + '/ready.json', result)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    prepare(*sys.argv[1:])

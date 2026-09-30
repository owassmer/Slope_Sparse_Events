"""Publish live shard logs and host CPU/memory readings without interrupting workers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3


def follow(bucket, prefix):
    s3 = boto3.client('s3', region_name='us-east-1')
    host = os.uname().nodename
    offsets = {}
    while True:
        paths = list(Path('/tmp').glob('walk-*/walk.log'))
        boot = Path('/var/log/cloud-init-output.log')
        if boot.exists():
            paths.append(boot)
        for path in paths:
            try:
                data = path.read_bytes()
                key = path.parent.name if path.name == 'walk.log' else host
                s3.put_object(Bucket=bucket, Key=f'{prefix}/live/{key}.log', Body=data)
                previous = offsets.get(str(path), 0)
                if path.name == 'walk.log' and len(data) > previous:
                    print(f'[{key}]\n{data[previous:].decode(errors="replace")}', flush=True)
                offsets[str(path)] = len(data)
            except FileNotFoundError:
                continue
        processes = subprocess.check_output(['ps', '-eo', 'pid,ppid,pcpu,rss,args'], text=True)
        state = {'time': time.time(), 'host': host, 'cpus': len(os.sched_getaffinity(0)),
                 'load': os.getloadavg(), 'memory': Path('/proc/meminfo').read_text(),
                 'workers': [x for x in processes.splitlines() if 'app.disputes.parallel' in x]}
        s3.put_object(Bucket=bucket, Key=f'{prefix}/live/{host}.json', Body=json.dumps(state).encode())
        time.sleep(20)


if __name__ == '__main__':
    follow(*sys.argv[1:])

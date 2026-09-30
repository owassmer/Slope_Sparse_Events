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
    identity = os.environ.get('SLOPE_WORKER_ID', host)
    previous_cpu = None
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
        ticks = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        total, idle = sum(ticks), ticks[3] + ticks[4]
        busy = None
        if previous_cpu and total > previous_cpu[0]:
            busy = 100 * (1 - (idle - previous_cpu[1]) / (total - previous_cpu[0]))
        previous_cpu = total, idle
        processes = subprocess.check_output(['ps', '-eo', 'pid,ppid,pcpu,rss,args'], text=True)
        state = {'time': time.time(), 'host': host, 'cpus': len(os.sched_getaffinity(0)),
                 'cpu_busy_percent': busy, 'worker': identity, 'load': os.getloadavg(), 'memory': Path('/proc/meminfo').read_text(),
                 'workers': [x for x in processes.splitlines() if 'app.disputes.parallel' in x]}
        s3.put_object(Bucket=bucket, Key=f'{prefix}/live/{identity}.json', Body=json.dumps(state).encode())
        time.sleep(20)


if __name__ == '__main__':
    follow(*sys.argv[1:])

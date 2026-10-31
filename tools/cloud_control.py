"""Watch the combined walk, recover claims from terminated EC2 workers, and launch pooling once."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path

import boto3
from cloud_walk import create, read


def launch(ec2, config, revision, mode='worker'):
    bucket, prefix = config['bucket'], config['prefix']
    work = f'worker {bucket} {prefix} --slots 8 --minutes 320' if mode == 'worker' else f'pool {bucket} {prefix} --slots 4'
    script = f'''#!/bin/bash
set -euxo pipefail
shutdown -h +360
export DEBIAN_FRONTEND=noninteractive AWS_DEFAULT_REGION=us-east-1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
apt-get update -qq
apt-get install -y -qq git curl python3-venv unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH=/root/.local/bin:$PATH
uv tool install awscli
trap 'aws s3 cp /var/log/cloud-init-output.log s3://{bucket}/{prefix}/boot/$(hostname).log || true; shutdown -h now' EXIT
git clone --quiet https://github.com/owassmer/Slope_Sparse_Events.git /opt/slope
cd /opt/slope
git checkout {revision}
uv sync --locked
uv run slope evidence build akoustis_20240514
uv run --with boto3 python tools/cloud_walk.py {work}
'''
    args = dict(ClientToken=f'slope-20260930-{mode}-{revision[:12]}' if mode == 'pool' else uuid.uuid4().hex, ImageId='ami-0045d7fc2ad003464', InstanceType='m7a.8xlarge', MinCount=1, MaxCount=1,
                SubnetId='subnet-0e34eefc56497cf9a', SecurityGroupIds=[config['security_group']],
                IamInstanceProfile={'Name': config['role']}, UserData=script,
                InstanceInitiatedShutdownBehavior='terminate', MetadataOptions={'HttpTokens': 'required'},
                BlockDeviceMappings=[{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': 200, 'VolumeType': 'gp3',
                    'DeleteOnTermination': True, 'Encrypted': True}}],
                TagSpecifications=[{'ResourceType': 'instance', 'Tags': [
                    {'Key': 'Name', 'Value': 'slope-walk-20260930'}, {'Key': 'Stage', 'Value': mode}]}])
    if mode == 'worker' and config.get('spot', False):
        args['InstanceMarketOptions'] = {'MarketType': 'spot', 'SpotOptions': {
            'SpotInstanceType': 'one-time', 'InstanceInterruptionBehavior': 'terminate'}}
    result = ec2.run_instances(**args)
    instance = result['Instances'][0]['InstanceId']
    print(f'launched {mode}: {instance}', flush=True)
    return instance


def watch(config, revision):
    s3 = boto3.client('s3', region_name='us-east-1')
    ec2 = boto3.client('ec2', region_name='us-east-1')
    bucket, prefix = config['bucket'], config['prefix']
    queue = read(s3, bucket, f'{prefix}/config.json')
    original = set(queue.get('original_jobs', set(range(100)) - set(queue['jobs'])))
    retired_key = f'{prefix}/drain/original-retired.json'
    original_retired = read(s3, bucket, retired_key) is not None
    previous = None
    recoveries = 0
    hosts = {}
    while True:
        try:
            objects = s3.list_objects_v2(Bucket=bucket, Prefix=f'{prefix}/done/').get('Contents', [])
            done = {int(Path(x['Key']).stem) for x in objects}
            if len(done) != previous:
                print(time.strftime('%Y-%m-%d %H:%M:%S'), f'completed {len(done)}/100', flush=True)
                previous = len(done)
            if not original_retired and original and original <= done:
                run = '36781427817'
                status = subprocess.check_output(
                    ['gh', 'run', 'view', run, '-R', 'owassmer/Slope_Sparse_Events',
                     '--json', 'status', '--jq', '.status'], text=True).strip()
                if status != 'completed':
                    subprocess.run(['gh', 'run', 'cancel', run, '-R', 'owassmer/Slope_Sparse_Events'], check=True)
                create(s3, bucket, retired_key, {'run': run, 'saved_shards': sorted(original), 'time': time.time()})
                original_retired = True
                print('Original shards saved; retired the original workflow and its queued duplicates.', flush=True)
            if done == set(range(100)):
                # An idempotent EC2 launch token prevents an uncertain response from starting a second pool VM.
                marker = read(s3, bucket, f'{prefix}/pool/launch.json')
                if marker is None:
                    instance = launch(ec2, config, revision, 'pool')
                    create(s3, bucket, f'{prefix}/pool/launch.json', {'instance': instance, 'revision': revision})
                instances = ec2.describe_instances(Filters=[{'Name': 'tag:Name', 'Values': ['slope-walk-20260930']}])
                workers = [i['InstanceId'] for r in instances['Reservations'] for i in r['Instances']
                           if i['State']['Name'] in ('pending', 'running')
                           and not any(t['Key'] == 'Stage' and t['Value'] == 'pool' for t in i.get('Tags', []))]
                if workers:
                    ec2.terminate_instances(InstanceIds=workers)
                for run in ('36781427817', '36789061458'):
                    subprocess.run(['gh', 'run', 'cancel', run, '-R', 'owassmer/Slope_Sparse_Events'], check=False)
                print('Pooling launched; redundant walk workers stopped. Follow S3 pool/ready.json.', flush=True)
                return
            instances = ec2.describe_instances(Filters=[{'Name': 'tag:Name', 'Values': ['slope-walk-20260930']}])
            dead = set()
            for r in instances['Reservations']:
                for instance in r['Instances']:
                    host = instance.get('PrivateDnsName', '').split('.')[0]
                    if host:
                        hosts[instance['InstanceId']] = host
                    if instance['State']['Name'] in ('terminated', 'stopped'):
                        dead.add(hosts.get(instance['InstanceId'], ''))
            released = []
            claims = s3.list_objects_v2(Bucket=bucket, Prefix=f'{prefix}/claims/').get('Contents', [])
            for obj in claims:
                job = int(Path(obj['Key']).stem)
                if job in done:
                    continue
                claim = read(s3, bucket, obj['Key'])
                if claim and claim['host'] and claim['host'] in dead:
                    s3.delete_object(Bucket=bucket, Key=obj['Key'])
                    released.append(job)
            if released:
                print('Released interrupted worker shards:', released, flush=True)
                if recoveries >= 3:
                    raise RuntimeError('Three replacement launches exhausted; inspect capacity before more spending')
                launch(ec2, config, revision)
                recoveries += 1
        except Exception as e:
            print(f'watch error: {e!r}', flush=True)
        time.sleep(45)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    args = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    watch(json.loads(Path(args.config).read_text()), revision)

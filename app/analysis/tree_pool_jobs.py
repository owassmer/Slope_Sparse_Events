"""Write a dependency stage's run.txt; never submit or pay for a workflow.

Usage: python -m app.analysis.tree_pool_jobs --phase nodes --runs WALK_ID ...
Then submit that file. Generate --phase node-union --runs NODE_RUN ...; submit.
Generate --phase parts --runs WALK_ID ... --node-run UNION_RUN; submit.
Finally --phase combine --runs PART_RUN ... . Each artifact is one bounded job.
Use the same walk run list for nodes and parts, including any local whole pieces.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path


def commands(*, phase, runs=(), local=(), node_run=None, workers=4):
    base = ['uv', 'run', 'slope', 'pool-tree']
    prefix = 'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 '
    if phase in ('node-union', 'combine'):
        args = base + ['--combine', '--database', 'combined.sqlite', '--output', 'var/pool']
        if phase == 'node-union':
            args += ['--nodes-only']
        for run in runs:
            args += ['--runs', run]
        for root in local:
            args += ['--local', root]
        return [prefix + shlex.join(args)]
    if phase == 'parts' and not node_run:
        raise ValueError('parts require the frozen --node-run from node-union')
    inputs = []
    for run in runs:
        result = subprocess.run(['gh', 'api', f'repos/{{owner}}/{{repo}}/actions/runs/{run}/artifacts',
                                 '--paginate', '--jq', '.artifacts[].name'],
                                check=True, capture_output=True, text=True)
        names = sorted(n for n in result.stdout.splitlines() if n.startswith('heavy-'))
        if not names:
            raise ValueError(f'{run}: no heavy artifacts')
        inputs.extend([['--runs', run, '--artifact', name] for name in names])
    for root in local:
        manifests = sorted(Path(root).rglob('piece.json'))
        if not manifests:
            raise ValueError(f'{root}: no whole pieces')
        inputs.extend([['--local', str(p.parent)] for p in manifests])
    lines = []
    for i, source in enumerate(inputs):
        args = base + source + ['--database', 'scratch.sqlite', '--output', f'var/pool/part-{i:05d}',
                                '--seconds', '1000', '--workers', str(workers)]
        args += ['--nodes-only'] if phase == 'nodes' else ['--partial', '--node-run', node_run]
        lines.append(prefix + shlex.join(args))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('nodes', 'node-union', 'parts', 'combine'), required=True)
    parser.add_argument('--runs', action='append', default=[])
    parser.add_argument('--run-list', type=Path)
    parser.add_argument('--local', action='append', default=[])
    parser.add_argument('--node-run')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--output', type=Path, default=Path('run.txt'))
    args = parser.parse_args()
    runs = args.runs
    if args.run_list:
        runs += [line.strip() for line in args.run_list.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
    lines = commands(phase=args.phase, runs=runs, local=args.local,
                     node_run=args.node_run, workers=args.workers)
    if not lines:
        raise ValueError('no jobs')
    args.output.write_text('\n'.join(lines) + '\n')
    print(f'{len(lines)} lines written to {args.output}; submit only this stage, then use its run ID in the next stage')


if __name__ == '__main__':
    main()

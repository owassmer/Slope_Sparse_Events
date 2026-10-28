"""Make/run the staged, offline 001-15a measurement.

prepare uses local out-N manifests to bound each job to <= 10,000 histories.
Run lines in stage order, retaining var/diag/001-15a between stages. Independent
lines within a stage can run in parallel. On ephemeral runners stage outputs
must be restored before the next stage. No cloud jobs or model calls launched.
Missing heavy/out pairs are downloaded with gh (read-only). Supply .runs logs
or --run-ids JSON mapping wave names to run IDs when those pieces are not local.
"""
import argparse
import csv
import json
import pickle
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from app.analysis.tree_concentration import measure, scan_nodes

BASE = Path('var/diag/001-15a')
WAVES = [f'walk-h{i}' for i in (1, 2)] + [f'walk-f{i}' for i in range(1, 42)]


def run_id(wave, mapping):
    known = {'walk-h1': 38271466884, 'walk-h2': 38273580489}
    known.update(mapping)
    if wave in known:
        return known[wave]
    log = Path('.runs') / f'remote-{wave}.log'
    text = log.read_text()
    ids = set(re.findall(r'actions/runs/(\d+)', text))
    if len(ids) != 1:
        raise ValueError(f'{log}: expected one GitHub actions/runs URL; supply --run-ids')
    return int(ids.pop())


def pair(root, artifact):
    number = artifact.removeprefix('heavy-')
    manifests = list((root / f'out-{number}').rglob('piece.json'))
    events = list((root / artifact).rglob('events.pkl.gz'))
    events += list((root / artifact).rglob('events.pkl'))
    mm = {p.parent.name: p for p in manifests}
    em = {p.parent.name: p for p in events}
    if not mm or mm.keys() != em.keys() or len(mm) != len(manifests) or len(em) != len(events):
        raise ValueError(f'{root}/{artifact}: incomplete or duplicate manifest/event pair')
    for name in mm:
        meta = json.loads(mm[name].read_text())
        if 'events_bytes' in meta and meta['events_bytes'] != em[name].stat().st_size:
            raise ValueError(f'{name}: truncated event stream')
    return [em[name] for name in sorted(mm)]


def prepare(root):
    BASE.mkdir(parents=True, exist_ok=True)
    stages = [[], [], [], [], [], []]
    inventories = []
    prefix = 'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m '
    for vintage in ('h', 'f'):
        node_parts = []
        reduced = [[] for _ in range(9)]
        for wave in (w for w in WAVES if w.startswith(f'walk-{vintage}')):
            summaries = []
            artifacts = sorted(p.name.replace('out-', 'heavy-') for p in (root / wave).glob('out-*'))
            if not artifacts:
                raise ValueError(f'No manifests for {wave}')
            for artifact in artifacts:
                manifests = sorted((root / wave / artifact.replace('heavy-', 'out-')).rglob('piece.json'))
                count = sum(json.loads(p.read_text())['histories'] for p in manifests)
                tag = f'{wave}-{artifact}'
                node = BASE / 'nodes' / f'{tag}.pkl'
                node_parts.append(str(node))
                common = f' --root {shlex.quote(str(root))} --wave {wave} --artifact {artifact}'
                stages[0].append(prefix + 'tools.tree_concentration_plan nodes' + common + f' --output {node}')
                inventories.append(dict(wave=wave, artifact=artifact, histories=count,
                                        pieces=[p.parent.name for p in manifests]))
                for lower in range(0, max(count, 1), 10000):
                    upper = min(lower + 10000, count)
                    output = BASE / 'parts' / f'{tag}-{lower}-{upper}.npz'
                    summaries.append(str(output))
                    stages[2].append(prefix + 'tools.tree_concentration_plan measure' + common +
                                     f' --lower {lower} --upper {upper} --catalog {BASE}/{vintage}-nodes.pkl --output {output}')
            inputs = BASE / f'{wave}-part-inputs.json'
            inputs.write_text(json.dumps(summaries))
            for d in range(9):
                output = BASE / 'reduced' / f'{wave}-{d}.npz'
                reduced[d].append(str(output))
                stages[3].append(prefix + f'app.analysis.tree_concentration reduce --distribution {d} --output {output} --input-list {inputs}')
        (BASE / f'{vintage}-node-inputs.json').write_text(json.dumps(node_parts))
        stages[1].append(prefix + f'app.analysis.tree_concentration union --output {BASE}/{vintage}-nodes.pkl --input-list {BASE}/{vintage}-node-inputs.json')
        for d in range(9):
            inputs = BASE / f'{vintage}-{d}-reduced-inputs.json'
            inputs.write_text(json.dumps(reduced[d]))
            stages[4].append(prefix + f'app.analysis.tree_concentration merge --distribution {d} --output {BASE}/{vintage}/{d} --input-list {inputs}')
    stages[5].append(prefix + 'tools.tree_concentration_plan finalize')
    (BASE / 'inventory.json').write_text(json.dumps(inventories, indent=2))
    (BASE / 'run.txt').write_text('\n'.join(line for stage in stages for line in stage) + '\n')
    (BASE / 'stages.json').write_text(json.dumps(dict(stage_line_counts=list(map(len, stages)),
        order=['nodes', 'catalog union', 'measurement', 'wave reductions', 'distribution tables', 'final tables'],
        excluded='AWS halves of f7-f19. f1-f41 historical code had a fact leak.',
        prerequisites='Restore prior-stage var/diag/001-15a outputs on each runner. For remote download, supply .runs logs or var/diag/001-15a/run-ids.json.'), indent=2))


def finalize():
    for vintage in ('h', 'f'):
        for table in ('draws.csv', 'summary.csv'):
            with (BASE / vintage / table).open('w') as output:
                writer = None
                for d in range(9):
                    with (BASE / vintage / str(d) / table).open() as source:
                        reader = csv.DictReader(source)
                        if writer is None:
                            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
                            writer.writeheader()
                        writer.writerows(reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'nodes', 'measure', 'finalize'))
    parser.add_argument('--root', type=Path, default=Path('var/remote'))
    parser.add_argument('--wave', choices=WAVES)
    parser.add_argument('--artifact')
    parser.add_argument('--run-ids', type=Path, default=BASE / 'run-ids.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--lower', type=int, default=0)
    parser.add_argument('--upper', type=int)
    args = parser.parse_args()
    if args.mode == 'finalize':
        finalize()
        return
    if args.mode == 'prepare':
        prepare(args.root)
        return
    mapping = json.loads(args.run_ids.read_text()) if args.run_ids.exists() else {}
    with tempfile.TemporaryDirectory(prefix='concentration-') as temporary:
        root = args.root / args.wave
        if not (root / args.artifact).exists():
            root = Path(temporary)
            for artifact in (args.artifact, args.artifact.replace('heavy-', 'out-')):
                subprocess.run(['gh', 'run', 'download', str(run_id(args.wave, mapping)),
                                '--name', artifact, '--dir', str(root / artifact)], check=True)
        streams = pair(root, args.artifact)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.mode == 'nodes':
            args.output.write_bytes(pickle.dumps(scan_nodes(streams), protocol=5))
        else:
            measure(streams, pickle.loads(args.catalog.read_bytes()), args.lower, args.upper).save(args.output)



# ---- Packed lines (the coordinator, October 28): the staged plan above makes 4,075 commands in six dependent stages,
# each restoring the last stage's files on fresh runners. The question needs two runs of 40 lines: every line takes a
# share of the walk's artifacts (balanced by histories), finds their questions, and the catalogs are unioned; then every
# line measures its share against the one catalog and writes one summary on a fixed sample of draws.

LINES = BASE / 'lines'


def plan_lines(lines, vintage, draws):
    """Write the share lists and the two run.txt files for one walk vintage (h or f)."""
    inventory = [r for r in json.loads((BASE / 'inventory.json').read_text()) if r['wave'].startswith(f'walk-{vintage}')]
    shares = [[] for _ in range(lines)]
    load = [0] * lines
    for r in sorted(inventory, key=lambda r: -r['histories']):
        i = load.index(min(load))
        shares[i].append([r['wave'], r['artifact']])
        load[i] += r['histories']
    out = LINES / vintage
    out.mkdir(parents=True, exist_ok=True)
    for i, share in enumerate(shares):
        (out / f'share-{i:02d}.json').write_text(json.dumps(share))
    prefix = 'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m tools.tree_concentration_plan '
    (out / 'nodes.txt').write_text(f'# 001-15a nodes, walk-{vintage}: {lines} lines\n' + ''.join(
        f'{prefix}line-nodes --share {out}/share-{i:02d}.json --output {out}/nodes-{i:02d}.pkl\n' for i in range(lines)))
    (out / 'measure.txt').write_text(f'# 001-15a measure, walk-{vintage}: {lines} lines, {draws} draws\n' + ''.join(
        f'{prefix}line-measure --share {out}/share-{i:02d}.json --catalog {out}/catalog.pkl --draws {draws} '
        f'--output {out}/hist-{i:02d}.npz\n' for i in range(lines)))
    print(f'{vintage}: {len(inventory)} artifacts, {sum(load):,} histories, per line {min(load):,}-{max(load):,}')


def _fetch(wave, artifact, mapping, into):
    root = Path('var/remote') / wave
    if (root / artifact).exists():
        return pair(root, artifact)
    for name in (artifact, artifact.replace('heavy-', 'out-')):
        subprocess.run(['gh', 'run', 'download', str(run_id(wave, mapping)), '--name', name, '--dir', str(into / name)],
                       check=True, capture_output=True)
    return pair(into, artifact)


def _line_job(job):
    mode, wave, artifact, catalog, draws = job
    mapping = json.loads((BASE / 'run-ids.json').read_text())
    from app.analysis.tree_concentration import Histogram
    with tempfile.TemporaryDirectory(prefix='concentration-') as temporary:
        streams = _fetch(wave, artifact, mapping, Path(temporary))
        if mode == 'nodes':
            return scan_nodes(streams)
        nodes = pickle.loads(Path(catalog).read_bytes())
        return measure(streams, nodes, hist=Histogram(draws, .01))


def line(mode, share, output, catalog=None, draws=32, workers=4):
    import multiprocessing as mp

    import numpy as np

    from app.analysis.tree_concentration import Histogram
    sample = np.linspace(0, 511, draws).round().astype(int) if draws < 512 else None
    jobs = [(mode, w, a, catalog, sample) for w, a in json.loads(Path(share).read_text())]
    output.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with mp.get_context('spawn').Pool(workers) as pool:
        if mode == 'nodes':
            nodes = {}
            for part in pool.imap_unordered(_line_job, jobs):
                for key, node in part.items():
                    if key in nodes and nodes[key] != node:
                        raise ValueError(f'domain mismatch: {key}')
                    nodes[key] = node
                done += 1
                print(f'{done}/{len(jobs)} artifacts, {len(nodes)} questions', flush=True)
            output.write_bytes(pickle.dumps(nodes, protocol=5))
        else:
            hist = Histogram(sample, .01)
            for part in pool.imap_unordered(_line_job, jobs):
                hist.merge(part)
                done += 1
                print(f'{done}/{len(jobs)} artifacts', flush=True)
                hist.save(output)       # as it goes, so a line stopped at its limit still answers for what it read
            hist.save(output)


def line_main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('plan-lines', 'line-nodes', 'line-measure', 'catalog', 'tables'))
    parser.add_argument('--vintage', choices=('h', 'f'))
    parser.add_argument('--lines', type=int, default=40)
    parser.add_argument('--draws', type=int, default=32)
    parser.add_argument('--share', type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--inputs', nargs='*', type=Path)
    args = parser.parse_args(argv)
    if args.mode == 'plan-lines':
        plan_lines(args.lines, args.vintage, args.draws)
    elif args.mode in ('line-nodes', 'line-measure'):
        line(args.mode.removeprefix('line-'), args.share, args.output, args.catalog, args.draws)
    elif args.mode == 'catalog':
        nodes = {}
        for path in args.inputs:
            for key, node in pickle.loads(path.read_bytes()).items():
                if key in nodes and nodes[key] != node:
                    raise ValueError(f'domain mismatch: {key}')
                nodes[key] = node
        args.output.write_bytes(pickle.dumps(nodes, protocol=5))
        print(f'{len(nodes)} questions')
    else:
        from app.analysis.tree_concentration import Histogram, tables
        hist = None
        for path in args.inputs:
            part = Histogram.load(path)
            if hist is None:
                hist = part
            else:
                hist.merge(part)
        tables(hist, args.output)


if __name__ == '__main__' and len(__import__('sys').argv) > 1 and __import__('sys').argv[1] in (
        'plan-lines', 'line-nodes', 'line-measure', 'catalog', 'tables'):
    line_main(__import__('sys').argv[1:])
elif __name__ == '__main__':
    main()

"""Offline concentration of terminal history probabilities; no forecast/model calls.

Bins have width .001 in -log(p). Integer bin populations merge exactly. Floating
mass is summed separately; cutoff counts are bounded, never interpolated as exact.
The catalog must be the union for ONE walk vintage (h and f stay separate).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
from pathlib import Path

import numpy as np

from app.analysis.piece_store import read_events
from app.analysis.tree_pool import GateDist, digest, random_dist
from app.disputes.forecast import class_firsts, expand_classes, path_mask, path_probability

WIDTH = .001
SEEDS = (17, 91, 307)
TARGETS = (.9, .99, .999, .9999)
LABELS = [f'{kind}-{seed}' for kind in ('random', 'skew80', 'skew95') for seed in SEEDS]


def distributions(nodes):
    result = [random_dist(nodes, seed) for seed in SEEDS]
    for confidence in (.8, .95):
        for seed in SEEDS:
            dist = GateDist()
            for key, node in nodes.items():
                branches = list(node.branches)
                rng = np.random.default_rng(int(digest((seed, key))[:16], 16))
                chosen = rng.integers(len(branches))
                dist[key] = {b: (1. if len(branches) == 1 else confidence if i == chosen
                                else (1 - confidence) / (len(branches) - 1))
                             for i, b in enumerate(branches)}
            result.append(dist)
    return result


class Histogram:
    def __init__(self):
        self.counts = {}
        self.mass = {}

    def add(self, distribution, probability, mask):
        if not 0 < probability <= 1 or not math.isfinite(probability):
            raise ValueError(f'zero/underflow or invalid probability: {probability}')
        key = distribution, int(math.floor(-math.log(probability) / WIDTH))
        if key not in self.counts:
            self.counts[key] = np.zeros(512, dtype=np.int64)
            self.mass[key] = np.zeros(512)
        self.counts[key][mask] += 1
        self.mass[key][mask] += probability

    def merge(self, other):
        for key in other.counts:
            if key not in self.counts:
                self.counts[key] = other.counts[key].copy()
                self.mass[key] = other.mass[key].copy()
            else:
                self.counts[key] += other.counts[key]
                self.mass[key] += other.mass[key]

    def save(self, path):
        keys = sorted(self.counts)
        np.savez_compressed(path, keys=np.array(keys, dtype=np.int64).reshape(-1, 2),
                            counts=np.array([self.counts[k] for k in keys], dtype=np.int64).reshape(-1, 512),
                            mass=np.array([self.mass[k] for k in keys]).reshape(-1, 512))

    @classmethod
    def load(cls, path, distribution=None):
        result = cls()
        with np.load(path) as data:
            for key, count, mass in zip(data['keys'], data['counts'], data['mass'], strict=True):
                if distribution is None or key[0] == distribution:
                    result.counts[tuple(key)] = count.copy()
                    result.mass[tuple(key)] = mass.copy()
        return result

    def rows(self, selected=None):
        for distribution, label in enumerate(LABELS):
            if selected is not None and distribution != selected:
                continue
            keys = sorted(k for k in self.counts if k[0] == distribution)
            counts = np.array([self.counts[k] for k in keys]).reshape(-1, 512)
            masses = np.array([self.mass[k] for k in keys]).reshape(-1, 512)
            cumulative = masses.cumsum(axis=0)
            for draw in range(512):
                total = float(cumulative[-1, draw]) if len(keys) else 0.
                if total > 1 + 1e-8:
                    raise ValueError(f'walked probability exceeds one: {label}/{draw}: {total}')
                for basis in ('walked', 'absolute'):
                    for share in TARGETS:
                        target = share * total if basis == 'walked' else share
                        lo = hi = None
                        if total > 0 and target <= total:
                            j = int(np.searchsorted(cumulative[:, draw], target))
                            before = int(counts[:j, draw].sum())
                            need = target - (cumulative[j - 1, draw] if j else 0)
                            b = keys[j][1]
                            # Widen by roundoff at bin boundaries.
                            low_p = math.exp(-(b + 1) * WIDTH) * (1 - 1e-12)
                            high_p = math.exp(-b * WIDTH) * (1 + 1e-12)
                            n = int(counts[j, draw])
                            lo = before + min(n, max(1, math.ceil(need / high_p)))
                            hi = before + min(n, max(1, math.ceil(need / low_p)))
                        yield dict(distribution=label, draw=draw, walked_mass=total,
                                   histories=int(counts[:, draw].sum()), basis=basis, share=share,
                                   count_lower=lo, count_upper=hi)


def scan_nodes(streams):
    nodes = {}
    for stream in streams:
        for kind, payload in read_events(stream):
            if kind == 'nodes':
                for key, node in payload.items():
                    if key in nodes and nodes[key] != node:
                        raise ValueError(f'domain mismatch: {key}')
                    nodes[key] = node
    return nodes


def measure(streams, nodes, lower=0, upper=None):
    first, dists = class_firsts(nodes), distributions(nodes)
    hist = Histogram()
    index = 0
    for stream in streams:
        for kind, payload in read_events(stream):
            if kind != 'path':
                continue
            index += 1
            if index <= lower:
                continue
            if upper is not None and index > upper:
                return hist
            p, _ = payload
            for q in expand_classes([p], nodes, 512, first=first):
                mask = path_mask(q, 512)
                mask = np.ones(512, bool) if mask is None else mask
                for i, dist in enumerate(dists):
                    hist.add(i, path_probability(q.edges, dist), mask)
    return hist


def tables(hist, output, distribution=None):
    output.mkdir(parents=True, exist_ok=True)
    rows = list(hist.rows(distribution))
    with (output / 'draws.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = []
    for label in (LABELS if distribution is None else [LABELS[distribution]]):
        selected = [r for r in rows if r['distribution'] == label]
        for basis in ('walked', 'absolute'):
            for share in TARGETS:
                group = [r for r in selected if r['basis'] == basis and r['share'] == share]
                row = dict(distribution=label, basis=basis, share=share,
                           unreachable_draws=sum(r['count_lower'] is None for r in group))
                for field in ('walked_mass', 'histories', 'count_lower', 'count_upper'):
                    values = [r[field] for r in group if r[field] is not None]
                    for name, percentile in (('median', 50), ('p90', 90), ('worst', 100)):
                        row[f'{field}_{name}'] = float(np.percentile(values, percentile)) if values else None
                    if field.startswith('count_'):
                        row[f'history_draws_{field}'] = sum(values) if len(values) == 512 else None
                summary.append(row)
    with (output / 'summary.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    (output / 'method.json').write_text(json.dumps(dict(
        bin_log_width=WIDTH, seeds=SEEDS, draws=512,
        counts='Lower/upper bounds on descending-probability cutoff; not interpolated counts.',
        history_draws='Sum of per-draw cutoffs; not union of distinct histories across draws.',
        percentiles='Linear percentiles over reachable draws only; empty absolute totals are unreachable.',
        population='Partial terminal histories only. f excludes AWS halves f7-f19 and has historical fact leak.',
        interpretation='Synthetic answer distributions, not model judgments or observed frequencies.'), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('nodes', 'union', 'measure', 'reduce', 'merge'))
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--inputs', nargs='+', type=Path)
    inputs.add_argument('--input-list', type=Path, help='JSON list of explicit expected input paths')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--distribution', type=int, choices=range(len(LABELS)))
    parser.add_argument('--lower', type=int, default=0)
    parser.add_argument('--upper', type=int)
    args = parser.parse_args()
    if args.input_list:
        args.inputs = [Path(p) for p in json.loads(args.input_list.read_text())]
    if not args.inputs:
        parser.error('input list must not be empty')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == 'nodes':
        args.output.write_bytes(pickle.dumps(scan_nodes(args.inputs), protocol=5))
    elif args.mode == 'union':
        nodes = {}
        for path in args.inputs:
            for key, node in pickle.loads(path.read_bytes()).items():
                if key in nodes and nodes[key] != node:
                    raise ValueError(f'domain mismatch: {key}')
                nodes[key] = node
        args.output.write_bytes(pickle.dumps(nodes, protocol=5))
    elif args.mode == 'measure':
        measure(args.inputs, pickle.loads(args.catalog.read_bytes()), args.lower, args.upper).save(args.output)
    else:
        hist = Histogram()
        if len(set(args.inputs)) != len(args.inputs):
            raise ValueError('duplicate summary input')
        for path in args.inputs:
            hist.merge(Histogram.load(path, args.distribution))
        if args.mode == 'reduce':
            hist.save(args.output)
        else:
            tables(hist, args.output, args.distribution)


if __name__ == '__main__':
    main()

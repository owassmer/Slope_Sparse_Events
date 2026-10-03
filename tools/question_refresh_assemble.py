"""Rebind saved paths to refreshed questions, preserving their financial equivalence."""
from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import pickle
import sqlite3
import time
from pathlib import Path

from app.disputes import pool
from tools.question_refresh import Results

_CONTEXT = None


def _part(source):
    fc, databases, out = _CONTEXT
    source = Path(source)
    destination = out / 'walked' / source.name
    checkpoint = destination.with_suffix('.refresh')
    if checkpoint.exists() and destination.exists():
        return str(checkpoint)
    stores = {}
    for instance, filename in databases.items():
        db = sqlite3.connect(f'file:{filename}?mode=ro', uri=True)
        # Per-part support is reduced by the coordinator; workers never contend
        # for writes to the shared result registry.
        db.execute('CREATE TEMP TABLE wanted (binding INTEGER PRIMARY KEY, mask BLOB NOT NULL)')
        stores[instance] = Results(db)
    baseline = set(fc.nodes)
    with source.open('rb') as fh:
        original = pickle.load(fh)
    with source.with_suffix('.meta').open('rb') as fh:
        _, (_, old_meta, missing, count, seconds) = pickle.load(fh)
    if missing or count != len(original) or len(old_meta) != count:
        raise ValueError(f'Incomplete preserved source: {source.name}')
    refreshed, meta, removed = [], [], 0
    for index, (path, entry) in enumerate(zip(original, old_meta, strict=True)):
        event, financial_key, filename, old_index = entry
        if filename != source.name or old_index != index:
            raise ValueError('Preserved source metadata order changed')
        new = stores[path.instance_id].rebind(fc, path, source.name, index)
        if new.steps != path.steps or new.edges != path.edges or new.outcome != path.outcome:
            raise ValueError('Question refresh changed financial history')
        if not any(new.mask):
            removed += 1
            continue
        # Equal original financial arrays remain equal on an equal retained
        # population. A different population cannot share this merge key.
        meta.append((event, (financial_key, new.mask), filename, len(refreshed)))
        refreshed.append(new)
    temporary = destination.with_suffix('.tmp')
    with temporary.open('wb') as fh:
        pickle.dump(refreshed, fh, protocol=5)
    temporary.replace(destination)
    report = {'meta': meta, 'source_count': count, 'retained': len(refreshed), 'removed': removed,
              'seconds': seconds, 'nodes': {k: fc.nodes[k] for k in fc.nodes.keys() - baseline},
              'node_group': dict(fc.node_group), 'classed': set(fc.classed),
              'wanted': {iid: list(store.db.execute('SELECT binding,mask FROM wanted'))
                         for iid, store in stores.items()}}
    with checkpoint.with_suffix('.tmp').open('wb') as fh:
        pickle.dump(report, fh, protocol=5)
    checkpoint.with_suffix('.tmp').replace(checkpoint)
    for store in stores.values():
        store.db.close()
    return str(checkpoint)


def assemble(run, control_file, walked, refresh, out, processes=8):
    """Only run after every globally assigned calculation has been ingested."""
    global _CONTEXT
    refresh, out = Path(refresh), Path(out)
    (out / 'walked').mkdir(parents=True, exist_ok=True)
    identity = {'control': hashlib.sha256(Path(control_file).read_bytes()).hexdigest(),
                'preparation': json.loads((refresh / 'fleet.json').read_text())['preparation'],
                'assembly': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    marker = out / 'assembly-source.json'
    if marker.exists():
        if json.loads(marker.read_text()) != identity:
            raise ValueError('Assembly checkpoints belong to another refresh preparation or implementation')
    else:
        if any((out / 'walked').iterdir()):
            raise ValueError('Unbound assembly checkpoints must not be reused')
        marker.write_text(json.dumps(identity, sort_keys=True))
    with open(control_file, 'rb') as fh:
        control = pickle.load(fh)
    prepared = json.loads((refresh / 'prepared.json').read_text())
    databases, stores = {}, {}
    for iid, item in prepared['instances'].items():
        filename = refresh / item['folder'] / 'plan.sqlite'
        db = sqlite3.connect(filename)
        store = Results(db)
        store.require_complete(item['calculations'])
        db.execute('DELETE FROM wanted')
        db.commit()
        databases[iid], stores[iid] = filename, store
    fc = pool.forecaster(run, control)
    fc.grouped.update(k for k in fc.classed if fc.nodes[k].node in
                      ('judgment_response', 'financing_at_floor', 'petition_cash_out'))
    _CONTEXT = fc, databases, out
    sources = sorted(Path(walked).glob('part*.pkl'), key=lambda p: int(p.stem[4:]))
    meta, total, retained, cost = [], 0, 0, {}
    started = time.monotonic()
    with multiprocessing.get_context('fork').Pool(processes) as workers:
        for number, checkpoint in enumerate(workers.imap_unordered(_part, sources, chunksize=1), 1):
            with open(checkpoint, 'rb') as fh:
                report = pickle.load(fh)
            meta.extend(report['meta'])
            total += report['source_count']
            retained += report['retained']
            fc.nodes.update(report['nodes'])
            fc.node_group.update(report['node_group'])
            fc.classed |= report['classed']
            cost[Path(checkpoint).with_suffix('.pkl').name] = (report['retained'], report['seconds'])
            for iid, wanted in report['wanted'].items():
                with stores[iid].db:
                    stores[iid].db.executemany('INSERT INTO wanted VALUES(?,?) ON CONFLICT(binding) '
                                              'DO UPDATE SET mask=mask_union(mask,excluded.mask)', wanted)
            progress = {'stage': 'rebind_saved_questions', 'sources': number, 'total_sources': len(sources),
                        'histories': total, 'retained': retained, 'seconds': round(time.monotonic()-started, 1)}
            (out / 'progress.json').write_text(json.dumps(progress))
            print(progress, flush=True)
    _CONTEXT = None
    if total != prepared['histories'] or total != control['walked']:
        raise ValueError('Refreshed source coverage differs')
    handles = [gzip.open(out / f'facts{b}.pkl.gz', 'wb', compresslevel=1) for b in range(pool.BUCKETS)]
    fact_count = 0
    try:
        for store in stores.values():
            for key, prefix, row in store.facts(fc):
                pickle.dump((key, prefix, row), handles[pool.bucket(key)], protocol=5)
                fact_count += 1
            store.db.close()
    finally:
        for fh in handles:
            fh.close()
    control.update(nodes=fc.nodes, node_group=fc.node_group, classed=fc.classed,
                   walked=retained, source_walked=total, part_cost=cost)
    with (out / 'control.pkl').open('wb') as fh:
        pickle.dump(control, fh, protocol=5)
    with (out / 'merge-meta.pkl').open('wb') as fh:
        pickle.dump(meta, fh, protocol=5)
    result = {'stage': 'questions_rebound', 'source_histories': total, 'retained': retained,
              'facts': fact_count, 'jev_started': False, 'ready_for_jev': False}
    (out / 'rebound.json').write_text(json.dumps(result, indent=2))
    print(result, flush=True)


def prepare_states(control_file, facts_file, bucket_number, out):
    """Use the existing balanced state workers on already globally deduplicated rows."""
    from app.disputes.forecast import Rows
    from tools.pool_fleet import PARTITIONS, balanced_questions

    with open(control_file, 'rb') as fh:
        control = pickle.load(fh)
    facts = {}
    with gzip.open(facts_file, 'rb') as fh:
        while True:
            try:
                key, _, row = pickle.load(fh)
            except EOFError:
                break
            facts.setdefault(key, Rows()).append_blob(row)
    keys = sorted(k for k in control['nodes'] if pool.bucket(k) == bucket_number and k not in control['classed'])
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for part, selected in enumerate(balanced_questions(keys, facts, PARTITIONS)):
        with gzip.open(out / f'{bucket_number}-{part}.pkl.gz', 'wb', compresslevel=1) as fh:
            pickle.dump({'keys': selected, 'facts': {k: facts[k] for k in selected if k in facts}}, fh, protocol=5)
    print({'bucket': bucket_number, 'questions': len(keys), 'partitions': PARTITIONS}, flush=True)


if __name__ == '__main__':
    import sys
    mode, *args = sys.argv[1:]
    if mode == 'assemble':
        assemble(*args[:5], processes=int(args[5]) if len(args) > 5 else 8)
    elif mode == 'states':
        prepare_states(args[0], args[1], int(args[2]), args[3])
    else:
        raise SystemExit('expected assemble or states')

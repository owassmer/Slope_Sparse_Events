"""001-5a: bounded, local-only sharing census and exclusive-time profile.

uv run python -m tools.measure_walk_sharing --root review --seconds 180
Uses the existing unbilled question-futures diagnostic, including its checks.
No memoization, pruning, model calls, or product changes. Results in var/diag/001-5a.

The full key is deliberately sufficient, not claimed minimal: chain state (except
read-only inputs and computational caches), scheduler state, canonical answer
facts and class labels. Canonical answers retain historical facts because later
questions replay them. Edges are incoming probability bookkeeping, not future
state. Cash-only is an optimistic LOWER BOUND, not a usable memoization key.
Counts concern visited decision histories in a DFS prefix, not a random sample.
"""
from __future__ import annotations

import argparse
import cProfile
import dataclasses
import hashlib
import inspect
import json
import pstats
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np


def freeze(value):
    if isinstance(value, np.ndarray):
        return ('array', value.dtype.str, value.shape,
                freeze(value.tolist()) if value.dtype.hasobject else value.tobytes())
    if dataclasses.is_dataclass(value):
        return freeze({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})
    if isinstance(value, dict):
        return tuple(sorted(((freeze(k), freeze(v)) for k, v in value.items()), key=repr))
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(map(freeze, value), key=repr))
    if isinstance(value, (tuple, list)):
        return tuple(map(freeze, value))
    if isinstance(value, np.generic):
        return value.item()
    return value


def digest(value):
    return hashlib.sha256(repr(freeze(value)).encode()).hexdigest()


# Do not use Chain.UNSEEN: it explicitly omits question facts.
OMIT = set('d s m dr sens basis fin bookings merton ev _ev_own _cum _cv _av _hd _keys '
           '_tau _out _stay_memo _atm_memo _atm_cols _atm_v _eq_v _offer_memo '
           '_shares_memo _price_v _price_key capture_questions capture_indices questions '
           'rec grec late reads _grp'.split())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='review')
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=180)
    args = parser.parse_args()
    from app.analysis.events import Chain, plain
    from tools import check_question_futures as diagnostic

    folder = Path('var/diag/001-5a')
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f'{args.root}-{args.row}'
    buckets = defaultdict(lambda: dict(histories=set(), cash=set(), full=set()))
    original = Chain.next_decisions
    census_seconds = 0.0
    fields = set()

    def measured(chain, cursors, support=None):
        nonlocal census_seconds
        result = original(chain, cursors, support)
        frame = inspect.currentframe().f_back
        if frame.f_code.co_name != '_loop':
            return result
        start = time.perf_counter()
        local = frame.f_locals
        s, pending = local['s'], local['pending']
        for j, candidate in enumerate(result.candidates):
            if candidate.chain == 'deterministic' or not (result.pick == j).any():
                continue
            assert chain.n == 1, 'this census intentionally measures one native draw'
            # pending records every asked decision, including answers with no step.
            depth = len(set(pending) | {type(candidate.decision)(n, c) for n, c, _ in s.steps})
            bucket = buckets[depth]
            history = digest((s.steps, pending, candidate.decision))
            if history in bucket['histories']:
                continue  # deterministic scheduler transitions are not new histories
            bucket['histories'].add(history)
            ev = chain.ev
            cash = (candidate.day, ev.cash, ev.lock, ev.capacity, ev.petition,
                    ev.kinds, ev.incurred, ev.proceeds)
            bucket['cash'].add(digest(cash))
            state = {k: v for k, v in vars(chain).items() if k not in OMIT}
            fields.update(state)
            # Sorted facts allow order-only convergence, without erasing any answer
            # which question construction or scheduling might subsequently read.
            answers = sorted((n, c, plain(b)) for n, c, b in s.steps)
            walk_state = {f.name: getattr(s, f.name) for f in dataclasses.fields(s)
                          if f.name not in ('steps', 'edges')}
            bucket['full'].add(digest((cash, state, answers, walk_state, pending,
                                      cursors, local['outcome'], local['committed'], candidate.decision)))
        census_seconds += time.perf_counter() - start
        return result

    Chain.next_decisions = measured
    profile = cProfile.Profile()
    started = time.perf_counter()
    sys.argv = ['check_question_futures', '--walker', 'chronological', '--root', args.root,
                '--row', str(args.row), '--seconds', str(args.seconds), '--order']
    status = 'completed'
    try:
        profile.enable()
        diagnostic.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            status = f'failed: {exc!r}'
            raise
        status = 'bounded prefix'
    except Exception as exc:
        status = f'failed: {exc!r}'
        raise
    finally:
        profile.disable()
        Chain.next_decisions = original
        profile.dump_stats(str(stem) + '.prof')
        stats = pstats.Stats(profile)
        categories = defaultdict(float)
        functions = []
        for (filename, line, name), (_cc, nc, tt, ct, _callers) in stats.stats.items():
            if filename == __file__ or name in ('<built-in method builtins.repr>',):
                category = 'measurement'
            elif any(x in filename for x in ('walk_order.py', 'question_history.py', 'measurement_stream.py')):
                category = 'checks_and_recording'
            elif 'frontier.py' in filename or name in ('decision_day', 'settlement_window', 'next_decisions'):
                category = 'frontier_dates'
            elif 'events.py' in filename and name in ('__init__', 'clone', 'sliced', '_run', 'event_trace', 'run', 'advance', 'finish', 'completed'):
                category = 'chain_build_replay'
            elif 'events.py' in filename or 'engine.py' in filename or '/finance/' in filename or 'share_price.py' in filename:
                category = 'booking_cash_engine'
            elif 'forecast.py' in filename or '/disputes/' in filename:
                category = 'questions_and_walk'
            else:
                category = 'other_including_numpy'
            categories[category] += tt
            functions.append(dict(file=filename, line=line, function=name, calls=nc,
                                  self_seconds=tt, cumulative_seconds=ct))
        output = dict(root=args.root, row=args.row, status=status, wall_seconds=time.perf_counter()-started,
                      census_inclusive_seconds=census_seconds,
                      depth=[dict(depth=d, **{k: len(v) for k, v in b.items()}) for d, b in sorted(buckets.items())],
                      exclusive_seconds=dict(categories), full_key_chain_fields=sorted(fields),
                      omitted_fields=sorted(OMIT), functions=sorted(functions, key=lambda r: -r['self_seconds']))
        # Keep this census's checks beside its profile, not only at the diagnostic's
        # shared output path (which the next measurement will overwrite).
        check_stem = 'chronological-1-' + (str(args.row) if args.root == 'review' else args.root)
        check_folder = Path('var/diag/001-1n')
        for suffix in ('.json', '.jsonl'):
            source = check_folder / (check_stem + suffix)
            if source.exists():
                Path(str(stem) + '-checks' + suffix).write_bytes(source.read_bytes())
        check_summary = check_folder / (check_stem + '.json')
        if check_summary.exists():
            checks = json.loads(check_summary.read_text())
            output['native_row'] = checks['native_row']
            output['emitted_histories'] = checks['histories']
            output['check_violations'] = checks['violations']
        Path(str(stem) + '.json').write_text(json.dumps(output, indent=2))
        print(json.dumps({k: v for k, v in output.items() if k not in ('functions', 'full_key_chain_fields', 'omitted_fields')}, indent=2))


if __name__ == '__main__':
    main()

"""Unbilled §1 check on emitted histories; outputs and recording sidecars live in var/.

--walker current starts at review and walks the entire tree (never a cut root).
--walker chronological starts at benchmark_chronological's saved root.
Default is one native draw; --draws 512 is reserved for run.txt.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from copy import copy
from pathlib import Path

import numpy as np

from tools.measurement_stream import MeasurementStream, emitted_histories

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from benchmark_chronological import root  # noqa: E402
from question_history import question_records  # noqa: E402
from walk_order import FutureChecker  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--walker', choices=('current', 'chronological'), required=True)
    parser.add_argument('--draws', type=int, choices=(1, 512), default=1)
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--root', default='saved')
    parser.add_argument('--saved', action='store_true', help='recheck the saved emitted histories and actual records')
    args = parser.parse_args()
    from app.analysis.build import basis_for, run_context
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import _S, Forecaster, _Walk, path_mask
    from app.disputes.parallel import _variant
    from tools.fresh_walk import RUN

    def forbidden(_):
        raise RuntimeError('no hydration or judgment calls in this diagnostic')

    if args.walker == 'current':
        ctx = run_context(RUN, Path('runs/recorded'))
        setup, sens = _variant(ctx)
        fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                        horizon=setup.horizon, hydrate=forbidden, setup=setup, basis=basis_for(ctx['feed'], setup),
                        slots=ctx['slots'], model=ctx['m'], sens=sens)
        d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
        native = args.row
        state = None
    else:
        import akoustis_20240514_fixture as fx

        steps, native, cls = root(args.root)
        d = fx.pending(instance_id='dispute_002')
        fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                        hydrate=forbidden, model=fx.model(), setup=fx.setup(), basis=fx.basis())
        state = _S(steps=steps, cls=cls, a4='seek', stayed=True, early=True)
    if not 0 <= native < fc.draws.n:
        raise ValueError('native row outside the operating population')
    if args.draws == 1:
        fc.draws = fc.draws.sub(np.arange(fc.draws.n) == native)
        fc.draws.prefixes = {}
    folder = Path('var/diag/001-1n')
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{args.walker}-{args.draws}-' + (str(native) if state is None else args.root)
    saved = folder / f'{stem}.pkl'
    metadata = dict(walker=args.walker, draws=fc.draws.n, native_row=native if args.draws == 1 else None,
                    root=None if state is None else args.root, loaded_saved=args.saved,
                    scope='whole tree from review' if state is None else 'saved root',
                    counting='question occurrences in completed history/draws, not probability weighted')
    with MeasurementStream(folder / f'{stem}.json', metadata) as stream:
        def consume(p):
            checks = []
            mask = path_mask(p, fc.draws.n)
            # Replay may create rebuilt nodes; do not let those alter the live walk.
            replay_fc = copy(fc)
            replay_fc.nodes, replay_fc.grouped = dict(fc.nodes), set(fc.grouped)
            for row in range(fc.draws.n):
                if mask is not None and not mask[row]:
                    continue
                checker = FutureChecker(replay_fc, d, row, records)
                try:
                    bad = checker.history(p)
                except AssertionError as exc:
                    # Missing recording/replay coverage is unknown, never a pass.
                    checks.append(dict(row=row, violations=[], check_error=str(exc)))
                else:
                    checks.append(dict(row=row, violations=bad, questions_checked=checker.checked))
            stream.append(dict(history=stream.histories, steps=p.steps, outcome=p.outcome, checks=checks))

        if args.saved:
            with saved.open('rb') as source:
                nodes, grouped, out, records = pickle.load(source)
            fc.nodes, fc.grouped = nodes, grouped
            for p in out:
                consume(p)
        else:
            walk = _Walk(fc, d) if state is None else ChronologicalWalk(fc, d)
            with question_records(walk) as records, emitted_histories(walk, consume):
                if state is None:
                    walk.run()
                else:
                    walk.run_from(state, np.ones(fc.draws.n, dtype=bool))
            with saved.open('wb') as target:
                pickle.dump((fc.nodes, fc.grouped, walk.out, records), target, protocol=pickle.HIGHEST_PROTOCOL)
    print(json.dumps({k: v for k, v in stream.result.items() if k != 'examples'}), flush=True)


if __name__ == '__main__':
    main()

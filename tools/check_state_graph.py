"""Bounded exact review-root comparison, local and unbilled. Streams results."""
import argparse
import json
import random
import signal
import sys
import time
from pathlib import Path

import numpy as np

from app.analysis.build import basis_for, run_context
from app.analysis.walk_measurement import LEAD_RUN, TimeLimit
from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import Forecaster
from app.disputes.parallel import _variant
from app.disputes.state_graph import GraphWalk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from question_history import question_records  # noqa: E402
from walk_order import Checker, FutureChecker  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=60, help='budget for each walker')
    args = parser.parse_args()
    if not 0 < args.seconds <= 450:
        parser.error('budget must be in (0, 450]')
    folder = Path('var/diag/001-6a')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'check-{args.row}.jsonl'
    ctx = run_context(LEAD_RUN, Path('runs/recorded'))
    setup, sens = _variant(ctx)

    def forbidden(*a, **kw):
        raise RuntimeError('no judgment calls')

    def make(kind):
        fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                        horizon=setup.horizon, hydrate=forbidden, setup=setup,
                        basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
        d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
        fc.draws = fc.draws.sub(np.arange(fc.draws.n) == args.row)
        fc.draws.prefixes = {}
        return kind(fc, d)

    def stop(*_):
        raise TimeLimit()

    old = signal.signal(signal.SIGALRM, stop)
    with target.open('w') as stream:
        def report(**values):
            stream.write(json.dumps(values) + '\n')
            stream.flush()
            print(json.dumps(values), flush=True)

        graph = make(GraphWalk)
        started = time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, args.seconds)
        try:
            graph.run()
        except TimeLimit:
            pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        report(phase='graph', seconds=time.monotonic()-started, histories=graph.graph.history_count(),
               states=len(graph.graph.nodes), hits=graph.graph.hits, complete=graph.graph.complete)
        expanded = iter(graph.graph._histories())
        plain = make(ChronologicalWalk)
        emit = plain.emit
        checked = 0
        samples = []
        rng = random.Random(6)

        def compared(state, outcome):
            nonlocal checked
            other, result, classes, captured = next(expanded)
            assert (state.steps, state.edges, outcome) == (other.steps, other.edges, result)
            emit(state, outcome)
            p = graph.materialize_history(other, result, classes)
            assert p == plain.out[-1], (p, plain.out[-1])
            assert graph.keys[-1] == plain.keys[-1]  # exact dated financial arrays
            for key, prefix, blob in captured:
                assert blob in records[key, prefix, None], (key, prefix)
            checked += 1
            if len(samples) < 5:
                samples.append(p)
            else:
                j = rng.randrange(checked)
                if j < 5:
                    samples[j] = p
            if checked % 100 == 0:
                report(phase='exact_prefix', histories=checked, seconds=time.monotonic()-started)

        plain.emit = compared
        started = time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, args.seconds)
        try:
            with question_records(plain) as records:
                plain.run()
        except (TimeLimit, StopIteration):
            pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
        report(phase='plain_plus_exact_comparison', histories=checked, seconds=time.monotonic()-started)
        violations = []
        for p in samples:
            bad = Checker(plain.fc, plain.d, 0, 0).history(p)
            bad += FutureChecker(plain.fc, plain.d, 0, records).history(p)
            if bad:
                violations.append(dict(steps=p.steps, failures=bad))
        report(phase='random_expanded_checks', histories=len(samples), violations=len(violations),
               examples=violations, plain_and_expanded_identical=True)
        if violations:
            raise SystemExit('Chronology acceptance failed (also present in identical plain histories).')


if __name__ == '__main__':
    main()

"""Reproduce the review-date chronology blocker without walking or judging.

The native pending-claim question constructors open I1 after motions=yes and
I2 after motions=no. Both settlement windows can precede the motion decision.
This probes the unchanged booking rules, not a proposed replacement schedule.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from app.analysis.build import basis_for, run_context
from app.disputes.forecast import Forecaster
from app.disputes.parallel import _variant
from tools.fresh_walk import RUN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--out', type=Path, default=Path('var/diag/001-1l/start-blocker.json'))
    args = parser.parse_args()
    ctx = run_context(RUN, Path('runs/recorded'))
    setup, sens = _variant(ctx)

    def no_judgment(_):
        raise RuntimeError('diagnostic must not hydrate or request judgment')

    fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                    horizon=setup.horizon, hydrate=no_judgment, setup=setup,
                    basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
    if not 0 <= args.row < fc.draws.n:
        raise ValueError('row outside the operating population')
    # Preserve this native draw's operating cash and keyed uniforms, rather than
    # regenerating a new one-draw population with different verdict bands.
    fc.draws = fc.draws.sub(np.arange(fc.draws.n) == args.row)
    fc.draws.prefixes = {}
    d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
    verdict = next(b for b in fc.verdict_classes(d) if b.startswith('award:'))
    prefix = (('settle', 'I0', 'no'), ('verdict', 'I0', verdict),
              ('judgment_response', 'entry', 'none'))
    results = []
    for answer, interval in (('yes', 'I1'), ('no', 'I2')):
        steps = prefix + (('post_trial_motions', '', answer), ('settle', interval, 'no'))
        tr = fc.trace(d, steps)
        motion = int(fc.trace(d, steps[:-1]).day[-1][0])
        settlement = int(tr.day[-1][0])
        results.append(dict(motions=answer, opened_interval=interval, motion_day=motion,
                            settlement_day=settlement,
                            motion_date=str(fc.review + timedelta(days=motion)),
                            settlement_date=str(fc.review + timedelta(days=settlement)),
                            settlement_offer_cents=int(tr.settle_offer[0]),
                            opens_before_prerequisite=settlement < motion))
    report = dict(run=RUN, row=args.row, draws=1, prefix=prefix, results=results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

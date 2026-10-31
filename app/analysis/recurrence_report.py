"""Render recurrence measurements, retaining missing/partial results explicitly."""
import json
from pathlib import Path

from app.analysis.recurrence_measurement import summarize_pairs
from app.disputes.recurrence import DECLARATIONS, SCENARIOS

ROWS = (146, 365, 438, 73, 511)


def report(output):
    output = Path(output)
    lines = ['# Recurrence bounds: savings and lender effects', '',
        'All switches are off by default. No cloud or judgment calls. Synthetic answer weights apply to '
        'supported flattened scheduler children, not individual model questions. Uniform gives each child equal '
        'weight; early-heavy gives the first child weight 4 and others 1; late-heavy gives the last child weight 4 '
        'and others 1. These are sensitivity scenarios, not observed frequencies or model judgments.', '',
        'The fourth candidate is the judgment-default recurrence: its second occurrence accounted for 31.5% '
        'of attributed extra histories on draw 365. Listing and cash exhaustion were large but not recurring '
        'in that attribution, so neither is bounded here.', '',
        *[f'- **{name}**: {text}' for name, text in DECLARATIONS.items()], '',
        '## Tree estimates', '',
        'Independent Knuth probes, including terminal emission. Seconds exclude setup, replay and retained-tree '
        'memory. Savings are ratios of noisy means, not precise speedups; JSON contains standard errors and '
        'approximate intervals. A negative saving may be sampling noise. Partial prefixes are not fixed-count '
        'estimates. History-touch shares weight baseline leaves by Knuth weights, not answer probabilities.', '',
        '| Draw | Scenario | Status / probes | Leaves | Walk seconds | Leaf saving | Time saving | Baseline histories touched |',
        '|---|---|---|---:|---:|---:|---:|---:|']
    def load(path):
        return json.loads(path.read_text()) if path.exists() else None
    def fmt(value, percent=False):
        return 'unknown' if value is None else (f'{value:.2%}' if percent else f'{value:,.2f}')
    for row in ROWS:
        base = load(output / 'unbounded' / f'draw-{row}.json')
        for name in SCENARIOS:
            x = load(output / name / f'draw-{row}.json')
            if not x:
                lines.append(f'| {row} | {name} | missing | | | | | |')
                continue
            leaves, seconds = x['leaves']['mean'], x['seconds']['mean']
            savings = [1 - v / base[k]['mean'] if base and base[k]['mean'] else None
                       for k, v in [('leaves', leaves), ('seconds', seconds)]]
            touch = base.get('touched_histories', {}).get(name, {}).get('share') if base else None
            lines.append(f"| {row} | {name} | {x['status']} / {x['completed_probes']} | {fmt(leaves)} | "
                         f'{fmt(seconds)} | {fmt(savings[0], True)} | {fmt(savings[1], True)} | {fmt(touch, True)} |')
    lines += ['', '## Lender effects through the 10 Nov horizon', '',
        'USD. Exposure is balance at filing multiplied by the filing indicator, NOT a recovery or loss. '
        'Conditional balance is unknown where no filings were sampled. Every paired history, including '
        'both filing indicators and balances, is in economics-<draw>.jsonl. Delta = bounded minus unbounded. '
        'Touched means the rule applies at a reached expansion (including removing an initiation option). '
        'The per-touched column is a paired conditional mean, not a full-population forecast. Zero touches '
        'means insufficient coverage, not proof the bound is harmless.', '',
        '| Draw | Answers | Bound | Pairs / touched | Δ collections | Δ filing (pp) | Δ exposure | Per-touched Δ collections / filing pp / exposure | Balance given filing before → after |',
        '|---|---|---|---:|---:|---:|---:|---|---|']
    pooled = []
    statuses = []
    draws_touched = {}
    for row in ROWS:
        x = load(output / f'economics-{row}.json')
        if not x:
            lines.append(f'| {row} | missing | | | | | | | |')
            continue
        statuses.append(f"- Draw {row}: {x['status']}; {x['elapsed_seconds']:.1f}s.")
        records = [json.loads(line) for line in (output / f'economics-{row}.jsonl').read_text().splitlines()]
        pooled.extend(records)
        for answers, groups in x['summary'].items():
            for name, group in groups.items():
                draws_touched.setdefault((answers, name), []).append(bool(group['touched']))
    # Equal selected-draw aggregate only when each draw has equal fixed sample sizes.
    summaries = [(str(row), load(output / f'economics-{row}.json')) for row in ROWS]
    complete = [x for _, x in summaries if x and x['status'] == 'completed']
    if len(complete) == len(ROWS) and len({x['samples'] for x in complete}) == 1:
        summaries.append(('selected-draw mean', {'summary': summarize_pairs(pooled)}))
    else:
        lines += ['', 'Aggregate across draws withheld: missing, unequal or incomplete samples.', '']
    for row, x in summaries:
        if not x:
            continue
        for answers, groups in x['summary'].items():
            for name, g in groups.items():
                delta, touched = g['aggregate_delta'], g['per_touched_history_delta']
                def values(stats):
                    return [stats[k]['mean'] for k in ('collections_dollars', 'filing_probability', 'filing_exposure_dollars')]
                ds, ts = values(delta), values(touched)
                ds[1] = None if ds[1] is None else 100 * ds[1]
                ts[1] = None if ts[1] is None else 100 * ts[1]
                balances = [g[side]['balance_given_filing_dollars']['mean'] for side in ('before', 'after')]
                lines.append(f"| {row} | {answers} | {name} | {g['n']} / {g['touched']} | "
                             + ' | '.join(fmt(v) for v in ds) + ' | ' + ' / '.join(fmt(v) for v in ts)
                             + ' | ' + ' → '.join(fmt(v) for v in balances) + ' |')
    lines += ['', '### Sample completion', '', *statuses, '', '## Selected draws touched', '',
        'Only these five draws, not an estimate of the 512-draw population. Missed rare paths can understate coverage.', '']
    for (answers, name), hits in draws_touched.items():
        lines.append(f'- {answers}, {name}: {sum(hits)}/{len(hits)} draws sampled touched.')
    lines += ['', 'Larger fixed-count runs are declared in run.txt; not submitted here. '
        'Small samples with no rare recurrence cannot support a decision to remove it. '
        'No bound has been enabled on the recorded analysis page.', '']
    target = output / 'report.md'
    target.write_text('\n'.join(lines))
    return target


if __name__ == '__main__':
    import sys
    print(report(sys.argv[1] if len(sys.argv) > 1 else 'var/diag/001-11a'))

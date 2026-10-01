"""Before-decision note facts from a complete path, including its earlier-dated events."""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from app.analysis.events import BIG, event_trace, pval
from app.disputes.forecast import _S, DisputePath, _Prefix, _Walk, as_of, situation_class

NAMES = {'petition_on_notes', 'holders_involuntary'}


def occasion(context: str) -> tuple[str, str]:
    origin = context.split('|')[0]
    if origin.startswith('judgment_'):
        return 'judgment_default', origin.removeprefix('judgment_')
    if origin.startswith('delisting_'):
        return 'delisting_notes', origin.removeprefix('delisting_')
    if origin == 'nonpayment':
        return 'nonpayment', ''
    raise ValueError(f'No deferred notes occasion for {origin!r}')


def decision_row(fc, d, steps: tuple, index: int, actor: str, mask=None) -> tuple[dict, object]:
    """The actor's state before its own petition, tied to the specific acceleration/default.

    A generic notes-due probe alone can accidentally read a different default later
    on the path. Require the named originating step to have actually fired and set
    the notes' due day. Exclude the actor's own filing when reading its decision.
    """
    if actor not in ('issuer', 'holders'):
        raise ValueError(actor)
    node, context, _ = steps[index]
    if node not in ('judgment_default', 'delisting_notes', 'nonpayment'):
        raise ValueError(f'Not a notes trigger: {steps[index]!r}')
    quiet = 'due' if node == 'nonpayment' else 'accelerated'
    counterfactual = steps[:index] + ((node, context, quiet),) + steps[index + 1:]
    probe = counterfactual + (('notes_due_date', actor, ''),)
    trace = event_trace(d, DisputePath(instance_id=d.instance_id, steps=probe, outcome='', edges=()),
                        fc.setup, fc.m, fc.draws, fc.sens, day_only=True)
    tr = _Prefix.of(trace, digest=False)
    row = as_of(fc.row_of(tr))
    # fired includes speculative views; only the completed path's booking counts.
    fired = trace.day[index]
    lag = 0 if node == 'nonpayment' else int(pval(fc.m, 'holder_notice_lag_days', fc.sens.get('holder_notice_lag_days', False)))
    due = row['sit']['notes_due_day']
    on = (fired < fc.days) & (due == fired + lag)
    if mask is not None:
        on &= mask
    row = {**row, 'day': np.where(on, row['day'], BIG)}
    return row, replace(tr, day=[row['day']], petition=row['petition'])


def record(fc, d, steps: tuple, key: str, mask=None):
    """Reclassify one original note question from its complete before-action state.

    Context in a note question's old key is an identity, not authority for its
    rendered facts. Each new class records the context actually true on its draws.
    """
    n = fc.nodes[key]
    origin = occasion(n.context)
    matches = [i for i, step in enumerate(steps) if step[:2] == origin]
    if len(matches) != 1:
        raise ValueError(f'{key}: expected one originating step, found {len(matches)}')
    index = matches[0]
    actor = 'holders' if n.node == 'holders_involuntary' else 'issuer'
    row, tr = decision_row(fc, d, steps, index, actor, mask)
    live = fc.live(n, row)
    cls = situation_class(row, live)
    if cls is None:
        raise ValueError('Deferred notes require a decision-state snapshot')
    conds = list(fc.spec[n.node].get('situation', ()))
    bits = np.zeros(len(live), dtype=np.int64)
    for i, condition in enumerate(conds):
        bits |= (np.asarray(tr.marks[condition]) <= row['day']).astype(np.int64) << i
    cls = np.where(live, np.strings.add(np.strings.add(cls.astype(str), '.t'), bits.astype(str)), '')
    context = np.full(len(live), '', dtype=object)
    ruling = next((s[2] for s in steps if s[0] == 'post_trial_ruling'), '')
    verdict = next((s[2] for s in steps if s[0] == 'verdict'), '')
    label = 'reduced' + ruling.split(':')[1] if ruling.startswith('reduced:') else (
        'set_aside' if ruling == 'set_aside' else
        'award' + verdict.split(':')[1] if verdict.startswith('award:') else 'entered')
    walk = _Walk(fc, d)
    for tag in sorted(set(cls[live])):
        selected = cls == tag
        state = replace(tr, day=[np.where(selected, row['day'], BIG)])
        tags = walk._tags(_S(steps=steps, cls=label), conds, (n.context.split('|')[0],), state, ())
        context[selected] = '|'.join((n.context.split('|')[0], *tags))
    row = {**row, 'note_context': context}
    return fc._split((key,), row, lambda k, r: fc._keep_late(k, steps[:index], r), cls)

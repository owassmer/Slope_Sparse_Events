"""Python rendering boundary for the dated walk's numeric question rows.

The verdict's probability is a sum of conjunctions, not one question. Its
selected class must be supplied by a followed history; pass 1 uses all classes.
The walk's TK retains amounts but not the verdict form's question keys, so the
rendering tables retain that case data separately, in the same class order.

A deferred question's key retains its origin's amount label. Callers must pass
that label when it differs from QCLS (the current dated state). This is distinct
from the amount band in its before-answer record.
"""
from dataclasses import dataclass

import numpy as np

from app.analysis.events import pending_template
from app.exact import fields_agenda as a
from app.exact import fields_walk as w
from app.exact import replay


@dataclass(frozen=True)
class Tables:
    instance: str
    branches: tuple[str, ...]
    verdicts: tuple[tuple[tuple[tuple[str, str], ...], ...], ...]
    conditions: tuple[tuple[str, ...], ...] = ()
    masks: object = None
    appeals: object = None
    stays: object = None


def case_tables(fc, d, ch0, tk):
    """Retain the verdict form, checking its order against pass1.case_tables."""
    classes = fc.verdict_classes(d)
    encoded = np.array([replay.encode(ch0, ('verdict', 'I0', b)) for b in classes], dtype=np.int64)
    if not np.array_equal(encoded, tk.verdicts):
        raise ValueError('verdict rendering tables do not match the walk tables')
    from app.analysis.events import MARKS
    from app.disputes.forecast import _Walk

    walk = _Walk(fc, d)
    conditions, appeals, stays = [], [], []
    for name in w.QNODES:
        conds = tuple(walk.situation_conditions(name)) if name in fc.spec else ()
        conditions.append(conds)
        if name == 'verdict':
            appeals.append(False)
            stays.append(False)
        else:
            node = fc.new_node(d, name)
            appeals.append(fc.uses_appeal_status(node) or name == 'enforce_after_final')
            stays.append(fc.uses_stay_status(node))
    masks = np.array([sum(1 << MARKS.index(c) for c in set(cs) if c in MARKS) for cs in conditions], np.int64)
    return Tables(d.instance_id, tuple(pending_template(fc.m)['verdict_branches']),
                  tuple(tuple(tuple(tuple(edge) for edge in conjunction) for conjunction in parts)
                        for parts in classes.values()), tuple(conditions), masks,
                  np.array(appeals, dtype=np.bool_), np.array(stays, dtype=np.bool_))


def amount_label(q, tables):
    kind, value = int(q[w.QCLS_KIND]), int(q[w.QCLS_VALUE])
    if kind == w.CLS_CLAIMED:
        return 'claimed'
    if kind == w.CLS_BRANCH:
        return tables.branches[value]
    if kind == w.CLS_AWARD:
        return f'award{value}'
    if kind == w.CLS_REDUCED:
        return f'reduced{value}'
    if kind == w.CLS_SET_ASIDE:
        return 'set_aside'
    raise ValueError(f'unknown amount class kind {kind}')


def context(q, tables, *, origin_amount=None):
    """Raw key context (not the dated situation's .ctx suffix)."""
    node, ctx, extra = (int(q[i]) for i in (w.QN, w.QCTX, w.QEXTRA))
    label = amount_label(q, tables) if origin_amount is None else origin_amount
    if node in (w.Q_SETTLE_OFFER, w.Q_SETTLE_ACCEPT):
        return a.SETTLE_CTX[ctx], label
    if node in (w.Q_MOTIONS, w.Q_RULING, w.Q_APPEAL):
        return (label,)
    if node == w.Q_EXECUTE:
        return 'I1', label
    if node in (w.Q_STAY_MOTION, w.Q_STAY_APPROVED, w.Q_REGISTRATION):
        return a.PHASE_CTX[ctx], label
    if node == w.Q_ENFORCE:
        return label, 'appealed' if extra else 'final'
    if node == w.Q_RESPONSE:
        return a.RESPONSE_CTX[ctx], label, ('first', 'after_none', 'after_offer')[extra]
    if node == w.Q_OFFERING:
        return (a.OCCASIONS[ctx],)
    if node == w.Q_REMITTITUR:
        return label, f'remit{extra}'
    if node == w.Q_HOLDERS_ACT:
        return (a.JDEFAULT_CTX[ctx], label) + (('entered_not_acted',) if extra else ())
    if node in (w.Q_PETITION_NOTES, w.Q_HOLDERS_INV):
        return ((*('judgment_' + p for p in a.JDEFAULT_CTX),
                 'delisting_delisted_suspension', 'nonpayment')[ctx],)
    if node == w.Q_BID:
        return ('deadline',)
    if node == w.Q_HEARING:
        return ('determination',)
    if node == w.Q_HOLDERS_DELIST:
        return ('delisted_suspension',)
    if node == w.Q_FLOOR:
        return (f'floor{ctx}',)
    if node == w.Q_CASH_OUT:
        return ('cash_exhausted',)
    raise ValueError(f'no scalar key context for question node {node}')


def keys(q, tables, *, verdict_class=None, origin_amount=None):
    """Question keys carried by a row; verdict_class is its selected table index.

    Without a selected verdict class return the full form's union (pass 1).
    Conjunctions remain available in tables.verdicts for probability arithmetic;
    a set of keys must never substitute for those conjunctions.
    """
    node = int(q[w.QN])
    if node == w.Q_VERDICT:
        parts = tables.verdicts if verdict_class is None else (tables.verdicts[verdict_class],)
        return tuple(sorted({key for outcome in parts for conjunction in outcome for key, _ in conjunction}))
    return (tables.instance + ':' + w.QNODES[node] + '|' +
            '|'.join(context(q, tables, origin_amount=origin_amount)),)


def situation_class(q, code, tables):
    """Render classify's numeric context, with the dated walk's amount label.

    Notes retain their condition-order .t bits and include the origin in .ctx,
    as notes.record does. Deferred questions use their first context fragment;
    regular constructors use all of their explicit fragments.
    """
    from app.exact import decode, qrecord
    from app.exact import fields_record as r

    node = int(q[w.QN])
    if node == w.Q_VERDICT:
        raise ValueError('a verdict form has per-question classes, not one aggregate class')
    base = code.copy()
    base[10], base[11] = 0, -2
    text = decode.situation_class(base)
    if not text:
        return ''
    conds = tables.conditions[node]
    bits = int(code[10])
    held = {c for j, c in enumerate(r.MARKS) if bits & (1 << j)}
    ctx = context(q, tables)
    notes = node in (w.Q_PETITION_NOTES, w.Q_HOLDERS_INV)
    deferred = node in (w.Q_STAY_APPROVED, w.Q_REGISTRATION, w.Q_FLOOR, w.Q_CASH_OUT,
                        w.Q_OFFERING, w.Q_BID, w.Q_HEARING, w.Q_HOLDERS_DELIST) or notes or (
        node == w.Q_RESPONSE and int(q[w.QCTX]) in (a.R_RIPE, a.R_POST)) or (
        node in (w.Q_SETTLE_OFFER, w.Q_SETTLE_ACCEPT) and a.SETTLE_CTX[int(q[w.QCTX])] == 'I3')
    if deferred:
        ctx = ctx[:1]
    label = amount_label(q, tables)
    tags = []
    for c in conds:
        if c == 'ruled' and label in ('claimed', 'no_award', 'set_aside'):
            if label not in ctx:
                tags.append(label)
        elif c == 'ruled':
            if not held & {'settled', 'paid'} and c in held and label not in ctx:
                tags.append(label)
        elif c not in held:
            continue
        elif c == 'stay_moved' and ('stay_pending' in ctx or 'stayed' in held or 'I4' in ctx):
            continue
        elif (c == 'stayed' and 'I4' in ctx) or (c == 'seeking' and 'after_seek' in ctx):
            continue
        else:
            tags.append(c)
    if notes:
        text += '.t' + str(sum(1 << j for j, c in enumerate(conds) if c in held))
        text += '.ctx' + '|'.join((*ctx, *tags)).encode().hex()
    elif conds and node not in (w.Q_EXECUTE, w.Q_REMITTITUR):
        motions = (bits >> qrecord.MOTION_SHIFT) & 3
        if 'ruled' in conds and motions:
            tags.append(('motions_pending', 'motions_open')[motions - 1])
        status = (bits >> qrecord.STATUS_SHIFT) & 7
        if 'stay_moved' in conds and status:
            tags.append('stay_' + r.STAY_STATUSES[status])
        text += '.ctx' + '|'.join(tags).encode().hex()
    if node == w.Q_OFFERING and bits & (1 << qrecord.FAILED_SHIFT):
        text += '.after_failed'
    if code[11] >= 0:
        text += '.g' + str(code[11])
    return text

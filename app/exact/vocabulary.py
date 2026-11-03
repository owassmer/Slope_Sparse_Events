"""The question vocabulary the compiled walk reads, from the case's dispute model (the contract's node templates):
per question node, the situation conditions that class it, and whether its wording reads the appeal status and the
stay status. One place for what the walk and the question keys both read."""
from __future__ import annotations

import numpy as np

from app.analysis.events import MARKS
from app.exact import fields_walk as w

PENDING_STAY = ("enforce_after_final", "judgment_response")  # their stay motion and its denial are dated facts


def texts(fc, d, name: str) -> dict:
    """The node's question texts: a pending claim's block where the node has one (`Forecaster.texts`)."""
    return fc.texts(name, d)


def conditions(fc, d, name: str) -> tuple[str, ...]:
    """The situation conditions that class the node's question: the contract's, and for a pending claim a stay motion
    and its denial wherever a stay motion is one (and on the two nodes whose route a pending motion changes)."""
    if name not in fc.spec:
        return ()
    conds = list(fc.spec[name].get("situation", []))
    if name in PENDING_STAY and "stay_moved" not in conds:
        conds.append("stay_moved")
    if "stay_moved" in conds:
        conds.append("stay_denied")
    return tuple(conds)


def reads_appeal_status(fc, d, name: str) -> bool:
    """Whether the question's wording reads the deadline to appeal or the judgment's status."""
    keys = set(texts(fc, d, name).get("situation_keys", ()))
    return bool(keys & {"judgment_status", "appeal_deadline"}) or name == "enforce_after_final"


def reads_stay_status(fc, d, name: str) -> bool:
    """Whether the question's wording reads the judgment's status (stayed, a stay pending or denied)."""
    return "judgment_status" in texts(fc, d, name).get("situation_keys", ())


def tables(fc, d):
    """Per question node (`fields_walk.QNODES`): its conditions, their marks as bits, and whether it reads the appeal
    and the stay status. The compiled walk is a pending claim's (`replay.constants`)."""
    from app.disputes.forecast import PENDING

    if d.stage != PENDING:
        raise NotImplementedError(f"the compiled walk: a pending claim, not {d.stage}")
    conds, bits = [], np.zeros(len(w.QNODES), dtype=np.int64)
    appeals = np.zeros(len(w.QNODES), dtype=np.bool_)
    stays = np.zeros(len(w.QNODES), dtype=np.bool_)
    for q, name in enumerate(w.QNODES):
        c = conditions(fc, d, name)
        conds.append(c)
        for x in set(c):
            if x in MARKS:
                bits[q] |= 1 << MARKS.index(x)
        if name != "verdict":
            appeals[q] = reads_appeal_status(fc, d, name)
            stays[q] = reads_stay_status(fc, d, name)
    return tuple(conds), bits, appeals, stays

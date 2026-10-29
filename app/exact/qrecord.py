"""A question's record and class in the dated walk (`tree.explore`, `tree.follow` call these at each question, on the
state before its answer): which record a question node reads (`record.record` with the agenda's node and context)
and its situation class (`record.situation_class`).

To be completed (task 002-10): the elementary questions' own records (the court's answer to a stay motion at the
approval, the registration orders, the issuer's and holders' petitions on the notes, the hearing request, the
offering's close), the context tags (`_Walk.contexts`, `_context_tags`) as the class's context id, and whether a node
reads the appeal and stay status (`Forecaster.uses_appeal_status`, `uses_stay_status`).
"""
import numpy as np
from numba import njit

from app.exact import fields_agenda as a
from app.exact import fields_record as r
from app.exact import fields_walk as tree
from app.exact import record

# the agenda's node for each question node (record.record's vocabulary); -1: an elementary question still to map
NODE_OF = np.array([a.SETTLE, a.SETTLE, a.VERDICT, a.MOTIONS, a.EXECUTE, a.STAY, -1, a.REGISTRATION, a.APPEAL,
                    a.ENFORCE, a.RESPONSE, -1, a.RULING, a.RULING, a.JDEFAULT, -1, -1, a.LISTING, -1, a.DELISTING,
                    a.CASH_FLOOR, a.CASH_OUT], dtype=np.int64)
assert len(NODE_OF) == len(tree.QNODES)


@njit(cache=True)
def question(st, k, scratch, q, ints, floats):
    """The record of question q (a `tree.QCOLS` row) on the state now, before its answer."""
    node = NODE_OF[q[tree.QN]]
    ctx = q[tree.QCTX]
    if q[tree.QN] == tree.Q_REGISTRATION and ctx == 1:
        node = a.ENFORCE
        ctx = 0
    record.record(st, k, q[tree.QDAY], node, ctx, scratch, ints, floats)


@njit(cache=True)
def klass(q, ints, code):
    """The class of question q with record `ints` (`record.situation_class`; context 0 until the tags are derived)."""
    record.situation_class(ints, code, True, False, 0, q[tree.QGROUP] >= 0 or q[tree.QGROUP] == -1 and False)
    code[11] = q[tree.QGROUP] if q[tree.QGROUP] >= 0 else -2


CLASS_COLS = r.NC

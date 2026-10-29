"""A question's record and class in the dated walk (`tree.explore`, `tree.follow` call these at each question, on the
state before its answer): which record a question node reads (`record.record` with the agenda's node and context)
and its situation class (`record.situation_class`).

`classify` takes case-level condition and consumption tables. The protected tree's
current `klass` call does not yet pass those tables; its output is only the old
base-class placeholder, not a faithful question identity. `question_keys` renders
classify's packed context at the Python boundary.
"""
import numpy as np
from numba import njit

from app.exact import agenda, core, notes, record
from app.exact import fields_agenda as a
from app.exact import fields_record as r
from app.exact import fields_walk as tree

# The agenda's node for each question (record.record's vocabulary).
# -1 is a plain dated read with no step-specific projection or declaration.
NODE_OF = np.array([a.SETTLE, a.SETTLE, a.VERDICT, a.MOTIONS, a.EXECUTE, a.STAY, -1, a.REGISTRATION, a.APPEAL,
                    a.ENFORCE, a.RESPONSE, a.OFFERING, a.RULING, a.RULING, a.JDEFAULT, -1, -1, a.LISTING, -1, a.DELISTING,
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
    day = q[tree.QDAY]
    source = st
    if q[tree.QN] in (tree.Q_PETITION_NOTES, tree.Q_HOLDERS_INV) and ctx == tree.ORIGIN_NONPAYMENT:
        # Section 7.02 makes the notes due without either actor's answer. Read
        # that deterministic consequence, not a hypothetical petition. Keep the
        # walk's pre-answer state untouched (both questions share this boundary).
        agenda.copy_into(scratch, st)
        notes.decide_nonpayment(scratch.S, scratch.D, scratch.coupons, k.nc, agenda.eq(scratch, k), day, a.NP_DUE)
        source = scratch
    record.record(source, k, day, node, ctx, scratch, ints, floats)
    # The court's direct read and offering's waiting capture use question_row
    # availability. The other probes retain advance's reset step raise_offer.
    if q[tree.QN] in (tree.Q_STAY_APPROVED, tree.Q_OFFERING):
        ints[r.RAISE_OFFER] = ints[r.OFFER_AVAILABLE]
    if q[tree.QN] == tree.Q_STAY_APPROVED:
        collateral = agenda.bond_collateral(st, k, day)
        balance = ints[r.CASH]
        need = k.need[min(max(day, 0), k.N - 1)]
        live = core.live(st.S, day) and day < k.N
        offer = max(balance - need, 0) if live and balance - need < collateral else 0
        ints[r.COLLATERAL], ints[r.STAY_OFFER] = collateral, offer
        ints[r.STAY_STATUS] = 1 if live and ints[r.OWED] > 0 else 4
        # 2 is the direct court read: unlike the motion projection its terms
        # omit owed (Chain._size_stay); the top-level owed remains present.
        ints[r.HAS_SECURITY] = 2
        terms = (day, balance, need, ints[r.OWED], collateral, offer, int(live))
        for j in range(7):
            ints[r.SECURITY0 + j] = terms[j]


@njit(cache=True)
def klass(q, ints, code):
    """The class of question q with record `ints` (`record.situation_class`; context 0 until the tags are derived)."""
    record.situation_class(ints, code, True, False, 0, q[tree.QGROUP] >= 0 or q[tree.QGROUP] == -1 and False)
    code[11] = q[tree.QGROUP] if q[tree.QGROUP] >= 0 else -2


# Context code: one held bit per MARKS entry, two motions bits, three stay
# status bits, one prior-failed-offering bit. No hashes or vocabulary truncation.
MOTION_SHIFT = len(r.MARKS)
STATUS_SHIFT = MOTION_SHIFT + 2
FAILED_SHIFT = STATUS_SHIFT + 3


@njit(cache=True)
def classify(q, ints, code, conditions, appeals, stays):
    """Numeric class with case-supplied consumption and dated context bits.

    Unlike klass's legacy placeholder, this does not assume every node reads the
    appeal deadline. conditions is a per-node mask in events.MARKS order.
    Render with question_keys.situation_class, not decode's vocabulary-id mode.
    """
    node, day = q[tree.QN], ints[r.DAY]
    record.situation_class(ints, code, appeals[node], stays[node], 0, False)
    code[11] = q[tree.QGROUP] if q[tree.QGROUP] >= 0 else -2
    bits = 0
    for j in range(len(r.MARKS)):
        if (conditions[node] & (1 << j)) and ints[r.MARK0 + j] <= day:
            bits |= 1 << j
    pending = ints[r.MOTIONS_FILED] <= day and ints[r.RULING] > day
    opened = ints[r.ENTRY] <= day and ints[r.MOTIONS_DEADLINE] > day and not pending
    bits |= (1 if pending else 2 if opened else 0) << MOTION_SHIFT
    bits |= ints[r.STAY_STATUS] << STATUS_SHIFT
    for j in range(ints[r.OFFERING_COUNT]):
        start = r.OFFERS0 + 3 * j
        if ints[start] < day and ints[start + 1] <= day and not ints[start + 2]:
            bits |= 1 << FAILED_SHIFT
    code[10] = bits


CLASS_COLS = r.NC

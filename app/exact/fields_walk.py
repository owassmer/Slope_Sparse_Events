"""The compiled walk's per-point state (design/002-exact-evaluator.md, The walk, compiled): each decision's answer and
the path's flags (`forecast._S`), in the walk block of S (`fields.S_WALK`), so a save of S saves them.

Decisions are numbered (the `DEC_*` ids); each has one scalar: OPEN (not resolved), ABSENT (resolved without a step:
it does not arise), or its answer's code (`replay.encode`'s answer column; the verdict's: AWARD, JUDGMENT,
NO_JUDGMENT).
"""
from __future__ import annotations

from app.exact import fields_agenda as a
from app.exact.fields import S_WALK

OPEN, ABSENT = -1, -2

# --- decisions ---------------------------------------------------------------------------------------------------------
DEC_SETTLE0 = 0                       # + fields_agenda.SETTLE_CTX index (I0, Ientry, I1, Istay, Ienforce, Iappeal, I2,
#                                       I3, I4)
DEC_VERDICT = 9
DEC_RESP0 = 10                        # + RESPONSE_CTX index (entry, I1, post, ripe)
DEC_MOTIONS = 14
DEC_EXECUTE = 15
DEC_STAY0 = 16                        # + PHASE_CTX index (I1, post)
DEC_REGISTRATION = 18                 # registration_early I1
DEC_RULING = 19
DEC_APPEAL = 20
DEC_JDEFAULT0 = 21                    # + JDEFAULT_CTX index (I1, post, ruling)
DEC_ENFORCE = 24
DEC_LISTING = 25                      # listing_date compliance (answered by the 'listing' step)
DEC_DELISTING = 26                    # delisting_notes delisted_suspension
DEC_CASH_OUT = 27
DEC_NONPAYMENT = 28
DEC_FLOOR0 = 28                       # + k (k = 1..MAX_FLOORS): cash_floor k
N_DEC = DEC_FLOOR0 + a.MAX_FLOORS + 1

AWARD, JUDGMENT, NO_JUDGMENT = 0, 1, 2  # the verdict's answer

# --- scalars ------------------------------------------------------------------------------------------------------------
ANS0 = S_WALK                         # ANS0 + decision id: its answer, OPEN or ABSENT
CLS_KIND = S_WALK + 50                # the amount class (_S.cls): 0 claimed, 1 a verdict branch (CLS_VALUE: its index),
CLS_VALUE = S_WALK + 51               # 2 an award (its booked amount), 3 reduced (the amount), 4 set aside
STAYED = S_WALK + 52
APPEALED = S_WALK + 53
EARLY = S_WALK + 54
A4 = S_WALK + 55                      # 0 open, 1 seek, 2 closed
RESP = S_WALK + 56                    # 0 none, 1 offer (the last answer leaving the response open)
FLOOR_K = S_WALK + 57                 # the next cash-floor decision's index
OUT_DONE = S_WALK + 58
NP_DONE = S_WALK + 59
COMMITTED = S_WALK + 60               # the latest decision day selected (no decision is inserted before it)
N_OFFERINGS = S_WALK + 61             # offering steps on the path (the recurrence bound counts them)
assert N_DEC <= 50

CLS_CLAIMED, CLS_BRANCH, CLS_AWARD, CLS_REDUCED, CLS_SET_ASIDE = range(5)
A4_OPEN, A4_SEEK, A4_CLOSED = range(3)

# --- frontier items -----------------------------------------------------------------------------------------------------
IT_NONE, IT_DECISION, IT_LEVY, IT_WAITING, IT_APPROVAL = range(5)
CH_LITIGATION, CH_DISTRESS, CH_LISTING, CH_NOTES, CH_DETERMINISTIC = range(5)
MAX_ITEMS = 64

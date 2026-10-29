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
DEC_STAY0 = 16                        # + PHASE_CTX index (I1, post): the stay motion
DEC_REGISTRATION = 18                 # registration_early I1
DEC_RULING = 19
DEC_APPEAL = 20
DEC_JDEFAULT0 = 21                    # + JDEFAULT_CTX index (I1, post, ruling): the holders act on the default
DEC_ENFORCE = 24
DEC_LISTING = 25                      # listing_date compliance (answered by the 'listing' step)
DEC_DELISTING = 26                    # delisting_notes delisted_suspension
DEC_CASH_OUT = 27
DEC_NONPAYMENT = 28
DEC_FLOOR0 = 28                       # + k (k = 1..MAX_FLOORS): cash_floor k
# the dated walk's elementary questions that the Python's constructors answer together with another (each at its own
# date: design/002-exact-evaluator.md, The walk, compiled)
DEC_APPROVAL0 = DEC_FLOOR0 + a.MAX_FLOORS + 1  # + stay row j: the court's answer at the approval
DEC_REG_POST = DEC_APPROVAL0 + a.MAX_STAYS     # registration_early (post), at its order date
DEC_A5_0 = DEC_REG_POST + 1                    # + notes origin o: the issuer's petition when the notes fall due
DEC_H3_0 = DEC_A5_0 + 4                        # + o: the holders' petition at their route's date
DEC_HEARING = DEC_H3_0 + 4                     # hearing_request, at its deadline
DEC_N1_0 = DEC_HEARING + 1                     # + offering row: the offering closes, at its close
N_DEC = DEC_N1_0 + 2
O_JD_I1, O_JD_POST, O_JD_RULING, O_DELISTING = range(4)  # notes origins

AWARD, JUDGMENT, NO_JUDGMENT = 0, 1, 2  # the verdict's answer
L_PENDING = 3                           # the listing: compliance not regained, the hearing request still to decide

# --- scalars ------------------------------------------------------------------------------------------------------------
ANS0 = S_WALK                         # ANS0 + decision id: its answer, OPEN or ABSENT
F0 = S_WALK + 70
CLS_KIND = F0                         # the amount class (_S.cls): 0 claimed, 1 a verdict branch (CLS_VALUE: its index),
CLS_VALUE = F0 + 1                    # 2 an award (its booked amount), 3 reduced (the amount), 4 set aside
STAYED = F0 + 2
APPEALED = F0 + 3
EARLY = F0 + 4
A4 = F0 + 5                           # 0 open, 1 seek, 2 closed
RESP = F0 + 6                         # 0 none, 1 offer (the last answer leaving the response open)
FLOOR_K = F0 + 7                      # the next cash-floor decision's index
OUT_DONE = F0 + 8
NP_DONE = F0 + 9
COMMITTED = F0 + 10                   # the latest item day selected (no item is inserted before it)
N_OFFERINGS = F0 + 11                 # offering outcomes on the path
REG_POST_DAY = F0 + 12                # registration_early (post): its order date while it waits (BIG: none)
HEARING_DAY = F0 + 13                 # hearing_request: its deadline while it waits (BIG: none)
VERDICT_IX = F0 + 14                  # the verdict class taken (its row in the case's table)
NOTES_DAY0 = F0 + 15                  # + o: the pending notes question's date (BIG: none)
NOTES_STAGE0 = F0 + 19                # + o: 0 none, 1 the issuer's petition pending, 2 the holders'
assert N_DEC <= 70 and NOTES_STAGE0 + 4 <= S_WALK + 100

CLS_CLAIMED, CLS_BRANCH, CLS_AWARD, CLS_REDUCED, CLS_SET_ASIDE = range(5)
A4_OPEN, A4_SEEK, A4_CLOSED = range(3)

# --- frontier items -----------------------------------------------------------------------------------------------------
IT_NONE, IT_DECISION, IT_LEVY, IT_WAITING, IT_APPROVAL = range(5)
CH_LITIGATION, CH_DISTRESS, CH_LISTING, CH_NOTES, CH_DETERMINISTIC = range(5)
MAX_ITEMS = 64

# --- the questions, as pass 1 emits them (`tree.explore`) -------------------------------------------------------------
QNODES = ("settlement_offer", "settlement_accept", "verdict", "post_trial_motions", "execute_pre_ruling",
          "stay_motion", "stay_approved", "registration_early", "appeal", "enforce_after_final", "judgment_response",
          "offering_closes", "post_trial_ruling", "remittitur_elected", "holders_act_judgment", "petition_on_notes",
          "holders_involuntary", "bid_compliance", "hearing_request", "holders_act_delisting", "financing_at_floor",
          "petition_cash_out")
(Q_SETTLE_OFFER, Q_SETTLE_ACCEPT, Q_VERDICT, Q_MOTIONS, Q_EXECUTE, Q_STAY_MOTION, Q_STAY_APPROVED, Q_REGISTRATION,
 Q_APPEAL, Q_ENFORCE, Q_RESPONSE, Q_OFFERING, Q_RULING, Q_REMITTITUR, Q_HOLDERS_ACT, Q_PETITION_NOTES, Q_HOLDERS_INV,
 Q_BID, Q_HEARING, Q_HOLDERS_DELIST, Q_FLOOR, Q_CASH_OUT) = range(len(QNODES))
# an occurrence: node, context (the interval, phase, occasion or origin), amount class (kind, value), extra (the
# response's 'after'; 'entered_not_acted'; the remittitur's booked amount; enforce's 'appealed'), option group, day
QCOLS = 8
QN, QCTX, QCLS_KIND, QCLS_VALUE, QEXTRA, QGROUP, QDAY, QDEPTH = range(QCOLS)
ORIGIN_NONPAYMENT = 4


"""The dated walk's state for one draw (design/002-exact-evaluator.md, The dated walk): the pending writs, the answered
decisions waiting for their own day, the stays, each step's record, and the codes the compiled walk uses for nodes,
contexts and answers (tables built in Python once; the compiled walk never reads a string).

Scalars live in the distress block of S (`fields.S_DISTRESS`); the tables are their own arrays.
"""
from __future__ import annotations

import numpy as np

from app.exact.fields import S_DISTRESS

# --- scalars ---------------------------------------------------------------------------------------------------------
N_WRITS = S_DISTRESS          # pending writs (Chain._pending_writs) on this draw, in the order ordered
N_WAIT = S_DISTRESS + 1       # rows used in the waiting table (Chain.waiting)
N_STAYS = S_DISTRESS + 2      # rows used in the stays table (Chain.stays)
N_STEPS = S_DISTRESS + 3      # steps walked (len(Chain.rec[0]))
CASH_FIRST_UNPAID = S_DISTRESS + 5  # the last cash run's first unpaid day (Chain.processed()[1])
CASH_NONPAYMENT = S_DISTRESS + 6   # the last cash run's §3.3 day (Chain.processed()[2])
FLOOR0 = S_DISTRESS + 10      # Chain.floor_days[k] at FLOOR0 + k (BIG: not decided), k = 1..MAX_FLOORS
MAX_FLOORS = 16               # a later fall needs new money after the last (an offering's close, a stay's release):
#                               at most the two initiations and the stays, so far fewer than 16
WRITS0 = S_DISTRESS + 40      # the pending writs' days (BIG: consumed)
MAX_WRITS = 16                # an enforcement, an early registration and an increase's writ each: a few at most
assert WRITS0 + MAX_WRITS <= S_DISTRESS + 100

# --- the waiting table (Chain.waiting): one row per answered decision waiting for its own day ------------------------
W_INDEX, W_NODE, W_CTX, W_BRANCH, W_DONE = range(5)
WCOLS = 5
MAX_WAIT = 32

# --- the stays table (Chain.stays): motion, approval, the answer ------------------------------------------------------
T_INDEX, T_MOTION, T_APPROVAL, T_APPROVED, T_MOVED, T_DONE = range(6)  # T_DONE: its approval item processed
TCOLS = 6
MAX_STAYS = 8

# --- each step's record (Chain.rec): day, cash, owed, collateral -------------------------------------------------------
MAX_STEPS = 96

# --- nodes -----------------------------------------------------------------------------------------------------------
NODES = ("verdict", "post_trial_motions", "settle", "execute_pre_ruling", "stay", "registration_early",
         "judgment_default", "post_trial_ruling", "appeal", "enforce", "listing", "delisting_notes",
         "judgment_response", "cash_floor", "cash_out", "offering", "nonpayment")
(VERDICT, MOTIONS, SETTLE, EXECUTE, STAY, REGISTRATION, JDEFAULT, RULING, APPEAL, ENFORCE, LISTING, DELISTING,
 RESPONSE, CASH_FLOOR, CASH_OUT, OFFERING, NONPAYMENT) = range(len(NODES))

# contexts
SETTLE_CTX = ("I0", "Ientry", "I1", "Istay", "Ienforce", "Iappeal", "I2", "I3", "I4")
PHASE_CTX = ("I1", "post")                      # stay, registration_early
JDEFAULT_CTX = ("I1", "post", "ruling")         # fields_notes.CTX_I1, CTX_POST, CTX_RULING
RESPONSE_CTX = ("entry", "I1", "post", "ripe")
R_ENTRY, R_I1, R_POST, R_RIPE = range(4)
# the occasions an offering is initiated on (the equity area's answers are indexed by them): cash-out, the responses'
# contexts, the k-th cash floor at OCCASIONS_IX_FLOOR0 + k
OCCASIONS = ("cash_out", "entry", "I1", "post", "ripe", *(f"floor{k}" for k in range(1, MAX_FLOORS + 1)))
OCCASIONS_IX_FLOOR0 = 4
RESPONSE_OCCASION = np.array([OCCASIONS.index(c) for c in RESPONSE_CTX], dtype=np.int64)

# answers
NO, YES, DENIED = 0, 1, 2                       # yes/no questions; a stay's denial
V_AWARD, V_BRANCH = 0, 1                        # verdict: an award with its band, or a template branch
R_UNCHANGED, R_REDUCED, R_SET_ASIDE = range(3)  # post_trial_ruling
E_NONE, E_LEVY = 0, 1                           # enforce
L_COMPLIANT, L_HEARING, L_SUSPENDED = range(3)  # listing
# the company's bookings (Chain.respond): a response's or a distress decision's answer
B_NONE, B_PAY, B_SEEK, B_PETITION, B_OFFER = range(5)
NP_DUE, NP_PETITION, NP_HOLDERS = range(3)      # nonpayment

# a step encoded: node, ctx, answer, and up to four amounts
STEP_COLS = 7

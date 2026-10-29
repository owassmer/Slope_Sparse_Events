"""Where each part of one draw's state lives in the exact evaluator's arrays (design/002-exact-evaluator.md, The
frame). Module constants, so numba compiles them as constants. Each names the Python it holds (`events.Chain`
attribute, on that draw); an area adds its own as it is ported. `export.chain_state` reads a Python Chain into them.

S: int64 scalars. D: int64 per-day arrays [ND, days]. P: float64 per-day arrays [NP, days]. Day indices follow the
Python: 0 is the day after the review date, BIG (10**6) means never or not on this draw.
"""
from __future__ import annotations

from app.analysis.events import KINDS, MARKS, OBLIGATIONS

BIG = 10**6
NONE = -(2**62)  # a Python None held in an int64 field (an amount that is not set)

# --- the judgment's timeline (Chain._timeline_pending, _enter, the verdict, motions and ruling steps) ---------------
V = 0                 # Chain.V: the verdict day
E_IX = 1              # Chain.E_ix: the judgment's entry day
EX_IX = 2             # Chain.e_ix: execution may issue from (Rule 62(a) ended)
E0 = 3                # Chain.E0: max(e_ix, 0)
AD = 4                # Chain.AD: the appeal deadline
A = 5                 # Chain.A: the last tolling order
F = 6                 # Chain.F: the final judgment (the post-trial ruling's day)
EF = 7                # Chain.EF: the surviving amount is enforceable from
EI = 8                # Chain.EI: an increase is enforceable from
FEE_DAY = 9           # Chain.fee_day
MOTIONS_FILED = 10    # Chain.motions_filed

# --- the judgment's amounts ---------------------------------------------------------------------------------------
ENTERED = 11          # Chain.entered
CLS_AMOUNT = 12       # Chain.cls_amount (NONE: the judgment as entered)
CLS_FEES = 13         # Chain.cls_fees
INCREASE = 14         # Chain.increase
RETRIAL = 15          # Chain.retrial (0/1)
Q1 = 16               # Chain.q1 (0/1)
APPEALED = 17         # Chain.appealed (0/1)
BAND_LO = 18          # Chain.band_range[0] (NONE: no band)
BAND_HI = 19          # Chain.band_range[1] (NONE: above the top line, or no band)
REMITTED = 20         # Chain.remitted_amount
ADVERSE_FROM = 21     # Chain.adverse_from
ADVERSE_UNTIL = 22    # Chain.adverse_until

# --- what was paid, levied, resolved, petitioned --------------------------------------------------------------------
RESOLVED = 23         # Chain.resolved
TAKEN = 24            # Chain.taken
LEVIED = 25           # Chain.levied (0/1)
PETITION = 26         # Chain.ev.petition (-1: none)
PET_CAUSE = 27        # Chain.pet_cause (events.PETITION_CAUSES index)
EARLY_REGISTRATION = 28  # Chain.early_registration
STAYED_FROM = 29      # Chain.stayed_from
LOCK_AMOUNT = 30      # Chain.lock_amount
LOCK_DAY = 31         # Chain.lock_day
COLLATERAL_REQUIRED = 32  # Chain.collateral_required
RELEASE_AT = 33       # Chain.release_at
DELISTED = 34         # Chain.delisted
HEARING_REQUESTED = 35  # Chain.hearing_requested
SUSPENDED = 36        # Chain.suspended
JD_ACTED = 37         # Chain.jd_acted (0/1)

# --- the current step's offers and reads ----------------------------------------------------------------------------
SETTLE_OFFER = 38     # Chain.settle_offer
STAY_OFFER = 39       # Chain.stay_offer
RAISE_OFFER = 40      # Chain.raise_offer
READS = 41            # Chain.reads

# --- marks: the day each condition holds from (Chain.marks[name]) ---------------------------------------------------
MARK0 = 42
MARK = {name: MARK0 + i for i, name in enumerate(MARKS)}
for _name, _ix in MARK.items():  # MARK_<NAME>: numba reads module ints as constants, never a dict
    globals()[f"MARK_{_name.upper()}"] = _ix

# --- the event cash's obligations: the day each was incurred (Chain.ev.incurred[name]) ------------------------------
INCURRED0 = MARK0 + len(MARKS)
INCURRED = {name: INCURRED0 + i for i, name in enumerate(OBLIGATIONS)}
for _name, _ix in INCURRED.items():
    globals()[f"INCURRED_{_name.upper()}"] = _ix

# --- table counts ---------------------------------------------------------------------------------------------------
N_TAKES = INCURRED0 + len(OBLIGATIONS)   # rows used in T_TAKES (Chain.takes)

NI = N_TAKES + 1

# --- per-day int64 arrays: the event cash (Chain.ev) ----------------------------------------------------------------
D_CASH = 0            # Chain.ev.cash
D_LOCK = 1            # the lock as booked (Chain.ev.base_lock under daily processing, else Chain.ev.lock)
D_CAPACITY = 2        # Chain.ev.capacity
D_KIND0 = 3
D_KIND = {name: D_KIND0 + i for i, name in enumerate(KINDS)}  # Chain.ev.kinds[name]
for _name, _ix in D_KIND.items():
    globals()[f"D_KIND_{_name.upper()}"] = _ix
ND = D_KIND0 + len(KINDS)

# --- per-day float64 arrays ------------------------------------------------------------------------------------------
NP = 0

# --- tables ----------------------------------------------------------------------------------------------------------
MAX_TAKES = 64        # Chain.takes: (day, amount) of each levy or payment; T_TAKES[i] = (day, amount)

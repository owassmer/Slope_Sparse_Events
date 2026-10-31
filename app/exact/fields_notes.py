"""Notes-owned one-draw state; names refer to events.Chain.

The coupon table has len(fin.interest_dates) rows, fixed when the case is loaded.
Chain.instrument_cash visits each contractual date once; no decision appends a
coupon. C_PRICE_DAYS holds its immutable ten-day calendar instead of a date.
No daily notes array is needed: cash is in the core's notes_interest row.
"""
from app.exact.fields import S_NOTES
from app.exact.fields_equity import C_CASH, C_COLS, C_DAY, C_KEPT, C_PRICE_DAYS  # noqa: F401

N_COUPONS = S_NOTES  # len(Chain.coupons)
NOTES_DUE_HOW = S_NOTES + 1  # Chain.notes_due_how[r]
HOW = ('', 'declared', 'automatic_j', 'repurchase')
DECLARED = 1
AUTOMATIC_J = 2
REPURCHASE = 3
STANDING = ('none', 'unpaid', 'stayed', 'levied_in_part', 'reduced', 'set_aside', 'paid', 'settled')
CTX_I1, CTX_POST, CTX_RULING = range(3)
READ_ENTERED, READ_POST, READ_BOTH = range(3)
# listing_dates table (immutable)
LISTING_KEYS = ('deadline', 'suspension', 'vote_call', 'effective_by', 'determination',
                'hearing_request', 'panel_decision', 'delisted_suspension', 'delisted_panel')
DEADLINE, SUSPENSION, VOTE_CALL, EFFECTIVE_BY, DETERMINATION, HEARING, PANEL, DELIST_SUSP, DELIST_PANEL = range(9)
# book_default branches
NO, YES, HOLDERS_FILE, ACCELERATED = range(4)
# coupon_cash_cents modes
CASH, SHARES, PRICED, FIXED = range(4)

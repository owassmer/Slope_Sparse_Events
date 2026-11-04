"""One-draw equity layout; indices name the corresponding events.Chain state.

Offers: two active initiations, including failures (the case's recurrence bound).
ATM: one row per scheduled trading day, allocated once from _atm_schedule, including
sales outside the cash horizon. Neither table grows during a walk. Occasion answers
are indexed by the walk's precompiled occasion vocabulary, not a bounded event list.
Settlement parts and coupons are caller-owned tables (the settlement/notes areas).
"""
from app.exact.fields import D_EQUITY, P_EQUITY, S_EQUITY

N_OFFERS = S_EQUITY  # number of _offers with rows[r] true
HAVE_ATM = S_EQUITY + 1  # _atm is not None
D_ATM = D_EQUITY  # _atm[r]
D_ATM_CUM = D_EQUITY + 1  # _atm_cum[r]
P_PRICE = P_EQUITY  # share_price[r]
MAX_OFFERS = 2
# T_OFFERS columns: _offers entries on this draw; rows is implicitly true.
O_OCCASION = 0
O_INIT = 1
O_CLOSE = 2
O_SHARES = 3
O_NET = 4
O_CLOSED = 5
O_BOOKED = 6
O_GROSS = 7  # terms['gross']
O_COSTS = 8  # terms['costs']
O_PRICE = 9  # terms['price_cents_x1e4']
O_DAYS = 10  # terms['close_days']
O_SHELF = 11  # terms['shelf_capacity']
O_COLS = 12
# T_ATM columns, indexed by the immutable schedule's sale index.
A_SOLD = 0  # _atm_sold[r, j]
A_CSOLD = 1  # _atm_csold[r, j]
# Coupon dependency table: Chain.coupons (day, cash[r], kept[r], when).
C_DAY = 0
C_CASH = 1
C_KEPT = 2
C_PRICE_DAYS = 3  # ten trading-day indices for 'when', columns 3..12
C_COLS = 13

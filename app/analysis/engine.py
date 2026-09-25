"""The cash and collections engine, vectorised over operating draws.

Per trajectory (one joint event path x one operating draw), day by day from the day after the review date: available
cash = opening available cash + operating flows + event cash - encumbered cash - the invoice remainder the borrower
pays itself - Slope's collections. Slope pays the supplier up to the financed amount on the funding date; the borrower
pays any remainder of the invoice on its due date. Collections follow the declared autopay rule: on each due date, and
at each month-end while anything is overdue, Slope takes the lesser of what is owed and the available cash (never
below zero). A deficit is reported, never funded by an imaginary source; unpaid amounts stay outstanding.

Lender outputs per trajectory: dated collections; principal collected (pro rata, reconciled so full collection closes
principal exactly); outstanding principal and principal dollar-days (capital tied up); the uncollected balance at
maturity and at the horizon; lender cash flows discounted at the supplied rate (funding outflow included).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from app.analysis.events import EventCash
from app.analysis.setup import Setup


@dataclass
class Trajectories:
    """Results for one joint path across all operating draws. Arrays are [draws, days] or [draws]."""
    cash: np.ndarray  # available cash at each day's end
    collections: np.ndarray  # Slope's collections by day
    locked: np.ndarray  # cash encumbered at each day's end
    capacity: np.ndarray  # credit capacity committed at each day's end
    min_cash: np.ndarray
    uncollected_maturity: np.ndarray
    uncollected_horizon: np.ndarray
    lender_pv: np.ndarray
    dollar_days: np.ndarray
    outstanding: np.ndarray  # outstanding principal by day (cents)


def schedule_arrays(setup: Setup) -> tuple[np.ndarray, int, int, int]:
    """Contractual payments by day [days], the maturity index, the funding index and the total repayable."""
    days = (setup.horizon - setup.review).days
    due = np.zeros(days, dtype=np.int64)
    offer = setup.offer
    last = 0
    if offer.amount_cents:
        for p in offer.schedule(setup.funding):
            i = (p.due - setup.review).days - 1
            if 0 <= i < days:
                due[i] += p.amount_cents
                last = max(last, i)
    return due, last, (setup.funding - setup.review).days - 1, offer.total_cents if offer.amount_cents else 0


def month_end_mask(setup: Setup) -> np.ndarray:
    days = (setup.horizon - setup.review).days
    first = setup.review + timedelta(days=1)
    return np.array([(first + timedelta(days=t + 1)).month != (first + timedelta(days=t)).month for t in range(days)])


def run(setup: Setup, opening_cents: int, operating: np.ndarray, events: EventCash) -> Trajectories:
    n, days = operating.shape
    due, maturity, fund_idx, total = schedule_arrays(setup)
    month_end = month_end_mask(setup)
    invoice = np.zeros(days, dtype=np.int64)
    remainder = setup.invoice_cents - setup.amount_cents
    inv_idx = (setup.invoice_due - setup.review).days - 1
    if remainder > 0 and 0 <= inv_idx < days:
        invoice[inv_idx] = remainder  # the borrower pays the rest of its invoice itself, once
    locked = np.cumsum(events.lock, axis=1)
    capacity = np.cumsum(events.capacity, axis=1)
    before = opening_cents + np.cumsum(operating + events.cash - events.lock - invoice, axis=1)  # before collections
    collections = np.zeros((n, days), dtype=np.int64)
    owed = np.zeros(n, dtype=np.int64)
    taken = np.zeros(n, dtype=np.int64)
    cash = np.empty((n, days), dtype=np.int64)
    for t in range(days):
        owed += due[t]
        avail = before[:, t] - taken
        if due[t] or month_end[t]:
            take = np.minimum(owed, np.maximum(avail, 0))
            collections[:, t] = take
            owed -= take
            taken += take
            avail = avail - take
        cash[:, t] = avail
    cum = np.cumsum(collections, axis=1)
    amount = setup.amount_cents
    if total:
        principal = np.where(cum >= total, amount, (cum * amount) // total)
    else:
        principal = np.zeros_like(cum)
    outstanding = np.zeros((n, days), dtype=np.int64)
    if amount:
        outstanding[:, fund_idx:] = amount - principal[:, fund_idx:]
    t = np.arange(1, days + 1)
    df = (1 + setup.discount_rate_bps / 10_000) ** (-t / 365)
    pv = collections @ df - (amount * df[fund_idx] if amount else 0)
    return Trajectories(
        cash=cash, collections=collections, locked=locked, capacity=capacity, min_cash=cash.min(axis=1),
        uncollected_maturity=(total - cum[:, maturity]) if total else np.zeros(n, dtype=np.int64),
        uncollected_horizon=(total - cum[:, -1]) if total else np.zeros(n, dtype=np.int64),
        lender_pv=pv, dollar_days=outstanding.sum(axis=1) / 100.0, outstanding=outstanding)

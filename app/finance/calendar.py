"""Calendar conventions: calendar-month arithmetic and U.S. banking business days.

Every convention here is declared. Payment calendars are generated only from an explicit
anchor date that the operator or scenario chose; nothing here invents a historical date.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

# Federal Reserve bank holidays (observed dates), 2024-2026: the business-day convention used
# for "Business Day" in the merchant agreement and for proposed-loan due dates.
BANK_HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-10-14", "2024-11-11", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-10-13", "2025-11-11", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25",
))


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in BANK_HOLIDAYS


def next_business_day(d: date) -> date:
    """d itself if it is a business day, else the following business day."""
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def previous_business_day(d: date) -> date:
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def add_months(d: date, months: int) -> date:
    """Calendar-month addition; a day past the target month's end clamps to its last day."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def month_ends(start: date, end: date) -> list[date]:
    """Last calendar day of each month from start's month through end's month, within [start, end]."""
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        if start <= last <= end:
            out.append(last)
        cur = add_months(cur, 1)
    return out


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(n) for n in range((end - start).days + 1)]

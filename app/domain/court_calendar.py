"""A court's calendar, for counting the periods the rules set: under FRCP 6(a)(1)(C) and FRAP 26(a)(1)(C) a period's
last day that is a Saturday, a Sunday or a legal holiday runs to the next day that is none of these, and a clerk enters
a judgment on a day its office is open.

Legal holidays (FRCP 6(a)(6), FRAP 26(a)(6)): the days set aside by statute for observing the federal holidays
(5 U.S.C. §6103(a); one that falls on a Saturday is observed the Friday before, on a Sunday the Monday after:
§6103(b) and Executive Order 11582), any other day declared a holiday by the President or Congress, and any day
declared a holiday by the state where the district court is located (FRCP 6(a)(6)(C)) or, for a period under the
appellate rules, where the district court or the circuit clerk's principal office is located (FRAP 26(a)(6)(C)). The
federal holidays follow from the statute; the state's, and any day declared by the President or Congress, are the
case's to declare (cases/<case>/scenario.json `courts`), each with its source: a court calendar is never assumed.

This is not the banking calendar (app/finance/calendar.py), which serves payment dates.
"""
from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th given weekday of the month (n = -1: the last)."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """5 U.S.C. §6103(b): a holiday on a Saturday is observed the Friday before, on a Sunday the Monday after."""
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def federal_holidays(year: int) -> frozenset[date]:
    """The days set aside by 5 U.S.C. §6103(a) for observing the federal holidays of `year` (Juneteenth from 2021), as
    observed (§6103(b)); a 1 January of the next year observed on 31 December is this year's."""
    mon, thu = 0, 3
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    if year >= 2021:
        fixed.append(date(year, 6, 19))
    days = {_observed(d) for d in fixed}
    days |= {_nth_weekday(year, 1, mon, 3), _nth_weekday(year, 2, mon, 3), _nth_weekday(year, 5, mon, -1),
             _nth_weekday(year, 9, mon, 1), _nth_weekday(year, 10, mon, 2), _nth_weekday(year, 11, thu, 4)}
    nxt = _observed(date(year + 1, 1, 1))
    if nxt.year == year:
        days.add(nxt)
    return frozenset(d for d in days if d.year == year)


class CourtCalendar:
    """The days a court's clerk's office is open: not a Saturday, a Sunday, a federal holiday or a declared day."""

    def __init__(self, declared: frozenset[date] = frozenset(), through: date | None = None) -> None:
        self.declared, self.through = frozenset(declared), through
        self._years: dict[int, frozenset[date]] = {}

    def holiday(self, d: date) -> bool:
        if self.through is not None and d > self.through:
            raise LookupError(f"{d}: beyond the days the case's courts are declared through ({self.through})")
        if d.year not in self._years:
            self._years[d.year] = federal_holidays(d.year)
        return d in self._years[d.year] or d in self.declared

    def open(self, d: date) -> bool:
        return d.weekday() < 5 and not self.holiday(d)

    def on_or_after(self, d: date) -> date:
        """d itself if the office is open, else the next day it is: a period's last day (Rule 6(a)(1)(C))."""
        while not self.open(d):
            d += timedelta(days=1)
        return d

    def after(self, d: date) -> date:
        """The first day after d the office is open (the next day the clerk can enter a judgment)."""
        return self.on_or_after(d + timedelta(days=1))


def for_case(m: dict) -> tuple[CourtCalendar, CourtCalendar]:
    """The case's district-court calendar (the periods of the civil rules) and its appeal calendar (the periods of the
    appellate rules), from the days it declares (`case_courts`). A case that declares no courts has no court calendar:
    a period cannot be counted without one."""
    courts = m.get("case_courts")
    if not courts:
        raise LookupError("the case declares no courts (scenario.json `courts`): a court period cannot be counted")

    def days(key: str) -> frozenset[date]:
        return frozenset(date.fromisoformat(x["date"]) for x in courts[key]["declared_holidays"])
    through = min(date.fromisoformat(courts[k]["through"]) for k in ("district", "appeal"))
    return CourtCalendar(days("district"), through), CourtCalendar(days("district") | days("appeal"), through)

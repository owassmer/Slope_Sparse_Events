"""The court's calendar (app/domain/court_calendar.py): the federal holidays as 5 U.S.C. §6103 sets and observes them,
the days the case's courts' states declare, and a period counted past the declared calendar refused."""
from datetime import date

import pytest

from app.domain.court_calendar import CourtCalendar, federal_holidays, for_case


def test_federal_holidays_as_observed():
    assert date(2026, 7, 3) in federal_holidays(2026)  # 4 July 2026 is a Saturday: the Friday before
    assert date(2027, 12, 31) in federal_holidays(2027)  # 1 January 2028 is a Saturday: observed in 2027
    assert date(2024, 6, 19) in federal_holidays(2024) and date(2020, 6, 19) not in federal_holidays(2020)
    assert {date(2024, 10, 14), date(2024, 11, 28), date(2024, 5, 27)} <= federal_holidays(2024)


def test_a_period_runs_past_weekends_and_holidays_and_not_past_the_declared_calendar():
    cal = CourtCalendar(frozenset({date(2024, 11, 5)}), through=date(2024, 12, 31))
    assert cal.on_or_after(date(2024, 6, 19)) == date(2024, 6, 20)  # Juneteenth
    assert cal.on_or_after(date(2024, 11, 5)) == date(2024, 11, 6)  # a declared state holiday
    assert cal.on_or_after(date(2024, 11, 9)) == date(2024, 11, 12)  # Saturday, Sunday, Veterans Day
    assert cal.after(date(2024, 5, 24)) == date(2024, 5, 28)  # the clerk enters after Memorial Day
    with pytest.raises(LookupError):
        cal.open(date(2025, 1, 2))


def test_the_appeal_calendar_adds_the_circuit_clerks_state():
    m = {"case_courts": {"district": {"declared_holidays": [{"date": "2024-11-29"}], "through": "2024-12-31"},
                         "appeal": {"declared_holidays": [{"date": "2024-06-14"}], "through": "2024-12-31"}}}
    district, appeal = for_case(m)
    assert district.open(date(2024, 6, 14)) and not appeal.open(date(2024, 6, 14))
    assert not district.open(date(2024, 11, 29)) and not appeal.open(date(2024, 11, 29))
    with pytest.raises(LookupError):
        for_case({})

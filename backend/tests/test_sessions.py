"""Slice 3: the calendar fixtures. §16 M2's gate asks for "holiday/half-day fixtures". These
call the library through app.services.sessions.compute_sessions; no database. Dates are real: a
failure here means the library disagrees with the exchange, and the test is not bent to fit it.
"""

from datetime import UTC, date, datetime

import pytest

from app.services.sessions import SessionRow, compute_sessions


@pytest.fixture(scope="module")
def nyse() -> dict[date, SessionRow]:
    return {
        r.session_date: r for r in compute_sessions("XNYS", date(2025, 1, 1), date(2025, 12, 31))
    }


@pytest.fixture(scope="module")
def krx() -> dict[date, SessionRow]:
    return {
        r.session_date: r for r in compute_sessions("XKRX", date(2025, 1, 1), date(2025, 12, 31))
    }


def test_nyse_holiday_has_no_session(nyse: dict[date, SessionRow]) -> None:
    # The spike-2 finding (§6.12): 2025-07-04 simply does not exist in SPY's window.
    assert date(2025, 7, 4) not in nyse
    assert date(2025, 12, 25) not in nyse


def test_nyse_special_closure_has_no_session(nyse: dict[date, SessionRow]) -> None:
    # 2025-01-09, the national day of mourning for President Carter: a closure that no rule of the
    # form "weekday and not a fixed holiday" would find.
    assert date(2025, 1, 9) not in nyse


def test_weekend_has_no_session(nyse: dict[date, SessionRow]) -> None:
    assert date(2025, 7, 5) not in nyse
    assert date(2025, 7, 6) not in nyse


def test_nyse_half_day_closes_at_one(nyse: dict[date, SessionRow]) -> None:
    for day in (date(2025, 7, 3), date(2025, 11, 28)):
        row = nyse[day]
        assert row.is_early_close
    # 13:00 America/New_York: EDT on 2025-07-03 (UTC-4), EST on 2025-11-28 (UTC-5).
    assert nyse[date(2025, 7, 3)].closes_at == datetime(2025, 7, 3, 17, 0, tzinfo=UTC)
    assert nyse[date(2025, 11, 28)].closes_at == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)


def test_nyse_ordinary_day(nyse: dict[date, SessionRow]) -> None:
    row = nyse[date(2025, 7, 7)]
    assert not row.is_early_close
    assert row.opens_at == datetime(2025, 7, 7, 13, 30, tzinfo=UTC)  # 09:30 EDT
    assert row.closes_at == datetime(2025, 7, 7, 20, 0, tzinfo=UTC)  # 16:00 EDT


def test_nyse_open_follows_the_clock_change(nyse: dict[date, SessionRow]) -> None:
    # The same 09:30 local open is 14:30 UTC in winter. Fixed UTC offsets would fail this.
    assert nyse[date(2025, 1, 6)].opens_at == datetime(2025, 1, 6, 14, 30, tzinfo=UTC)


def test_krx_holidays_have_no_session(krx: dict[date, SessionRow]) -> None:
    assert date(2025, 1, 29) not in krx  # Seollal
    assert date(2025, 10, 3) not in krx  # National Foundation Day
    assert date(2025, 10, 9) not in krx  # Hangul Day


def test_krx_ordinary_day(krx: dict[date, SessionRow]) -> None:
    row = krx[date(2025, 6, 2)]
    assert not row.is_early_close
    assert row.opens_at == datetime(2025, 6, 2, 0, 0, tzinfo=UTC)  # 09:00 KST, no daylight saving
    assert row.closes_at == datetime(2025, 6, 2, 6, 30, tzinfo=UTC)  # 15:30 KST


def test_range_is_inclusive_and_ordered(nyse: dict[date, SessionRow]) -> None:
    days = sorted(nyse)
    assert days[0] == date(2025, 1, 2)  # 2025-01-01 is a holiday
    assert days[-1] == date(2025, 12, 31)
    assert 245 <= len(days) <= 255
    assert all(r.opens_at.tzinfo is not None and r.closes_at > r.opens_at for r in nyse.values())

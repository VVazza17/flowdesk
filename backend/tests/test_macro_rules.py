"""M2 slice 5, step 3: the pure rules of the macro ingest (app.ingest.macro). No database.

The rule they enforce: nothing enters a point-in-time table without a real publication date.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app import settings
from app.ingest.macro import (
    VINTAGE_CHUNK,
    VINTAGE_DATE_CAP,
    available_at_for,
    chunk_vintage_dates,
    vintage_for,
)


def d(n: int) -> date:
    return date(2020, 1, 1) + timedelta(days=n)


# --- available_at: the end of the publication day in New York -----------------------------------


def test_available_at_is_midnight_new_york_the_day_after_publication() -> None:
    # 2025-05-29 (EDT, UTC-4): the day ends at 04:00 UTC on the 30th.
    assert available_at_for(date(2025, 5, 29)) == datetime(2025, 5, 30, 4, 0, tzinfo=UTC)
    # In winter (EST, UTC-5): 05:00 UTC.
    assert available_at_for(date(2025, 1, 15)) == datetime(2025, 1, 16, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        # The day before the clocks go forward (2026-03-08): the next midnight is still EST.
        (date(2026, 3, 7), datetime(2026, 3, 8, 5, 0, tzinfo=UTC)),
        # The 23-hour day itself: the next midnight is EDT.
        (date(2026, 3, 8), datetime(2026, 3, 9, 4, 0, tzinfo=UTC)),
        # The day before the clocks go back (2026-11-01): the next midnight is still EDT.
        (date(2026, 10, 31), datetime(2026, 11, 1, 4, 0, tzinfo=UTC)),
        # The 25-hour day itself: the next midnight is EST.
        (date(2026, 11, 1), datetime(2026, 11, 2, 5, 0, tzinfo=UTC)),
    ],
)
def test_available_at_across_the_clock_changes(published: date, expected: datetime) -> None:
    assert available_at_for(published) == expected


def test_available_at_is_never_earlier_than_the_end_of_the_publication_day() -> None:
    # The documented bias is one-sided: a replay may be told late (up to 24 h), never early.
    for day in (date(2025, 6, 2), date(2026, 3, 8), date(2026, 11, 1)):
        assert available_at_for(day).astimezone(UTC) > datetime(
            day.year, day.month, day.day, 23, 59, tzinfo=UTC
        )


def test_available_at_may_precede_effective_at_and_nothing_forbids_it() -> None:
    # IORB, measured 2026-11-12: the first print of the 2021-07-30 rate was published 2021-07-28.
    # A value can be knowable before the period it describes. available_at < effective_at is
    # legitimate and must stay unconstrained.
    effective = date(2021, 7, 30)
    assert available_at_for(date(2021, 7, 28)).date() < effective


# --- chunking the vintage dates -----------------------------------------------------------------


def test_the_chunk_size_leaves_a_margin_under_the_cap() -> None:
    assert VINTAGE_CHUNK < VINTAGE_DATE_CAP == 2000


@pytest.mark.parametrize("count", [0, 1, 1899, 1900, 1901, 3800, 5148, 5149])
def test_chunks_are_disjoint_ordered_and_cover_every_date(count: int) -> None:
    dates = [d(i) for i in range(count)]
    windows = chunk_vintage_dates(dates)
    assert len(windows) == -(-count // VINTAGE_CHUNK)
    covered = 0
    previous_end: date | None = None
    for start, end in windows:
        inside = [x for x in dates if start <= x <= end]
        assert 1 <= len(inside) <= VINTAGE_CHUNK
        assert previous_end is None or start > previous_end
        previous_end = end
        covered += len(inside)
    assert covered == count


def test_the_six_measured_series_each_split_into_windows_under_the_cap() -> None:
    # Vintage counts measured 2026-11-12 for the six series FRED refused in one piece.
    for name, count in {
        "DGS10": 5148,
        "DGS2": 5149,
        "DFII10": 5130,
        "VIXCLS": 3971,
        "T10YIE": 3164,
        "T10Y2Y": 3160,
    }.items():
        windows = chunk_vintage_dates([d(i) for i in range(count)])
        assert len(windows) in (2, 3), name


def test_chunking_refuses_unsorted_or_duplicate_dates_and_a_bad_size() -> None:
    with pytest.raises(ValueError):
        chunk_vintage_dates([d(2), d(1)])
    with pytest.raises(ValueError):
        chunk_vintage_dates([d(1), d(1)])
    with pytest.raises(ValueError):
        chunk_vintage_dates([d(1)], size=2001)
    with pytest.raises(ValueError):
        chunk_vintage_dates([d(1)], size=0)


# --- the configured series --------------------------------------------------------------------


def test_the_series_list_is_the_seventeen_of_section_6_4_with_distinct_ids() -> None:
    ids = [s.series_id for s in settings.FRED_SERIES]
    assert len(ids) == len(set(ids)) == 17


def test_each_series_records_where_its_point_in_time_record_starts() -> None:
    for s in settings.FRED_SERIES:
        # The first covered period is at or before the first vintage's own date by months at most,
        # and never after it by more than a few days (IORB's rate is dated ahead of publication).
        assert s.first_covered_period <= s.first_vintage + timedelta(days=7), s.series_id
        assert s.first_vintage.year >= 1960


# --- vintage_for: the inverse of available_at_for ---------------------------------------------


def test_vintage_for_inverts_available_at_for_including_the_clock_change_days() -> None:
    # 2025-03-09 is the 23-hour spring-forward day and 2025-11-02 the 25-hour fall-back day in New
    # York; the publication date must come back unchanged on both, and on either side of them.
    for day in (
        date(2025, 3, 8),
        date(2025, 3, 9),
        date(2025, 3, 10),
        date(2025, 11, 1),
        date(2025, 11, 2),
        date(2025, 11, 3),
        date(2020, 1, 7),
    ):
        assert vintage_for(available_at_for(day)) == day


def test_one_microsecond_before_the_end_of_the_day_is_still_that_day() -> None:
    boundary = available_at_for(date(2025, 5, 29))
    assert vintage_for(boundary) == date(2025, 5, 29)
    assert vintage_for(boundary + timedelta(microseconds=1)) == date(2025, 5, 30)

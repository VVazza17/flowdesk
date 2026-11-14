"""M2 slice 5, step 5: the cadence maths (app/ingest/cadence.py) and the configured thresholds."""

from datetime import date, timedelta

import pytest

from app import settings
from app.ingest.cadence import (
    FLOOR,
    MULTIPLE,
    Cadence,
    cadence,
    gaps_between,
    percentile,
    suggest_stale_after,
)


def days(*offsets: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=o) for o in offsets]


def test_gaps_are_the_days_between_consecutive_distinct_publication_days() -> None:
    assert gaps_between(days(0, 1, 4, 4, 11)) == [1, 3, 7]  # duplicates collapse
    assert gaps_between(days(11, 0, 4)) == [4, 7]  # order does not matter
    assert gaps_between(days(0)) == [] and gaps_between([]) == []


@pytest.mark.parametrize(
    ("values", "q", "want"),
    [
        ([1, 2, 3, 4], 0.5, 2.5),
        ([1, 2, 3, 4], 0.0, 1.0),
        ([1, 2, 3, 4], 1.0, 4.0),
        ([5], 0.95, 5.0),
    ],
)
def test_percentile_interpolates_like_postgres_percentile_cont(
    values: list[float], q: float, want: float
) -> None:
    assert percentile(values, q) == want


def test_p95_of_twenty_gaps_interpolates_between_the_top_two() -> None:
    # 19 gaps of 1 and one of 21: position 0.95 * 19 = 18.05 -> 1 + (21 - 1) * 0.05 = 2.0.
    assert percentile([1.0] * 19 + [21.0], 0.95) == pytest.approx(2.0)


def test_nothing_to_measure_with_fewer_than_two_publication_days() -> None:
    assert cadence([]) is None and cadence(days(0)) is None


def test_one_outlier_does_not_set_the_threshold_a_shutdown_gap_is_visible_not_baked_in() -> None:
    """CPI-shaped: monthly, one 55-day gap in 58. p95 ignores it; the maximum would not."""
    offsets = [0]
    for gap in [29, 30, 28, 31] * 14 + [55, 29]:
        offsets.append(offsets[-1] + gap)
    c = cadence(days(*offsets))
    assert c is not None and c.longest == 55 and c.p95 < 40
    assert suggest_stale_after(c) == timedelta(days=int(-(-MULTIPLE * c.p95 // 1)))
    assert suggest_stale_after(c) < timedelta(days=MULTIPLE * 55)


def test_the_threshold_rounds_up_to_whole_days_and_never_goes_below_the_floor() -> None:
    assert suggest_stale_after(Cadence(10, 1.0, 3.0, 4)) == timedelta(days=9)  # a daily series
    assert suggest_stale_after(Cadence(10, 1.0, 3.2, 4)) == timedelta(days=10)  # 9.6 -> up
    # 3 x 0.2 = 0.6 days would round up to 1 day, below the floor. (Real gaps are whole days, so
    # p95 >= 1 and the floor is a guard that real data never reaches; the test reaches it directly.)
    assert suggest_stale_after(Cadence(10, 0.5, 0.2, 1)) == FLOOR


# ---- the thresholds that are actually configured --------------------------------------------


def test_every_configured_fred_series_has_a_threshold_and_nothing_else_does() -> None:
    assert set(settings.FRED_DATA_STALE_AFTER) == {s.series_id for s in settings.FRED_SERIES}
    assert len(settings.FRED_DATA_STALE_AFTER) == 17


def test_the_thresholds_follow_each_series_own_cadence_not_one_global_number() -> None:
    t = settings.FRED_DATA_STALE_AFTER
    assert t["DGS10"] == t["VIXCLS"] == t["SOFR"] == timedelta(days=9)  # daily
    assert timedelta(days=9) < t["MORTGAGE30US"] < t["CPIAUCSL"] < t["DRCCLACBS"]  # weekly, monthly
    assert all(v >= FLOOR for v in t.values())
    assert settings.data_stale_after("NOSUCH") is None
    assert settings.data_stale_after("CPIAUCSL") == timedelta(days=105)

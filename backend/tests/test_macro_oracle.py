"""M2 slice 5, step 4: the point-in-time read against data it did not produce.

Two kinds of oracle, both from RECORDED live FRED responses (tests/fixtures/fred/):

1. For every vintage date of two real series, `series_as_of` must equal an independent resolution
   computed straight from the fixture's own columns (pure Python, no database, no read module).
2. ADR-0003's own stated assertion, which until now nothing verified: GDPC1 Q1-2025 read 23,526.085
   to an observer on 2025-05-01 and 23,548.210 on 2026-07-31, and one 2025 quarter was visible on
   the first date, four on the second. GDPC1 is NOT one of the 17 ingested series (a production
   scope change for a test is not worth it), so this runs on a recorded fixture, loaded through the
   real rules and the real store.

An ADR assertion that nothing verifies is exactly the drift this project keeps finding.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.macro import available_at_for
from app.services.macro_read import (
    Missing,
    Observation,
    Reason,
    latest_as_of,
    series_as_of,
    value_as_of,
)
from tests.seedfred import Recorded, seed

NY = ZoneInfo("America/New_York")


def go(scratch_url: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


def recorded(series: str) -> Recorded:
    if series == "DRCCLACBS":
        # Its vintage-date fixture is a day older than its history fixture: one vintage is missing
        # from the list (rule 5 needs every column to be a known date).
        return Recorded(series, extra_vintages=[date(2026, 8, 25)], latest=False)
    return Recorded(series, latest=True)


def resolve(rec: Recorded, as_of: datetime) -> dict[date, Decimal | None]:
    """The independent oracle: what was known at `as_of`, from the fixture's columns alone.

    A column (vintage date -> value) is visible when the end of its vintage day in New York is at or
    before `as_of`; a period's value is its latest visible column; periods before the earliest first
    print were never stored (exclusions A and B), so they are not in the answer.
    """
    earliest = min(rec.first)
    out: dict[date, Decimal | None] = {}
    for period, columns in rec.columns().items():
        if period < earliest:
            continue
        visible = [v for v in columns if available_at_for(v) <= as_of]
        if visible:
            out[period] = columns[max(visible)]
    return out


@pytest.mark.parametrize("series", ["DRCCLACBS", "GDPC1"])
def test_the_six_rules_accept_the_recorded_real_history(scratch_url: str, series: str) -> None:
    rec = recorded(series)

    async def fn(engine: AsyncEngine) -> Any:
        return await seed(engine, series, rec)

    verdict = go(scratch_url, fn)
    assert verdict.ok, verdict.refusal_text()
    assert verdict.revisions > 0 and verdict.revised_pre_vintage_excluded > 0


@pytest.mark.parametrize("series", ["DRCCLACBS", "GDPC1"])
def test_the_read_equals_an_independent_resolution_at_every_vintage(
    scratch_url: str, series: str
) -> None:
    rec = recorded(series)
    bounds = rec.bounds()

    async def fn(engine: AsyncEngine) -> list[tuple[datetime, dict[Any, Any], dict[Any, Any]]]:
        await seed(engine, series, rec)
        results = []
        for vintage in rec.vintages:
            exact = available_at_for(vintage)
            for t in (exact - timedelta(microseconds=1), exact):
                snap = await series_as_of(engine, series, t, bounds=bounds)
                results.append(
                    (t, {o.effective_at: o.value for o in snap.observations}, resolve(rec, t))
                )
        return results

    results = go(scratch_url, fn)
    assert len(results) == 2 * len(rec.vintages)
    wrong = [t for t, got, want in results if got != want]
    assert wrong == [], f"{len(wrong)} instants disagree with the fixture, first {wrong[:3]}"
    # Not vacuous: the answer changes over time, and some instants hold revised values.
    sizes = {len(want) for _t, _got, want in results}
    assert len(sizes) > 5


@pytest.mark.parametrize("series", ["DRCCLACBS", "GDPC1"])
def test_latest_as_of_is_the_newest_period_of_the_independent_resolution(
    scratch_url: str, series: str
) -> None:
    rec = recorded(series)
    bounds = rec.bounds()
    sample = rec.vintages[:: max(1, len(rec.vintages) // 60)] + rec.vintages[-3:]

    async def fn(engine: AsyncEngine) -> list[tuple[Any, Any]]:
        await seed(engine, series, rec)
        out = []
        for vintage in sample:
            t = available_at_for(vintage)
            got = await latest_as_of(engine, series, t, bounds=bounds)
            want = resolve(rec, t)
            out.append((got, want))
        return out

    empty = 0
    for got, want in go(scratch_url, fn):
        if not want:
            # The first vintage of a series only revises periods that predate its record, none of
            # which is stored, so nothing is known yet: an absence, with its own reason.
            empty += 1
            assert isinstance(got, Missing) and got.reason is Reason.NOT_PUBLISHED_AS_OF
            continue
        assert isinstance(got, Observation)
        newest = max(want)
        assert (got.effective_at, got.value) == (newest, want[newest])
    assert empty <= 1


# ---- ADR-0003's stated assertion, run -------------------------------------------------------


def test_adr_0003_gdpc1_assertion_as_measured_in_m0_spike_3(scratch_url: str) -> None:
    """ADR-0003, Evidence: 'Q1-2025 GDP read 23,526.085 to an observer standing on 2025-05-01; the
    same period reads 23,548.210 today (+0.094%) ... On 2025-05-01 exactly one 2025 quarter existed
    in the series; today four.' 'Today' was the spike's own date, 2026-07-31."""
    rec = recorded("GDPC1")
    bounds = rec.bounds()
    q1 = date(2025, 1, 1)
    spike_day = datetime(2025, 5, 1, 12, 0, tzinfo=NY)
    spike_today = datetime(2026, 7, 31, 12, 0, tzinfo=NY)

    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        await seed(engine, "GDPC1", rec)

        async def year_2025(t: datetime) -> list[Observation]:
            snap = await series_as_of(
                engine, "GDPC1", t, start=date(2025, 1, 1), end=date(2025, 12, 31), bounds=bounds
            )
            return list(snap.observations)

        return {
            "past": await value_as_of(engine, "GDPC1", q1, spike_day, bounds=bounds),
            "today": await value_as_of(engine, "GDPC1", q1, spike_today, bounds=bounds),
            "quarters_past": await year_2025(spike_day),
            "quarters_today": await year_2025(spike_today),
            # The annual update of 2026-09-30 has revised it again since the spike ran.
            "now": await value_as_of(
                engine, "GDPC1", q1, datetime(2026, 11, 14, 12, 0, tzinfo=NY), bounds=bounds
            ),
            "before_print": await value_as_of(
                engine, "GDPC1", q1, datetime(2025, 4, 30, 23, 59, 59, tzinfo=NY), bounds=bounds
            ),
            "at_midnight": await value_as_of(
                engine, "GDPC1", q1, datetime(2025, 5, 1, 0, 0, tzinfo=NY), bounds=bounds
            ),
        }

    out = go(scratch_url, fn)
    assert isinstance(out["past"], Observation) and out["past"].value == Decimal("23526.085")
    assert isinstance(out["today"], Observation) and out["today"].value == Decimal("23548.21")
    change = (out["today"].value - out["past"].value) / out["past"].value * 100
    assert round(change, 3) == Decimal("0.094")  # "+0.094%"
    assert [o.effective_at for o in out["quarters_past"]] == [q1]  # exactly one 2025 quarter
    assert len(out["quarters_today"]) == 4
    # The same question asked on the fixture's own day gives a different number, which is why the
    # assertion is pinned to the spike's date and not to "today".
    assert out["now"].value == Decimal("23650.213") != out["today"].value
    # The documented end-of-day rule: published on 2025-04-30, visible from 00:00 New York on 05-01.
    assert isinstance(out["before_print"], Missing)
    assert out["before_print"].reason is Reason.NOT_PUBLISHED_AS_OF
    assert isinstance(out["at_midnight"], Observation)
    assert out["at_midnight"].value == Decimal("23526.085")


def test_the_three_absences_on_real_gdpc1(scratch_url: str) -> None:
    """Unknown period, predates the vintage record, nothing published as of T: three answers, on
    real data, with `first_covered_period` keeping the middle one distinct."""
    rec = recorded("GDPC1")
    bounds = rec.bounds()
    t = datetime(2026, 1, 1, 12, 0, tzinfo=NY)

    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        await seed(engine, "GDPC1", rec)
        return {
            # Q1-1960: FRED's latest values hold a number for it, but it predates the first vintage
            # (1991-12-04), so it has no publication date and was never stored.
            "predates": await value_as_of(engine, "GDPC1", date(1960, 1, 1), t, bounds=bounds),
            # Q3-2026 exists in our data (published 2026-10-29) but not yet on 2026-01-01.
            "not_yet": await value_as_of(engine, "GDPC1", date(2026, 7, 1), t, bounds=bounds),
            # Not a quarter start: no revision of this date exists at any time.
            "unknown": await value_as_of(engine, "GDPC1", date(2025, 2, 15), t, bounds=bounds),
            "before_record": await value_as_of(
                engine, "GDPC1", date(2025, 1, 1), datetime(1990, 1, 1, tzinfo=UTC), bounds=bounds
            ),
        }

    out = go(scratch_url, fn)
    assert out["predates"].reason is Reason.PREDATES_VINTAGE_RECORD
    assert out["not_yet"].reason is Reason.NOT_PUBLISHED_AS_OF
    assert out["unknown"].reason is Reason.UNKNOWN_PERIOD
    assert out["before_record"].reason is Reason.BEFORE_VINTAGE_RECORD
    # "A value exists but predates the vintage record" is TRUE here, not a figure of speech:
    assert str(date(1960, 1, 1)) in {k for k in _latest_dates(rec)}


def _latest_dates(rec: Recorded) -> set[str]:
    return {d.isoformat() for d, v in rec.latest.items() if v is not None}

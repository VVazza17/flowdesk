"""M2 slice 5, step 5: the two SQL statements behind data age, on a real Postgres.

Both are keyed per metric and both read FIRST PRINTS only. CLAUDE.md rule 9: the other metric's rows
here can beat the right ones (a newer first print, a later effective date), and a revision published
long after the first print must not move either result. Checked by deleting the filter and watching
a test fail (BUILD-LOG, step 5).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.macro import available_at_for
from app.measure_cadence import publication_days
from app.services.health_probes import SqlRedisProbes


def go(scratch_url: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def put(
    engine: AsyncEngine, metric: str, period: date, revision: int, published: date
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO macro_metrics (metric, effective_at, revision, value, available_at, "
                "source) VALUES (:m, :p, :r, 1, :a, 'fred')"
            ),
            {"m": metric, "p": period, "r": revision, "a": available_at_for(published)},
        )


def test_macro_age_is_per_metric_and_counts_first_prints_only(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> list[dict[str, Any]]:
        # A: January's first print published 2026-02-12; a REVISION published long after (11-01).
        await put(engine, "A", date(2026, 1, 1), 0, date(2026, 2, 12))
        await put(engine, "A", date(2026, 1, 1), 1, date(2026, 11, 1))
        # B beats A on every count: a newer first print, a later period, and revisions of its own.
        await put(engine, "B", date(2026, 2, 1), 0, date(2026, 3, 12))
        await put(engine, "B", date(2026, 9, 1), 0, date(2026, 10, 14))
        await put(engine, "B", date(2026, 9, 1), 1, date(2026, 11, 10))
        return await SqlRedisProbes(engine, None).macro_age()

    got = {r["metric"]: r for r in go(scratch_url, fn)}
    assert list(got) == ["A", "B"]
    # A's age is its FIRST PRINT's: the November revision proves the source is alive, not that a
    # new period arrived, and counting it would hide a quarter-long stall (DRCCLACBS revises often).
    assert got["A"]["newest_available_at"] == available_at_for(date(2026, 2, 12))
    assert got["A"]["newest_effective_at"] == date(2026, 1, 1)
    assert got["B"]["newest_available_at"] == available_at_for(date(2026, 10, 14))
    assert got["B"]["newest_effective_at"] == date(2026, 9, 1)


def test_macro_age_of_nothing_is_empty(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> Any:
        return await SqlRedisProbes(engine, None).macro_age()

    assert go(scratch_url, fn) == []


def test_publication_days_are_new_york_publication_days_first_prints_in_the_window(
    scratch_url: str,
) -> None:
    now = datetime(2026, 11, 14, tzinfo=UTC)

    async def fn(engine: AsyncEngine) -> dict[str, list[date]]:
        # A: three first prints on three publication days, one of them outside a 1-year window.
        for i, published in enumerate([date(2024, 6, 3), date(2026, 3, 2), date(2026, 3, 9)]):
            await put(engine, "A", date(2026, 1, 1) + timedelta(days=i), 0, published)
        await put(engine, "A", date(2026, 1, 1), 1, date(2026, 10, 1))  # a revision: not counted
        # B: two first prints on the SAME day count once; the other metric's days never leak in.
        await put(engine, "B", date(2026, 5, 1), 0, date(2026, 5, 20))
        await put(engine, "B", date(2026, 5, 2), 0, date(2026, 5, 20))
        await put(engine, "B", date(2026, 5, 3), 0, date(2026, 5, 27))
        return await publication_days(engine, now, years=1)

    got = go(scratch_url, fn)
    # available_at is 00:00 New York the day AFTER publication; the publication day is recovered
    # exactly, not shifted by one.
    assert got["A"] == [date(2026, 3, 2), date(2026, 3, 9)]
    assert got["B"] == [date(2026, 5, 20), date(2026, 5, 27)]

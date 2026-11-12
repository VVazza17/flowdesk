"""M2 slice 5, step 3: the FRED backfill into macro_metrics, on a real Postgres and a fake FRED.

Needs DATABASE_URL (a scratch database per test). The fake FRED serves what the real one was
measured to serve: vintage dates, first prints by window with the 2000-vintage cap, latest values.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.errors import SchemaMismatch
from app.ingest.fred_feed import PROVIDER, backfill_body
from app.ingest.macro import FredSeries
from app.ingest.runner import RetryPolicy, run_feed
from app.services.macro_store import FirstPrint, insert_first_prints
from app.tasks.fred_ingest import BackfillIncomplete, backfill_all

ADMIN_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
NO_RETRY = RetryPolicy(attempts=1)

START = date(2020, 1, 6)  # a Monday


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture
def scratch_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"flowdesk_fredbf_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


class Limiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self, provider: str) -> None:
        self.acquired += 1


class FakeFred:
    """A series with a first print per day from `first`, published `lag` days after the period.

    `pre_vintage` earlier periods exist only in the latest values. `gap_days` are "." in the latest
    values and in no vintage. `hide` removes a real value from the first prints only (the silent
    shortfall the backfill must catch). Enforces the 2000-vintage cap like the real API.
    """

    def __init__(
        self,
        days: int,
        *,
        pre_vintage: int = 0,
        gap_days: tuple[int, ...] = (),
        hide: tuple[int, ...] = (),
        lag: int = 1,
    ) -> None:
        self.lag = lag
        self.first_print = [START + timedelta(days=i) for i in range(days) if i not in gap_days]
        self.gaps = [START + timedelta(days=i) for i in gap_days]
        self.pre = [START - timedelta(days=i + 1) for i in range(pre_vintage)]
        self.hidden = {START + timedelta(days=i) for i in hide}
        self.max_window = 0

    def pub(self, day: date) -> date:
        return day + timedelta(days=self.lag)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        q = request.url.params
        path = request.url.path
        vintages = sorted({self.pub(d) for d in self.first_print if d not in self.hidden})
        if path.endswith("/series/vintagedates"):
            return httpx2.Response(
                200,
                json={"count": len(vintages), "vintage_dates": [v.isoformat() for v in vintages]},
            )
        if q["output_type"] == "1":
            every = sorted(self.pre + self.first_print + self.gaps)
            rows = [
                {
                    "realtime_start": "2026-11-12",
                    "realtime_end": "2026-11-12",
                    "date": d.isoformat(),
                    "value": "." if d in self.gaps else "2.5",
                }
                for d in every
            ]
            return httpx2.Response(200, json={"count": len(rows), "observations": rows})
        start, end = date.fromisoformat(q["realtime_start"]), date.fromisoformat(q["realtime_end"])
        in_window = [v for v in vintages if start <= v <= end]
        self.max_window = max(self.max_window, len(in_window))
        if len(in_window) > 2000:
            return httpx2.Response(
                400,
                json={
                    "error_code": 400,
                    "error_message": "This exceeds the maximum number "
                    "of vintage dates allowed for this file type (2000).",
                },
            )
        rows = [
            {
                "realtime_start": self.pub(d).isoformat(),
                "realtime_end": "9999-12-31",
                "date": d.isoformat(),
                "value": "1.5",
            }
            for d in self.first_print
            if d not in self.hidden and start <= self.pub(d) <= end
        ]
        return httpx2.Response(200, json={"count": len(rows), "observations": rows})


def series(first_covered: date = START) -> FredSeries:
    return FredSeries("TESTSER", first_covered + timedelta(days=1), first_covered)


async def _run(
    engine: AsyncEngine,
    fred: FakeFred,
    *,
    item: FredSeries | None = None,
    limiter: Limiter | None = None,
) -> Any:
    item = item or series()
    lim = limiter or Limiter()

    async def pace() -> None:
        await lim.acquire(PROVIDER)

    return await run_feed(
        engine,
        PROVIDER,
        item.series_id,
        backfill_body(
            item,
            api_key=KEY,
            user_agent="FlowDesk-test",
            pace=pace,
            transport=httpx2.MockTransport(fred),
            engine=engine,
        ),
        limiter=lim,
        retry=NO_RETRY,
        secrets=[KEY],
    )


async def _all(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


def test_the_backfill_stores_first_prints_with_end_of_day_new_york_availability(
    scratch_url: str,
) -> None:
    async def go() -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        try:
            result = await _run(engine, FakeFred(10, pre_vintage=4, gap_days=(3,)))
            rows = await _all(engine, "SELECT * FROM macro_metrics ORDER BY effective_at")
            runs = await _all(engine, "SELECT * FROM ingestion_runs")
            return result, rows, runs
        finally:
            await engine.dispose()

    result, rows, runs = asyncio.run(go())
    assert (result.rows_read, result.rows_written) == (9, 9)  # 10 days, one is a gap with no trace
    assert len(rows) == 9
    first = rows[0]
    assert (first["metric"], first["revision"], first["source"]) == ("TESTSER", 0, "fred")
    assert first["effective_at"] == START
    assert first["value"] == Decimal("1.5")
    # Published START+1 (Tue 2020-01-07, EST): available at 00:00 on the 8th = 05:00 UTC.
    assert first["available_at"] == datetime(2020, 1, 8, 5, 0, tzinfo=UTC)
    # The pre-vintage periods and the never-published gap are NOT in the table.
    stored = {r["effective_at"] for r in rows}
    assert START - timedelta(days=1) not in stored
    assert START + timedelta(days=3) not in stored
    assert [r["status"] for r in runs] == ["succeeded"]
    assert {r["ingestion_run_id"] for r in rows} == {runs[0]["id"]}


def test_a_rerun_writes_nothing(scratch_url: str) -> None:
    async def go() -> tuple[int, int, int]:
        engine = create_async_engine(scratch_url)
        try:
            fred = FakeFred(10)
            first = await _run(engine, fred)
            second = await _run(engine, fred)
            count = (await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
            return first.rows_written, second.rows_written, count
        finally:
            await engine.dispose()

    first, second, count = asyncio.run(go())
    assert (first, second, count) == (10, 0, 10)


def test_a_series_past_the_cap_is_stored_complete_through_windows(scratch_url: str) -> None:
    async def go() -> tuple[int, int]:
        engine = create_async_engine(scratch_url)
        try:
            fred = FakeFred(4100)
            result = await _run(engine, fred)
            return result.rows_written, fred.max_window
        finally:
            await engine.dispose()

    written, widest = asyncio.run(go())
    assert written == 4100
    assert widest <= 1900


def test_a_missing_first_print_refuses_and_writes_nothing(scratch_url: str) -> None:
    async def go() -> tuple[str | None, int, str]:
        engine = create_async_engine(scratch_url)
        try:
            with pytest.raises(SchemaMismatch):
                await _run(engine, FakeFred(10, hide=(4,)))
            runs = await _all(engine, "SELECT status, error FROM ingestion_runs")
            n = (await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
            return runs[0]["error"], n, runs[0]["status"]
        finally:
            await engine.dispose()

    error, stored, status = asyncio.run(go())
    assert stored == 0  # a partial history must never look complete
    assert status == "failed"
    assert error is not None and "SchemaMismatch" in error


def test_a_first_print_that_disagrees_with_the_stored_one_refuses(scratch_url: str) -> None:
    async def go() -> int:
        engine = create_async_engine(scratch_url)
        try:
            await _run(engine, FakeFred(5))
            changed = FakeFred(5, lag=2)  # same periods, published a day later
            with pytest.raises(SchemaMismatch):
                await _run(engine, changed)
            return int((await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"])
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == 5  # nothing added, nothing changed


def test_a_gap_in_the_first_prints_is_stored_as_a_null_not_a_zero(scratch_url: str) -> None:
    async def go() -> list[dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        try:
            await insert_first_prints(
                engine,
                "TESTSER",
                [FirstPrint(START, None, datetime(2020, 1, 8, 5, tzinfo=UTC))],
                ingestion_run_id=None,
            )
            return await _all(engine, "SELECT value FROM macro_metrics")
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == [{"value": None}]


def test_backfill_all_continues_past_a_failed_series_then_reports_it(scratch_url: str) -> None:
    good, bad = FredSeries("GOODSER", START, START), FredSeries("BADSER", START, START)
    fred = FakeFred(5)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["series_id"] == "BADSER":
            body = {"error_code": 400, "error_message": "Bad Request.  The series does not exist."}
            return httpx2.Response(400, json=body)
        return fred(request)

    async def go() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        engine = create_async_engine(scratch_url)
        try:
            with pytest.raises(BackfillIncomplete) as info:
                await backfill_all(
                    engine,
                    [bad, good],
                    api_key=KEY,
                    user_agent="FlowDesk-test",
                    limiter=Limiter(),
                    base_url="https://example.invalid/fred",
                    transport=httpx2.MockTransport(handler),
                    retry=NO_RETRY,
                )
            progress = await _all(engine, "SELECT * FROM backfill_runs")
            runs = await _all(engine, "SELECT feed, status FROM ingestion_runs ORDER BY id")
            return progress, runs, str(info.value)
        finally:
            await engine.dispose()

    progress, runs, message = asyncio.run(go())
    assert "BADSER" in message and "GOODSER" not in message
    assert runs == [
        {"feed": "BADSER", "status": "failed"},
        {"feed": "GOODSER", "status": "succeeded"},
    ]
    assert len(progress) == 1
    assert (progress[0]["total_units"], progress[0]["completed_units"]) == (2, 1)
    assert progress[0]["status"] == "failed"
    assert progress[0]["current_unit"] is None


def test_a_clean_backfill_all_marks_progress_succeeded(scratch_url: str) -> None:
    items = [FredSeries("AAA", START, START), FredSeries("BBB", START, START)]

    async def go() -> list[dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        try:
            await backfill_all(
                engine,
                items,
                api_key=KEY,
                user_agent="FlowDesk-test",
                limiter=Limiter(),
                base_url="https://example.invalid/fred",
                transport=httpx2.MockTransport(FakeFred(3)),
                retry=NO_RETRY,
            )
            return await _all(engine, "SELECT * FROM backfill_runs")
        finally:
            await engine.dispose()

    progress = asyncio.run(go())
    assert (progress[0]["total_units"], progress[0]["completed_units"]) == (2, 2)
    assert progress[0]["status"] == "succeeded"

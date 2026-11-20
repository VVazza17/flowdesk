"""M2 slice 6 step 3: the yfinance client (app.ingest.yfinance_client) and the stored-error rule.

The download is injected, so no test calls Yahoo. The shape of the real call is pinned against
yfinance 1.8.0 (the private `_download_impl` it relies on). The two-sink test runs the client
inside `run_feed` on a real database and reads back what was stored: nothing the library said
(a URL, Yahoo's words) may be in `ingestion_runs.error` or `providers_health.last_error`.
"""

import asyncio
import inspect
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import curl_cffi
import pandas as pd  # type: ignore[import-untyped]
import pytest
import requests
import yfinance.exceptions as yfe  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.ingest.bars import DAILY, INTRADAY_15M
from app.ingest.errors import Permanent, RateLimited, Transient
from app.ingest.runner import FeedContext, FeedResult, RetryPolicy, run_feed
from app.ingest.yfinance_client import (
    INTRADAY_START_LIMIT_DAYS,
    YFinanceClient,
    intraday_floor,
    map_exception,
    yf_download,
)
from tests.recorded import recorded
from tests.yfixtures import load

TODAY = date(2026, 11, 18)
LEAK = "https://query2.finance.yahoo.com/v8/finance/chart/XLK?crumb=Zx9secret said: go away"


class Recorder:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raises = raises

    def __call__(self, tickers: Any, **kw: Any) -> tuple[Any, dict[str, str]]:
        self.calls.append({"tickers": list(tickers), **kw})
        if self.raises:
            raise self.raises
        frame, errors, _ = load("daily_3mo_universe_plus_dead")
        return frame, errors


def client(download: Any) -> YFinanceClient:
    return YFinanceClient(max_batch=settings.RATE_BUDGETS["yfinance"].max_batch, download=download)


def fetch(c: YFinanceClient, *args: Any, **kw: Any) -> Any:
    return asyncio.run(c.fetch(*args, today=TODAY, **kw))


# --- the rate budget (Kyle, 2026-11-18) -----------------------------------------------------------


def test_the_yfinance_budget_is_one_24_ticker_batch_a_minute() -> None:
    b = settings.RATE_BUDGETS["yfinance"]
    assert (b.min_interval_seconds, b.max_batch) == (60.0, 24)


# --- the request ----------------------------------------------------------------------------------


def test_daily_asks_for_actions_and_intraday_does_not() -> None:
    rec = Recorder()
    fetch(client(rec), ["XLK", "SPY"], DAILY, period="2y")
    fetch(client(rec), ["XLK"], INTRADAY_15M, start=TODAY - timedelta(days=5))
    assert rec.calls[0] == {
        "tickers": ["XLK", "SPY"],
        "interval": DAILY,
        "start": None,
        "period": "2y",
        "actions": True,
    }
    assert rec.calls[1]["actions"] is False and rec.calls[1]["interval"] == INTRADAY_15M


@pytest.mark.parametrize(
    ("tickers", "kw", "match"),
    [
        ([f"T{i}" for i in range(25)], {"period": "5d"}, "max_batch"),
        ([], {"period": "5d"}, "max_batch"),
        (["XLK", "XLK"], {"period": "5d"}, "twice"),
        (["XLK"], {}, "exactly one"),
        (["XLK"], {"period": "5d", "start": TODAY}, "exactly one"),
    ],
)
def test_a_malformed_request_is_refused_before_any_call(
    tickers: list[str], kw: dict[str, Any], match: str
) -> None:
    rec = Recorder()
    with pytest.raises(Permanent, match=match):
        fetch(client(rec), tickers, DAILY, **kw)
    assert rec.calls == []


def test_a_15m_start_beyond_58_days_is_refused_and_58_is_not() -> None:
    rec = Recorder()
    with pytest.raises(Permanent, match="58-day"):
        fetch(client(rec), ["XLK"], INTRADAY_15M, start=TODAY - timedelta(days=59))
    fetch(client(rec), ["XLK"], INTRADAY_15M, start=TODAY - timedelta(days=58))
    assert len(rec.calls) == 1
    # Daily has no such limit (the backfill reaches two years).
    fetch(client(rec), ["XLK"], DAILY, start=TODAY - timedelta(days=730))


NY = ZoneInfo("America/New_York")


def _span(start: date, now: datetime) -> timedelta:
    # yfinance sends a start DATE as New York midnight (MEASURED 2026-11-19, F-6a:
    # startTime=1789876800 is 2026-09-20 00:00 EDT). In UTC: the same tzinfo on both sides
    # would subtract wall clocks.
    return now.astimezone(UTC) - datetime.combine(start, time(0), tzinfo=NY).astimezone(UTC)


def test_the_15m_window_is_inside_yahoos_60_x_24_h_at_the_worst_case() -> None:
    # F-6a (Kyle, 2026-11-20): the worst case is 23:59 New York with the window spanning the
    # November DST change: 58 d + 23 h 59 min + 1 h, about 59.04 d, against the MEASURED
    # 60 x 24 h limit.
    now = datetime(2026, 11, 18, 23, 59, tzinfo=NY)
    start = intraday_floor(now.date())
    assert start == date(2026, 9, 21) and INTRADAY_START_LIMIT_DAYS == 58
    assert start < date(2026, 11, 1) < now.date()  # the window spans the change (EDT -> EST)
    assert _span(start, now) <= timedelta(days=60)
    assert _span(start, now) == timedelta(days=59, hours=24, minutes=59) - timedelta(hours=24)
    # The refusal that was MEASURED: 59 days, 23:08 New York on 2026-11-18, spanned 60.006 d.
    measured = _span(date(2026, 9, 20), datetime(2026, 11, 18, 23, 8, 54, tzinfo=NY))
    assert measured > timedelta(days=60)
    assert round(measured / timedelta(days=1), 3) == 60.006
    # The guard and the floor are the same date: the earliest start the client lets through.
    rec = Recorder()
    c = YFinanceClient(max_batch=24, download=rec)
    asyncio.run(c.fetch(["XLK"], INTRADAY_15M, today=now.date(), start=start))
    with pytest.raises(Permanent):
        asyncio.run(
            c.fetch(["XLK"], INTRADAY_15M, today=now.date(), start=start - timedelta(days=1))
        )


def test_the_real_call_shape_against_yfinance_1_8_0() -> None:
    # The client relies on a PRIVATE function; this pins the parameters it passes against the
    # installed version, so a yfinance bump that changes them fails here, not in production.
    from yfinance.multi import _download_impl, _DownloadCtx  # type: ignore[import-untyped]

    params = inspect.signature(_download_impl).parameters
    for name in ("ctx", "tickers", "start", "period", "interval", "actions", "auto_adjust",
                 "prepost", "group_by", "threads", "progress"):  # fmt: skip
        assert name in params, name
    assert {"dfs", "errors"} <= set(_DownloadCtx.__slots__)
    source = inspect.getsource(yf_download)
    assert "auto_adjust=False" in source and "prepost=False" in source  # ADR-0002: always explicit
    # A threaded cold cache raised `database is locked` (MEASURED 2026-11-18, 1 in 10).
    assert "threads=False" in source


# --- the exceptions: mapped, with fixed text ------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "mapped"),
    [
        (yfe.YFRateLimitError(), RateLimited),
        (curl_cffi.CurlError(LEAK), Transient),
        (requests.exceptions.ConnectionError(LEAK), Transient),
        (TimeoutError(LEAK), Transient),
        (ConnectionResetError(LEAK), Transient),
        (yfe.YFInvalidPeriodError("XLK", "9y", ["1d", "5d"]), Permanent),
        (yfe.YFException(LEAK), Permanent),
    ],
    ids=["rate_limit", "curl", "requests", "timeout", "reset", "invalid_period", "yf_other"],
)
def test_library_exceptions_become_ours_with_fixed_text(raised: Exception, mapped: type) -> None:
    with pytest.raises(mapped) as caught:
        fetch(client(Recorder(raises=raised)), ["XLK"], DAILY, period="5d")
    assert "yahoo" not in str(caught.value).lower() and "secret" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_an_exception_the_client_does_not_know_passes_through_unchanged() -> None:
    boom = RuntimeError(LEAK)
    assert map_exception(boom) is boom
    with pytest.raises(RuntimeError):
        fetch(client(Recorder(raises=boom)), ["XLK"], DAILY, period="5d")


def test_an_empty_answer_is_an_empty_frame_never_none() -> None:
    batch = fetch(client(lambda tickers, **kw: (None, {})), ["XLK"], DAILY, period="5d")
    assert isinstance(batch.frame, pd.DataFrame) and batch.frame.empty


# --- the two sinks: what is stored, read back from the database -----------------------------------


def _two_sinks(scratch_url: str, raised: Exception) -> tuple[str, str]:
    async def go() -> tuple[str, str]:
        engine = create_async_engine(scratch_url)
        c = client(Recorder(raises=raised))

        async def body(ctx: FeedContext) -> FeedResult:
            await c.fetch(["XLK"], DAILY, today=TODAY, period="5d")
            return FeedResult(rows_read=0, rows_written=0)

        class Limiter:
            async def acquire(self, provider: str) -> None:
                return None

        async def no_sleep(seconds: float) -> None:
            return None

        try:
            async with recorded(engine):
                with pytest.raises(Exception):  # noqa: B017, PT011
                    await run_feed(
                        engine,
                        "yfinance",
                        "daily",
                        body,
                        limiter=Limiter(),
                        retry=RetryPolicy(attempts=1, initial_seconds=0.0, max_seconds=0.0),
                        sleep=no_sleep,
                    )
            async with engine.connect() as conn:
                run_error = (await conn.execute(text("SELECT error FROM ingestion_runs"))).scalar()
                last = (
                    await conn.execute(text("SELECT last_error FROM providers_health"))
                ).scalar()
            return str(run_error), str(last)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_a_mapped_failure_stores_our_text_and_nothing_of_the_librarys(scratch_url: str) -> None:
    stored_run, stored_health = _two_sinks(scratch_url, curl_cffi.CurlError(LEAK))
    assert stored_health == "transient:Transient"
    assert stored_run == "transient:Transient: the request to yfinance failed"
    for sink in (stored_run, stored_health):
        assert "yahoo" not in sink.lower() and "secret" not in sink and "https" not in sink


def test_an_unmapped_library_failure_stores_kind_and_class_only(scratch_url: str) -> None:
    stored_run, stored_health = _two_sinks(scratch_url, RuntimeError(LEAK))
    assert stored_run == "unexpected:RuntimeError"
    assert stored_health == "unexpected:RuntimeError"

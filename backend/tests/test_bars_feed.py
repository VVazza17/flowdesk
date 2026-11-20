"""The 15m backfill and the two settled polls (M2 slice 6, step 6), against recorded batches.

Fixtures: `daily_3mo_universe_plus_dead` (to 2026-11-18) and `m15_5d_three_plus_dead` (QQQ, SPY,
XLK, 2026-11-12 to 11-18), served by a fake download that honours `start` as Yahoo does and records
every call. "Now" is Thu 2026-11-19 09:05 New York: 15m bars have settled through 11-18 (D+1 09:00),
daily bars through 11-17 (D+2 09:00); the 15m look-back floor is 2026-09-21.

Rule 9 (CLAUDE.md): every keyed start is tested against a second symbol whose rows would WIN a
lost filter: a newer last bar must not make another symbol look backfilled, or hide its gap.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import pytest
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.bars_backfill import Report, execute, print_report
from app.celery_app import create_celery
from app.ingest.bars import (
    DAILY,
    INTRADAY_15M,
    NEW_YORK,
    DeadSymbols,
    NotBackfilled,
    SymbolFailure,
    SymbolsFailed,
    run_failure,
)
from app.ingest.errors import Transient
from app.ingest.runner import RetryPolicy
from app.services.reference_seed import seed_reference
from app.services.sessions import sync_sessions
from app.tasks.bars_ingest import poll
from app.tasks.names import (
    BARS_15M_POLL,
    BARS_BACKFILL,
    BARS_DAILY_POLL,
    INGEST_QUEUE,
    INGEST_WORKER_TASKS,
)
from tests.yfixtures import load, with_today_row

NOW = datetime(2026, 11, 19, 9, 5, tzinfo=NEW_YORK)
TODAY = NOW.date()  # the daily fixture gets a row for it (Download)
FLOOR = date(2026, 9, 22)  # NOW's date - 58 calendar days (F-6a, Kyle 2026-11-20)
THREE = ["QQQ", "SPY", "XLK"]
NO_RETRY = RetryPolicy(attempts=1, initial_seconds=0.0, max_seconds=0.0)


class Limiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self, provider: str) -> None:
        self.acquired += 1


class Download:
    """Serves the recorded daily or 15m batch for the interval asked, cut to the tickers and to
    `start` (inclusive, as Yahoo does), after an optional tamper. Records every call. The daily
    batch gets a row for `today` (NOW's date unless given; None for none) for every ticker but
    those `without_today` (Kyle, 2026-11-20: no bar is accepted without today's row)."""

    def __init__(
        self,
        daily_tamper: Any = None,
        m15_tamper: Any = None,
        *,
        today: date | None = TODAY,
        without_today: Sequence[str] = (),
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._tamper = {DAILY: daily_tamper, INTRADAY_15M: m15_tamper}
        self._today = today
        self._without_today = tuple(without_today)

    def __call__(self, tickers: Sequence[str], **kw: Any) -> tuple[Any, dict[str, str]]:
        interval = kw["interval"]
        self.calls.append({"tickers": list(tickers), **kw})
        name = "daily_3mo_universe_plus_dead" if interval == DAILY else "m15_5d_three_plus_dead"
        frame, errors, _ = load(name)
        frame = frame[[c for c in frame.columns if c[0] in tickers]].copy()
        if kw.get("start") is not None:
            days = [(i.tz_convert(NEW_YORK) if i.tzinfo else i).date() for i in frame.index]
            frame = frame[[d >= kw["start"] for d in days]]
        frame = frame.dropna(how="all")
        if self._tamper[interval] is not None:
            self._tamper[interval](frame)
        if interval == DAILY and self._today is not None:
            frame = with_today_row(frame, self._today, skip=self._without_today)
        return frame, {t: e for t, e in errors.items() if t in tickers}


def run(url: str, fn: Any) -> Any:
    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def rows(url: str, sql: str, **params: Any) -> list[Any]:
    async def fn(engine: AsyncEngine) -> list[Any]:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []

    return run(url, fn)  # type: ignore[no-any-return]


@pytest.fixture
def db(scratch_url: str) -> str:
    async def prepare(engine: AsyncEngine) -> None:
        await seed_reference(engine)
        await sync_sessions(engine, ["XNYS"], date(2024, 1, 1), date(2027, 1, 31))

    run(scratch_url, prepare)
    return scratch_url


def backfill(
    url: str, interval: str, tickers: Sequence[str], download: Download, now: datetime = NOW
) -> tuple[Report, BaseException | None, Limiter]:
    report, limiter = Report(), Limiter()

    async def fn(engine: AsyncEngine) -> BaseException | None:
        try:
            await execute(
                engine,
                report,
                limiter=limiter,
                tickers=tickers,
                download=download,
                clock=lambda: now,
                retry=NO_RETRY,
                interval=interval,
            )
        except Exception as exc:  # the run's failure: returned so the test can read the report
            return exc
        return None

    return report, run(url, fn), limiter


def do_poll(
    url: str, interval: str, download: Download, now: datetime = NOW
) -> tuple[Report, BaseException | None, Limiter]:
    """The poll task's body (every mapped symbol, as Beat would run it). The seed maps 24 symbols;
    only those the fixture serves are alive, so the tests pass `tickers` through a seeded subset."""
    report, limiter = Report(), Limiter()

    async def fn(engine: AsyncEngine) -> BaseException | None:
        try:
            await poll(
                engine,
                interval,
                report,
                limiter=limiter,
                download=download,
                clock=lambda: now,
                retry=NO_RETRY,
            )
        except Exception as exc:
            return exc
        return None

    return report, run(url, fn), limiter


def only_map(url: str, tickers: Sequence[str]) -> None:
    """Leave an active yfinance mapping on `tickers` only (the poll runs every mapped symbol)."""
    rows(
        url,
        "UPDATE provider_mappings SET retired_at = now() "
        "WHERE provider = 'yfinance' AND retired_at IS NULL AND symbol_id NOT IN "
        "(SELECT id FROM symbols WHERE ticker = ANY(:t))",
        t=list(tickers),
    )


def intraday(url: str, ticker: str) -> list[Any]:
    return rows(
        url,
        "SELECT b.* FROM bars_intraday b JOIN symbols s ON s.id = b.symbol_id "
        "WHERE s.ticker = :t ORDER BY b.effective_at",
        t=ticker,
    )


def et(y: int, m: int, d: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=NEW_YORK)


def split_on(ticker: str, day: str, ratio: float) -> Any:
    """A split on `day`, as Yahoo would show it: the split row, and every earlier volume on the
    post-split basis (raw x ratio, so a whole multiple)."""

    def tamper(frame: pd.DataFrame) -> None:
        ix = [i for i in frame.index if str(i.date()) == day]
        frame.loc[ix, (ticker, "Stock Splits")] = ratio
        earlier = [i for i in frame.index if str(i.date()) < day]
        frame.loc[earlier, (ticker, "Volume")] = frame.loc[earlier, (ticker, "Volume")] * ratio

    return tamper


# --- the 15m backfill ----------------------------------------------------------------------------


# --- today's row (Kyle, 2026-11-20) ------------------------------------------------------------


def stored_daily(url: str, ticker: str) -> int:
    return rows(  # type: ignore[no-any-return]
        url,
        "SELECT count(*) FROM bars_daily b JOIN symbols s ON s.id = b.symbol_id "
        "WHERE s.ticker = :t",
        t=ticker,
    )[0][0]


def splits_of(url: str) -> list[tuple[str, date]]:
    return [
        (r.ticker, r.d)
        for r in rows(
            url,
            "SELECT s.ticker, sp.effective_at AS d "
            "FROM splits sp JOIN symbols s ON s.id = sp.symbol_id ORDER BY 1, 2",
        )
    ]


def two_splits(frame: pd.DataFrame) -> None:
    split_on("SPY", "2026-11-13", 2.0)(frame)
    split_on("XLK", "2026-11-13", 2.0)(frame)


def test_a_symbol_without_todays_row_is_held_and_one_with_it_is_written(db: str) -> None:
    # Rule 9: SPY and QQQ have today's row and XLK has not, in the same batch. XLK's absence must
    # not hold the others, and their presence must not admit XLK. Both SPY and XLK carry a split:
    # SPY's is written, XLK's is not (nothing of a held symbol is written).
    report, error, _ = backfill(db, DAILY, THREE, Download(two_splits, without_today=["XLK"]))
    assert error is None and report.failed == []
    assert stored_daily(db, "XLK") == 0
    spy = stored_daily(db, "SPY")
    assert spy > 0 and stored_daily(db, "QQQ") == spy
    assert report.held == {"XLK": spy}  # the settled bars it would have had
    assert splits_of(db) == [("SPY", date(2026, 11, 13))]
    assert [r.status for r in rows(db, "SELECT status FROM ingestion_runs")] == ["succeeded"]


def test_the_15m_run_holds_a_symbol_whose_split_check_has_no_todays_row(db: str) -> None:
    # The 15m run's daily check is its daily actions=True frame. XLK lacks today's row there: no
    # XLK 15m bar and no XLK split is written; SPY's split (same day) is, and SPY and QQQ get bars.
    def adjusted_volumes(frame: pd.DataFrame) -> None:
        # Yahoo's pre-split 15m volume is on the post-split basis (raw x 2), as in the test below.
        ix = [i for i in frame.index if i < pd.Timestamp(et(2026, 11, 13))]
        for t in ("SPY", "XLK"):
            frame.loc[ix, (t, "Volume")] = frame.loc[ix, (t, "Volume")] * 2

    dl = Download(two_splits, adjusted_volumes, without_today=["XLK"])
    report, error, _ = backfill(db, INTRADAY_15M, THREE, dl)
    assert error is None and report.failed == []
    assert intraday(db, "XLK") == []
    assert len(intraday(db, "SPY")) == 130 and len(intraday(db, "QQQ")) == 130
    assert report.held == {"XLK": 130}  # 15m bars only: the check's daily bars are not counted
    assert splits_of(db) == [("SPY", date(2026, 11, 13))]


def test_before_the_open_a_backfill_holds_every_symbol_succeeds_and_says_to_rerun(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    report, error, _ = backfill(db, DAILY, THREE, Download(today=None), now=et(2026, 11, 19, 8))
    assert error is None and report.failed == []
    assert set(report.held) == set(THREE) and report.rows_written == 0
    assert all(stored_daily(db, t) == 0 for t in THREE)
    assert [r.status for r in rows(db, "SELECT status FROM ingestion_runs")] == ["succeeded"]
    print_report(report, DAILY)
    err = capsys.readouterr().err
    assert "HELD" in err and "Re-run after 09:35 ET" in err
    assert all(t in err for t in THREE)


def test_on_a_day_with_no_session_no_todays_row_is_needed(db: str) -> None:
    # Saturday 2026-11-21: no session, so no row for today can exist; the rule does not apply.
    report, error, _ = backfill(db, DAILY, THREE, Download(today=None), now=et(2026, 11, 21, 10, 5))
    assert error is None and report.held == {}
    assert all(stored_daily(db, t) > 0 for t in THREE)


def test_the_15m_backfill_checks_splits_first_then_stores_settled_bars_as_backfill(db: str) -> None:
    dl = Download()
    report, error, limiter = backfill(db, INTRADAY_15M, THREE, dl)
    assert error is None and report.failed == []
    # The split check first, its own daily actions batch over the same window; then the 15m batch.
    assert [(c["interval"], c["start"], c["period"]) for c in dl.calls] == [
        (DAILY, FLOOR, None),
        (INTRADAY_15M, FLOOR, None),
    ]
    assert dl.calls[0]["actions"] is True
    assert limiter.acquired == 2  # each request its own rate-budget slot
    # Five sessions x 26 bars (11-12 to 11-18), every one settled at NOW.
    for ticker in THREE:
        stored = intraday(db, ticker)
        assert len(stored) == 130
        assert {r.feed for r in stored} == {"backfill"}
        assert {r.interval for r in stored} == {"15m"}
    # available_at = the 15m settle point, 09:00 New York on the next session (the recorded
    # exception: fetched later, stamped at S).
    last = intraday(db, "SPY")[-1]
    assert last.effective_at == et(2026, 11, 18, 15, 45)
    assert last.available_at == et(2026, 11, 19, 9, 0)
    first = intraday(db, "SPY")[0]
    assert first.effective_at == et(2026, 11, 12, 9, 30)
    assert first.available_at == et(2026, 11, 13, 9, 0)
    assert report.bars == 390 and report.gaps == 0
    # A re-run writes 0 rows.
    again, error, _ = backfill(db, INTRADAY_15M, THREE, Download())
    assert error is None and again.rows_written == 0


REFUSED = (
    "15m data not available for startTime=1789876800 and endTime=1795061334. "
    "The requested range must be within the last 60 days."
)


class RefusedFifteen(Download):
    """F-6a as MEASURED 2026-11-19: Yahoo refuses the whole 15m request. Every ticker has a soft
    error (library text, no exception class), every group is all-NaN, and the index is
    timezone-naive. The daily split check is served normally."""

    def __call__(self, tickers: Sequence[str], **kw: Any) -> tuple[Any, dict[str, str]]:
        if kw["interval"] == DAILY:
            return super().__call__(tickers, **kw)
        self.calls.append({"tickers": list(tickers), **kw})
        frame, _, _ = load("m15_5d_three_plus_dead")
        frame = frame[[c for c in frame.columns if c[0] in tickers]].iloc[:3].copy()
        frame.loc[:, :] = float("nan")
        frame.index = pd.DatetimeIndex(frame.index.tz_localize(None))
        return frame, {t: f"${t}: {REFUSED}" for t in tickers}


def test_a_batch_refused_for_every_ticker_is_named_per_symbol_not_as_a_naive_index(
    db: str,
) -> None:
    # F-6a (Kyle, 2026-11-20): classified before the timezone check, named per symbol, and what is
    # stored is <kind>:<Class> and FlowDesk's own text only. Three symbols, so a run that named one
    # (or none, as before) fails.
    report, error, _ = backfill(db, INTRADAY_15M, THREE, RefusedFifteen())
    assert isinstance(error, DeadSymbols), repr(error)
    assert report.failed == THREE
    assert all(intraday(db, t) == [] for t in THREE)
    (run_row,) = rows(db, "SELECT status, error FROM ingestion_runs ORDER BY id")
    assert run_row.status == "failed"
    assert run_row.error == "empty_result:DeadSymbols: no rows in this batch for: QQQ, SPY, XLK"
    health = [r.last_error for r in rows(db, "SELECT last_error FROM providers_health")]
    assert "empty_result:DeadSymbols" in health
    for stored in [run_row.error, *health]:
        assert stored is None or not any(
            w in stored for w in ("startTime", "60 days", "timezone-naive", "$")
        ), stored


def test_no_15m_bar_is_accepted_before_a_split_in_its_window_is_known_in_the_same_run(
    db: str,
) -> None:
    # A 2:1 split on Mon 11-16 that no earlier run stored. The 15m frame carries no split column:
    # only this run's own daily check can know it, so bars before 11-16 are rebuilt x2 (as traded)
    # and bars from 11-16 on are as given. QQQ (no split) is untouched.
    def adjusted_volume(frame: pd.DataFrame) -> None:
        # Yahoo's pre-split volume is on the post-split basis: raw x 2, so always even.
        ix = [i for i in frame.index if i < pd.Timestamp(et(2026, 11, 16))]
        frame.loc[ix, ("XLK", "Volume")] = frame.loc[ix, ("XLK", "Volume")] * 2

    dl = Download(daily_tamper=split_on("XLK", "2026-11-16", 2.0), m15_tamper=adjusted_volume)
    report, error, _ = backfill(db, INTRADAY_15M, THREE, dl)
    assert error is None, error
    (split,) = rows(
        db,
        "SELECT p.* FROM splits p JOIN symbols s ON s.id = p.symbol_id WHERE s.ticker = 'XLK'",
    )
    assert split.effective_at == date(2026, 11, 16)
    frame, _, _ = load("m15_5d_three_plus_dead")
    given = {i.to_pydatetime(): Decimal(str(v)) for i, v in frame[("XLK", "Close")].items()}
    by_at = {r.effective_at: r.c_raw for r in intraday(db, "XLK")}
    before, after = et(2026, 11, 13, 15, 45), et(2026, 11, 16, 9, 30)
    assert by_at[before] == given[before] * 2
    assert by_at[after] == given[after]
    qqq = load("m15_5d_three_plus_dead")[0][("QQQ", "Close")]
    assert intraday(db, "QQQ")[0].c_raw == Decimal(str(qqq.iloc[0]))
    assert report.splits == 1


def test_a_symbol_whose_split_check_fails_gets_no_15m_bars_and_is_named(db: str) -> None:
    # The first run stores a 3:1 split; the second sees 2:1 for the same day (a disagreement),
    # so XLK is held back from the 15m batch, named, and nothing of it is written. The others
    # are still fetched.
    def times3(frame: pd.DataFrame) -> None:
        ix = [i for i in frame.index if i < pd.Timestamp(et(2026, 11, 16))]
        frame.loc[ix, ("XLK", "Volume")] = frame.loc[ix, ("XLK", "Volume")] * 3

    first = Download(daily_tamper=split_on("XLK", "2026-11-16", 3.0), m15_tamper=times3)
    _, error, _ = backfill(db, INTRADAY_15M, ["XLK"], first)
    assert error is None, error
    before = len(intraday(db, "XLK"))
    dl = Download(daily_tamper=split_on("XLK", "2026-11-16", 2.0))
    report, error, _ = backfill(db, INTRADAY_15M, THREE, dl)
    assert error is not None
    assert report.failed == ["XLK"]
    assert dl.calls[1]["interval"] == INTRADAY_15M
    assert dl.calls[1]["tickers"] == ["QQQ", "SPY"]
    assert len(intraday(db, "XLK")) == before
    assert len(intraday(db, "QQQ")) == 130 and len(intraday(db, "SPY")) == 130


# --- the daily poll ------------------------------------------------------------------------------


def test_the_daily_poll_starts_at_the_last_stored_session_inclusive_and_a_rerun_writes_0(
    db: str,
) -> None:
    only_map(db, THREE)
    backfill(db, DAILY, THREE, Download(), now=et(2026, 11, 18, 9, 5))  # settled through 11-16
    dl = Download()
    report, error, limiter = do_poll(db, DAILY, dl)
    assert error is None and report.failed == []
    (call,) = dl.calls
    assert (call["start"], call["period"], call["actions"]) == (date(2026, 11, 16), None, True)
    assert limiter.acquired == 1
    assert report.bars == 3  # 11-17 for each; 11-16 is re-fetched, compared and not rewritten
    (first,) = rows(
        db,
        "SELECT rows_written FROM ingestion_runs WHERE feed = 'bars_daily' "
        "ORDER BY id DESC LIMIT 1",
    )
    assert first.rows_written == report.rows_written >= 3  # this batch's rows, never attempts
    assert {
        r.feed
        for r in rows(
            db,
            "SELECT feed FROM bars_daily b JOIN trading_sessions t ON t.opens_at = b.effective_at "
            "AND t.calendar_code = 'XNYS' WHERE t.session_date = '2026-11-17'",
        )
    } == {"poll"}
    # Next morning, nothing new has settled (as on a holiday): a successful 0-row run.
    again, error, _ = do_poll(db, DAILY, Download())
    assert error is None and again.rows_written == 0
    (last,) = rows(
        db,
        "SELECT status, rows_written FROM ingestion_runs WHERE feed = 'bars_daily' "
        "ORDER BY id DESC LIMIT 1",
    )
    assert (last.status, last.rows_written) == ("succeeded", 0)


def test_the_inclusive_start_counts_a_late_volume_revision_and_keeps_the_stored_value(
    db: str,
) -> None:
    only_map(db, THREE)
    backfill(db, DAILY, THREE, Download())  # settled through 11-17

    def later_volume(frame: pd.DataFrame) -> None:
        ix = [i for i in frame.index if str(i.date()) == "2026-11-17"]
        frame.loc[ix, ("SPY", "Volume")] = frame.loc[ix, ("SPY", "Volume")] + 1000

    stored = rows(
        db,
        "SELECT b.v_raw FROM bars_daily b JOIN symbols s ON s.id = b.symbol_id "
        "JOIN trading_sessions t ON t.opens_at = b.effective_at AND t.calendar_code = 'XNYS' "
        "WHERE s.ticker = 'SPY' AND t.session_date = '2026-11-17'",
    )
    report, error, _ = do_poll(db, DAILY, Download(daily_tamper=later_volume))
    assert error is None
    assert report.volume_revisions == {"SPY": 1}  # counted, not a failure; QQQ and XLK are not
    assert report.rows_written == 0
    assert (
        rows(
            db,
            "SELECT b.v_raw FROM bars_daily b JOIN symbols s ON s.id = b.symbol_id "
            "JOIN trading_sessions t ON t.opens_at = b.effective_at AND t.calendar_code = 'XNYS' "
            "WHERE s.ticker = 'SPY' AND t.session_date = '2026-11-17'",
        )
        == stored
    )


def test_a_symbol_with_no_stored_bar_is_not_backfilled_even_beside_one_with_newer_bars(
    db: str,
) -> None:
    # Rule 9: QQQ's stored bars would WIN a lost symbol filter (newest, for every symbol).
    only_map(db, ["QQQ", "SPY"])
    backfill(db, DAILY, ["QQQ"], Download())
    dl = Download()
    report, error, _ = do_poll(db, DAILY, dl)
    assert error is not None
    assert report.failed == ["SPY"]
    assert dl.calls[0]["tickers"] == ["QQQ"]  # SPY is not fetched: the poll never goes deep
    (stored,) = rows(
        db,
        "SELECT error FROM ingestion_runs WHERE feed = 'bars_daily' ORDER BY id DESC LIMIT 1",
    )
    assert stored.error.startswith("permanent:NotBackfilled: ")
    assert stored.error.endswith(": SPY")


def test_daily_bars_do_not_count_as_15m_history(db: str) -> None:
    only_map(db, ["QQQ"])
    backfill(db, DAILY, ["QQQ"], Download())
    dl = Download()
    report, error, _ = do_poll(db, INTRADAY_15M, dl)
    assert error is not None and report.failed == ["QQQ"]
    assert dl.calls == []


# --- the 15m poll --------------------------------------------------------------------------------


def _copy_back(url: str, source: str, target: str, days: int) -> datetime:
    """Give `target` one 15m bar: `source`'s first, `days` earlier (a past poll's last bar)."""

    async def fn(engine: AsyncEngine) -> datetime:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TEMP TABLE one ON COMMIT DROP AS SELECT b.* FROM bars_intraday b "
                    "JOIN symbols s ON s.id = b.symbol_id WHERE s.ticker = :src "
                    "ORDER BY b.effective_at LIMIT 1"
                ),
                {"src": source},
            )
            await conn.execute(
                text(
                    "UPDATE one SET symbol_id = (SELECT id FROM symbols WHERE ticker = :dst), "
                    "effective_at = effective_at - make_interval(days => :n), "
                    "available_at = available_at - make_interval(days => :n), feed = 'poll'"
                ),
                {"dst": target, "n": days},
            )
            at = (await conn.execute(text("SELECT effective_at FROM one"))).scalar_one()
            await conn.execute(text("INSERT INTO bars_intraday SELECT * FROM one"))
            return at  # type: ignore[no-any-return]

    return run(url, fn)  # type: ignore[no-any-return]


def test_the_15m_poll_records_a_gap_beyond_the_look_back_for_that_symbol_only(db: str) -> None:
    # Rule 9: QQQ's recent last bar would WIN a lost symbol filter and hide XLK's gap.
    only_map(db, ["QQQ", "XLK"])
    backfill(db, INTRADAY_15M, ["QQQ"], Download())
    old = _copy_back(db, "QQQ", "XLK", 70)  # 2026-09-03, before the floor
    assert old.astimezone(NEW_YORK).date() == date(2026, 9, 3)
    dl = Download()
    report, error, _ = do_poll(db, INTRADAY_15M, dl)
    assert error is None, error
    (gap,) = rows(
        db,
        "SELECT g.*, s.ticker FROM bar_gaps g JOIN symbols s ON s.id = g.symbol_id",
    )
    assert (gap.ticker, gap.interval) == ("XLK", "15m")
    assert gap.missing_from == old + timedelta(minutes=15)
    assert gap.missing_to == et(2026, 9, 22, 9, 30)  # the open of the first session at the floor
    assert report.gaps == 1
    # One request for both, from the earliest start the look-back allows; the split check first.
    assert [(c["interval"], c["start"]) for c in dl.calls] == [
        (DAILY, FLOOR),
        (INTRADAY_15M, FLOOR),
    ]
    assert len(intraday(db, "XLK")) == 1 + 130
    assert {r.feed for r in intraday(db, "XLK")[1:]} == {"poll"}
    # The gap is recorded once: the next run starts from XLK's new last bar.
    again, error, _ = do_poll(db, INTRADAY_15M, Download())
    assert error is None and again.gaps == 0 and again.rows_written == 0


def test_the_15m_poll_starts_at_its_last_stored_session_and_counts_volume_revisions(
    db: str,
) -> None:
    only_map(db, THREE)
    backfill(db, INTRADAY_15M, THREE, Download(), now=et(2026, 11, 18, 9, 5))  # through 11-17

    def later_volume(frame: pd.DataFrame) -> None:
        at = pd.Timestamp(et(2026, 11, 17, 15, 45))
        frame.loc[at, ("XLK", "Volume")] = frame.loc[at, ("XLK", "Volume")] + 7

    dl = Download(m15_tamper=later_volume)
    report, error, _ = do_poll(db, INTRADAY_15M, dl)
    assert error is None
    assert [(c["interval"], c["start"]) for c in dl.calls] == [
        (DAILY, date(2026, 11, 17)),
        (INTRADAY_15M, date(2026, 11, 17)),
    ]
    assert report.bars == 3 * 26  # 11-18 only
    assert report.volume_revisions == {"XLK": 1}


def test_the_polls_are_recorded_task_executions(db: str) -> None:
    only_map(db, THREE)
    backfill(db, DAILY, THREE, Download())
    backfill(db, INTRADAY_15M, THREE, Download())
    do_poll(db, DAILY, Download())
    do_poll(db, INTRADAY_15M, Download())
    names = [r.task_name for r in rows(db, "SELECT task_name FROM task_runs ORDER BY id")]
    assert names[-2:] == [BARS_DAILY_POLL, BARS_15M_POLL]
    assert {
        r.task_run_id is not None
        for r in rows(
            db,
            "SELECT task_run_id FROM ingestion_runs WHERE feed IN ('bars_daily', 'bars_15m')",
        )
    } == {True}


def test_run_failure_names_not_backfilled_and_never_drops_an_unknown_failure() -> None:
    nb = run_failure([SymbolFailure("SPY", NotBackfilled("x"))])
    assert isinstance(nb, NotBackfilled) and str(nb).endswith(": SPY")
    # A failure type run_failure does not know is named and fails the run as broken data.
    other = run_failure(
        [SymbolFailure("ABC", Transient("x")), SymbolFailure("SPY", NotBackfilled("x"))]
    )
    assert isinstance(other, SymbolsFailed)
    assert "failed for: ABC" in str(other) and "SPY" in str(other)


# --- the schedule and health ---------------------------------------------------------------------


def make_app(monkeypatch: pytest.MonkeyPatch, enabled: str | None) -> Celery:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    if enabled is None:
        monkeypatch.delenv("BARS_POLL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BARS_POLL_ENABLED", enabled)
    return create_celery()


@pytest.mark.parametrize("value", [None, "false", "", "yes"])
def test_beat_does_not_schedule_the_polls_unless_enabled(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    app = make_app(monkeypatch, value)
    assert "bars_daily_poll" not in app.conf.beat_schedule
    assert "bars_15m_poll" not in app.conf.beat_schedule
    # A manual run still works: the tasks are registered either way.
    app.loader.import_default_modules()
    assert {BARS_DAILY_POLL, BARS_15M_POLL} <= set(app.tasks)


def due_at(schedule: crontab, local: datetime) -> bool:
    setattr(schedule, "nowfun", lambda: local.astimezone(UTC))  # noqa: B010
    return bool(schedule.is_due(local - timedelta(seconds=30)).is_due)


def test_enabled_both_polls_run_at_1005_new_york_monday_to_friday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(monkeypatch, "true")
    for key, task in (("bars_daily_poll", BARS_DAILY_POLL), ("bars_15m_poll", BARS_15M_POLL)):
        entry: dict[str, Any] = app.conf.beat_schedule[key]
        assert entry["task"] == task
        assert "queue" not in entry["options"]  # the default: ingest
        assert app.conf.task_routes.get(task, {}).get("queue", INGEST_QUEUE) == INGEST_QUEUE
        assert entry["options"]["expires"] == 6 * 3600
        schedule = entry["schedule"]
        assert isinstance(schedule, crontab)
        ny = NEW_YORK
        # 10:05 (Kyle, 2026-11-20): today's daily row was absent at 09:05 (MEASURED that day).
        assert due_at(schedule, datetime(2026, 1, 12, 10, 5, 10, tzinfo=ny))  # Monday, winter
        assert due_at(schedule, datetime(2026, 7, 17, 10, 5, 10, tzinfo=ny))  # Friday, summer
        assert not due_at(schedule, datetime(2026, 7, 18, 10, 5, 10, tzinfo=ny))  # Saturday
        assert not due_at(schedule, datetime(2026, 1, 11, 10, 5, 10, tzinfo=ny))  # Sunday
        assert not due_at(schedule, datetime(2026, 1, 12, 10, 4, 40, tzinfo=ny))  # not yet
        assert not due_at(schedule, datetime(2026, 1, 12, 9, 5, 10, tzinfo=ny))  # the old time


def test_the_polls_count_as_ingest_worker_work_and_the_backfill_does_not() -> None:
    assert {BARS_DAILY_POLL, BARS_15M_POLL} <= set(INGEST_WORKER_TASKS)
    assert BARS_BACKFILL not in INGEST_WORKER_TASKS


def test_both_bars_feeds_go_stale_after_four_days() -> None:
    stale = settings.feed_stale_after()
    assert stale[("yfinance", "bars_daily")] == timedelta(days=4)
    assert stale[("yfinance", "bars_15m")] == timedelta(days=4)

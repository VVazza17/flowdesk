"""M2 slice 6 step 4: the daily bars backfill (`python -m app.bars_backfill`,
app.services.bars_store).

A scratch database per test (conftest `scratch_url`), seeded (symbols, the 24 yfinance mappings) and
with XNYS sessions synced, exactly as the stack's `migrate` service leaves it. The download is
injected and serves the batches recorded 2026-11-18 (tests/fixtures/yfinance). What is asserted is
read back from the database, not from the parser.

Rule 9: the re-fetch comparison is keyed by symbol. In the mismatch test a SECOND symbol (XLK)
re-fetches cleanly while XLE's stored bar is contradicted; XLK's new rows must be written and XLE's
stored rows must be exactly as they were. Mutation-checked: `bash infra/mutation-check.sh
m2-slice6-step4`."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bars_backfill import Report, execute
from app.ingest.bars import NEW_YORK
from app.ingest.errors import EmptyResult, SchemaMismatch
from app.ingest.runner import RetryPolicy
from app.services.reference_seed import seed_reference
from app.services.sessions import sync_sessions
from app.tasks.names import BARS_BACKFILL
from tests.yfixtures import load

SPLIT5 = ["XLB", "XLE", "XLK", "XLU", "XLY"]
DEAD = "ZZZQXFAKE"


class Limiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self, provider: str) -> None:
        self.acquired += 1


def fixture_download(name: str, tamper: Any = None) -> Any:
    """A download serving one recorded batch, cut to the tickers asked for (as Yahoo would)."""

    def download(tickers: Sequence[str], **kw: Any) -> tuple[Any, dict[str, str]]:
        frame, errors, _ = load(name)
        keep = [c for c in frame.columns if c[0] in tickers]
        frame = frame[keep].copy()
        frame = frame.dropna(how="all")
        if tamper is not None:
            tamper(frame)
        return frame, {t: e for t, e in errors.items() if t in tickers}

    return download


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
    url: str,
    *,
    tickers: Sequence[str] | None,
    download: Any,
    now: datetime,
) -> tuple[Report, BaseException | None]:
    report = Report()

    async def fn(engine: AsyncEngine) -> BaseException | None:
        try:
            await execute(
                engine,
                report,
                limiter=Limiter(),
                tickers=tickers,
                download=download,
                clock=lambda: now,
                retry=RetryPolicy(attempts=1, initial_seconds=0.0, max_seconds=0.0),
            )
        except Exception as exc:  # the run's failure: returned so the test can read the report
            return exc
        return None

    return report, run(url, fn)


def et(y: int, m: int, d: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=NEW_YORK)


RECORDED = datetime(2026, 11, 18, 21, 56, tzinfo=UTC)


def bar(url: str, ticker: str, day: date) -> Any:
    (found,) = rows(
        url,
        "SELECT b.* FROM bars_daily b JOIN symbols s ON s.id = b.symbol_id "
        "JOIN trading_sessions t ON t.opens_at = b.effective_at AND t.calendar_code = 'XNYS' "
        "WHERE s.ticker = :t AND t.session_date = :d",
        t=ticker,
        d=day,
    )
    return found


# --- what is written ---------------------------------------------------------------------------


def test_bars_total_returns_and_splits_are_written_with_backfill_provenance(db: str) -> None:
    report, error = backfill(
        db, tickers=SPLIT5, download=fixture_download("daily_split_2025_12_05"), now=RECORDED
    )
    assert error is None and report.failed == []

    xlk = bar(db, "XLK", date(2025, 12, 4))
    assert xlk.c_raw == Decimal("291.07000732421876")  # D3, read back from bars_daily
    assert xlk.v_raw == 9_995_200 // 2 and xlk.raw_basis == "reconstructed"
    assert xlk.feed == "backfill" and xlk.source == "yfinance"
    assert xlk.available_at == et(2025, 12, 8, 9, 0)  # the settle point: 09:00 ET on D+2
    after = bar(db, "XLK", date(2025, 12, 5))
    assert after.raw_basis == "live" and after.c_raw == Decimal("146.60000610351562")

    splits = rows(
        db,
        "SELECT s.ticker, p.effective_at, p.ratio, p.available_at, p.ingestion_run_id "
        "FROM splits p JOIN symbols s ON s.id = p.symbol_id ORDER BY s.ticker",
    )
    assert [(r.ticker, r.effective_at, r.ratio) for r in splits] == [
        (t, date(2025, 12, 5), Decimal("2.0")) for t in SPLIT5
    ]
    assert all(r.available_at == et(2025, 12, 5, 9, 30) for r in splits)  # the effective open

    # Every rebuilt row has a whole volume that is the provider's divided by 2, and every bar has
    # its revision-0 total return.
    counts = rows(
        db,
        "SELECT count(*) AS bars, "
        "count(*) FILTER (WHERE b.raw_basis = 'reconstructed') AS rebuilt, "
        "count(*) FILTER (WHERE t.session_date < DATE '2025-12-05') AS before_split, "
        "(SELECT count(*) FROM bar_total_return WHERE revision = 0) AS tr "
        "FROM bars_daily b JOIN trading_sessions t "
        "ON t.opens_at = b.effective_at AND t.calendar_code = 'XNYS'",
    )[0]
    assert counts.bars == counts.tr == report.bars
    # Exactly the bars before the split are rebuilt (the window holds Thanksgiving, so the count is
    # read from the calendar join, not assumed).
    assert counts.rebuilt == counts.before_split > 0
    # Revision 0 of total return is knowable at its bar's settle point (decided 2026-11-18).
    tr_off = rows(
        db,
        "SELECT count(*) FROM bar_total_return r JOIN bars_daily b "
        "ON b.symbol_id = r.symbol_id AND b.effective_at = r.effective_at "
        "WHERE r.revision = 0 AND r.available_at <> b.available_at",
    )[0][0]
    assert tr_off == 0
    run_ids = rows(db, "SELECT DISTINCT ingestion_run_id FROM bars_daily")
    assert len(run_ids) == 1 and run_ids[0][0] is not None


def test_the_run_is_a_recorded_task_execution(db: str) -> None:
    backfill(db, tickers=["XLK"], download=fixture_download("daily_split_2025_12_05"), now=RECORDED)
    task = rows(db, "SELECT task_name, status FROM task_runs")
    assert [(r.task_name, r.status) for r in task] == [(BARS_BACKFILL, "succeeded")]
    ing = rows(db, "SELECT provider, feed, status, task_run_id FROM ingestion_runs")
    assert [(r.provider, r.feed, r.status) for r in ing] == [
        ("yfinance", "bars_daily", "succeeded")
    ]
    assert ing[0].task_run_id is not None
    progress = rows(db, "SELECT feed, status, completed_units, total_units FROM backfill_runs")
    assert [tuple(r) for r in progress] == [("yfinance.bars_daily", "succeeded", 1, 1)]


def test_a_second_run_writes_zero_rows(db: str) -> None:
    download = fixture_download("daily_split_2025_12_05")
    first, _ = backfill(db, tickers=SPLIT5, download=download, now=RECORDED)
    second, error = backfill(db, tickers=SPLIT5, download=download, now=RECORDED)
    assert first.rows_written > 0
    assert error is None and second.rows_written == 0
    assert (second.bars, second.total_returns, second.splits) == (0, 0, 0)
    written = rows(db, "SELECT rows_written FROM ingestion_runs ORDER BY id")
    assert [r.rows_written for r in written] == [first.rows_written, 0]


# --- the settle point and the as-of read ----------------------------------------------------------


def test_only_settled_bars_are_stored_and_an_as_of_read_sees_only_what_had_settled(db: str) -> None:
    # Run at Tue 2025-12-09 09:00 ET: Fri 12-05's settle point (D+2) is exactly then; Mon 12-08's
    # is Wed. So 12-05 is stored and 12-08 onward is not.
    backfill(
        db,
        tickers=SPLIT5,
        download=fixture_download("daily_split_2025_12_05"),
        now=et(2025, 12, 9, 9),
    )
    last = rows(
        db,
        "SELECT max(t.session_date) FROM bars_daily b JOIN trading_sessions t "
        "ON t.opens_at = b.effective_at AND t.calendar_code = 'XNYS'",
    )[0][0]
    assert last == date(2025, 12, 5)

    def as_of(t: datetime) -> date:
        return rows(  # type: ignore[no-any-return]
            db,
            "SELECT max(t.session_date) FROM bars_daily b JOIN trading_sessions t "
            "ON t.opens_at = b.effective_at AND t.calendar_code = 'XNYS' WHERE b.available_at <= "
            ":t",
            t=t,
        )[0][0]

    # One minute before Tue 09:00, Fri's bar had not settled: Thu 12-04 is the latest known.
    assert as_of(et(2025, 12, 9, 8, 59)) == date(2025, 12, 4)
    assert as_of(et(2025, 12, 9, 9, 0)) == date(2025, 12, 5)
    # Every stored bar's settle point is the 09:00 ET of the second session after its own.
    bad = rows(
        db,
        "SELECT count(*) FROM bars_daily b JOIN trading_sessions t ON t.opens_at = b.effective_at "
        "AND t.calendar_code = 'XNYS' WHERE b.available_at <> ("
        " SELECT (s2.session_date + time '09:00') AT TIME ZONE 'America/New_York' FROM trading_se"
        "ssions s2"
        " WHERE s2.calendar_code = 'XNYS' AND s2.session_date > t.session_date"
        " ORDER BY s2.session_date OFFSET 1 LIMIT 1)",
    )[0][0]
    assert bad == 0


# --- D3: a re-fetch never overwrites (rule 9) -----------------------------------------------------


def _stored_rows(url: str, ticker: str) -> list[Any]:
    return rows(
        url,
        "SELECT b.* FROM bars_daily b JOIN symbols s ON s.id = b.symbol_id "
        "WHERE s.ticker = :t ORDER BY effective_at",
        t=ticker,
    )


def test_a_price_difference_fails_its_symbol_a_volume_revision_does_not(db: str) -> None:
    # Kyle, 2026-11-18. In ONE re-fetch: XLE's stored 2025-12-04 bar comes back with a different
    # Close (a price: most likely a missed split, an operator must look), and XLK's comes back
    # with a different Volume only (a post-settle revision, measured to happen). XLE must fail
    # with its rows
    # untouched and none of its new rows written; XLK must keep its stored volume, be counted, and
    # write its new rows normally. Rule 9: each symbol's verdict must stay its own.
    backfill(
        db,
        tickers=["XLE", "XLK"],
        download=fixture_download("daily_split_2025_12_05"),
        now=et(2025, 12, 9, 9),
    )
    xle_before = _stored_rows(db, "XLE")
    xlk_1204 = bar(db, "XLK", date(2025, 12, 4))

    def tamper(frame: Any) -> None:
        frame.loc[pd.Timestamp("2025-12-04"), ("XLE", "Close")] += 0.01
        # +2 shares before the 2:1 split rebuilds to +1 as traded: still whole, so volume only.
        frame.loc[pd.Timestamp("2025-12-04"), ("XLK", "Volume")] += 2.0

    second, error = backfill(
        db,
        tickers=["XLE", "XLK"],
        download=fixture_download("daily_split_2025_12_05", tamper),
        now=RECORDED,
    )
    # XLE: a symbol failure, named; its stored first settled values stand; no new XLE rows.
    assert isinstance(error, SchemaMismatch) and "XLE" in str(error) and "XLK" not in str(error)
    assert second.failed == ["XLE"]
    assert _stored_rows(db, "XLE") == xle_before
    # XLK: not a failure; counted; the stored volume kept; its new sessions written.
    assert second.volume_revisions == {"XLK": 1}
    assert bar(db, "XLK", date(2025, 12, 4)) == xlk_1204
    assert bar(db, "XLK", date(2025, 12, 12)).feed == "backfill"


def test_a_volume_revision_alone_does_not_fail_the_run(db: str) -> None:
    backfill(
        db,
        tickers=["XLK"],
        download=fixture_download("daily_split_2025_12_05"),
        now=et(2025, 12, 9, 9),
    )

    def tamper(frame: Any) -> None:
        frame.loc[pd.Timestamp("2025-12-05"), ("XLK", "Volume")] += 1.0

    report, error = backfill(
        db,
        tickers=["XLK"],
        download=fixture_download("daily_split_2025_12_05", tamper),
        now=RECORDED,
    )
    assert error is None and report.failed == []
    assert report.volume_revisions == {"XLK": 1}
    assert bar(db, "XLK", date(2025, 12, 5)).v_raw == 6_628_000  # the stored first settled value
    status = rows(db, "SELECT status FROM ingestion_runs ORDER BY id")
    assert [r.status for r in status] == ["succeeded", "succeeded"]


# --- D4: dead symbols, coverage -------------------------------------------------------------------


def _add_dead_symbol(url: str) -> None:
    rows(url, "INSERT INTO symbols (ticker) VALUES (:t)", t=DEAD)
    rows(
        url,
        "INSERT INTO provider_mappings (symbol_id, provider, id_kind, external_id) "
        "SELECT id, 'yfinance', 'ticker', :t FROM symbols WHERE ticker = :t",
        t=DEAD,
    )


def test_a_dead_symbol_fails_the_run_by_name_and_the_others_are_stored(db: str) -> None:
    _add_dead_symbol(db)
    report, error = backfill(
        db, tickers=None, download=fixture_download("daily_3mo_universe_plus_dead"), now=RECORDED
    )
    assert isinstance(error, EmptyResult) and DEAD in str(error)
    assert report.failed == [DEAD]
    stored = rows(db, "SELECT count(DISTINCT symbol_id) FROM bars_daily")[0][0]
    assert stored == 24
    # 25 mapped symbols: two batches of at most 24 (max_batch), each its own ingestion run. The 24
    # live ones succeed; the batch holding the dead one fails and names it, in our own words.
    runs = rows(db, "SELECT status, error FROM ingestion_runs ORDER BY id")
    assert [r.status for r in runs] == ["succeeded", "failed"]
    assert runs[1].error == f"empty_result:DeadSymbols: no rows in this batch for: {DEAD}"
    progress = rows(db, "SELECT status, completed_units, total_units FROM backfill_runs")
    assert [tuple(r) for r in progress] == [("failed", 1, 2)]
    by_ticker = {c.ticker: c for c in report.coverage}
    assert by_ticker[DEAD].bars == 0


def test_coverage_reports_a_young_symbol_as_young_and_a_hole_as_missing(db: str) -> None:
    def tamper(frame: Any) -> None:
        idx = frame.index
        frame.loc[idx[:10], [c for c in frame.columns if c[0] == "XLF"]] = float(
            "nan"
        )  # listed late
        frame.loc[idx[30], [c for c in frame.columns if c[0] == "SPY"]] = float("nan")  # a hole

    report, error = backfill(
        db,
        tickers=["SPY", "XLF", "XLK"],
        download=fixture_download("daily_3mo_universe_plus_dead", tamper),
        now=RECORDED,
    )
    assert error is None
    by = {c.ticker: c for c in report.coverage}
    assert by["XLF"].younger_than_window and by["XLF"].missing == ()
    assert "younger than the window" in by["XLF"].describe()
    assert not by["SPY"].younger_than_window and len(by["SPY"].missing) == 1
    assert "MISSING 1 sessions" in by["SPY"].describe()
    assert not by["XLK"].younger_than_window and by["XLK"].missing == ()
    # A young symbol is never recorded as a gap.
    assert rows(db, "SELECT count(*) FROM bar_gaps")[0][0] == 0


def test_likely_gone_is_judged_against_every_symbol_not_just_its_batch(db: str) -> None:
    # D4: "5 or more sessions behind the newest session ANY symbol stored". The dead symbol sorts
    # last, so it sits alone in the second batch (25 mapped symbols, max_batch 24). Its only stored
    # bar is the window's first session; the other 24 store through 2026-11-16 in the first batch.
    # Judged against its own batch it would be 0 behind (empty today); against every symbol it is
    # likely gone.
    _add_dead_symbol(db)
    rows(
        db,
        "INSERT INTO bars_daily (symbol_id, effective_at, o_raw, h_raw, l_raw, c_raw, v_raw, "
        "raw_basis, source, feed, venue_scope, available_at) "
        "SELECT s.id, t.opens_at, 1, 1, 1, 1, 100, 'live', 'yfinance', 'backfill', "
        "'provider_default', t.opens_at + interval '3 days' "
        "FROM symbols s, trading_sessions t "
        "WHERE s.ticker = :d AND t.calendar_code = 'XNYS' AND t.session_date = DATE '2026-08-19'",
        d=DEAD,
    )
    report, error = backfill(
        db, tickers=None, download=fixture_download("daily_3mo_universe_plus_dead"), now=RECORDED
    )
    assert isinstance(error, EmptyResult)
    assert type(error).__name__ == "LikelyGoneSymbols" and DEAD in str(error)
    assert "never automatic" in str(error)
    # Never retired by the run: its membership and mapping are untouched.
    assert (
        rows(
            db,
            "SELECT count(*) FROM provider_mappings m JOIN symbols s ON s.id = m.symbol_id "
            "WHERE s.ticker = :d AND m.retired_at IS NULL",
            d=DEAD,
        )[0][0]
        == 1
    )

"""Entry point: `python -m app.bars_backfill`. Two years of settled daily bars for every mapped
symbol.

    docker compose run --rm worker python -m app.bars_backfill          # every mapped symbol
    docker compose run --rm worker python -m app.bars_backfill --symbols XLK,XLE

The counterpart to `python -m app.backfill` (M2 slice 6, step 4; Kyle, 2026-11-15: the poll never
fetches deep history; deep history comes from a separate, RECORDED backfill command). Like it, it is
an entry point, not a Celery task, and it goes through `run_recorded` (a manual backfill is a task
execution with a `task_runs` row) and `run_feed` (the rate budget, retries, `ingestion_runs`,
`providers_health`, the stored-error rule). Batches of at most `max_batch` (24) tickers, each its
own `run_feed` and ingestion run; the 24 seeded symbols are one batch.

What it writes, all ON CONFLICT DO NOTHING (a second run writes 0 rows): `bars_daily` with `feed =
'backfill'` and `available_at` = the settle point (09:00 New York on D+2: the RECORDED EXCEPTION of
2026-11-18; the values were fetched later and may include corrections made after it, and a strict
replay excludes `feed = 'backfill'`), `bar_total_return` revision 0, and `splits`. A re-fetch that
differs from a stored bar fails that symbol and overwrites nothing (D3).

It reports per-symbol coverage over the window: a symbol younger than the window is reported as such
and is NOT recorded as a gap; a session missing inside a symbol's own span is reported loudly.
Nothing here writes `bar_gaps` (that record is for the poll's unrecoverable intraday gaps).

Exit status: 0 if every symbol was stored, 1 if the run failed (the good symbols' rows are kept,
D4)."""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.bars import (
    DAILY,
    NEW_YORK,
    classify_dead,
    parse_batch,
    run_failure,
)
from app.ingest.budget import Limiter, RedisSpacingLimiter
from app.ingest.errors import ProviderError
from app.ingest.runner import FeedContext, FeedResult, RetryPolicy, run_feed
from app.ingest.yfinance_client import Download, YFinanceClient, yf_download
from app.logging import configure_logging
from app.models.ops import BackfillRun
from app.services import bars_store
from app.services.bars_store import Coverage
from app.tasks.names import BARS_BACKFILL, STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED
from app.tasks.runs import run_recorded

log = structlog.get_logger(__name__)

FEED = "bars_daily"  # the ingestion feed; the poll (step 6) uses the same name
BACKFILL_FEED_VALUE = "backfill"  # bars_daily.feed (CHECK poll | backfill)
PROGRESS_FEED = "yfinance.bars_daily"  # backfill_runs.feed
PERIOD = "2y"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Report:
    rows_written: int = 0
    bars: int = 0
    total_returns: int = 0
    splits: int = 0
    total_return_differs: int = 0
    unsettled: int = 0
    coverage: list[Coverage] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def backfill(
    engine: AsyncEngine,
    *,
    limiter: Limiter,
    tickers: Sequence[str] | None = None,
    download: Download = yf_download,
    clock: Callable[[], datetime] = utcnow,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    period: str = PERIOD,
    report: Report | None = None,
) -> Report:
    """Fetch, parse, store and report every mapped symbol, in batches of at most `max_batch`.

    Each batch is its own `run_feed` (so each takes its own rate-budget slot: several fetches in one
    body would go round the limiter) and its own ingestion run. A failed batch does not stop the
    others. At the end the run fails if any symbol failed, after the good symbols' rows are written
    (D4); `report` (pass one in to read it after a failure) is filled either way."""
    calendar = await bars_store.load_calendar(engine)
    symbols = await bars_store.load_symbols(engine, tickers)
    if not symbols:
        raise ValueError("no symbol has an active yfinance mapping (run the seed)")
    max_batch = settings.RATE_BUDGETS[bars_store.PROVIDER].max_batch
    batches = [symbols[i : i + max_batch] for i in range(0, len(symbols), max_batch)]
    client = YFinanceClient(max_batch=max_batch, download=download)
    report = report if report is not None else Report()
    window: list[date] = []  # the first and last settled session any batch parsed
    started = clock()
    async with engine.begin() as conn:
        progress_id = (
            await conn.execute(
                insert(BackfillRun)
                .values(
                    feed=PROGRESS_FEED,
                    total_units=len(batches),
                    completed_units=0,
                    status=STATUS_RUNNING,
                    started_at=started,
                    updated_at=started,
                )
                .returning(BackfillRun.id)
            )
        ).scalar_one()

    def body_for(chunk: Sequence[bars_store.MappedSymbol]) -> Callable[[FeedContext], Any]:
        by_provider = {m.provider_symbol: m.symbol_id for m in chunk}

        async def body(ctx: FeedContext) -> FeedResult:
            stored = await bars_store.load_splits(engine, by_provider.values())
            known = {p: stored[sid] for p, sid in by_provider.items()}
            now = clock()
            batch = await client.fetch(
                list(by_provider), DAILY, today=now.astimezone(NEW_YORK).date(), period=period
            )
            result = parse_batch(
                batch.frame,
                batch.errors,
                tickers=list(by_provider),
                interval=DAILY,
                calendar=calendar,
                now=now,
                known_splits=known,
            )
            counts = await bars_store.write_daily(
                engine,
                result.bars,
                result.new_splits,
                by_provider,
                calendar=calendar,
                feed=BACKFILL_FEED_VALUE,
                ingestion_run_id=ctx.run_id,
            )
            # D4: likely_gone is judged against the newest session ANY symbol has stored (every
            # mapped symbol, not just this batch), read after this batch's write.
            every = await bars_store.stored_sessions(engine, [m.symbol_id for m in symbols])
            newest = max((d[-1] for d in every.values() if d), default=None)
            dead = classify_dead(
                result.dead,
                last_stored={
                    p: (every[sid][-1] if every[sid] else None) for p, sid in by_provider.items()
                },
                newest_stored=newest,
                calendar=calendar,
            )
            parsed = [b.session_date for rows in result.bars.values() for b in rows]
            if parsed:
                window.extend([min(parsed), max(parsed)])
            report.bars += counts.bars
            report.total_returns += counts.total_returns
            report.splits += counts.splits
            report.rows_written += counts.rows_written
            report.total_return_differs += counts.total_return_differs
            report.unsettled += result.unsettled
            failures = [*result.failures, *counts.mismatched, *dead]
            report.failed = sorted({*report.failed, *(f.ticker for f in failures)})
            error = run_failure(failures)
            if error is not None:
                raise error
            return FeedResult(rows_read=len(parsed), rows_written=counts.rows_written)

        return body

    errors: list[BaseException] = []
    done = 0
    for chunk in batches:
        try:
            await run_feed(
                engine, bars_store.PROVIDER, FEED, body_for(chunk), limiter=limiter, retry=retry
            )
            done += 1
        except ProviderError as exc:
            # run_feed has recorded it (ingestion_runs, providers_health). A whole-batch failure
            # (rate limit, network) names no symbol in report.failed: name the batch's symbols.
            if not any(m.provider_symbol in report.failed for m in chunk):
                report.failed = sorted({*report.failed, *(m.provider_symbol for m in chunk)})
            errors.append(exc)
        async with engine.begin() as conn:
            await conn.execute(
                update(BackfillRun)
                .where(BackfillRun.id == progress_id)
                .values(updated_at=clock(), completed_units=done)
            )

    if window:
        report.coverage = bars_store.coverage(
            symbols,
            await bars_store.stored_sessions(engine, [m.symbol_id for m in symbols]),
            calendar=calendar,
            window_first=min(window),
            last_settled=max(window),
        )
    async with engine.begin() as conn:
        await conn.execute(
            update(BackfillRun)
            .where(BackfillRun.id == progress_id)
            .values(updated_at=clock(), status=STATUS_FAILED if errors else STATUS_SUCCEEDED)
        )
    if errors:
        # DECISION: one batch today (24 symbols). With several, the first failure is raised,
        # and every failed symbol is named in report.failed and in each batch's ingestion run.
        raise errors[0]
    return report


async def execute(
    engine: AsyncEngine,
    report: Report,
    *,
    limiter: Limiter,
    tickers: Sequence[str] | None = None,
    download: Download = yf_download,
    clock: Callable[[], datetime] = utcnow,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
) -> None:
    """`backfill` inside `run_recorded`: the execution gets its `task_runs` row. `report` is filled
    whether or not the run fails."""

    async def body() -> None:
        await backfill(
            engine,
            limiter=limiter,
            tickers=tickers,
            download=download,
            clock=clock,
            retry=retry,
            report=report,
        )

    await run_recorded(engine, BARS_BACKFILL, body)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.bars_backfill",
        description="Two years of settled daily bars for every symbol with a yfinance mapping.",
    )
    parser.add_argument(
        "--symbols",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None,
        help="comma-separated tickers (default: every symbol with an active yfinance mapping)",
    )
    return parser.parse_args(argv)


def print_report(report: Report) -> None:
    print(
        f"bars_daily +{report.bars}, bar_total_return +{report.total_returns}, "
        f"splits +{report.splits} (rows written {report.rows_written}); "
        f"unsettled rows skipped {report.unsettled}; "
        f"stored total returns that differ from today's {report.total_return_differs}",
        file=sys.stderr,
    )
    for c in report.coverage:
        print("  " + c.describe(), file=sys.stderr)
    if report.failed:
        print("FAILED symbols: " + ", ".join(report.failed), file=sys.stderr)


async def run(tickers: Sequence[str] | None, report: Report) -> None:
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:
        await execute(
            engine,
            report,
            limiter=RedisSpacingLimiter(redis, settings.RATE_BUDGETS),
            tickers=tickers,
            retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
        )
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    report = Report()
    try:
        asyncio.run(run(args.symbols, report))
    except ProviderError as exc:
        # FlowDesk's own text only (the stored-error rule): the tickers and what was wrong.
        print_report(report)
        log.error("bars_backfill_failed", error=f"{exc.kind}:{type(exc).__name__}", detail=str(exc))
        return 1
    print_report(report)
    log.info(
        "bars_backfill_done",
        rows_written=report.rows_written,
        bars=report.bars,
        total_returns=report.total_returns,
        splits=report.splits,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

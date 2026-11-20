"""Entry point: `python -m app.bars_backfill`. Settled bars for every mapped symbol: two years of
daily bars, or (`--interval 15m`) the last 58 calendar days of 15-minute bars.

    docker compose run --rm worker python -m app.bars_backfill                # daily, every symbol
    docker compose run --rm worker python -m app.bars_backfill --symbols XLK,XLE
    docker compose run --rm worker python -m app.bars_backfill --interval 15m # 58 days of 15m

The counterpart to `python -m app.backfill` (M2 slice 6, step 4; Kyle, 2026-11-15: the poll never
fetches deep history; deep history comes from a separate, RECORDED backfill command). Like it, it is
an entry point, not a Celery task, and it goes through `run_recorded` (a manual backfill is a task
execution with a `task_runs` row) and `run_feed` (the rate budget, retries, `ingestion_runs`,
`providers_health`, the stored-error rule). The run itself is `app.ingest.bars_feed.run_bars`, which
the polls share.

What it writes, all ON CONFLICT DO NOTHING (a second run writes 0 rows): `bars_daily` or
`bars_intraday` with `feed = 'backfill'` and `available_at` = the settle point (daily 09:00 New York
on D+2, 15m 09:00 on D+1: the RECORDED EXCEPTION of 2026-11-18; the values were fetched later and
may include corrections made after it, and a strict replay excludes `feed = 'backfill'`), `splits`,
and `distributions` (step 5b; the same exception and `feed`). A re-fetch that differs from a stored
bar fails that symbol and overwrites nothing (D3).

**15m (Kyle, 2026-11-18): no 15m bar is accepted until splits for its window are known in the same
run.** The 15m backfill first fetches its own daily `actions=True` batch over the 58 days, stores
and checks any new split and distribution, and only then parses the 15m bars.

A daily run reports per-symbol coverage over the window: a symbol younger than the window is
reported as such and is NOT recorded as a gap; a session missing inside a symbol's own span is
reported loudly. The backfill writes no `bar_gaps` (that record is the poll's).

Exit status: 0 if every symbol was stored, 1 if the run failed (the good symbols' rows are kept,
D4)."""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.bars import DAILY, INTRADAY_15M
from app.ingest.bars_feed import BACKFILL, Report, run_bars, yfinance_limiter
from app.ingest.budget import Limiter
from app.ingest.errors import ProviderError
from app.ingest.runner import RetryPolicy
from app.ingest.yfinance_client import Download, yf_download
from app.logging import configure_logging
from app.tasks.names import BARS_BACKFILL
from app.tasks.runs import run_recorded

log = structlog.get_logger(__name__)

__all__ = ["Report", "execute", "main"]


def utcnow() -> datetime:
    return datetime.now(UTC)


async def execute(
    engine: AsyncEngine,
    report: Report,
    *,
    limiter: Limiter,
    tickers: Sequence[str] | None = None,
    download: Download = yf_download,
    clock: Callable[[], datetime] = utcnow,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    interval: str = DAILY,
) -> None:
    """The backfill inside `run_recorded`: the execution gets its `task_runs` row. `report` is
    filled whether or not the run fails."""

    async def body() -> None:
        await run_bars(
            engine,
            interval=interval,
            mode=BACKFILL,
            limiter=limiter,
            clock=clock,
            tickers=tickers,
            download=download,
            retry=retry,
            report=report,
        )

    await run_recorded(engine, BARS_BACKFILL, body)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.bars_backfill",
        description="Settled bars for every symbol with a yfinance mapping: 2 years daily, or 58 "
        "days of 15m.",
    )
    parser.add_argument(
        "--symbols",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None,
        help="comma-separated tickers (default: every symbol with an active yfinance mapping)",
    )
    parser.add_argument(
        "--interval",
        choices=[DAILY, INTRADAY_15M],
        default=DAILY,
        help="1d (two years, the default) or 15m (58 calendar days)",
    )
    return parser.parse_args(argv)


def print_report(report: Report, interval: str = DAILY) -> None:
    table = "bars_daily" if interval == DAILY else "bars_intraday"
    print(
        f"{table} +{report.bars}, distributions +{report.distributions}, "
        f"splits +{report.splits}, bar_gaps +{report.gaps} (rows written {report.rows_written}); "
        f"unsettled rows skipped {report.unsettled}",
        file=sys.stderr,
    )
    if report.volume_revisions:
        revised = ", ".join(f"{t} {n}" for t, n in sorted(report.volume_revisions.items()))
        print(
            "volume revised after settle (kept the stored value, not a failure): " + revised,
            file=sys.stderr,
        )
    else:
        print("volume revised after settle: none", file=sys.stderr)
    if report.held:
        held = ", ".join(f"{t} {n}" for t, n in sorted(report.held.items()))
        print(
            "HELD, nothing written for these symbols: this run's daily frame has no row for today "
            "yet (Kyle, 2026-11-20). Re-run after 09:35 ET. Settled bars not accepted: " + held,
            file=sys.stderr,
        )
    for c in report.coverage:
        print("  " + c.describe(), file=sys.stderr)
    if report.failed:
        print("FAILED symbols: " + ", ".join(report.failed), file=sys.stderr)


async def run(tickers: Sequence[str] | None, report: Report, interval: str = DAILY) -> None:
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:
        await execute(
            engine,
            report,
            limiter=yfinance_limiter(redis),
            tickers=tickers,
            retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
            interval=interval,
        )
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    report = Report()
    try:
        asyncio.run(run(args.symbols, report, args.interval))
    except ProviderError as exc:
        # FlowDesk's own text only (the stored-error rule): the tickers and what was wrong.
        print_report(report, args.interval)
        log.error("bars_backfill_failed", error=f"{exc.kind}:{type(exc).__name__}", detail=str(exc))
        return 1
    print_report(report, args.interval)
    log.info(
        "bars_backfill_done",
        interval=args.interval,
        rows_written=report.rows_written,
        bars=report.bars,
        distributions=report.distributions,
        splits=report.splits,
        volume_revisions=dict(sorted(report.volume_revisions.items())),
        held=dict(sorted(report.held.items())),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

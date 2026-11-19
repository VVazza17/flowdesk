"""The settled bars polls (M2 slice 6, step 6). Two Celery tasks on the ingest queue; Beat runs both
at 09:05 America/New_York, Monday to Friday, only when BARS_POLL_ENABLED is true (off until IDEA-029
closes). By hand on the dev stack (allowed, Kyle 2026-11-18):

    docker compose exec worker celery -A app.worker call ingest.bars_daily_poll
    docker compose exec worker celery -A app.worker call ingest.bars_15m_poll

Each is one `run_recorded` execution around `app.ingest.bars_feed.run_bars(mode="poll")`: gap-driven
from each symbol's last stored bar, inclusive; the 15m poll checks splits and distributions in the
same run before it accepts a 15m bar. The per-run report (rows, volume revisions) is logged as
`bars_poll_done`: the volume revision counts feed the revision-policy decision.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from celery import shared_task
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.bars import DAILY, INTRADAY_15M
from app.ingest.bars_feed import POLL, Report, run_bars, yfinance_limiter
from app.ingest.budget import Limiter
from app.ingest.runner import RetryPolicy
from app.ingest.yfinance_client import Download, yf_download
from app.tasks.names import BARS_15M_POLL, BARS_DAILY_POLL
from app.tasks.runs import run_recorded

log = structlog.get_logger(__name__)

TASKS = {DAILY: BARS_DAILY_POLL, INTRADAY_15M: BARS_15M_POLL}


def utcnow() -> datetime:
    return datetime.now(UTC)


async def poll(
    engine: AsyncEngine,
    interval: str,
    report: Report,
    *,
    limiter: Limiter,
    download: Download = yf_download,
    clock: Callable[[], datetime] = utcnow,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
) -> None:
    """One poll inside `run_recorded`. `report` is filled whether or not it fails."""

    async def body() -> None:
        try:
            await run_bars(
                engine,
                interval=interval,
                mode=POLL,
                limiter=limiter,
                clock=clock,
                download=download,
                retry=retry,
                report=report,
            )
        finally:
            log.info(
                "bars_poll_done",
                interval=interval,
                rows_written=report.rows_written,
                bars=report.bars,
                splits=report.splits,
                distributions=report.distributions,
                gaps=report.gaps,
                unsettled=report.unsettled,
                volume_revisions=dict(sorted(report.volume_revisions.items())),
                volume_revisions_total=sum(report.volume_revisions.values()),
                failed=report.failed,
            )

    await run_recorded(engine, TASKS[interval], body)


async def run_poll(interval: str) -> None:
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:
        await poll(
            engine,
            interval,
            Report(),
            limiter=yfinance_limiter(redis),
            retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
        )
    finally:
        await redis.aclose()
        await engine.dispose()


@shared_task(name=BARS_DAILY_POLL, ignore_result=True, max_retries=0)
def bars_daily_poll() -> None:
    asyncio.run(run_poll(DAILY))


@shared_task(name=BARS_15M_POLL, ignore_result=True, max_retries=0)
def bars_15m_poll() -> None:
    asyncio.run(run_poll(INTRADAY_15M))

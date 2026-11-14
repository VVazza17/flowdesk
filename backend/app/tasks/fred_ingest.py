"""The daily FRED poll (M2 slice 5, step 3). A Celery task on the ingest queue; Beat runs it at
08:00 America/New_York (docs 6.4, `settings.FRED_POLL_HOUR`).

Every series in settings.FRED_SERIES, one after another, each through run_feed (so each has its
own ingestion_runs and providers_health row), inside one recorded task. A series that fails does
not stop the others; the task raises at the end naming them, so the ledger row says `failed`.

This is the only thing that runs forever; the history backfill is `python -m app.backfill`. A
poll takes minutes, so it sits comfortably under the task limit that the backfill could not.
"""

import asyncio
from collections.abc import Sequence

import httpx2
import structlog
from celery import shared_task
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.budget import Limiter, RedisSpacingLimiter
from app.ingest.fred_feed import PROVIDER, poll_body
from app.ingest.macro import FRED_RULE_VERSION, FredSeries
from app.ingest.runner import RetryPolicy, run_feed
from app.tasks.names import FRED_POLL
from app.tasks.runs import run_recorded

log = structlog.get_logger(__name__)


class PollIncomplete(Exception):
    """Some series failed. The message names them; the reasons are in ingestion_runs."""


async def poll_all(
    engine: AsyncEngine,
    series: Sequence[FredSeries],
    *,
    api_key: str,
    user_agent: str,
    limiter: Limiter,
    base_url: str,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    rule_version: int = FRED_RULE_VERSION,
) -> None:
    """Poll each series, continuing past a failure; raise PollIncomplete at the end if any failed.
    Must run inside `run_recorded` (run_feed refuses otherwise)."""

    async def pace() -> None:
        await limiter.acquire(PROVIDER)

    failed: list[str] = []
    for item in series:
        try:
            await run_feed(
                engine,
                PROVIDER,
                item.series_id,
                poll_body(
                    item,
                    api_key=api_key,
                    user_agent=user_agent,
                    pace=pace,
                    base_url=base_url,
                    transport=transport,
                    engine=engine,
                    rule_version=rule_version,
                ),
                limiter=limiter,
                retry=retry,
                secrets=[api_key],
            )
        except Exception:
            # run_feed has already written the failure (kind and class) to ingestion_runs.
            failed.append(item.series_id)
            log.error("fred_poll_series_failed", series=item.series_id)
    if failed:
        raise PollIncomplete(f"{len(failed)} of {len(series)} series failed: {', '.join(failed)}")


async def run_fred_poll() -> None:
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    api_key = settings.fred_api_key()
    if api_key is None:
        # Loud on purpose: a poll that silently does nothing would read as a feed that is fine.
        raise RuntimeError("NotConfigured")
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:

        async def body() -> None:
            await poll_all(
                engine,
                settings.FRED_SERIES,
                api_key=api_key,
                user_agent=settings.fred_user_agent(),
                # DECISION: redis-py types hgetall as sync-or-async; ours is the async client.
                limiter=RedisSpacingLimiter(redis, settings.RATE_BUDGETS),
                base_url=settings.fred_base_url(),
                retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
            )

        await run_recorded(engine, FRED_POLL, body)
    finally:
        await redis.aclose()
        await engine.dispose()


@shared_task(name=FRED_POLL, ignore_result=True, max_retries=0)
def fred_poll() -> None:
    asyncio.run(run_fred_poll())

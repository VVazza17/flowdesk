"""The FRED backfill task (M2 slice 5, step 3). Started by hand, never by Beat.

    docker compose exec worker celery -A app.worker call ingest.fred_backfill

Every series in settings.FRED_SERIES, one after another, each through run_feed (so each has its own
ingestion_runs and providers_health row), inside one recorded task and one backfill_runs row.

# DECISION: resuming is re-running. Every series' write is idempotent (a re-run creates 0 rows),
# so a killed or failed backfill is finished by calling the task again; a completed series costs its
# requests and writes nothing. Skipping completed series, and what "picked up recently" means for
# an orphaned backfill_runs row, are decided with the resume design at slice 10 (BUILD-LOG, step 3
# plan, part C): backfill_runs is deliberately outside the sweep.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx2
import structlog
from celery import shared_task
from redis.asyncio import Redis
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.budget import Limiter, RedisSpacingLimiter
from app.ingest.fred_feed import PROVIDER, backfill_body
from app.ingest.macro import FredSeries
from app.ingest.runner import RetryPolicy, run_feed
from app.models.ops import BackfillRun
from app.tasks.names import FRED_BACKFILL, STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED
from app.tasks.runs import run_recorded

log = structlog.get_logger(__name__)

BACKFILL_FEED = "fred"


class BackfillIncomplete(Exception):
    """Some series failed. The message names them; the reasons are in ingestion_runs."""


async def backfill_all(
    engine: AsyncEngine,
    series: Sequence[FredSeries],
    *,
    api_key: str,
    user_agent: str,
    limiter: Limiter,
    base_url: str,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
) -> None:
    """Run every series, continuing past a failed one, and raise at the end if any failed."""
    started = datetime.now(UTC)
    async with engine.begin() as conn:
        progress_id = (
            await conn.execute(
                insert(BackfillRun)
                .values(
                    feed=BACKFILL_FEED,
                    total_units=len(series),
                    completed_units=0,
                    status=STATUS_RUNNING,
                    started_at=started,
                    updated_at=started,
                )
                .returning(BackfillRun.id)
            )
        ).scalar_one()

    async def pace() -> None:
        await limiter.acquire(PROVIDER)

    async def progress(**values: object) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                update(BackfillRun)
                .where(BackfillRun.id == progress_id)
                .values(updated_at=datetime.now(UTC), **values)
            )

    done = 0
    failed: list[str] = []
    for item in series:
        await progress(current_unit=item.series_id)
        try:
            await run_feed(
                engine,
                PROVIDER,
                item.series_id,
                backfill_body(
                    item,
                    api_key=api_key,
                    user_agent=user_agent,
                    pace=pace,
                    base_url=base_url,
                    transport=transport,
                    engine=engine,
                ),
                limiter=limiter,
                retry=retry,
                secrets=[api_key],
            )
            done += 1
            await progress(completed_units=done)
        except Exception:
            # run_feed has already written the failure (kind and class) to ingestion_runs.
            failed.append(item.series_id)
    await progress(current_unit=None, status=STATUS_FAILED if failed else STATUS_SUCCEEDED)
    if failed:
        raise BackfillIncomplete(
            f"{len(failed)} of {len(series)} series failed: {', '.join(failed)}"
        )


async def run_fred_backfill() -> None:
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    api_key = settings.fred_api_key()
    if api_key is None:
        raise RuntimeError("NotConfigured")
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:

        async def body() -> None:
            await backfill_all(
                engine,
                settings.FRED_SERIES,
                api_key=api_key,
                user_agent=settings.fred_user_agent(),
                # DECISION: redis-py types hgetall as sync-or-async; ours is the async client.
                limiter=RedisSpacingLimiter(redis, settings.RATE_BUDGETS),
                base_url=settings.fred_base_url(),
                retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
            )

        await run_recorded(engine, FRED_BACKFILL, body)
    finally:
        await redis.aclose()
        await engine.dispose()


@shared_task(name=FRED_BACKFILL, ignore_result=True, max_retries=0)
def fred_backfill() -> None:
    asyncio.run(run_fred_backfill())

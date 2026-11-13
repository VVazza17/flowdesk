"""The fake provider's ingest task (M2 slice 4). Scheduled by Beat only when FAKE_PROVIDER_ENABLED.

The shape every real ingest task will have: build an engine, run the feed through run_feed, dispose.
Celery retries are off (`max_retries=0`): tenacity retries inside the run and Beat is the outer
retry, so one logical run is one ingestion_runs row (app.ingest.runner, IDEA-042).
"""

import asyncio

from celery import shared_task
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.ingest.budget import RedisSpacingLimiter
from app.ingest.fake import FakeProvider, RedisFakeStore, fake_body
from app.ingest.runner import RetryPolicy, run_feed
from app.tasks.names import FAKE_INGEST
from app.tasks.runs import run_recorded


async def run_fake_ingest() -> None:
    # DECISION: a no-op unless the fake is switched on. Beat only schedules it when enabled, but a
    # message already queued in Redis when the flag was turned off (or a Beat that was restarted
    # from an older schedule) would still reach a worker, and a normal stack must never write fake
    # rows into providers_health. Found by smoke 6 failing on a stray 'fake' feed.
    if not settings.fake_provider_enabled():
        return
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    provider_name, feed = settings.FAKE_FEED
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:

        async def body() -> None:
            await run_feed(
                engine,
                provider_name,
                feed,
                fake_body(
                    FakeProvider(
                        [settings.fake_provider_mode()],
                        slow_seconds=settings.fake_provider_slow_seconds(),
                    ),
                    # DECISION: redis-py types hgetall as sync-or-async; ours is the async client.
                    RedisFakeStore(redis),  # type: ignore[arg-type]
                ),
                limiter=RedisSpacingLimiter(redis, settings.RATE_BUDGETS),
                retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
            )

        # Every task goes through run_recorded: it gives this run its task_runs row (the heartbeat,
        # and the link run_feed needs), and the ingest worker `busy` state reads that row.
        await run_recorded(engine, FAKE_INGEST, body)
    finally:
        await redis.aclose()
        await engine.dispose()


@shared_task(name=FAKE_INGEST, ignore_result=True, max_retries=0)
def fake_ingest() -> None:
    asyncio.run(run_fake_ingest())

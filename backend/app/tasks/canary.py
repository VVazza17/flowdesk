"""The worker canaries (M2 slice 1; the ingest canary is slice 5, step 0): the smallest task that
exercises the whole path.

Beat schedules it, Redis carries it, a worker process runs it, and Postgres records it. If any link
breaks, completions stop and /health/ready degrades (docs/HEALTH.md). It does nothing else, on
purpose.

There are two, with the same body: `canary` runs on the `ops` queue, in a worker that takes nothing
else, so it cannot be starved by ingest work; `canary_ingest` runs on the `ingest` queue, where it
can wait behind a long task and is judged against a threshold sized to the longest allowed one.
"""

import asyncio

from celery import shared_task
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.tasks.names import CANARY, INGEST_CANARY
from app.tasks.runs import run_recorded


async def _nothing() -> None:
    return None


async def run_canary(task_name: str = CANARY) -> None:
    url = settings.database_url()
    if url is None:
        raise RuntimeError("NotConfigured")
    # DECISION: a fresh engine per run, disposed after, inside its own event loop. Celery tasks are
    # sync, and asyncpg connections belong to the loop that opened them, so an engine shared across
    # asyncio.run() calls breaks. Once a minute, the cost is nothing.
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    try:
        await run_recorded(engine, task_name, _nothing)
    finally:
        await engine.dispose()


@shared_task(name=CANARY, ignore_result=True)
def canary() -> None:
    asyncio.run(run_canary(CANARY))


@shared_task(name=INGEST_CANARY, ignore_result=True)
def canary_ingest() -> None:
    asyncio.run(run_canary(INGEST_CANARY))

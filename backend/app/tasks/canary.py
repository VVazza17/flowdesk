"""The worker canary (M2 slice 1): the smallest task that exercises the whole path.

Beat schedules it, Redis carries it, a worker process runs it, and Postgres records it. If any link
breaks, completions stop and /health/ready degrades (docs/HEALTH.md). It does nothing else, on
purpose.
"""

import asyncio

from celery import shared_task
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.tasks.names import CANARY
from app.tasks.runs import run_recorded


async def _nothing() -> None:
    return None


async def run_canary() -> None:
    url = settings.database_url()
    if url is None:
        raise RuntimeError("NotConfigured")
    # DECISION: a fresh engine per run, disposed after, inside its own event loop. Celery tasks are
    # sync, and asyncpg connections belong to the loop that opened them, so an engine shared across
    # asyncio.run() calls breaks. Once a minute, the cost is nothing.
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    try:
        await run_recorded(engine, CANARY, _nothing)
    finally:
        await engine.dispose()


@shared_task(name=CANARY, ignore_result=True)
def canary() -> None:
    asyncio.run(run_canary())

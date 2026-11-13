"""A stand-in for `run_recorded` around a feed under test.

`run_feed` refuses to run outside a recorded task (`NotRecorded`), because it links each ingestion
run to a `task_runs` row. Tests that exercise `run_feed` or `backfill_all` directly use this to
have a real, running, fresh row and the context variable set, without the heartbeat thread or the
final status write that `run_recorded` adds (those have their own tests).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.ops import TaskRun
from app.tasks.names import STATUS_RUNNING
from app.tasks.runs import current_task_run_id


@asynccontextmanager
async def recorded(engine: AsyncEngine, task_name: str = "test.task") -> AsyncIterator[int]:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        run_id = (
            await conn.execute(
                insert(TaskRun)
                .values(
                    task_name=task_name, started_at=now, heartbeat_at=now, status=STATUS_RUNNING
                )
                .returning(TaskRun.id)
            )
        ).scalar_one()
    token = current_task_run_id.set(run_id)
    try:
        yield run_id
    finally:
        current_task_run_id.reset(token)

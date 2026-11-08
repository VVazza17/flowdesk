"""The task_runs ledger: one row per execution, written to Postgres (the source of truth).

Celery's own result backend is off; this table is how anything outside the worker learns that a task
ran. /health/ready reads it (app.services.health_probes).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.ops import TaskRun

# DECISION: §8 gives task_runs.status no vocabulary (IDEA-022), so this is the stopgap, TEXT with no
# CHECK like the other ops tables. `completed_at` is when the run finished, whichever way it ended;
# only status = 'succeeded' counts as a completion for health.
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

# error_message can carry connection strings or provider text. It is stored for the operator and is
# never selected into a /health/* body (assemble_data reports error_class only).
ERROR_MESSAGE_MAX_CHARS = 500


def utcnow() -> datetime:
    return datetime.now(UTC)


async def run_recorded(
    engine: AsyncEngine,
    task_name: str,
    body: Callable[[], Awaitable[None]],
    *,
    attempt: int = 1,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    """Record a run as running, execute `body`, then record how it ended. Re-raises a failure.

    Each write is its own transaction: the 'running' row must be visible while the body works, and a
    failure in the body must not roll back the record that it failed.

    # DECISION: a run whose process dies mid-body stays 'running' forever. Nothing sweeps those yet:
    # the canary is instant, so there is nothing long to wedge. The sweep belongs with the first
    # long task (backfill, M2 slice 10), using heartbeat_at, which is why the column is written.
    """
    started = clock()
    async with engine.begin() as conn:
        run_id = (
            await conn.execute(
                insert(TaskRun)
                .values(
                    task_name=task_name,
                    started_at=started,
                    heartbeat_at=started,
                    status=STATUS_RUNNING,
                    attempt=attempt,
                )
                .returning(TaskRun.id)
            )
        ).scalar_one()
    try:
        await body()
    except Exception as exc:
        async with engine.begin() as conn:
            await conn.execute(
                update(TaskRun)
                .where(TaskRun.id == run_id)
                .values(
                    status=STATUS_FAILED,
                    completed_at=clock(),
                    error_class=type(exc).__name__,
                    error_message=str(exc)[:ERROR_MESSAGE_MAX_CHARS],
                )
            )
        raise
    finished = clock()
    async with engine.begin() as conn:
        await conn.execute(
            update(TaskRun)
            .where(TaskRun.id == run_id)
            .values(status=STATUS_SUCCEEDED, completed_at=finished, heartbeat_at=finished)
        )

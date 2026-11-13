"""The task_runs ledger: one row per execution, written to Postgres (the source of truth).

Celery's own result backend is off; this table is how anything outside the worker learns that a task
ran. /health/ready reads it (app.services.health_probes).

`run_recorded` is the ONE wrapper every task goes through, and since M2 slice 5 part C it also:

* publishes its row id in `current_task_run_id`, which `run_feed` reads to link each ingestion run
  to
  the task that started it (migration 0008);
* keeps the row's `heartbeat_at` fresh from a background thread, so a long task is distinguishable
  from a dead one without any task body having to remember to beat;
* finishes the row only if it is still `running`. If the sweep failed it in the meantime, the
  sweep's verdict stands and the late result is recorded as a note, never as a status (BUILD-LOG,
  step 3 plan, part C).

A manual backfill (`python -m app.backfill`) is a task execution and goes through here too.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, insert, update
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.ops import TaskRun
from app.tasks.liveness import HEARTBEAT_INTERVAL_SECONDS
from app.tasks.names import STATUS_FAILED, STATUS_RUNNING, STATUS_SKIPPED, STATUS_SUCCEEDED

log = structlog.get_logger(__name__)

__all__ = [
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "STATUS_SKIPPED",
    "STATUS_SUCCEEDED",
    "current_task_run_id",
    "run_recorded",
    "utcnow",
]

# DECISION: §8 gives task_runs.status no vocabulary (IDEA-022), so the values in app.tasks.names
# are the stopgap, TEXT with no CHECK like the other ops tables. `completed_at` is when the run
# finished, whichever way it ended; only status = 'succeeded' counts as a completion for health.

# error_message can carry connection strings or provider text. It is stored for the operator and is
# never selected into a /health/* body (assemble_data reports error_class only).
ERROR_MESSAGE_MAX_CHARS = 500

# The id of the task_runs row of the task now executing, or None outside one. A ContextVar, not a
# global: it follows the awaited calls of one task and nothing else, so two tasks in one process
# (tests) cannot see each other's. `app.ingest.runner.run_feed` reads it and refuses to run when it
# is unset.
current_task_run_id: ContextVar[int | None] = ContextVar("current_task_run_id", default=None)


def utcnow() -> datetime:
    return datetime.now(UTC)


def after_sweep_note(what: str, when: datetime) -> str:
    """The text appended to a row the sweep already failed: the fact that the work did finish."""
    return f" | {what} after sweep at {when.isoformat()}"


async def _beat_once(engine: AsyncEngine, run_id: int, now: datetime) -> bool:
    """Write one heartbeat. False if the row is no longer `running` (swept, or finished)."""
    async with engine.begin() as conn:
        result = await conn.execute(
            update(TaskRun)
            .where(TaskRun.id == run_id, TaskRun.status == STATUS_RUNNING)
            .values(heartbeat_at=now)
        )
    return bool(result.rowcount)


def _beat_loop(
    url: URL,
    run_id: int,
    stop: threading.Event,
    interval: float,
    clock: Callable[[], datetime],
) -> None:
    """The heartbeat thread's body: its own event loop and its own short connection, so it neither
    shares the task's connection nor depends on the task's loop being free (a body stuck in a
    blocking call keeps beating, which proves the PROCESS is alive and can reach Postgres, not that
    the body is making progress: that is bounded by the per-request timeouts and, last, the hard
    task time limit, which kills the process and so stops the beats).

    It never raises into anything: a failed write is logged and tried again on the next tick, and a
    failing heartbeat only makes the row go not-fresh. It exits by itself when its row is no longer
    `running`, so a swept task's thread does not keep writing to a failed row.
    """

    async def go() -> None:
        engine = create_async_engine(
            url, poolclass=NullPool, connect_args={"timeout": 5, "command_timeout": 5}
        )
        try:
            while not stop.wait(interval):
                try:
                    alive = await _beat_once(engine, run_id, clock())
                except Exception as exc:
                    log.warning("heartbeat_write_failed", run_id=run_id, error=type(exc).__name__)
                    continue
                if not alive:
                    return
        finally:
            await engine.dispose()

    try:
        asyncio.run(go())
    except Exception as exc:  # a thread has no one to raise to
        log.warning("heartbeat_thread_failed", run_id=run_id, error=type(exc).__name__)


async def _finish_succeeded(engine: AsyncEngine, run_id: int, finished: datetime) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            update(TaskRun)
            .where(TaskRun.id == run_id, TaskRun.status == STATUS_RUNNING)
            .values(status=STATUS_SUCCEEDED, completed_at=finished, heartbeat_at=finished)
        )
        if result.rowcount:
            return
        # Zero rows: the sweep failed this row while the task was still working. Its verdict stands.
        await _note_late(conn, run_id, after_sweep_note("completed", finished))
    log.warning("task_completed_after_sweep", run_id=run_id)


async def _finish_failed(
    engine: AsyncEngine, run_id: int, finished: datetime, exc: Exception
) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            update(TaskRun)
            .where(TaskRun.id == run_id, TaskRun.status == STATUS_RUNNING)
            .values(
                status=STATUS_FAILED,
                completed_at=finished,
                error_class=type(exc).__name__,
                error_message=str(exc)[:ERROR_MESSAGE_MAX_CHARS],
            )
        )
        if result.rowcount:
            return
        await _note_late(
            conn, run_id, after_sweep_note("failed", finished) + f": {type(exc).__name__}"
        )
    log.warning("task_failed_after_sweep", run_id=run_id, error=type(exc).__name__)


async def _note_late(conn: AsyncConnection, run_id: int, note: str) -> None:
    await conn.execute(
        update(TaskRun)
        .where(TaskRun.id == run_id)
        .values(
            error_message=func.left(
                func.concat(func.coalesce(TaskRun.error_message, ""), note),
                ERROR_MESSAGE_MAX_CHARS,
            )
        )
    )


async def run_recorded(
    engine: AsyncEngine,
    task_name: str,
    body: Callable[[], Awaitable[None]],
    *,
    attempt: int = 1,
    clock: Callable[[], datetime] = utcnow,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Record a run as running, execute `body`, then record how it ended. Re-raises a failure.

    Each write is its own transaction: the 'running' row must be visible while the body works, and a
    failure in the body must not roll back the record that it failed.

    While the body runs, a daemon thread rewrites `heartbeat_at` every `heartbeat_interval`. It is
    stopped on every exit path by one `finally` that encloses everything after it starts: the body
    returning, raising `Exception` (including SoftTimeLimitExceeded), raising `BaseException`
    (CancelledError, SystemExit, KeyboardInterrupt), and the final status write itself raising. A
    path the `finally` cannot reach (the hard time limit's SIGKILL, kill -9, power loss) ends the
    thread with the process, which is the meaning wanted: no process, no beats.

    A run whose process dies mid-body stays 'running' until the sweep (app.tasks.sweep) fails it.
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
    token = current_task_run_id.set(run_id)
    stop = threading.Event()
    beat = threading.Thread(
        target=_beat_loop,
        args=(engine.url, run_id, stop, heartbeat_interval, clock),
        name="heartbeat",
        daemon=True,
    )
    beat.start()
    try:
        try:
            await body()
        except Exception as exc:
            await _finish_failed(engine, run_id, clock(), exc)
            raise
        await _finish_succeeded(engine, run_id, clock())
    finally:
        stop.set()
        beat.join(timeout=heartbeat_interval)
        if beat.is_alive():
            log.warning("heartbeat_thread_still_alive", run_id=run_id)
        current_task_run_id.reset(token)

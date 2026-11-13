"""The sweep (M2 slice 5, step 3, part C): fails the `running` rows that nothing is alive behind.

One rule for two tables, and the only test of liveness is `app.tasks.liveness.is_fresh` on a
`task_runs.heartbeat_at` (the SQL below uses the same `stale_cutoff`, so the rule is stated once):

* a `task_runs` row that is `running` and not fresh becomes `failed`, `error_class = 'Orphaned'`;
* an `ingestion_runs` row that is `running` and whose `task_run_id` does NOT point at a `task_runs`
  row that is both `running` and fresh becomes `failed`, `error = 'Orphaned...'`. A NULL
  `task_run_id` fails that test by itself, so it needs no special case: the rule is "not linked to a
  live task".

Both updates run in ONE transaction, `task_runs` first, so the two tables cannot disagree about the
same run for even one sweep: an ingestion run whose task was just failed is failed in the same pass.

WHAT THE SWEEP DOES NOT COVER, each a reasoned exclusion and not a gap:

* `backfill_runs`. A resumable backfill spans several task runs by design (M2 slice 10's gate is a
  worker restart mid-run, after which a new task picks the same row up). One `task_run_id` cannot
  express "the runs that worked on this", and a row that is `running` with no live task between a
  crash and the resume is CORRECT, not orphaned: the work is unfinished and waiting. The question
  there is "has anything picked this up recently", which depends on the resume design (who
  resumes, after how long), decided at slice 10. Sweeping it with the task rule would mark every
  crash-then-resume `failed`. A test pins that a `running` backfill_runs row with no live task is
  left alone. (Kyle, 2026-11-12.)
* the backup tasks (`app.tasks.names.UNSWEPT_TASKS`). The backup container writes those rows with
  psql, not through `run_recorded`, so nothing beats their heartbeat; a healthy dump or an SFTP copy
  longer than HEARTBEAT_FRESH_SECONDS would be failed as orphaned, and the script's unconditional
  final UPDATE would then overwrite the verdict. Found while building this; their staleness is read
  from /health/data and the external monitor's missed-run signal. Two writers of task_runs, two
  protocols: that script write is unconditional and run_recorded's is `AND status = 'running'`; if
  the backups ever join the sweep, the script's write becomes conditional in the same change.

A swept row whose task finishes later keeps the sweep's verdict; the late result is a note on the
row (app.tasks.runs, app.ingest.runner). The sweep's UPDATEs carry `status = 'running'` and the
freshness condition in the same statement, so a sweep and a completion that race are serialized by
the row lock: whichever commits first wins, and a beat that lands between the sweep's read and its
write saves the row.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

import structlog
from celery import shared_task
from sqlalchemy import exists, or_, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.models.ops import IngestionRun, TaskRun
from app.tasks.liveness import HEARTBEAT_FRESH_SECONDS, stale_cutoff
from app.tasks.names import STATUS_FAILED, STATUS_RUNNING, SWEEP, UNSWEPT_TASKS
from app.tasks.runs import run_recorded, utcnow

log = structlog.get_logger(__name__)

ORPHANED = "Orphaned"


@dataclass(frozen=True)
class Swept:
    task_runs: int
    ingestion_runs: int


async def sweep(engine: AsyncEngine, now: datetime) -> Swept:
    """Fail the orphaned rows as of `now`. Idempotent: a second call finds nothing."""
    cutoff = stale_cutoff(now)
    async with engine.begin() as conn:
        tasks = await conn.execute(
            update(TaskRun)
            .where(
                TaskRun.status == STATUS_RUNNING,
                TaskRun.task_name.not_in(UNSWEPT_TASKS),
                or_(TaskRun.heartbeat_at.is_(None), TaskRun.heartbeat_at < cutoff),
            )
            .values(
                status=STATUS_FAILED,
                completed_at=now,
                error_class=ORPHANED,
                error_message=f"no heartbeat within {HEARTBEAT_FRESH_SECONDS} s; swept",
            )
            .returning(TaskRun.id, TaskRun.task_name)
        )
        swept_tasks = list(tasks)
        alive = exists().where(
            TaskRun.id == IngestionRun.task_run_id,
            TaskRun.status == STATUS_RUNNING,
            TaskRun.heartbeat_at >= cutoff,
        )
        runs = await conn.execute(
            update(IngestionRun)
            .where(IngestionRun.status == STATUS_RUNNING, ~alive)
            .values(
                status=STATUS_FAILED,
                finished_at=now,
                error=f"{ORPHANED}: not linked to a live task; swept",
            )
            .returning(IngestionRun.id, IngestionRun.provider, IngestionRun.feed)
        )
        swept_runs = list(runs)
    for task_row in swept_tasks:
        log.warning("task_run_swept", run_id=task_row.id, task=task_row.task_name)
    for run_row in swept_runs:
        log.warning(
            "ingestion_run_swept", run_id=run_row.id, provider=run_row.provider, feed=run_row.feed
        )
    return Swept(len(swept_tasks), len(swept_runs))


async def run_sweep() -> None:
    url = settings.database_url()
    if url is None:
        raise RuntimeError("NotConfigured")
    # DECISION: a fresh engine per run, as the canary does (asyncpg connections belong to one loop).
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    try:

        async def body() -> None:
            await sweep(engine, utcnow())

        # The sweep is a task like any other: it has a row, so it shows in the ledger when it runs.
        await run_recorded(engine, SWEEP, body)
    finally:
        await engine.dispose()


@shared_task(name=SWEEP, ignore_result=True, max_retries=0)
def sweep_task() -> None:
    asyncio.run(run_sweep())

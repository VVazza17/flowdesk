"""Real Postgres/Redis probes for the health endpoints. Imported lazily (see app.deps) so the
/health/live path never pulls in sqlalchemy or redis."""

from datetime import datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.ops import BackfillRun, IngestionRun, ProvidersHealth, TaskRun
from app.services.health import FeedRow
from app.tasks.runs import STATUS_SUCCEEDED

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


class SqlRedisProbes:
    """Either dependency may be absent (URL unset); that surfaces as a failed probe, which the
    endpoints report as 'down', not as a crash."""

    def __init__(self, engine: AsyncEngine | None, redis: Redis | None) -> None:
        self._engine = engine
        self._redis = redis
        # DECISION: the head is read once, here, not per call. ScriptDirectory walks the versions
        # directory with blocking file I/O. Inside an `async def` that cannot be cancelled, so the
        # wait_for in run_check would not bound it. The head cannot change while the process runs.
        # get_probes() is lru_cached and sync, so this runs once per process, in FastAPI's
        # threadpool on the first health request that needs probes, outside any wait_for.
        # A failure is kept and raised from migration_revisions(): raising here would make
        # get_probes fail on every request (lru_cache does not cache exceptions) instead of
        # reporting "unknown".
        self._head: str | None = None
        self._head_error: Exception | None = None
        try:
            self._head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
        except Exception as exc:  # reported via the migrations check, same as before
            self._head_error = exc

    def _need_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("NotConfigured")
        return self._engine

    async def ping_postgres(self) -> None:
        async with self._need_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def ping_redis(self) -> None:
        if self._redis is None:
            raise RuntimeError("NotConfigured")
        await self._redis.ping()

    async def migration_revisions(self) -> tuple[str | None, str]:
        if self._head_error is not None:
            raise self._head_error
        head = self._head
        if head is None:
            raise RuntimeError("NoMigrationHead")
        async with self._need_engine().connect() as conn:
            exists = (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar()
            if exists is None:  # DB reachable but never migrated: "behind", not an error
                return None, head
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        return current, head

    async def last_task_success(self, task_name: str) -> datetime | None:
        # DECISION: no index on task_runs yet. At one canary a minute this scans a table that stays
        # small; add (task_name, status, completed_at) when ingestion tasks make it big.
        async with self._need_engine().connect() as conn:
            newest: datetime | None = (
                await conn.execute(
                    select(func.max(TaskRun.completed_at)).where(
                        TaskRun.task_name == task_name, TaskRun.status == STATUS_SUCCEEDED
                    )
                )
            ).scalar()
            return newest

    async def provider_rows(self) -> list[FeedRow]:
        async with self._need_engine().connect() as conn:
            result = await conn.execute(
                select(ProvidersHealth).order_by(ProvidersHealth.provider, ProvidersHealth.feed)
            )
            return [
                FeedRow(r.provider, r.feed, r.last_success_at, r.last_error_at, r.last_error)
                for r in result
            ]

    async def data_state(self) -> dict[str, list[dict[str, Any]]]:
        """Latest run per (provider, feed), per feed (backfills) and per task name."""
        async with self._need_engine().connect() as conn:
            ing = await conn.execute(
                select(IngestionRun)
                .distinct(IngestionRun.provider, IngestionRun.feed)
                .order_by(IngestionRun.provider, IngestionRun.feed, IngestionRun.started_at.desc())
            )
            bf = await conn.execute(
                select(BackfillRun)
                .distinct(BackfillRun.feed)
                .order_by(BackfillRun.feed, BackfillRun.started_at.desc())
            )
            tk = await conn.execute(
                select(TaskRun)
                .distinct(TaskRun.task_name)
                .order_by(TaskRun.task_name, TaskRun.started_at.desc())
            )
            return {
                "ingestion": [
                    {
                        "provider": r.provider,
                        "feed": r.feed,
                        "status": r.status,
                        "started_at": _iso(r.started_at),
                        "finished_at": _iso(r.finished_at),
                        "rows_read": r.rows_read,
                        "rows_written": r.rows_written,
                        "error": r.error,
                    }
                    for r in ing
                ],
                "backfills": [
                    {
                        "feed": r.feed,
                        "status": r.status,
                        "total_units": r.total_units,
                        "completed_units": r.completed_units,
                        "current_unit": r.current_unit,
                        "started_at": _iso(r.started_at),
                        "updated_at": _iso(r.updated_at),
                    }
                    for r in bf
                ],
                "tasks": [
                    {
                        "task_name": r.task_name,
                        "status": r.status,
                        "started_at": _iso(r.started_at),
                        "heartbeat_at": _iso(r.heartbeat_at),
                        "completed_at": _iso(r.completed_at),
                        "attempt": r.attempt,
                        "error_class": r.error_class,
                    }
                    for r in tk
                ],
            }

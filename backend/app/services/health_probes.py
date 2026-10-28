"""Real Postgres/Redis probes for the health endpoints. Imported lazily (see app.deps) so the
/health/live path never pulls in sqlalchemy or redis."""
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.ops import BackfillRun, IngestionRun, ProvidersHealth, TaskRun
from app.services.health import FeedRow

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


class SqlRedisProbes:
    """Either dependency may be absent (URL unset); that surfaces as a failed probe, which the
    endpoints report as 'down', not as a crash."""

    def __init__(self, engine: AsyncEngine | None, redis: Redis | None) -> None:
        self._engine = engine
        self._redis = redis

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
        await self._redis.ping()  # type: ignore[misc]

    async def migration_revisions(self) -> tuple[str | None, str]:
        head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
        if head is None:
            raise RuntimeError("NoMigrationHead")
        async with self._need_engine().connect() as conn:
            exists = (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar()
            if exists is None:  # DB reachable but never migrated: "behind", not an error
                return None, head
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        return current, head

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
                        "provider": r.provider, "feed": r.feed, "status": r.status,
                        "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
                        "rows_read": r.rows_read, "rows_written": r.rows_written,
                        "error": r.error,
                    }
                    for r in ing
                ],
                "backfills": [
                    {
                        "feed": r.feed, "status": r.status, "total_units": r.total_units,
                        "completed_units": r.completed_units, "current_unit": r.current_unit,
                        "started_at": _iso(r.started_at), "updated_at": _iso(r.updated_at),
                    }
                    for r in bf
                ],
                "tasks": [
                    {
                        "task_name": r.task_name, "status": r.status,
                        "started_at": _iso(r.started_at), "heartbeat_at": _iso(r.heartbeat_at),
                        "completed_at": _iso(r.completed_at), "attempt": r.attempt,
                        "error_class": r.error_class,
                    }
                    for r in tk
                ],
            }

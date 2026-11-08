"""M2 slice 1: the task_runs ledger and the probe that reads it, on a migrated scratch database.
Needs DATABASE_URL (see test_migrations.py)."""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.services.health_probes import SqlRedisProbes
from app.tasks.canary import run_canary
from app.tasks.names import CANARY
from app.tasks.runs import STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED, run_recorded

ADMIN_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture
def scratch_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"flowdesk_task_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        # Inside the try: a migration that raises must not strand the database it ran against.
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


async def _rows(engine: AsyncEngine) -> list[tuple[str, str, bool, str | None, str | None]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT task_name, status, completed_at IS NOT NULL, error_class, error_message"
                " FROM task_runs ORDER BY id"
            )
        )
        return [(r[0], r[1], r[2], r[3], r[4]) for r in result]


def test_a_successful_run_is_recorded_and_the_probe_sees_its_completion(scratch_url: str) -> None:
    async def go() -> tuple[datetime | None, datetime | None, list[tuple[str, ...]]]:
        engine = create_async_engine(scratch_url)
        probes = SqlRedisProbes(engine, None)
        try:
            before = await probes.last_task_success(CANARY)
            seen_while_running: list[str] = []

            async def body() -> None:
                # The 'running' row is committed and visible while the body works.
                seen_while_running.extend(r[1] for r in await _rows(engine))

            await run_recorded(engine, CANARY, body)
            return (
                before,
                await probes.last_task_success(CANARY),
                [
                    tuple(seen_while_running),
                    *[(r[1], str(r[2])) for r in await _rows(engine)],
                ],
            )
        finally:
            await engine.dispose()

    before, after, trail = asyncio.run(go())
    assert before is None
    assert after is not None
    assert trail[0] == (STATUS_RUNNING,)
    assert trail[1] == (STATUS_SUCCEEDED, "True")


def test_a_failed_run_is_recorded_reraised_and_is_not_a_completion(scratch_url: str) -> None:
    async def go() -> tuple[datetime | None, list[tuple[str, str, bool, str | None, str | None]]]:
        engine = create_async_engine(scratch_url)
        probes = SqlRedisProbes(engine, None)
        try:

            async def body() -> None:
                raise ValueError("provider said no")

            with pytest.raises(ValueError, match="provider said no"):
                await run_recorded(engine, CANARY, body)
            return await probes.last_task_success(CANARY), await _rows(engine)
        finally:
            await engine.dispose()

    last, rows = asyncio.run(go())
    assert last is None  # a failure is not evidence that the worker works end to end
    assert rows == [(CANARY, STATUS_FAILED, True, "ValueError", "provider said no")]


def test_the_probe_returns_the_newest_success_and_ignores_other_tasks(scratch_url: str) -> None:
    older = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    newer = older + timedelta(minutes=5)

    async def go() -> datetime | None:
        engine = create_async_engine(scratch_url)
        try:
            for name, status, when in (
                (CANARY, STATUS_SUCCEEDED, older),
                (CANARY, STATUS_SUCCEEDED, newer),
                (CANARY, STATUS_FAILED, newer + timedelta(minutes=5)),
                ("some.other.task", STATUS_SUCCEEDED, newer + timedelta(minutes=9)),
            ):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO task_runs (task_name, started_at, completed_at, status)"
                            " VALUES (:n, :t, :t, :s)"
                        ),
                        {"n": name, "t": when, "s": status},
                    )
            return await SqlRedisProbes(engine, None).last_task_success(CANARY)
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == newer


def test_the_canary_task_body_writes_a_succeeded_row(scratch_url: str) -> None:
    asyncio.run(run_canary())

    async def read() -> list[tuple[str, str, bool, str | None, str | None]]:
        engine = create_async_engine(scratch_url)
        try:
            return await _rows(engine)
        finally:
            await engine.dispose()

    assert asyncio.run(read()) == [(CANARY, STATUS_SUCCEEDED, True, None, None)]

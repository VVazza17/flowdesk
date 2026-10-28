"""Migration smoke: needs a reachable Postgres/TimescaleDB, skipped otherwise.

Run against the compose database (a scratch database is created and dropped, the real one
is never touched):
    DATABASE_URL=postgresql+asyncpg://flowdesk:<pw>@127.0.0.1:5432/flowdesk pytest tests/test_migrations.py
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def _scalar(url: str, sql: str):
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = (await conn.execute(text(sql))).scalar()
    await engine.dispose()
    return value


@pytest.fixture
def scratch_url(monkeypatch):
    name = f"flowdesk_mig_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)  # env.py reads this
    yield url
    asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def test_upgrade_head_on_empty_db(scratch_url):
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert asyncio.run(_scalar(scratch_url, "SELECT version_num FROM alembic_version")) == head
    assert asyncio.run(
        _scalar(scratch_url, "SELECT extname FROM pg_extension WHERE extname='timescaledb'")
    ) == "timescaledb"


def test_downgrade_then_upgrade_round_trips(scratch_url):
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_single_head():
    cfg = Config(str(ALEMBIC_INI))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


def test_models_match_migrations(scratch_url):
    # Fails once a model changes without a migration (meaningful from slice 4 on).
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    command.check(cfg)

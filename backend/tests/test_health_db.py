"""Slice 6: the real SQL behind /health/* against a migrated scratch database.
Needs DATABASE_URL (see test_migrations.py)."""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.deps import get_probes
from app.main import create_app
from app.services.health_probes import SqlRedisProbes

ADMIN_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def _exec(url: str, sql: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture
def scratch_url(monkeypatch):
    name = f"flowdesk_health_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    yield url
    asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _client(url: str) -> TestClient:
    # Redis points at a closed port: these endpoints must not care.
    probes = SqlRedisProbes(
        create_async_engine(url), Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=1)
    )
    app = create_app()
    app.dependency_overrides[get_probes] = lambda: probes
    # Callers use `with _client(...)`: one open client = one event loop, like uvicorn. Without
    # it each request gets a fresh loop and pooled asyncpg connections break across them.
    return TestClient(app)


def test_never_migrated_database_is_not_ready_and_data_is_unavailable(scratch_url) -> None:
    with _client(scratch_url) as c:
        ready = c.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["checks"]["migrations"]["status"] == "behind"
        assert ready.json()["checks"]["migrations"]["current"] is None
        # Tables do not exist: a failed read, reported as such, not a 500.
        assert c.get("/health/providers").status_code == 503
        assert c.get("/health/data").status_code == 503


def test_migrated_database_empty_tables(scratch_url) -> None:
    with _client(scratch_url) as c:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        ready = c.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "degraded"  # Redis is deliberately unreachable
        assert ready.json()["checks"]["migrations"]["status"] == "ok"
        assert c.get("/health/providers").json() == {"status": "no_feeds", "feeds": []}
        assert c.get("/health/data").json()["status"] == "no_runs"


def test_real_rows_come_back(scratch_url) -> None:
    with _client(scratch_url) as c:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        asyncio.run(_exec(scratch_url, "INSERT INTO providers_health (provider, feed) VALUES ('p','f')"))
        asyncio.run(_exec(scratch_url, "INSERT INTO backfill_runs (feed, status, started_at, "
                                       "total_units, completed_units) VALUES ('f','running',now(),10,4)"))
        # Two ingestion runs for one feed: only the latest is reported.
        asyncio.run(_exec(scratch_url, "INSERT INTO ingestion_runs (provider, feed, started_at, status)"
                                       " VALUES ('p','f', now() - interval '1 day', 'old'),"
                                       " ('p','f', now(), 'new')"))

        prov = c.get("/health/providers").json()
        assert prov["status"] == "pending"
        assert prov["feeds"][0]["status"] == "never_succeeded"

        data = c.get("/health/data").json()
        assert data["status"] == "reported"
        assert [r["status"] for r in data["ingestion"]] == ["new"]
        assert data["backfills"][0]["completed_units"] == 4

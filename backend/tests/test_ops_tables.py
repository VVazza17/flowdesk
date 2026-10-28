"""Slice 4: the §8 ops tables exist with the specified columns. Needs DATABASE_URL (see
test_migrations.py); a scratch database is created and dropped."""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# §8 lines 778-802: table -> [(column, data_type)], and the primary key. Column names, order
# and PKs are from the spec. The data_type values are the slice-4 decisions (IDEA-022), NOT spec
# requirements — §8 does not state them, so a pass here is not spec conformance on types.
SPEC = {
    "providers_health": (
        [("provider", "text"), ("feed", "text"),
         ("last_success_at", "timestamp with time zone"),
         ("last_error_at", "timestamp with time zone"), ("last_error", "text")],
        ["provider", "feed"],
    ),
    "ingestion_runs": (
        [("id", "bigint"), ("provider", "text"), ("feed", "text"),
         ("started_at", "timestamp with time zone"),
         ("finished_at", "timestamp with time zone"), ("status", "text"),
         ("rows_read", "integer"), ("rows_written", "integer"), ("error", "text")],
        ["id"],
    ),
    "task_runs": (
        [("id", "bigint"), ("task_name", "text"),
         ("scheduled_for", "timestamp with time zone"),
         ("started_at", "timestamp with time zone"),
         ("heartbeat_at", "timestamp with time zone"),
         ("completed_at", "timestamp with time zone"), ("status", "text"),
         ("attempt", "integer"), ("error_class", "text"), ("error_message", "text"),
         ("input_watermark", "timestamp with time zone"),
         ("output_watermark", "timestamp with time zone")],
        ["id"],
    ),
    "backfill_runs": (
        [("id", "bigint"), ("feed", "text"), ("total_units", "integer"),
         ("completed_units", "integer"), ("current_unit", "text"), ("status", "text"),
         ("started_at", "timestamp with time zone"),
         ("updated_at", "timestamp with time zone")],
        ["id"],
    ),
}

INSERTS = {
    "providers_health": "INSERT INTO providers_health (provider, feed) VALUES ('p', 'f')",
    "ingestion_runs": "INSERT INTO ingestion_runs (provider, feed, started_at, status) "
                      "VALUES ('p', 'f', now(), 'x')",
    "task_runs": "INSERT INTO task_runs (task_name, started_at, status) VALUES ('t', now(), 'x')",
    "backfill_runs": "INSERT INTO backfill_runs (feed, status, started_at) "
                     "VALUES ('f', 'x', now())",
}


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def _rows(url: str, sql: str, **params):
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.all() if result.returns_rows else []
    await engine.dispose()
    return rows


@pytest.fixture
def migrated_url(monkeypatch):
    name = f"flowdesk_ops_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)  # env.py reads this
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    yield url
    asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.mark.parametrize("table", SPEC)
def test_columns_and_primary_key_match_spec(migrated_url, table):
    columns, pk = SPEC[table]
    got = asyncio.run(_rows(
        migrated_url,
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = :t ORDER BY ordinal_position", t=table))
    assert [tuple(r) for r in got] == columns
    pk_got = asyncio.run(_rows(
        migrated_url,
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
        "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)", t=table))
    assert [r[0] for r in pk_got] == pk


@pytest.mark.parametrize("table", INSERTS)
def test_minimal_row_inserts(migrated_url, table):
    asyncio.run(_rows(migrated_url, INSERTS[table]))
    assert asyncio.run(_rows(migrated_url, f"SELECT count(*) FROM {table}"))[0][0] == 1


def test_downgrade_removes_tables_and_reupgrade_restores_them(migrated_url):
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "0001")
    left = asyncio.run(_rows(
        migrated_url,
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)",
        names=list(SPEC)))
    assert left == []
    command.upgrade(cfg, "head")
    assert len(asyncio.run(_rows(
        migrated_url,
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)",
        names=list(SPEC)))) == 4

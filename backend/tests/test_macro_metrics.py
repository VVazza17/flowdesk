"""M2 slice 5, step 1: the macro_metrics table (migration 0005) and the provenance vocabulary it
is the first table built to (IDEA-034 / IDEA-038, settled 2026-11-11).

Needs DATABASE_URL (see test_migrations.py); a scratch database is created and dropped per test.
These pin the SHAPE: names, meanings and types, and the rules ADR-0003 puts in the schema (vintages
are rows, never overwritten; a "." gap is a stored NULL). Ingesting into it is step 3, reading it
point-in-time is step 4.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Row, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# (column, data_type, is_nullable) in table order. The vocabulary: effective_at is the period the
# value describes (a DATE), available_at when it became knowable, ingested_at when we wrote it,
# revision 0 for the first print, source the provider id.
COLUMNS = [
    ("metric", "text", "NO"),
    ("effective_at", "date", "NO"),
    ("revision", "integer", "NO"),
    ("value", "numeric", "YES"),
    ("available_at", "timestamp with time zone", "NO"),
    ("ingested_at", "timestamp with time zone", "NO"),
    ("source", "text", "NO"),
    ("ingestion_run_id", "bigint", "YES"),
]

AVAILABLE = datetime(2025, 4, 30, 12, 30, tzinfo=UTC)


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def _rows(url: str, sql: str, **params: Any) -> Sequence[Row[Any]]:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.all() if result.returns_rows else []
    await engine.dispose()
    return rows


def _run(url: str, sql: str, **params: Any) -> Sequence[Row[Any]]:
    return asyncio.run(_rows(url, sql, **params))


@pytest.fixture
def migrated_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"flowdesk_macro_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)  # env.py reads this
    # try covers the upgrade too: a raising migration never reaches the yield.
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _insert(
    url: str,
    metric: str = "GDPC1",
    effective_at: date = date(2025, 1, 1),
    revision: int | None = 0,
    value: Decimal | None = Decimal("23526.085"),
    available_at: datetime = AVAILABLE,
    source: str = "fred",
    ingestion_run_id: int | None = None,
) -> None:
    cols = "metric, effective_at, value, available_at, source, ingestion_run_id"
    vals = ":m, :e, :v, :a, :s, :r"
    params: dict[str, Any] = {
        "m": metric,
        "e": effective_at,
        "v": value,
        "a": available_at,
        "s": source,
        "r": ingestion_run_id,
    }
    if revision is not None:
        cols, vals, params["rev"] = cols + ", revision", vals + ", :rev", revision
    _run(url, f"INSERT INTO macro_metrics ({cols}) VALUES ({vals})", **params)


def test_columns_types_and_primary_key(migrated_url: str) -> None:
    got = _run(
        migrated_url,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'macro_metrics' ORDER BY ordinal_position",
    )
    assert [tuple(r) for r in got] == COLUMNS
    pk = _run(
        migrated_url,
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
        "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = CAST('macro_metrics' AS regclass) AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)",
    )
    # Never effective_at alone (§8): the revision is part of the key, so a later value is a new row.
    assert [r[0] for r in pk] == ["metric", "effective_at", "revision"]


def test_it_is_a_plain_table_not_a_hypertable(migrated_url: str) -> None:
    # DECISION recorded in the migration: ~17 series, at most tens of thousands of rows each, and §8
    # does not mark it with the hypertable symbol. Revisit only with a measured reason.
    n = _run(
        migrated_url,
        "SELECT count(*) FROM timescaledb_information.hypertables "
        "WHERE hypertable_name = 'macro_metrics'",
    )[0][0]
    assert n == 0


def test_revision_and_ingested_at_default(migrated_url: str) -> None:
    _insert(migrated_url, revision=None)
    revision, ingested_at = _run(migrated_url, "SELECT revision, ingested_at FROM macro_metrics")[0]
    assert revision == 0
    assert abs((datetime.now(UTC) - ingested_at).total_seconds()) < 60


def test_effective_at_is_a_date_and_cannot_shift_with_the_session_time_zone(
    migrated_url: str,
) -> None:
    # The failure DATE exists to rule out: a Q1 observation stored as 2025-01-01T00:00Z reads as
    # 2024-12-31 19:00 in Toronto. A DATE has no time of day to shift.
    _insert(migrated_url, effective_at=date(2025, 1, 1))
    engine = create_async_engine(migrated_url)

    async def read() -> Any:
        async with engine.begin() as conn:
            await conn.execute(text("SET TIME ZONE 'America/Toronto'"))
            return (await conn.execute(text("SELECT effective_at FROM macro_metrics"))).scalar()

    got = asyncio.run(read())
    asyncio.run(engine.dispose())
    assert got == date(2025, 1, 1)


def test_a_revision_is_a_new_row_and_the_first_print_survives(migrated_url: str) -> None:
    # ADR-0003 decision 1: revisions never overwrite an earlier vintage. 23,526.085 is what an
    # observer on 2025-05-01 saw for GDPC1's Q1-2025; 23,548.210 is what it reads today.
    _insert(migrated_url, revision=0, value=Decimal("23526.085"))
    _insert(
        migrated_url,
        revision=1,
        value=Decimal("23548.210"),
        available_at=datetime(2026, 7, 30, 12, 30, tzinfo=UTC),
    )
    rows = _run(
        migrated_url,
        "SELECT revision, value, available_at FROM macro_metrics ORDER BY revision",
    )
    assert [(r[0], r[1]) for r in rows] == [(0, Decimal("23526.085")), (1, Decimal("23548.210"))]
    assert rows[0][2] < rows[1][2]


def test_the_same_vintage_cannot_be_written_twice(migrated_url: str) -> None:
    _insert(migrated_url, revision=0)
    with pytest.raises(IntegrityError):
        _insert(migrated_url, revision=0, value=Decimal("1"))
    # And the stored value is untouched by the attempt.
    assert _run(migrated_url, "SELECT value FROM macro_metrics")[0][0] == Decimal("23526.085")


def test_a_missing_observation_is_a_stored_null_never_zero(migrated_url: str) -> None:
    # ADR-0003 decision 7: FRED's "." is a genuine gap. It is a row with value NULL, so the period
    # still exists in the store and nothing can mistake it for 0.0 or forward-fill over it.
    _insert(migrated_url, metric="CPIAUCSL", effective_at=date(2025, 10, 1), value=None)
    row = _run(migrated_url, "SELECT value, value IS NULL FROM macro_metrics")[0]
    assert row[0] is None and row[1] is True


def test_numeric_values_round_trip_exactly(migrated_url: str) -> None:
    _insert(migrated_url, value=Decimal("23548.210"))
    assert _run(migrated_url, "SELECT value FROM macro_metrics")[0][0] == Decimal("23548.210")


@pytest.mark.parametrize("missing", ["metric", "effective_at", "available_at", "source"])
def test_the_identifying_and_provenance_columns_are_required(
    migrated_url: str, missing: str
) -> None:
    cols = {
        "metric": "'GDPC1'",
        "effective_at": "'2025-01-01'",
        "available_at": "now()",
        "source": "'fred'",
    }
    del cols[missing]
    names = ", ".join(cols)
    with pytest.raises(IntegrityError):
        _run(
            migrated_url, f"INSERT INTO macro_metrics ({names}) VALUES ({', '.join(cols.values())})"
        )


def test_ingestion_run_id_is_a_real_reference_and_the_observation_outlives_its_run(
    migrated_url: str,
) -> None:
    with pytest.raises(IntegrityError):
        _insert(migrated_url, ingestion_run_id=999_999)
    run_id = _run(
        migrated_url,
        "INSERT INTO ingestion_runs (provider, feed, started_at, status) "
        "VALUES ('fred', 'GDPC1', now(), 'succeeded') RETURNING id",
    )[0][0]
    _insert(migrated_url, ingestion_run_id=run_id)
    assert _run(migrated_url, "SELECT ingestion_run_id FROM macro_metrics")[0][0] == run_id
    # DECISION: ON DELETE SET NULL. A purged run ledger row must not take the observation with it,
    # and must not be blocked by it. Only the pointer goes; the vintage's value is not touched.
    _run(migrated_url, "DELETE FROM ingestion_runs WHERE id = :i", i=run_id)
    row = _run(migrated_url, "SELECT ingestion_run_id, value FROM macro_metrics")[0]
    assert row[0] is None and row[1] == Decimal("23526.085")


def test_downgrade_drops_the_table_and_upgrade_restores_it(migrated_url: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "0004")
    assert _run(migrated_url, "SELECT to_regclass('macro_metrics')")[0][0] is None
    command.upgrade(cfg, "head")
    assert _run(migrated_url, "SELECT to_regclass('macro_metrics')")[0][0] == "macro_metrics"


# --- Vintages are immutable: the database refuses, not just the ingest code ---------------------
# ADR-0003 decision 1: a revision is a new row, an earlier vintage is never overwritten. The
# primary key stops a duplicate vintage but not an UPDATE, and an overwritten vintage is the one
# failure here that is both catastrophic and undetectable. So BEFORE UPDATE / DELETE /
# TRUNCATE triggers refuse (Kyle,
# 2026-11-11). docs/RUNBOOK-VINTAGE-REPAIR.md is the one sanctioned way around them.

TRIGGERS = ["macro_metrics_no_delete", "macro_metrics_no_truncate", "macro_metrics_no_update"]


def _trigger_states(url: str) -> dict[str, str]:
    rows = _run(
        url,
        "SELECT tgname, tgenabled::text FROM pg_trigger "
        "WHERE tgrelid = CAST('macro_metrics' AS regclass) AND NOT tgisinternal",
    )
    return {r[0]: r[1] for r in rows}


def test_the_immutability_triggers_exist_and_are_enabled(migrated_url: str) -> None:
    # 'O' = enabled. A repair that forgot to re-enable would show here, and in the runbook's check.
    assert _trigger_states(migrated_url) == {name: "O" for name in TRIGGERS}


@pytest.mark.parametrize(
    ("column", "new_value"),
    [
        ("metric", "'OTHER'"),
        ("effective_at", "'2025-04-01'"),
        ("revision", "5"),
        ("value", "1"),
        ("available_at", "now()"),
        ("ingested_at", "now()"),
        ("source", "'other'"),
    ],
)
def test_no_stored_column_of_a_vintage_can_be_updated(
    migrated_url: str, column: str, new_value: str
) -> None:
    _insert(migrated_url)
    with pytest.raises(IntegrityError, match="immutable"):
        _run(migrated_url, f"UPDATE macro_metrics SET {column} = {new_value}")
    # The refused statement changed nothing.
    row = _run(migrated_url, "SELECT metric, value, revision, source FROM macro_metrics")[0]
    assert tuple(row) == ("GDPC1", Decimal("23526.085"), 0, "fred")


def test_a_vintage_cannot_be_deleted_or_truncated(migrated_url: str) -> None:
    _insert(migrated_url)
    with pytest.raises(IntegrityError, match="immutable"):
        _run(migrated_url, "DELETE FROM macro_metrics")
    with pytest.raises(IntegrityError, match="immutable"):
        _run(migrated_url, "TRUNCATE macro_metrics")
    assert _run(migrated_url, "SELECT count(*) FROM macro_metrics")[0][0] == 1


def test_the_run_pointer_may_only_be_cleared_never_repointed(migrated_url: str) -> None:
    # The one carve-out: ON DELETE SET NULL is itself an UPDATE, and without this a purged
    # ingestion_runs row could not be deleted while an observation pointed at it. The
    # provenance of a vintage may be forgotten; it may not be rewritten to claim another run.
    run_a, run_b = (
        _run(
            migrated_url,
            "INSERT INTO ingestion_runs (provider, feed, started_at, status) "
            "VALUES ('fred', 'GDPC1', now(), 'succeeded') RETURNING id",
        )[0][0]
        for _ in range(2)
    )
    _insert(migrated_url, ingestion_run_id=run_a)
    with pytest.raises(IntegrityError, match="immutable"):
        _run(migrated_url, "UPDATE macro_metrics SET ingestion_run_id = :b", b=run_b)
    _run(migrated_url, "UPDATE macro_metrics SET ingestion_run_id = NULL")
    assert _run(migrated_url, "SELECT ingestion_run_id FROM macro_metrics")[0][0] is None


def test_the_documented_repair_procedure_works_and_leaves_the_triggers_enabled(
    migrated_url: str,
) -> None:
    # docs/RUNBOOK-VINTAGE-REPAIR.md: disable and re-enable inside ONE transaction. DDL is
    # transactional in Postgres, so the triggers are off only for the repair and a failed repair
    # cannot leave them off.
    _insert(migrated_url)

    async def repair(*, fail: bool) -> None:
        # One engine per asyncio.run(): asyncpg connections belong to the loop that opened them.
        engine = create_async_engine(migrated_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("ALTER TABLE macro_metrics DISABLE TRIGGER USER"))
                await conn.execute(text("UPDATE macro_metrics SET value = 1"))
                if fail:
                    raise RuntimeError("repair abandoned")
                await conn.execute(text("ALTER TABLE macro_metrics ENABLE TRIGGER USER"))
        finally:
            await engine.dispose()

    with pytest.raises(RuntimeError):
        asyncio.run(repair(fail=True))
    # Rolled back: the value is untouched and the triggers never stayed disabled.
    assert _run(migrated_url, "SELECT value FROM macro_metrics")[0][0] == Decimal("23526.085")
    assert _trigger_states(migrated_url) == {name: "O" for name in TRIGGERS}

    asyncio.run(repair(fail=False))
    assert _run(migrated_url, "SELECT value FROM macro_metrics")[0][0] == Decimal("1")
    assert _trigger_states(migrated_url) == {name: "O" for name in TRIGGERS}
    with pytest.raises(IntegrityError, match="immutable"):
        _run(migrated_url, "UPDATE macro_metrics SET value = 2")

"""M2 slice 6, step 2: the price tables (migrations 0010, 0011) and universe_membership.close_reason
(0012). Needs DATABASE_URL; each test gets a fresh migrated database (conftest `scratch_url`).

What is pinned here: the SHAPE (columns, keys, hypertables and chunk intervals, comments on every
column), every CHECK, the insert-only triggers, and rule 9 for every key: in each keyed test a
SECOND symbol's rows could win every ordering the key applies (a later instant, a higher revision,
larger values), and the test asserts that symbol is exactly as it was. Each of these is
mutation-checked by infra/mutation-m2-slice6-step2.sh (the migration is broken in a copy and this
file must fail).
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

# A session open (2026-11-16 09:30 ET) and its settle points.
OPEN = datetime(2026, 11, 16, 14, 30, tzinfo=UTC)
DAILY_SETTLE = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)  # 09:00 ET on D+2
INTRADAY_SETTLE = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)  # 09:00 ET on D+1
LATER = OPEN + timedelta(days=1)

BAR_TABLES = ["bars_daily", "bars_intraday"]
INSERT_ONLY = ["bars_daily", "bars_intraday", "bar_total_return", "splits"]

TZ = "timestamp with time zone"
BAR_TAIL = [
    ("o_raw", "numeric", "NO"),
    ("h_raw", "numeric", "NO"),
    ("l_raw", "numeric", "NO"),
    ("c_raw", "numeric", "NO"),
    ("v_raw", "bigint", "NO"),
    ("raw_basis", "text", "NO"),
    ("source", "text", "NO"),
    ("feed", "text", "NO"),
    ("venue_scope", "text", "NO"),
    ("available_at", TZ, "NO"),
    ("ingested_at", TZ, "NO"),
    ("ingestion_run_id", "bigint", "YES"),
]
COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "bars_daily": [("symbol_id", "bigint", "NO"), ("effective_at", TZ, "NO"), *BAR_TAIL],
    "bars_intraday": [
        ("symbol_id", "bigint", "NO"),
        ("interval", "text", "NO"),
        ("effective_at", TZ, "NO"),
        *BAR_TAIL,
    ],
    "bar_total_return": [
        ("symbol_id", "bigint", "NO"),
        ("effective_at", TZ, "NO"),
        ("revision", "integer", "NO"),
        ("value", "numeric", "NO"),
        ("available_at", TZ, "NO"),
        ("ingested_at", TZ, "NO"),
        ("source", "text", "NO"),
        ("ingestion_run_id", "bigint", "YES"),
    ],
    "splits": [
        ("symbol_id", "bigint", "NO"),
        ("effective_at", "date", "NO"),
        ("ratio", "numeric", "NO"),
        ("available_at", TZ, "NO"),
        ("ingested_at", TZ, "NO"),
        ("source", "text", "NO"),
        ("ingestion_run_id", "bigint", "YES"),
    ],
    "bar_gaps": [
        ("id", "bigint", "NO"),
        ("symbol_id", "bigint", "NO"),
        ("interval", "text", "NO"),
        ("missing_from", TZ, "NO"),
        ("missing_to", TZ, "NO"),
        ("recorded_at", TZ, "NO"),
        ("ingestion_run_id", "bigint", "YES"),
        ("resolved_at", TZ, "YES"),
    ],
}
PRIMARY_KEYS = {
    "bars_daily": ["symbol_id", "effective_at"],
    "bars_intraday": ["symbol_id", "interval", "effective_at"],
    "bar_total_return": ["symbol_id", "effective_at", "revision"],
    "splits": ["symbol_id", "effective_at"],
    "bar_gaps": ["id"],
}
TIME_COLUMNS = {
    "bars_daily": ["effective_at", "available_at", "ingested_at"],
    "bars_intraday": ["effective_at", "available_at", "ingested_at"],
    "bar_total_return": ["effective_at", "available_at", "ingested_at"],
    "splits": ["effective_at", "available_at", "ingested_at"],
    "bar_gaps": ["missing_from", "missing_to", "recorded_at", "resolved_at"],
}


# --- helpers --------------------------------------------------------------------------------------


async def _rows(url: str, sql: str, **params: Any) -> Sequence[Row[Any]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return result.all() if result.returns_rows else []
    finally:
        await engine.dispose()


def run(url: str, sql: str, **params: Any) -> Sequence[Row[Any]]:
    return asyncio.run(_rows(url, sql, **params))


def symbol(url: str, ticker: str) -> int:
    return int(run(url, "INSERT INTO symbols (ticker) VALUES (:t) RETURNING id", t=ticker)[0][0])


def ingestion_run(url: str) -> int:
    return int(
        run(
            url,
            "INSERT INTO ingestion_runs (provider, feed, started_at, status) "
            "VALUES ('yfinance', 'bars', now(), 'succeeded') RETURNING id",
        )[0][0]
    )


def bar(table: str, symbol_id: int, effective_at: datetime = OPEN, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol_id": symbol_id,
        "effective_at": effective_at,
        "o_raw": Decimal("749.6599731445312"),
        "h_raw": Decimal("749.82"),
        "l_raw": Decimal("746.6506958007812"),
        "c_raw": Decimal("749.34"),
        "v_raw": 4_667_316,
        "raw_basis": "live",
        "source": "yfinance",
        "feed": "poll",
        "venue_scope": "provider_default",
        # Each bar's own settle point, the same distance after its open as the reference bar's.
        "available_at": effective_at
        + ((DAILY_SETTLE if table == "bars_daily" else INTRADAY_SETTLE) - OPEN),
        "ingestion_run_id": None,
    }
    if table == "bars_intraday":
        row["interval"] = "15m"
    row.update(over)
    return row


def insert(url: str, table: str, row: dict[str, Any], on_conflict_nothing: bool = False) -> int:
    cols = ", ".join(f'"{c}"' for c in row)
    vals = ", ".join(f":{c}" for c in row)
    tail = " ON CONFLICT DO NOTHING" if on_conflict_nothing else ""
    sql = f"WITH i AS (INSERT INTO {table} ({cols}) VALUES ({vals}){tail} RETURNING 1) "
    return int(run(url, sql + "SELECT count(*) FROM i", **row)[0][0])


def total_return(symbol_id: int, revision: int, value: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol_id": symbol_id,
        "effective_at": OPEN,
        "revision": revision,
        "value": Decimal(value),
        "available_at": DAILY_SETTLE + timedelta(days=revision),
        "source": "yfinance",
        "ingestion_run_id": None,
    }
    row.update(over)
    return row


def split(symbol_id: int, effective_at: date, ratio: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol_id": symbol_id,
        "effective_at": effective_at,
        "ratio": Decimal(ratio),
        "available_at": datetime.combine(effective_at, datetime.min.time(), UTC)
        + timedelta(hours=14, minutes=30),
        "source": "yfinance",
        "ingestion_run_id": None,
    }
    row.update(over)
    return row


def gap(symbol_id: int, interval: str = "15m", **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol_id": symbol_id,
        "interval": interval,
        "missing_from": OPEN - timedelta(days=90),
        "missing_to": OPEN - timedelta(days=59),
        "ingestion_run_id": None,
    }
    row.update(over)
    return row


def snapshot(url: str, table: str, symbol_id: int) -> list[tuple[Any, ...]]:
    """Every stored column of one symbol's rows, in a fixed order: the 'exactly as it was' check."""
    order = ", ".join(PRIMARY_KEYS[table])
    rows = run(url, f"SELECT * FROM {table} WHERE symbol_id = :s ORDER BY {order}", s=symbol_id)
    return [tuple(r) for r in rows]


def sample_row(url: str, table: str) -> tuple[int, dict[str, Any]]:
    """One valid stored row for `table`, and its symbol id."""
    s = symbol(url, "SPY")
    row = {
        "bars_daily": lambda: bar("bars_daily", s),
        "bars_intraday": lambda: bar("bars_intraday", s),
        "bar_total_return": lambda: total_return(s, 0, "741.20"),
        "splits": lambda: split(s, date(2025, 12, 5), "2"),
        "bar_gaps": lambda: gap(s),
    }[table]()
    assert insert(url, table, row) == 1
    return s, row


# --- shape ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", list(COLUMNS))
def test_columns_types_and_nullability(scratch_url: str, table: str) -> None:
    got = run(
        scratch_url,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position",
        t=table,
    )
    assert [tuple(r) for r in got] == COLUMNS[table]


@pytest.mark.parametrize("table", list(PRIMARY_KEYS))
def test_primary_keys(scratch_url: str, table: str) -> None:
    pk = run(
        scratch_url,
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
        "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)",
        t=table,
    )
    assert [r[0] for r in pk] == PRIMARY_KEYS[table]


def test_hypertables_and_their_chunk_intervals(scratch_url: str) -> None:
    # MEASURED 2026-11-18 (docs/evidence/2026-11-18-bars-step2-benchmark.txt): daily on 365-day
    # chunks, 15m on 28-day. The other three are plain tables.
    got = run(
        scratch_url,
        "SELECT hypertable_name, time_interval FROM timescaledb_information.dimensions "
        "ORDER BY hypertable_name",
    )
    assert [(r[0], r[1]) for r in got] == [
        ("bars_daily", timedelta(days=365)),
        ("bars_intraday", timedelta(days=28)),
    ]


@pytest.mark.parametrize("table", BAR_TABLES)
def test_the_only_indexes_are_the_key_and_the_named_time_index(
    scratch_url: str, table: str
) -> None:
    # No TimescaleDB default index (create_default_indexes => false): its DESC form cannot be
    # declared on the model, so `alembic check` would see drift. The plain btree serves both
    # directions, and it is what the cross-section read used when measured.
    got = run(scratch_url, "SELECT indexname FROM pg_indexes WHERE tablename = :t", t=table)
    assert sorted(r[0] for r in got) == sorted([f"pk_{table}", f"ix_{table}_effective_at"])


@pytest.mark.parametrize("table", list(COLUMNS))
def test_every_column_and_the_table_carry_a_comment(scratch_url: str, table: str) -> None:
    rows = run(
        scratch_url,
        "SELECT column_name, col_description(CAST(:t AS regclass), ordinal_position) "
        "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :n",
        t=table,
        n=table,
    )
    missing = [r[0] for r in rows if not r[1]]
    assert not missing, f"{table}: columns without a comment: {missing}"
    table_comment = run(scratch_url, "SELECT obj_description(CAST(:t AS regclass))", t=table)[0][0]
    assert table_comment
    # Kyle: comments state what each time column means. Each says so up front.
    comments = {r[0]: r[1] for r in rows}
    for column in TIME_COLUMNS[table]:
        assert comments[column].startswith("TIME COLUMN."), (table, column)


def test_the_settle_points_are_stated_on_the_bar_columns(scratch_url: str) -> None:
    def comment(table: str, column: str) -> str:
        return str(
            run(
                scratch_url,
                "SELECT col_description(CAST(:t AS regclass), ordinal_position) "
                "FROM information_schema.columns WHERE table_name = :n AND column_name = :c",
                t=table,
                n=table,
                c=column,
            )[0][0]
        )

    assert "D+2" in comment("bars_daily", "available_at")
    assert "D+1" in comment("bars_intraday", "available_at")
    for table in BAR_TABLES:
        assert "recorded exception" in comment(table, "available_at")
        assert "backfill" in comment(table, "feed")


# --- rule 9: every key keeps the other symbol's rows exactly as they were -------------------------


@pytest.mark.parametrize("table", BAR_TABLES)
def test_bar_key_is_per_symbol_and_a_rerun_writes_zero(scratch_url: str, table: str) -> None:
    a, b = symbol(scratch_url, "SPY"), symbol(scratch_url, "QQQ")
    # B's rows would win every ordering: the same instant and a later one, larger values.
    big = {"o_raw": Decimal("900"), "c_raw": Decimal("901"), "v_raw": 99_000_000}
    assert insert(scratch_url, table, bar(table, b, OPEN, **big)) == 1
    assert insert(scratch_url, table, bar(table, b, LATER, **big)) == 1
    before_b = snapshot(scratch_url, table, b)

    # A at the instant B already holds: a different symbol, so it is a new row.
    assert insert(scratch_url, table, bar(table, a, OPEN)) == 1
    # A re-fetch of A's bar with different values: the ingest's write is ON CONFLICT DO NOTHING,
    # so it writes 0 and the first settled value stands (the M2 gate's re-ingest clause).
    assert (
        insert(
            scratch_url, table, bar(table, a, OPEN, c_raw=Decimal("1")), on_conflict_nothing=True
        )
        == 0
    )
    # A at the later instant B holds: still A's own new row.
    assert insert(scratch_url, table, bar(table, a, LATER), on_conflict_nothing=True) == 1

    c_raw = [c for c, _, _ in COLUMNS[table]].index("c_raw")
    stored_a = snapshot(scratch_url, table, a)
    assert [r[c_raw] for r in stored_a] == [Decimal("749.34"), Decimal("749.34")]
    assert snapshot(scratch_url, table, b) == before_b
    # And a duplicate without ON CONFLICT is refused by the key.
    with pytest.raises(IntegrityError):
        insert(scratch_url, table, bar(table, a, OPEN))


def test_total_return_key_is_per_symbol_and_per_revision(scratch_url: str) -> None:
    a, b = symbol(scratch_url, "SPY"), symbol(scratch_url, "QQQ")
    # B has higher revisions and larger values for the same bar: it would win "latest revision".
    for rev in (0, 1, 2):
        assert insert(scratch_url, "bar_total_return", total_return(b, rev, f"{900 + rev}")) == 1
    before_b = snapshot(scratch_url, "bar_total_return", b)

    assert insert(scratch_url, "bar_total_return", total_return(a, 0, "741.20")) == 1
    assert insert(scratch_url, "bar_total_return", total_return(a, 1, "741.55")) == 1
    with pytest.raises(IntegrityError):
        insert(scratch_url, "bar_total_return", total_return(a, 1, "1"))

    # "Current total return" is the latest revision, per symbol and bar (Kyle, 2026-11-18).
    current = run(
        scratch_url,
        "SELECT value FROM bar_total_return WHERE symbol_id = :s AND effective_at = :e "
        "ORDER BY revision DESC LIMIT 1",
        s=a,
        e=OPEN,
    )[0][0]
    assert current == Decimal("741.55")
    assert snapshot(scratch_url, "bar_total_return", b) == before_b


def test_splits_key_is_per_symbol(scratch_url: str) -> None:
    a, b = symbol(scratch_url, "XLK"), symbol(scratch_url, "XLE")
    day = date(2025, 12, 5)
    assert insert(scratch_url, "splits", split(b, day, "10")) == 1
    assert insert(scratch_url, "splits", split(b, day + timedelta(days=1), "10")) == 1
    before_b = snapshot(scratch_url, "splits", b)

    assert insert(scratch_url, "splits", split(a, day, "2")) == 1
    assert insert(scratch_url, "splits", split(a, day + timedelta(days=1), "3")) == 1
    # A second ratio for the same split is refused, never an overwrite (a validation failure).
    with pytest.raises(IntegrityError):
        insert(scratch_url, "splits", split(a, day, "4"))
    assert [r[2] for r in snapshot(scratch_url, "splits", a)] == [Decimal("2"), Decimal("3")]
    assert snapshot(scratch_url, "splits", b) == before_b


def test_gap_uniqueness_is_per_symbol_and_per_interval(scratch_url: str) -> None:
    a, b = symbol(scratch_url, "SPY"), symbol(scratch_url, "QQQ")
    assert insert(scratch_url, "bar_gaps", gap(b, "15m")) == 1
    before_b = snapshot(scratch_url, "bar_gaps", b)

    # The same range for another symbol, and for another interval of the same symbol, are new gaps.
    assert insert(scratch_url, "bar_gaps", gap(a, "15m")) == 1
    assert insert(scratch_url, "bar_gaps", gap(a, "1d")) == 1
    # The same gap recorded twice is refused (a re-run cannot double-record).
    with pytest.raises(IntegrityError):
        insert(scratch_url, "bar_gaps", gap(a, "15m"))
    assert len(snapshot(scratch_url, "bar_gaps", a)) == 2
    assert snapshot(scratch_url, "bar_gaps", b) == before_b


def test_resolving_one_gap_leaves_every_other_gap_open(scratch_url: str) -> None:
    a, b = symbol(scratch_url, "SPY"), symbol(scratch_url, "QQQ")
    # B's gap was recorded first and sorts first by every column: a resolve that lost its key
    # would reach it.
    insert(scratch_url, "bar_gaps", gap(b, "1d", missing_from=OPEN - timedelta(days=200)))
    insert(scratch_url, "bar_gaps", gap(a, "1d"))
    insert(scratch_url, "bar_gaps", gap(a, "15m"))
    target = run(
        scratch_url,
        "SELECT id FROM bar_gaps WHERE symbol_id = :s AND interval = '15m'",
        s=a,
    )[0][0]
    run(scratch_url, "UPDATE bar_gaps SET resolved_at = now() WHERE id = :i", i=target)
    open_gaps = run(
        scratch_url,
        "SELECT symbol_id, interval FROM bar_gaps WHERE resolved_at IS NULL ORDER BY id",
    )
    assert [tuple(r) for r in open_gaps] == [(b, "1d"), (a, "1d")]


# --- CHECKs ---------------------------------------------------------------------------------------

BAR_BAD_VALUES = [
    ("raw_basis", "adjusted"),
    ("feed", "yfinance.daily.backfill"),
    ("feed", "Backfill"),
    ("venue_scope", "consolidated"),
    ("available_at", OPEN),  # the settle point cannot be the open itself
    ("available_at", OPEN - timedelta(minutes=1)),
]


@pytest.mark.parametrize("table", BAR_TABLES)
@pytest.mark.parametrize(("column", "value"), BAR_BAD_VALUES)
def test_bar_checks_refuse_bad_values(
    scratch_url: str, table: str, column: str, value: Any
) -> None:
    s = symbol(scratch_url, "SPY")
    with pytest.raises(IntegrityError, match="ck_"):
        insert(scratch_url, table, bar(table, s, **{column: value}))
    assert snapshot(scratch_url, table, s) == []


@pytest.mark.parametrize("table", BAR_TABLES)
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("raw_basis", "live"),
        ("raw_basis", "reconstructed"),
        ("raw_basis", "unreconstructed"),
        ("feed", "poll"),
        ("feed", "backfill"),
    ],
)
def test_bar_checks_accept_every_allowed_value(
    scratch_url: str, table: str, column: str, value: str
) -> None:
    s = symbol(scratch_url, "SPY")
    over: dict[str, Any] = {column: value}
    assert insert(scratch_url, table, bar(table, s, **over)) == 1


def test_intraday_interval_is_15m_only(scratch_url: str) -> None:
    s = symbol(scratch_url, "SPY")
    with pytest.raises(IntegrityError, match="ck_bars_intraday_interval"):
        insert(scratch_url, "bars_intraday", bar("bars_intraday", s, interval="1d"))


def test_total_return_is_settled_after_its_bar_opened(scratch_url: str) -> None:
    s = symbol(scratch_url, "SPY")
    with pytest.raises(IntegrityError, match="ck_bar_total_return_settled_after_open"):
        insert(scratch_url, "bar_total_return", total_return(s, 0, "741.2", available_at=OPEN))


@pytest.mark.parametrize("ratio", ["0", "-2"])
def test_split_ratio_must_be_positive(scratch_url: str, ratio: str) -> None:
    s = symbol(scratch_url, "XLK")
    with pytest.raises(IntegrityError, match="ck_splits_ratio_positive"):
        insert(scratch_url, "splits", split(s, date(2025, 12, 5), ratio))
    # A reverse split is below 1 and allowed.
    assert insert(scratch_url, "splits", split(s, date(2025, 12, 5), "0.1")) == 1


@pytest.mark.parametrize(
    ("over", "constraint"),
    [
        ({"interval": "30m"}, "ck_bar_gaps_interval"),
        ({"missing_to": OPEN - timedelta(days=90)}, "ck_bar_gaps_range_not_empty"),
        ({"missing_to": OPEN - timedelta(days=91)}, "ck_bar_gaps_range_not_empty"),
        (
            {"recorded_at": OPEN, "resolved_at": OPEN - timedelta(seconds=1)},
            "ck_bar_gaps_resolved_after_recorded",
        ),
    ],
)
def test_gap_checks(scratch_url: str, over: dict[str, Any], constraint: str) -> None:
    s = symbol(scratch_url, "SPY")
    with pytest.raises(IntegrityError, match=constraint):
        insert(scratch_url, "bar_gaps", gap(s, **over))


# --- insert-only triggers -------------------------------------------------------------------------

UPDATES: dict[str, list[tuple[str, str]]] = {
    "bars_daily": [
        ("effective_at", "effective_at + interval '1 day'"),
        ("o_raw", "o_raw + 1"),
        ("h_raw", "h_raw + 1"),
        ("l_raw", "l_raw - 1"),
        ("c_raw", "c_raw + 0.01"),
        ("v_raw", "v_raw + 1"),
        ("raw_basis", "'reconstructed'"),
        ("source", "'stooq'"),
        ("feed", "'backfill'"),
        ("available_at", "available_at + interval '1 day'"),
        ("ingested_at", "ingested_at + interval '1 second'"),
    ],
    "bar_total_return": [
        ("effective_at", "effective_at + interval '1 day'"),
        ("revision", "revision + 1"),
        ("value", "value + 1"),
        ("available_at", "available_at + interval '1 day'"),
        ("ingested_at", "ingested_at + interval '1 second'"),
        ("source", "'stooq'"),
    ],
    "splits": [
        ("effective_at", "effective_at + 1"),
        ("ratio", "ratio + 1"),
        ("available_at", "available_at + interval '1 day'"),
        ("ingested_at", "ingested_at + interval '1 second'"),
        ("source", "'stooq'"),
    ],
}
UPDATES["bars_intraday"] = [*UPDATES["bars_daily"], ("interval", "'30m'")]
UPDATE_CASES = [(t, c, e) for t, cases in UPDATES.items() for c, e in cases]


def trigger_states(url: str, table: str) -> dict[str, str]:
    rows = run(
        url,
        "SELECT tgname, tgenabled::text FROM pg_trigger "
        "WHERE tgrelid = CAST(:t AS regclass) AND NOT tgisinternal",
        t=table,
    )
    return {r[0]: r[1] for r in rows}


@pytest.mark.parametrize("table", [*INSERT_ONLY, "bar_gaps"])
def test_the_triggers_exist_and_are_enabled(scratch_url: str, table: str) -> None:
    # 'O' = enabled. TimescaleDB adds its own ts_insert_blocker on a hypertable: ignored here.
    states = {k: v for k, v in trigger_states(scratch_url, table).items() if k.startswith(table)}
    assert states == {f"{table}_no_{e}": "O" for e in ("delete", "truncate", "update")}


@pytest.mark.parametrize(("table", "column", "expr"), UPDATE_CASES)
def test_no_stored_column_can_be_updated(
    scratch_url: str, table: str, column: str, expr: str
) -> None:
    s, _ = sample_row(scratch_url, table)
    before = snapshot(scratch_url, table, s)
    with pytest.raises(IntegrityError, match="insert-only"):
        run(scratch_url, f'UPDATE {table} SET "{column}" = {expr}')
    assert snapshot(scratch_url, table, s) == before


@pytest.mark.parametrize("table", INSERT_ONLY)
def test_an_update_of_the_symbol_is_refused(scratch_url: str, table: str) -> None:
    s, _ = sample_row(scratch_url, table)
    other = symbol(scratch_url, "QQQ")
    with pytest.raises(IntegrityError, match="insert-only"):
        run(scratch_url, f"UPDATE {table} SET symbol_id = :o", o=other)
    assert len(snapshot(scratch_url, table, s)) == 1


@pytest.mark.parametrize("table", INSERT_ONLY)
def test_rows_cannot_be_deleted_or_truncated(scratch_url: str, table: str) -> None:
    s, _ = sample_row(scratch_url, table)
    with pytest.raises(IntegrityError, match="insert-only"):
        run(scratch_url, f"DELETE FROM {table}")
    with pytest.raises(IntegrityError, match="insert-only"):
        run(scratch_url, f"TRUNCATE {table}")
    assert len(snapshot(scratch_url, table, s)) == 1


@pytest.mark.parametrize("table", BAR_TABLES)
def test_a_chunk_cannot_be_updated_directly_either(scratch_url: str, table: str) -> None:
    # The triggers are cloned onto each chunk, so going around the hypertable does not help.
    s, _ = sample_row(scratch_url, table)
    chunk = run(scratch_url, f"SELECT show_chunks('{table}')")[0][0]
    with pytest.raises(IntegrityError, match="insert-only"):
        run(scratch_url, f"UPDATE {chunk} SET c_raw = c_raw + 1")
    with pytest.raises(IntegrityError, match="insert-only"):
        run(scratch_url, f"DELETE FROM {chunk}")
    assert len(snapshot(scratch_url, table, s)) == 1


def make_row(table: str, s: int, **over: Any) -> dict[str, Any]:
    builders = {
        "bars_daily": lambda: bar("bars_daily", s, **over),
        "bars_intraday": lambda: bar("bars_intraday", s, **over),
        "bar_total_return": lambda: total_return(s, 0, "741.20", **over),
        "splits": lambda: split(s, date(2025, 12, 5), "2", **over),
        "bar_gaps": lambda: gap(s, **over),
    }
    return builders[table]()


def without(rows: list[tuple[Any, ...]], col: int) -> list[tuple[Any, ...]]:
    return [tuple(v for i, v in enumerate(r) if i != col) for r in rows]


@pytest.mark.parametrize("table", [*INSERT_ONLY, "bar_gaps"])
def test_the_run_pointer_may_only_be_cleared_and_only_for_the_purged_run(
    scratch_url: str, table: str
) -> None:
    # The one carve-out (as macro_metrics): ON DELETE SET NULL is itself an UPDATE. Rule 9: B points
    # at another run, and purging A's run must leave B's pointer alone.
    run_a, run_b = ingestion_run(scratch_url), ingestion_run(scratch_url)
    a, b = symbol(scratch_url, "SPY"), symbol(scratch_url, "QQQ")
    assert insert(scratch_url, table, make_row(table, a, ingestion_run_id=run_a)) == 1
    assert insert(scratch_url, table, make_row(table, b, ingestion_run_id=run_b)) == 1
    with pytest.raises(IntegrityError, match="refused"):
        run(
            scratch_url,
            f"UPDATE {table} SET ingestion_run_id = :r WHERE symbol_id = :s",
            r=run_b,
            s=a,
        )
    col = [c for c, _, _ in COLUMNS[table]].index("ingestion_run_id")
    before_a, before_b = snapshot(scratch_url, table, a), snapshot(scratch_url, table, b)

    run(scratch_url, "DELETE FROM ingestion_runs WHERE id = :r", r=run_a)

    after_a = snapshot(scratch_url, table, a)
    assert [r[col] for r in after_a] == [None]
    assert without(after_a, col) == without(before_a, col)
    assert snapshot(scratch_url, table, b) == before_b


# --- bar_gaps: never deleted; resolved once -------------------------------------------------------


def test_a_gap_is_resolved_once_and_never_reopened(scratch_url: str) -> None:
    s = symbol(scratch_url, "SPY")
    insert(scratch_url, "bar_gaps", gap(s))
    run(scratch_url, "UPDATE bar_gaps SET resolved_at = now()")
    first = run(scratch_url, "SELECT resolved_at FROM bar_gaps")[0][0]
    assert first is not None
    with pytest.raises(IntegrityError, match="bar_gaps records"):
        run(scratch_url, "UPDATE bar_gaps SET resolved_at = resolved_at + interval '1 hour'")
    with pytest.raises(IntegrityError, match="bar_gaps records"):
        run(scratch_url, "UPDATE bar_gaps SET resolved_at = NULL")
    assert run(scratch_url, "SELECT resolved_at FROM bar_gaps")[0][0] == first


@pytest.mark.parametrize(
    ("column", "expr"),
    [
        ("symbol_id", "(SELECT max(id) FROM symbols)"),
        ("interval", "'1d'"),
        ("missing_from", "missing_from - interval '1 day'"),
        ("missing_to", "missing_to + interval '1 day'"),
        ("recorded_at", "recorded_at - interval '1 day'"),
    ],
)
def test_nothing_else_about_a_gap_can_change(scratch_url: str, column: str, expr: str) -> None:
    s = symbol(scratch_url, "SPY")
    symbol(scratch_url, "QQQ")  # so a symbol_id change has somewhere valid to point
    insert(scratch_url, "bar_gaps", gap(s))
    before = snapshot(scratch_url, "bar_gaps", s)
    with pytest.raises(IntegrityError, match="bar_gaps records"):
        run(scratch_url, f'UPDATE bar_gaps SET "{column}" = {expr}')
    # Not even together with a legitimate resolve.
    with pytest.raises(IntegrityError, match="bar_gaps records"):
        run(scratch_url, f'UPDATE bar_gaps SET "{column}" = {expr}, resolved_at = now()')
    assert snapshot(scratch_url, "bar_gaps", s) == before


def test_a_gap_cannot_be_deleted_or_truncated(scratch_url: str) -> None:
    s = symbol(scratch_url, "SPY")
    insert(scratch_url, "bar_gaps", gap(s))
    with pytest.raises(IntegrityError, match="bar_gaps records"):
        run(scratch_url, "DELETE FROM bar_gaps")
    with pytest.raises(IntegrityError, match="bar_gaps records"):
        run(scratch_url, "TRUNCATE bar_gaps")
    assert len(snapshot(scratch_url, "bar_gaps", s)) == 1


# --- universe_membership.close_reason (0012): two-way ---------------------------------------------


def membership(url: str, valid_to: date | None, close_reason: str | None) -> None:
    s = symbol(url, f"T{uuid4_hex()}")
    u = run(
        url,
        "INSERT INTO universes (slug, name) VALUES (:g, 'test') RETURNING id",
        g=f"u{uuid4_hex()}",
    )[0][0]
    run(
        url,
        "INSERT INTO universe_membership "
        "(entity_id, universe_id, valid_from, valid_to, source, available_at, close_reason) "
        "VALUES (:e, :u, DATE '2026-11-10', :t, 'seed', now(), :r)",
        e=s,
        u=u,
        t=valid_to,
        r=close_reason,
    )


def uuid4_hex() -> str:
    return uuid.uuid4().hex[:8]


CLOSED = date(2026, 11, 18)


@pytest.mark.parametrize(
    ("valid_to", "reason"),
    [(None, None), (CLOSED, "delisted"), (CLOSED, "merged"), (CLOSED, "removed_by_operator")],
)
def test_close_reason_allowed_combinations(
    scratch_url: str, valid_to: date | None, reason: str | None
) -> None:
    membership(scratch_url, valid_to, reason)


@pytest.mark.parametrize(
    ("valid_to", "reason", "constraint"),
    [
        (None, "delisted", "ck_universe_membership_close_reason_iff_closed"),  # open with a reason
        (CLOSED, None, "ck_universe_membership_close_reason_iff_closed"),  # closed, no reason
        (CLOSED, "renamed", "ck_universe_membership_close_reason_values"),
    ],
)
def test_close_reason_refused_combinations(
    scratch_url: str, valid_to: date | None, reason: str | None, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        membership(scratch_url, valid_to, reason)


def test_closing_one_membership_leaves_the_other_open(scratch_url: str) -> None:
    # The operator command's write, keyed by (universe, entity, valid_from). B's membership started
    # earlier and sorts first: a close that lost its key would close it too.
    u = run(scratch_url, "INSERT INTO universes (slug, name) VALUES ('rot', 'r') RETURNING id")[0][
        0
    ]
    a, b = symbol(scratch_url, "XLK"), symbol(scratch_url, "XLE")
    for s, start in ((b, date(2026, 1, 2)), (a, date(2026, 11, 10))):
        run(
            scratch_url,
            "INSERT INTO universe_membership (entity_id, universe_id, valid_from, source, "
            "available_at) VALUES (:e, :u, :f, 'seed', now())",
            e=s,
            u=u,
            f=start,
        )
    run(
        scratch_url,
        "UPDATE universe_membership SET valid_to = DATE '2026-11-18', close_reason = 'delisted' "
        "WHERE universe_id = :u AND entity_id = :e AND valid_from = DATE '2026-11-10'",
        u=u,
        e=a,
    )
    got = run(
        scratch_url,
        "SELECT entity_id, valid_to, close_reason FROM universe_membership ORDER BY valid_from",
    )
    assert [tuple(r) for r in got] == [(b, None, None), (a, CLOSED, "delisted")]


# --- the migrations round-trip --------------------------------------------------------------------


def test_downgrade_to_0009_removes_everything_and_upgrade_restores_it(scratch_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    from tests.conftest import ALEMBIC_INI

    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "0009")
    for table in COLUMNS:
        assert run(scratch_url, "SELECT to_regclass(:t)", t=table)[0][0] is None
    functions = run(
        scratch_url,
        "SELECT proname FROM pg_proc WHERE proname IN "
        "('insert_only_observation', 'bar_gaps_append_only')",
    )
    assert functions == []
    cols = run(
        scratch_url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'universe_membership' AND column_name = 'close_reason'",
    )
    assert cols == []
    command.upgrade(cfg, "head")
    for table in COLUMNS:
        assert run(scratch_url, "SELECT to_regclass(:t)", t=table)[0][0] == table

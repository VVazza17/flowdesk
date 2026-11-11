"""Slice 3: reference tables (IDEA-033), their constraints, the seeder and the session sync. Needs
DATABASE_URL (see test_migrations.py); a scratch database is created and dropped per test.

The provider_mappings cases pin the rule approved on 2026-11-10: one active value per
(symbol, provider, id_kind) for every kind, and one active symbol per
(provider, id_kind, external_id) for every kind but cik.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Row, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.services.reference_seed import seed_reference
from app.services.sessions import sync_sessions

ADMIN_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

NEW_TABLES = {
    "currencies": (["code", "name"], ["code"]),
    "exchanges": (
        ["mic", "name", "country", "currency_code", "calendar_code", "timezone"],
        ["mic"],
    ),
    "symbols": (
        [
            "id",
            "ticker",
            "name",
            "asset_class",
            "sector",
            "role",
            "active",
            "meta",
            "exchange_mic",
            "currency_code",
            "delisted_on",
        ],
        ["id"],
    ),
    "provider_mappings": (
        ["id", "symbol_id", "provider", "id_kind", "external_id", "retired_at"],
        ["id"],
    ),
    "universes": (["id", "slug", "name", "description"], ["id"]),
    "universe_membership": (
        ["entity_id", "universe_id", "valid_from", "valid_to", "source", "available_at"],
        ["universe_id", "entity_id", "valid_from"],
    ),
    "trading_sessions": (
        ["calendar_code", "session_date", "opens_at", "closes_at", "is_early_close"],
        ["calendar_code", "session_date"],
    ),
}

CIK = "0001652044"  # Alphabet: GOOG and GOOGL share it


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


def run[T](url: str, fn: Callable[[AsyncEngine], Awaitable[T]]) -> T:
    async def go() -> T:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def rows(url: str, sql: str, **params: Any) -> Sequence[Row[Any]]:
    async def go(engine: AsyncEngine) -> Sequence[Row[Any]]:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return result.all() if result.returns_rows else []

    return run(url, go)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"flowdesk_ref_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)  # env.py reads this
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def symbol(url: str, ticker: str, *, active: bool = True, exchange: str | None = None) -> int:
    got = rows(
        url,
        "INSERT INTO symbols (ticker, active, exchange_mic) VALUES (:t, :a, :e) RETURNING id",
        t=ticker,
        a=active,
        e=exchange,
    )
    return int(got[0][0])


def mapping(
    url: str, sid: int, kind: str, ext: str, *, provider: str = "p", retired: bool = False
) -> None:
    rows(
        url,
        "INSERT INTO provider_mappings (symbol_id, provider, id_kind, external_id, retired_at) "
        "VALUES (:s, :p, :k, :x, CASE WHEN :r THEN now() END)",
        s=sid,
        p=provider,
        k=kind,
        x=ext,
        r=retired,
    )


# --- schema -------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", NEW_TABLES)
def test_columns_and_primary_key(db: str, table: str) -> None:
    columns, pk = NEW_TABLES[table]
    got = rows(
        db,
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t "
        "ORDER BY ordinal_position",
        t=table,
    )
    assert [r[0] for r in got] == columns
    pk_got = rows(
        db,
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
        "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)",
        t=table,
    )
    assert [r[0] for r in pk_got] == pk


def test_ticker_is_no_longer_globally_unique(db: str) -> None:
    # §8 says `ticker UNIQUE`; §6.5 says a ticker is a mutable label (IDEA-033).
    defs = " ".join(
        r[0] for r in rows(db, "SELECT indexdef FROM pg_indexes WHERE tablename = 'symbols'")
    )
    assert "UNIQUE INDEX uq_symbols_active_ticker" in defs
    assert "NULLS NOT DISTINCT" in defs and "WHERE active" in defs
    assert "(ticker)" not in defs


def test_constraint_and_index_names(db: str) -> None:
    checks = {
        r[0]
        for r in rows(
            db, "SELECT conname FROM pg_constraint WHERE conrelid = 'provider_mappings'::regclass"
        )
    }
    assert {"ck_provider_mappings_id_kind", "ck_provider_mappings_cik_form"} <= checks
    idx = {
        r[0]: r[1]
        for r in rows(
            db, "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'provider_mappings'"
        )
    }
    assert "retired_at IS NULL" in idx["uq_provider_mappings_symbol_active"]
    assert "id_kind <> 'cik'" in idx["uq_provider_mappings_external_active"]


def test_downgrade_to_0002_removes_only_the_reference_tables(db: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "0002")
    left = {
        r[0]
        for r in rows(
            db,
            "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:n)",
            n=[*NEW_TABLES, "task_runs"],
        )
    }
    assert left == {"task_runs"}
    command.upgrade(cfg, "head")
    assert rows(db, "SELECT count(*) FROM symbols")[0][0] == 0


# --- provider_mappings uniqueness ---------------------------------------------------------------


def test_goog_and_googl_share_one_cik(db: str) -> None:
    goog, googl = symbol(db, "GOOG"), symbol(db, "GOOGL")
    mapping(db, goog, "cik", CIK, provider="sec_edgar")
    mapping(db, googl, "cik", CIK, provider="sec_edgar")  # the case CIK exists for
    assert rows(db, "SELECT count(*) FROM provider_mappings")[0][0] == 2


def test_a_symbol_has_one_active_value_per_kind(db: str) -> None:
    sid = symbol(db, "GOOG")
    mapping(db, sid, "cik", CIK, provider="sec_edgar")
    with pytest.raises(IntegrityError):
        mapping(db, sid, "cik", "0000320193", provider="sec_edgar")
    with pytest.raises(IntegrityError):
        mapping(db, sid, "ticker", "A", provider="yfinance")
        mapping(db, sid, "ticker", "B", provider="yfinance")


def test_replacing_a_value_retires_the_old_row_first(db: str) -> None:
    sid = symbol(db, "GOOG")
    mapping(db, sid, "cik", CIK, provider="sec_edgar", retired=True)
    mapping(db, sid, "cik", "0000320193", provider="sec_edgar")
    mapping(db, sid, "cik", "0000789019", provider="sec_edgar", retired=True)  # history may repeat
    assert rows(db, "SELECT count(*) FROM provider_mappings")[0][0] == 3


def test_a_live_ticker_string_names_one_symbol(db: str) -> None:
    a, b = symbol(db, "OLD"), symbol(db, "NEW")
    mapping(db, a, "ticker", "X", provider="yfinance")
    with pytest.raises(IntegrityError):
        mapping(db, b, "ticker", "X", provider="yfinance")
    mapping(db, b, "ticker", "X", provider="other")  # another provider's namespace


def test_a_reassigned_ticker_string_is_retire_then_add(db: str) -> None:
    a, b = symbol(db, "OLD", active=False), symbol(db, "NEW")
    mapping(db, a, "ticker", "X", provider="yfinance", retired=True)
    mapping(db, b, "ticker", "X", provider="yfinance")
    assert rows(db, "SELECT count(*) FROM provider_mappings")[0][0] == 2


def test_id_kind_is_a_closed_set(db: str) -> None:
    sid = symbol(db, "GOOG")
    with pytest.raises(IntegrityError):
        mapping(db, sid, "isin", "US02079K3059")


def test_cik_must_be_ten_zero_padded_digits(db: str) -> None:
    sid = symbol(db, "GOOG")
    for bad in ("1652044", "00016520440", "000165204X", ""):
        with pytest.raises(IntegrityError):
            mapping(db, sid, "cik", bad, provider="sec_edgar")
    mapping(db, sid, "cik", CIK, provider="sec_edgar")


def test_provider_mapping_needs_a_real_symbol(db: str) -> None:
    with pytest.raises(IntegrityError):
        mapping(db, 999999, "ticker", "X")


# --- symbols ------------------------------------------------------------------------------------


def test_two_active_symbols_cannot_share_a_ticker_even_without_an_exchange(db: str) -> None:
    symbol(db, "SPY")
    with pytest.raises(IntegrityError):
        symbol(db, "SPY")  # NULL exchange twice: NULLS NOT DISTINCT is what stops this


def test_a_delisted_holder_and_its_successor_can_share_a_ticker(db: str) -> None:
    symbol(db, "FB", active=False)
    symbol(db, "FB")
    assert rows(db, "SELECT count(*) FROM symbols WHERE ticker = 'FB'")[0][0] == 2


def test_the_same_ticker_on_two_exchanges_is_two_symbols(db: str) -> None:
    rows(db, "INSERT INTO currencies VALUES ('USD', 'US Dollar'), ('KRW', 'Won')")
    rows(
        db,
        "INSERT INTO exchanges VALUES ('XNYS','n','US','USD','XNYS','America/New_York'),"
        "('XKRX','k','KR','KRW','XKRX','Asia/Seoul')",
    )
    symbol(db, "000660", exchange="XKRX")
    symbol(db, "000660", exchange="XNYS")


# --- the seeder ---------------------------------------------------------------------------------

SEED_AT = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)


def seed(url: str, **kw: Any) -> Any:
    return run(url, lambda e: seed_reference(e, clock=lambda: SEED_AT, **kw))


def test_seed_matches_section_6_2(db: str) -> None:
    result = seed(db)
    assert (result.currencies, result.exchanges, result.universes) == (2, 2, 5)
    # 14 rotation + SPY + QQQ and RSP + 7 commodity ETFs, none shared between lists.
    assert result.symbols == 24
    assert result.memberships == 24
    assert result.drift == ()
    per_universe = dict(
        (r[0], r[1])
        for r in rows(
            db,
            "SELECT u.slug, count(m.entity_id) FROM universes u "
            "LEFT JOIN universe_membership m ON m.universe_id = u.id GROUP BY u.slug",
        )
    )
    assert per_universe == {
        "rotation": 14,
        "benchmark": 1,
        "style_extras": 2,
        "commodity_proxies": 7,
        "breadth": 0,  # §6.2 gives no list; seeding it would invent about 500 tickers
    }


def test_seed_is_idempotent(db: str) -> None:
    seed(db)
    again = seed(db)
    assert (again.currencies, again.exchanges, again.universes) == (0, 0, 0)
    assert (again.symbols, again.memberships, again.drift) == (0, 0, ())
    assert rows(db, "SELECT count(*) FROM universe_membership")[0][0] == 24


def test_seeded_membership_starts_on_the_seed_date(db: str) -> None:
    seed(db)
    got = rows(
        db,
        "SELECT DISTINCT valid_from, valid_to, source, available_at FROM universe_membership",
    )
    assert [tuple(r) for r in got] == [(date(2026, 11, 10), None, "config", SEED_AT)]
    # The consequence, stated as a test: no historical query returns anything before the seed date.
    assert (
        rows(
            db,
            "SELECT count(*) FROM universe_membership "
            "WHERE valid_from <= DATE '2026-11-09' "
            "AND (valid_to IS NULL OR valid_to > DATE '2026-11-09')",
        )[0][0]
        == 0
    )


def test_seeded_symbols_carry_no_invented_attributes(db: str) -> None:
    seed(db)
    got = rows(
        db,
        "SELECT DISTINCT asset_class, name, sector, role, exchange_mic, currency_code, active "
        "FROM symbols",
    )
    assert [tuple(r) for r in got] == [("etf", None, None, None, None, None, True)]


def test_a_symbol_a_provider_later_tagged_with_an_exchange_is_not_seeded_twice(db: str) -> None:
    seed(db)
    rows(db, "UPDATE symbols SET exchange_mic = 'XNYS' WHERE ticker = 'SPY'")
    again = seed(db)
    assert again.symbols == 0 and again.drift == ()
    assert rows(db, "SELECT count(*) FROM symbols WHERE ticker = 'SPY'")[0][0] == 1


def test_a_ticker_dropped_from_config_is_reported_and_never_closed(db: str) -> None:
    seed(db)
    trimmed = tuple(
        settings.UniverseSpec(
            u.slug, u.name, u.description, tuple(t for t in u.tickers if t != "ARKK")
        )
        for u in settings.UNIVERSES
    )
    result = seed(db, universes=trimmed)
    assert [d for d in result.drift if "rotation/ARKK" in d and "IDEA-040" in d]
    assert (
        rows(
            db,
            "SELECT count(*) FROM universe_membership m JOIN symbols s ON s.id = m.entity_id "
            "WHERE s.ticker = 'ARKK' AND m.valid_to IS NULL",
        )[0][0]
        == 1
    )


def test_an_interval_closed_by_hand_is_not_reopened(db: str) -> None:
    seed(db)
    rows(
        db,
        "UPDATE universe_membership SET valid_to = DATE '2026-11-11' WHERE entity_id = "
        "(SELECT id FROM symbols WHERE ticker = 'URA')",
    )
    result = seed(db)
    assert result.memberships == 0
    assert [d for d in result.drift if "commodity_proxies/URA" in d and "not reopened" in d]


# --- the session sync ---------------------------------------------------------------------------


def test_session_sync_inserts_then_is_idempotent(db: str) -> None:
    first = run(db, lambda e: sync_sessions(e, ["XNYS"], date(2025, 7, 1), date(2025, 7, 10)))
    # Jul 1-3, then Jul 7-10: the 4th is a holiday and the 5th-6th a weekend.
    assert first.inserted == 7 and first.drift == ()
    again = run(db, lambda e: sync_sessions(e, ["XNYS"], date(2025, 7, 1), date(2025, 7, 10)))
    assert again.inserted == 0 and again.drift == ()
    assert rows(
        db, "SELECT is_early_close FROM trading_sessions WHERE session_date = '2025-07-03'"
    )[0][0]


def test_a_stored_session_is_reported_when_the_library_disagrees_and_never_rewritten(
    db: str,
) -> None:
    run(db, lambda e: sync_sessions(e, ["XNYS"], date(2025, 7, 1), date(2025, 7, 10)))
    rows(
        db,
        "UPDATE trading_sessions SET closes_at = closes_at + interval '1 hour' "
        "WHERE session_date = '2025-07-07'",
    )
    result = run(db, lambda e: sync_sessions(e, ["XNYS"], date(2025, 7, 1), date(2025, 7, 10)))
    assert result.inserted == 0
    assert [d for d in result.drift if "2025-07-07" in d]
    got = rows(db, "SELECT closes_at FROM trading_sessions WHERE session_date = '2025-07-07'")
    assert got[0][0] == datetime(2025, 7, 7, 21, 0, tzinfo=UTC)  # the tampered value was kept

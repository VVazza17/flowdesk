"""Seeds the reference tables (M2 slice 3, IDEA-033): currencies, exchanges, universes, the symbols
those universes name, and their membership.

# DECISION: insert-only, and idempotent. Running it twice changes nothing; running it after config
# changed ADDS what is new and REPORTS what disagrees. It never closes an interval and never edits
# a row. Closing an interval needs a knowledge time for the closure that the spec's signature does
# not have (IDEA-040), and nothing in M2 may trigger that. A ticker dropped from config, or an
# interval someone closed by hand while config still lists the ticker, comes back as a `drift`
# message for a person to resolve.

# DECISION: seeded membership starts on the seed date (`valid_from` = today, UTC) with
# source='config'. An earlier date would assert something unverified (XLRE did not exist in 2012).
# Consequence: no historical query against these universes returns anything before the seed date,
# which is honest. Revisit at slice 6, when first-bar dates exist and a start can be declared from
# them (membership in a self-defined universe is a stipulation, not an observation).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app import settings
from app.models.reference import (
    Currency,
    Exchange,
    Symbol,
    Universe,
    UniverseMembership,
)

SOURCE_CONFIG = "config"

# ISO 4217 codes the seeded exchanges need. More arrive with the exchanges that use them.
CURRENCIES = (("USD", "US Dollar"), ("KRW", "South Korean Won"))

# ISO 10383 MICs. DECISION: only the two venues §6.12 names (NYSE for US, XKRX for the premium
# monitor's Korean leg). Nasdaq and Arca share XNYS's calendar and join when a provider reports
# which exchange a symbol trades on; the seeded ETFs have no exchange until then.
# (mic, name, country, currency_code, calendar_code, timezone)
EXCHANGES = (
    ("XNYS", "New York Stock Exchange", "US", "USD", "XNYS", "America/New_York"),
    ("XKRX", "Korea Exchange", "KR", "KRW", "XKRX", "Asia/Seoul"),
)

# DECISION: the seeded symbols carry a ticker and asset_class only. Names and sectors come from a
# provider's reference data (§6.1), not from this file; writing 24 fund names from memory would be
# inventing data. `role` stays NULL: the universe a symbol belongs to says what it is for. The value
# 'etf' is a stopgap, since §8 gives asset_class no vocabulary (IDEA-033).
SEED_ASSET_CLASS = "etf"


@dataclass(frozen=True)
class SeedResult:
    currencies: int
    exchanges: int
    universes: int
    symbols: int
    memberships: int
    drift: tuple[str, ...]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def seed_reference(
    engine: AsyncEngine,
    *,
    universes: tuple[settings.UniverseSpec, ...] = settings.UNIVERSES,
    clock: Callable[[], datetime] = _utcnow,
) -> SeedResult:
    now = clock()
    today: date = now.date()
    drift: list[str] = []

    async with engine.begin() as conn:
        have_ccy = set((await conn.execute(select(Currency.code))).scalars())
        new_ccy = [{"code": c, "name": n} for c, n in CURRENCIES if c not in have_ccy]
        if new_ccy:
            await conn.execute(pg_insert(Currency).values(new_ccy).on_conflict_do_nothing())

        have_mic = set((await conn.execute(select(Exchange.mic))).scalars())
        new_ex = [
            {
                "mic": m,
                "name": n,
                "country": country,
                "currency_code": ccy,
                "calendar_code": cal,
                "timezone": tz,
            }
            for m, n, country, ccy, cal, tz in EXCHANGES
            if m not in have_mic
        ]
        if new_ex:
            await conn.execute(pg_insert(Exchange).values(new_ex).on_conflict_do_nothing())

        have_slug = set((await conn.execute(select(Universe.slug))).scalars())
        new_uni = [
            {"slug": u.slug, "name": u.name, "description": u.description}
            for u in universes
            if u.slug not in have_slug
        ]
        if new_uni:
            await conn.execute(pg_insert(Universe).values(new_uni).on_conflict_do_nothing())
        universe_ids: dict[str, int] = {
            slug: uid
            for uid, slug in (await conn.execute(select(Universe.id, Universe.slug))).all()
        }

        # Symbols: matched by ticker among ACTIVE rows, whatever their exchange, so a symbol that a
        # provider later tags with an exchange is not seeded a second time.
        wanted = list(dict.fromkeys(t for u in universes for t in u.tickers))
        active: dict[str, list[int]] = {}
        for sid, ticker in (
            await conn.execute(select(Symbol.id, Symbol.ticker).where(Symbol.active))
        ).all():
            active.setdefault(ticker, []).append(sid)
        new_symbols = 0
        symbol_ids: dict[str, int] = {}
        for ticker in wanted:
            ids = active.get(ticker, [])
            if len(ids) == 1:
                symbol_ids[ticker] = ids[0]
            elif len(ids) > 1:
                drift.append(f"{ticker}: {len(ids)} active symbols share this ticker; not seeded")
            else:
                new_id = (
                    await conn.execute(
                        pg_insert(Symbol)
                        .values(ticker=ticker, asset_class=SEED_ASSET_CLASS, active=True)
                        .returning(Symbol.id)
                    )
                ).scalar_one()
                symbol_ids[ticker] = new_id
                new_symbols += 1

        # Every symbol, not just the ones config still names: a member that config dropped has to be
        # reported by its ticker, and it is no longer in `symbol_ids`.
        ticker_by_id = {
            sid: ticker
            for sid, ticker in (await conn.execute(select(Symbol.id, Symbol.ticker))).all()
        }
        new_members = 0
        for u in universes:
            uid = universe_ids[u.slug]
            rows = (
                await conn.execute(
                    select(
                        UniverseMembership.entity_id,
                        UniverseMembership.valid_from,
                        UniverseMembership.valid_to,
                        UniverseMembership.source,
                    ).where(UniverseMembership.universe_id == uid)
                )
            ).all()
            open_ids = {r.entity_id for r in rows if r.valid_to is None}
            closed = {r.entity_id: r.valid_to for r in rows if r.valid_to is not None}
            for ticker in u.tickers:
                sid = symbol_ids.get(ticker)
                if sid is None or sid in open_ids:
                    continue
                if sid in closed:
                    drift.append(
                        f"{u.slug}/{ticker}: config lists it but its interval was closed on "
                        f"{closed[sid]}; not reopened"
                    )
                    continue
                await conn.execute(
                    pg_insert(UniverseMembership)
                    .values(
                        entity_id=sid,
                        universe_id=uid,
                        valid_from=today,
                        source=SOURCE_CONFIG,
                        available_at=now,
                    )
                    .on_conflict_do_nothing()
                )
                new_members += 1
            # Config dropped a member that this seeder put there: report, never close.
            listed = {symbol_ids[t] for t in u.tickers if t in symbol_ids}
            for r in rows:
                if r.valid_to is None and r.source == SOURCE_CONFIG and r.entity_id not in listed:
                    drift.append(
                        f"{u.slug}/{ticker_by_id.get(r.entity_id, r.entity_id)}: no longer in "
                        "config; interval left open (closing needs IDEA-040)"
                    )

    return SeedResult(
        currencies=len(new_ccy),
        exchanges=len(new_ex),
        universes=len(new_uni),
        symbols=new_symbols,
        memberships=new_members,
        drift=tuple(drift),
    )

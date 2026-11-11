"""Reference tables (§16 M2 slice 3, IDEA-033): currencies, exchanges, symbols, provider mappings,
universes, universe membership and trading sessions.

§8 gives DDL for `symbols` only, and `universe_membership` only as a signature (line 1111). Every
other column, key and type here is a decision recorded in IDEA-033 and carries a `# DECISION:`
comment. Migration 0003 is the schema; this module mirrors it for queries.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TZ = TIMESTAMP(timezone=True)

# DECISION: the vocabulary of provider_mappings.id_kind. It is a closed set on purpose (a CHECK,
# the one deliberate exception to the ops tables' no-CHECK convention, IDEA-022 addendum): the two
# unique indexes below depend on the value, so a new kind must arrive in a migration that decides
# which of them applies to it.
ID_KINDS = ("ticker", "cik")


class Currency(Base):
    __tablename__ = "currencies"

    # DECISION: natural keys (ISO 4217, ISO 10383 below) for the standards-coded reference tables.
    # The code is stable and readable in fx_rates and in logs; an identity id would add a join.
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class Exchange(Base):
    __tablename__ = "exchanges"

    mic: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text)  # ISO 3166-1 alpha-2
    currency_code: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    # DECISION: the exchange_calendars key, not a foreign key. Several exchanges share one calendar
    # (NYSE, Arca and Nasdaq are all XNYS, §6.12's "NYSE for US"), and a calendars table would hold
    # nothing but the code. trading_sessions is keyed by the same text.
    calendar_code: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)  # IANA name


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        # DECISION: §8's `ticker UNIQUE` is dropped. §6.5 calls a ticker "a mutable label" that is
        # reassigned between companies, so a delisted holder and its successor need two rows. What
        # holds instead: at most one ACTIVE symbol per (exchange, ticker). NULLS NOT DISTINCT
        # because exchange_mic is nullable (the spec does not say which exchange each ETF lists
        # on, and we do not guess): without it two NULL-exchange rows with one ticker would both
        # be allowed.
        Index(
            "uq_symbols_active_ticker",
            "exchange_mic",
            "ticker",
            unique=True,
            postgresql_where=text("active"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ticker: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    asset_class: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # DECISION: exchange and currency are nullable foreign keys. They are added because §6.12 and
    # fx handling need them (IDEA-033), but the seeded ETFs have neither until a provider
    # reports it.
    exchange_mic: Mapped[str | None] = mapped_column(ForeignKey("exchanges.mic"))
    currency_code: Mapped[str | None] = mapped_column(ForeignKey("currencies.code"))
    # DECISION: §6.12's "delist date". `active` stays as §8 has it; this is when it stopped trading.
    delisted_on: Mapped[date | None] = mapped_column(Date)


class ProviderMapping(Base):
    __tablename__ = "provider_mappings"
    __table_args__ = (
        # What uniqueness holds depends on id_kind (approved 2026-11-10, IDEA-033):
        #   symbol -> id, every kind: a symbol has one ACTIVE value per (provider, id_kind).
        Index(
            "uq_provider_mappings_symbol_active",
            "symbol_id",
            "provider",
            "id_kind",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        #   id -> symbol, every kind but cik: a live provider string names one symbol. A CIK
        #   identifies an issuer, so GOOG and GOOGL share one, and the main case CIK exists for
        #   must not be forbidden.
        Index(
            "uq_provider_mappings_external_active",
            "provider",
            "id_kind",
            "external_id",
            unique=True,
            postgresql_where=text("retired_at IS NULL AND id_kind <> 'cik'"),
        ),
        CheckConstraint("id_kind IN ('ticker', 'cik')", name="id_kind"),
        CheckConstraint("id_kind <> 'cik' OR external_id ~ '^[0-9]{10}$'", name="cik_form"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"))
    provider: Mapped[str] = mapped_column(Text)
    id_kind: Mapped[str] = mapped_column(Text)
    # CIK is stored as the 10-digit zero-padded string EDGAR uses (§6.5), not an integer: the
    # padding is part of the identifier's published form, and an int would lose it.
    external_id: Mapped[str] = mapped_column(Text)
    # DECISION: retirement, not effective dating. A mapping that stopped being true is retired and a
    # new one added. Valid-time ranges for mappings would be one more point-in-time structure to
    # design, and nothing in M2 asks which ticker string a provider used on a past date.
    retired_at: Mapped[datetime | None] = mapped_column(TZ)


class Universe(Base):
    __tablename__ = "universes"

    # Same shape as `themes` (§8): id, slug UNIQUE, name, description.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class UniverseMembership(Base):
    """A validity range, not an observation (IDEA-034 tripwire).

    `valid_from`/`valid_to` are its effective interval; it has no effective_at, ingested_at or
    revision. It uses only `available_at` and `source`, with ADR-0003's meaning: `available_at` is
    when this row's interval became known to us. It does NOT say when the end of the interval
    became known: that is IDEA-040, and nothing in M2 writes a closure.
    """

    __tablename__ = "universe_membership"
    __table_args__ = (PrimaryKeyConstraint("universe_id", "entity_id", "valid_from"),)

    # DECISION: the spec's name `entity_id` is kept (line 1111). It points at symbols.id; non-symbol
    # membership (macro metrics in a theme) is theme_members.ref_type's job, not this table's.
    entity_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"))
    universe_id: Mapped[int] = mapped_column(ForeignKey("universes.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    # DECISION: half-open [valid_from, valid_to). valid_to is the first day NOT in the universe;
    # NULL means still a member. The spec does not say; half-open lets one interval end on the day
    # the next begins without an overlap or a gap.
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(TZ)


class TradingSession(Base):
    __tablename__ = "trading_sessions"
    __table_args__ = (PrimaryKeyConstraint("calendar_code", "session_date"),)

    calendar_code: Mapped[str] = mapped_column(Text)
    session_date: Mapped[date] = mapped_column(Date)
    opens_at: Mapped[datetime] = mapped_column(TZ)
    closes_at: Mapped[datetime] = mapped_column(TZ)
    is_early_close: Mapped[bool] = mapped_column(Boolean)

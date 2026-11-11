"""reference tables: currencies, exchanges, symbols, provider_mappings, universes,
universe_membership, trading_sessions (§16 M2 slice 3, IDEA-033)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10

§8 gives DDL for `symbols` only; the rest is design, and every choice is a DECISION below and in
IDEA-033. No hypertables: the largest table here (trading_sessions) gains about 250 rows a year
per calendar. Schema only; the rows come from `python -m app.seed`, which is idempotent.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False)


def upgrade() -> None:
    # DECISION: natural keys for the standards-coded tables (ISO 4217 currency, ISO 10383 MIC).
    op.create_table(
        "currencies",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_currencies"),
    )
    op.create_table(
        "exchanges",
        sa.Column("mic", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("currency_code", sa.Text(), nullable=False),
        # DECISION: the exchange_calendars key, not a foreign key; exchanges share calendars.
        sa.Column("calendar_code", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("mic", name="pk_exchanges"),
        sa.ForeignKeyConstraint(
            ["currency_code"], ["currencies.code"], name="fk_exchanges_currency_code_currencies"
        ),
    )
    op.create_table(
        "symbols",
        _id(),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("asset_class", sa.Text()),
        sa.Column("sector", sa.Text()),
        sa.Column("role", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # DECISION: three columns beyond §8, all nullable (IDEA-033).
        sa.Column("exchange_mic", sa.Text()),
        sa.Column("currency_code", sa.Text()),
        sa.Column("delisted_on", sa.Date()),
        sa.PrimaryKeyConstraint("id", name="pk_symbols"),
        sa.ForeignKeyConstraint(
            ["exchange_mic"], ["exchanges.mic"], name="fk_symbols_exchange_mic_exchanges"
        ),
        sa.ForeignKeyConstraint(
            ["currency_code"], ["currencies.code"], name="fk_symbols_currency_code_currencies"
        ),
    )
    # DECISION: §8's `ticker UNIQUE` is replaced, not kept (§6.5: a ticker is a mutable label that
    # gets reassigned). One ACTIVE symbol per (exchange, ticker). NULLS NOT DISTINCT (Postgres 15+,
    # the stack is 16) because exchange_mic is nullable: otherwise two rows with a NULL exchange and
    # the same ticker would both pass.
    op.execute(
        "CREATE UNIQUE INDEX uq_symbols_active_ticker ON symbols (exchange_mic, ticker) "
        "NULLS NOT DISTINCT WHERE active"
    )

    op.create_table(
        "provider_mappings",
        _id(),
        sa.Column("symbol_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("id_kind", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("retired_at", TZ),
        sa.PrimaryKeyConstraint("id", name="pk_provider_mappings"),
        sa.ForeignKeyConstraint(
            ["symbol_id"], ["symbols.id"], name="fk_provider_mappings_symbol_id_symbols"
        ),
        # DECISION: a CHECK, the one deliberate exception to the ops tables' no-CHECK convention
        # (IDEA-022 addendum). The index predicate below depends on the value of id_kind, so a new
        # kind must arrive in a migration that decides its uniqueness.
        sa.CheckConstraint("id_kind IN ('ticker', 'cik')", name="id_kind"),
        # EDGAR's published form: 10 digits, zero-padded (§6.5).
        sa.CheckConstraint("id_kind <> 'cik' OR external_id ~ '^[0-9]{10}$'", name="cik_form"),
    )
    # Approved 2026-11-10. symbol -> id holds for every kind: one active value per
    # (symbol, provider, id_kind).
    op.create_index(
        "uq_provider_mappings_symbol_active",
        "provider_mappings",
        ["symbol_id", "provider", "id_kind"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    # id -> symbol holds for every kind but cik: a live provider string names one symbol. GOOG and
    # GOOGL share a CIK, which is the main case CIK exists for, so cik is excluded.
    op.create_index(
        "uq_provider_mappings_external_active",
        "provider_mappings",
        ["provider", "id_kind", "external_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL AND id_kind <> 'cik'"),
    )

    op.create_table(
        "universes",
        _id(),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.PrimaryKeyConstraint("id", name="pk_universes"),
        sa.UniqueConstraint("slug", name="uq_universes_slug"),
    )
    op.create_table(
        "universe_membership",
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        # DECISION: half-open [valid_from, valid_to); valid_to is the first day NOT a member.
        sa.Column("valid_to", sa.Date()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("available_at", TZ, nullable=False),
        sa.PrimaryKeyConstraint(
            "universe_id", "entity_id", "valid_from", name="pk_universe_membership"
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["symbols.id"], name="fk_universe_membership_entity_id_symbols"
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"], ["universes.id"], name="fk_universe_membership_universe_id_universes"
        ),
        # DECISION: no CHECK on valid_to > valid_from and no exclusion constraint against
        # overlapping intervals (btree_gist would be a new extension). The seeder keeps at most one
        # open interval per (universe, entity); the CHECK exception is limited to id_kind.
    )

    op.create_table(
        "trading_sessions",
        sa.Column("calendar_code", sa.Text(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("opens_at", TZ, nullable=False),
        sa.Column("closes_at", TZ, nullable=False),
        sa.Column("is_early_close", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("calendar_code", "session_date", name="pk_trading_sessions"),
    )


def downgrade() -> None:
    # Safe, like 0002: nothing outside this revision references these tables yet. Later revisions
    # (bars, corporate_actions) will, and their downgrades run first.
    op.drop_table("trading_sessions")
    op.drop_table("universe_membership")
    op.drop_table("universes")
    op.drop_table("provider_mappings")
    op.drop_table("symbols")
    op.drop_table("exchanges")
    op.drop_table("currencies")

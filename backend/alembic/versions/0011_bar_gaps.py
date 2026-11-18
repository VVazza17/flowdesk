"""bar_gaps: the unrecoverable-gap record (M2 slice 6, step 2)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18

Kyle, 2026-11-15 (docs/BUILD-LOG.md, addendum fourth): the price poll is gap-driven, and a gap
longer than the provider's look-back (59 calendar days on yfinance's `start=`, decided 2026-11-16)
is RECORDED, never silently stored as a partial window. A record carries symbol, interval, the
missing range and when it was recorded. A gap later filled is marked resolved with a timestamp. It
is NEVER deleted: the history of what was missing is itself point-in-time data.

The range is two columns, half-open [missing_from, missing_to) over bar open instants, as
universe_membership's valid_from/valid_to; an exclusion constraint against overlap would need
btree_gist, a new extension (0003 declined it for the same reason). Gaps are computed against
trading_sessions by the ingest, so a holiday or an early close is never a gap.

`recorded_at`, not `ingested_at`: a gap is a fact about our own data, not a provider observation
(the reasoning of IDEA-034's universe_membership tripwire).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)

COMMENTS = {
    "id": "Surrogate key.",
    "symbol_id": "The symbol (symbols.id).",
    "interval": "Which bars are missing: '1d' (bars_daily) or '15m' (bars_intraday).",
    "missing_from": (
        "TIME COLUMN. The open instant of the first missing bar (inclusive). The range is "
        "half-open: [missing_from, missing_to)."
    ),
    "missing_to": (
        "TIME COLUMN. The first bar open instant AFTER the gap (exclusive): the first bar that "
        "is not missing."
    ),
    "recorded_at": "TIME COLUMN. When FlowDesk recorded the gap. Set once.",
    "ingestion_run_id": "The ingestion run that found the gap; cleared if that run is purged.",
    "resolved_at": (
        "TIME COLUMN. When the gap was filled; NULL while it is still missing. Set once, never "
        "cleared; the row is never deleted."
    ),
}
TABLE_COMMENT = (
    "Unrecoverable gaps in the stored bars: ranges beyond the provider's look-back. Never deleted; "
    "a filled gap gets resolved_at."
)


def upgrade() -> None:
    op.create_table(
        "bar_gaps",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
            comment=COMMENTS["id"],
        ),
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=COMMENTS["symbol_id"]),
        sa.Column("interval", sa.Text(), nullable=False, comment=COMMENTS["interval"]),
        sa.Column("missing_from", TZ, nullable=False, comment=COMMENTS["missing_from"]),
        sa.Column("missing_to", TZ, nullable=False, comment=COMMENTS["missing_to"]),
        sa.Column(
            "recorded_at",
            TZ,
            nullable=False,
            server_default=sa.text("now()"),
            comment=COMMENTS["recorded_at"],
        ),
        sa.Column(
            "ingestion_run_id", sa.BigInteger(), nullable=True, comment=COMMENTS["ingestion_run_id"]
        ),
        sa.Column("resolved_at", TZ, nullable=True, comment=COMMENTS["resolved_at"]),
        sa.PrimaryKeyConstraint("id"),
        # A re-run cannot record the same gap twice.
        sa.UniqueConstraint("symbol_id", "interval", "missing_from"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], ondelete="SET NULL"),
        sa.CheckConstraint("interval IN ('1d', '15m')", name="interval"),
        sa.CheckConstraint("missing_to > missing_from", name="range_not_empty"),
        # DECISION: a gap cannot be resolved before it was recorded.
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= recorded_at", name="resolved_after_recorded"
        ),
        comment=TABLE_COMMENT,
    )
    # Never deleted, and the only changes allowed are setting resolved_at once (NULL to a value)
    # and clearing ingestion_run_id (ON DELETE SET NULL). Everything else about a gap is fixed.
    op.execute("""
        CREATE FUNCTION bar_gaps_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND (to_jsonb(NEW) - 'resolved_at' - 'ingestion_run_id')
                   = (to_jsonb(OLD) - 'resolved_at' - 'ingestion_run_id')
               AND (NEW.resolved_at IS NOT DISTINCT FROM OLD.resolved_at
                    OR (OLD.resolved_at IS NULL AND NEW.resolved_at IS NOT NULL))
               AND (NEW.ingestion_run_id IS NULL
                    OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'bar_gaps records are never deleted or rewritten (% refused); the only change '
                'is setting resolved_at once (migration 0011)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END
        $$
    """)
    for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):
        op.execute(
            f"CREATE TRIGGER bar_gaps_no_{event.lower()} BEFORE {event} ON bar_gaps "
            f"FOR EACH {scope} EXECUTE FUNCTION bar_gaps_append_only()"
        )


def downgrade() -> None:
    op.drop_table("bar_gaps")
    op.execute("DROP FUNCTION bar_gaps_append_only()")

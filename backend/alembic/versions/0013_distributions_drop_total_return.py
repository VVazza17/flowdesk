"""distributions as dated events; bar_total_return dropped (M2 slice 6, step 5b)

Revision ID: 0013 Revises: 0012 Create Date: 2026-11-18

Kyle, 2026-11-18 (docs/BUILD-LOG.md, addendum seventh): total return is computed ON READ from
as-traded prices, `splits` and `distributions` knowable at T, by Yahoo's own convention (`1 - D /
Close_{E-1}` across an ex-date E, MEASURED 2026-11-18). D2 is superseded.

`bar_total_return` is DROPPED. It held Yahoo's Adj Close as fetched, on the fetch's basis: every
later split and dividend already applied, yet stamped at each bar's settle point. A replay at a past
T read events that had not happened (MEASURED 2026-11-18: XLK 2025-06-02 read 2.0162x off as of
2025-07-01; SPY 2025-03-03 read seven later dividends as of 2025-03-06). An unread table of values
stamped before they were knowable is a trap for the first direct query, and its contents can be
re-fetched. Nothing read it. DROP TABLE is not blocked by its row triggers.

`distributions` follows `splits` (0010): one row per (symbol, ex-date, kind), insert-only (the same
`insert_only_observation` trigger function), a disagreeing re-fetch a validation failure, never an
overwrite. Amounts are AS PAID: yfinance reports them on the split-adjusted basis (MEASURED
2026-11-18, all 20 ex-dates before the 2025-12-05 splits), so they are rebuilt x the ratios of later
splits, as prices are. `available_at` is the ex-date session's open (late, never early; ASSUMED
adequate). Backfilled rows carry `feed = 'backfill'` under the same recorded exception as bars.
Capital gains share the table (`kind`); none occurred in the 2-year window, so their treatment is
unmeasured until slice 7 checks the first one."""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)

COMMENTS = {
    "symbol_id": "The symbol (symbols.id).",
    "effective_at": (
        "TIME COLUMN. The ex-date: the exchange-local session date from which a buyer no longer "
        "receives the distribution. Bars before it are the ones its adjustment applies to."
    ),
    "kind": "dividend or capital_gain (both cash distributions; the same rule applies).",
    "amount": (
        "Per share, AS PAID at the time: yfinance's split-adjusted amount multiplied by the ratios "
        "of splits effective after the ex-date. Never rounded."
    ),
    "raw_basis": (
        "live (no split after the ex-date when fetched) or reconstructed (multiplied back by later "
        "split ratios)."
    ),
    "feed": (
        "poll or backfill. A strict replay excludes backfill rows (fetched after the ex-date, "
        "under "
        "the recorded backfill exception)."
    ),
    "available_at": (
        "TIME COLUMN. When the distribution became knowable: the open of its ex-date session (the "
        "declaration date is unknown; late, never early). The total return as of T applies only "
        "distributions with available_at <= T."
    ),
    "ingested_at": (
        "TIME COLUMN. When this row was written: when FlowDesk first saw the distribution. Never "
        "used for point-in-time filtering."
    ),
    "source": "The provider id ('yfinance').",
    "ingestion_run_id": (
        "The ingestion_runs row that wrote this row; cleared if that run is purged."
    ),
}
TABLE_COMMENT = (
    "Cash distributions (dividends, capital gains) as dated events, insert-only. Total return is "
    "computed on read from these, splits and as-traded prices knowable at T; it is never stored."
)


def upgrade() -> None:
    op.create_table(
        "distributions",
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=COMMENTS["symbol_id"]),
        sa.Column("effective_at", sa.Date(), nullable=False, comment=COMMENTS["effective_at"]),
        sa.Column("kind", sa.Text(), nullable=False, comment=COMMENTS["kind"]),
        sa.Column("amount", sa.Numeric(), nullable=False, comment=COMMENTS["amount"]),
        sa.Column("raw_basis", sa.Text(), nullable=False, comment=COMMENTS["raw_basis"]),
        sa.Column("feed", sa.Text(), nullable=False, comment=COMMENTS["feed"]),
        sa.Column("available_at", TZ, nullable=False, comment=COMMENTS["available_at"]),
        sa.Column(
            "ingested_at",
            TZ,
            nullable=False,
            server_default=sa.text("now()"),
            comment=COMMENTS["ingested_at"],
        ),
        sa.Column("source", sa.Text(), nullable=False, comment=COMMENTS["source"]),
        sa.Column(
            "ingestion_run_id", sa.BigInteger(), nullable=True, comment=COMMENTS["ingestion_run_id"]
        ),
        sa.PrimaryKeyConstraint("symbol_id", "effective_at", "kind"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], ondelete="SET NULL"),
        sa.CheckConstraint("kind IN ('dividend', 'capital_gain')", name="kind"),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.CheckConstraint("raw_basis IN ('live', 'reconstructed')", name="raw_basis"),
        sa.CheckConstraint("feed IN ('poll', 'backfill')", name="feed"),
        comment=TABLE_COMMENT,
    )
    for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):
        op.execute(
            f"CREATE TRIGGER distributions_no_{event.lower()} BEFORE {event} ON distributions "
            f"FOR EACH {scope} EXECUTE FUNCTION insert_only_observation()"
        )
    op.drop_table("bar_total_return")


# 0010's bar_total_return, exactly, so a downgrade restores the schema (not the rows).
_TR: dict[str, Any] = {
    "symbol_id": "The symbol (symbols.id).",
    "effective_at": (
        "TIME COLUMN. The daily bar this total return belongs to: its session's open instant, as "
        "bars_daily.effective_at."
    ),
    "revision": (
        "0 for the first value seen, +1 per detected change, per (symbol_id, effective_at). Each "
        "is "
        "a row; the current total return is the highest revision."
    ),
    "value": "The provider's total-return close (yfinance Adj Close, auto_adjust=False).",
    "available_at": (
        "TIME COLUMN. When this revision became knowable. Revision 0: the bar's settle point. A "
        "later revision: the fetch that saw it (no provider vintage date exists)."
    ),
    "ingested_at": (
        "TIME COLUMN. When this row was written. Never used for point-in-time filtering."
    ),
    "source": "The provider id ('yfinance').",
    "ingestion_run_id": (
        "The ingestion_runs row that wrote this row; cleared if that run is purged."
    ),
}


def downgrade() -> None:
    op.create_table(
        "bar_total_return",
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=_TR["symbol_id"]),
        sa.Column("effective_at", TZ, nullable=False, comment=_TR["effective_at"]),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=_TR["revision"],
        ),
        sa.Column("value", sa.Numeric(), nullable=False, comment=_TR["value"]),
        sa.Column("available_at", TZ, nullable=False, comment=_TR["available_at"]),
        sa.Column(
            "ingested_at",
            TZ,
            nullable=False,
            server_default=sa.text("now()"),
            comment=_TR["ingested_at"],
        ),
        sa.Column("source", sa.Text(), nullable=False, comment=_TR["source"]),
        sa.Column(
            "ingestion_run_id", sa.BigInteger(), nullable=True, comment=_TR["ingestion_run_id"]
        ),
        sa.PrimaryKeyConstraint("symbol_id", "effective_at", "revision"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], ondelete="SET NULL"),
        sa.CheckConstraint("available_at > effective_at", name="settled_after_open"),
        comment=(
            "Total return per daily bar, one row per revision, insert-only. The only home of total "
            "return; the current value is the highest revision."
        ),
    )
    for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):
        op.execute(
            f"CREATE TRIGGER bar_total_return_no_{event.lower()} BEFORE {event} "
            f"ON bar_total_return "
            f"FOR EACH {scope} EXECUTE FUNCTION insert_only_observation()"
        )
    op.drop_table("distributions")

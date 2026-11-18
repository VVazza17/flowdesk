"""price tables: bars_daily, bars_intraday, bar_total_return, splits (M2 slice 6, step 2; IDEA-046)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18

§8 gives one `bars` hypertable for every interval. Built instead, by Kyle's decision of 2026-11-18
(docs/BUILD-LOG.md, step 2 proposal and its third addendum; docs/SPEC-DEFECTS.md IDEA-046):

  bars_daily      settled daily bars. Hypertable, 365-day chunks.
  bars_intraday   settled intraday bars, `interval` limited to '15m'. Hypertable, 28-day chunks.
  bar_total_return  total return per daily bar, one row per revision (supersedes §8/D2's
                  bars.c_total_return). "Current total return" is the latest revision.
  splits          one row per split (D3 option b): stored prices are as traded, and the adjusted
                  read as of T applies only splits with available_at <= T.

Chunk intervals MEASURED 2026-11-18 (docs/evidence/2026-11-18-bars-step2-benchmark.txt): daily on
365-day chunks is 253 B/row with every read under 1 ms (756 B/row at 7 days); 15m does best at 28.

The vocabulary (IDEA-034) binds the names: `effective_at` (here an instant: the bar's open),
`available_at`, `ingested_at`, `revision`, `source`, `ingestion_run_id`. D5's `ts` was shorthand.

`available_at` on a bar is its SETTLE POINT (S, MEASURED 2026-11-18): 09:00 America/New_York on the
next session for 15m bars, on the second session after for daily bars. A bar is stored only after
it. Backfilled rows (`feed = 'backfill'`) carry the same settle point: a RECORDED EXCEPTION to the
unifying rule (Kyle, 2026-11-18). Trades are public at the close, so the bar was knowable then; but
a backfilled value was fetched later and may include provider corrections made after the settle
point. A strict replay excludes `feed = 'backfill'`.

Every table here is INSERT-ONLY, enforced by the database (the macro_metrics pattern, Kyle,
2026-11-18): `bars_*` hold the FIRST settled value and it is never overwritten. A re-fetch that
disagrees is a validation failure, detected in code, never an update. Known ways around the
triggers, all deliberate DDL and none a data path: DROP TABLE, and TimescaleDB's drop_chunks() on
the two hypertables (it drops chunk tables without row triggers firing). Nothing in FlowDesk calls
drop_chunks (IDEA-044 closed: no retention policy).
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)

# The comments live on the database objects so they show in \d+ and any SQL client, where the
# numbers are read. app/models/prices.py carries the same literals; `alembic check` fails if they
# differ. A test fails if any column here has no comment.
COMMON = {
    "symbol_id": "The symbol (symbols.id).",
    "o_raw": (
        "Open, as traded (D3 b): split adjustment is applied on read from splits, never stored."
    ),
    "h_raw": "High, as traded (D3 b).",
    "l_raw": "Low, as traded (D3 b).",
    "c_raw": "Close, as traded (D3 b).",
    "v_raw": (
        "Volume, as traded: the provider's split-adjusted volume divided by later split ratios."
    ),
    "raw_basis": (
        "How the as-traded prices were obtained: live (no split after the bar when fetched), "
        "reconstructed (provider-adjusted values multiplied back by later split ratios), or "
        "unreconstructed (a split could not be applied)."
    ),
    "source": "The provider id ('yfinance').",
    "feed": (
        "poll (the settled poll) or backfill (the recorded backfill command). A strict replay "
        "excludes backfill rows: their values were fetched after the settle point."
    ),
    "venue_scope": (
        "Which market view the volume covers. provider_default until consolidation is measured; "
        "never 'consolidated' before then (D1)."
    ),
    "ingested_at": (
        "TIME COLUMN. When this row was written (the fetch that stored it). Never used for "
        "point-in-time filtering."
    ),
    "ingestion_run_id": (
        "The ingestion_runs row that wrote this row; cleared if that run is purged."
    ),
}
BARS_DAILY = {
    **COMMON,
    "effective_at": (
        "TIME COLUMN. The instant the session opened (trading_sessions.opens_at), in UTC. The bar "
        "describes that whole session. Never cast it to a date: join trading_sessions."
    ),
    "available_at": (
        "TIME COLUMN. When the value became knowable: the settle point, 09:00 America/New_York on "
        "the second session after this one (D+2; S measured 2026-11-18). Point-in-time reads "
        "filter on this. For feed = backfill it is the same settle point, a recorded exception: "
        "the value was fetched later and may include corrections made after it."
    ),
}
BARS_INTRADAY = {
    **COMMON,
    "interval": "The bar length. '15m' only for now; another length arrives by migration.",
    "effective_at": (
        "TIME COLUMN. The instant the bar opened, in UTC (yfinance labels bars by their open, "
        "measured 2026-11-16). The bar covers [effective_at, effective_at + interval)."
    ),
    "available_at": (
        "TIME COLUMN. When the value became knowable: the settle point, 09:00 America/New_York on "
        "the next session after the bar's session (D+1; S measured 2026-11-18). Point-in-time "
        "reads filter on this. For feed = backfill it is the same settle point, a recorded "
        "exception: the value was fetched later and may include corrections made after it."
    ),
}
TOTAL_RETURN = {
    "symbol_id": "The symbol (symbols.id).",
    "effective_at": (
        "TIME COLUMN. The daily bar this total return belongs to: its session's open instant, "
        "as bars_daily.effective_at."
    ),
    "revision": (
        "0 for the first value seen, +1 per detected change, per (symbol_id, effective_at). Each "
        "is a row; the current total return is the highest revision."
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
SPLITS = {
    "symbol_id": "The symbol (symbols.id).",
    "effective_at": (
        "TIME COLUMN. The exchange-local session date the split takes effect: bars before it are "
        "multiplied back by ratio to give as-traded prices."
    ),
    "ratio": "New shares per old share (10 for a 10-for-1; below 1 for a reverse split).",
    "available_at": (
        "TIME COLUMN. When the split became knowable: the open of its effective session (the "
        "announcement date is unknown). The adjusted read as of T applies splits with "
        "available_at <= T."
    ),
    "ingested_at": (
        "TIME COLUMN. When this row was written: when FlowDesk first saw the split. Never used "
        "for point-in-time filtering."
    ),
    "source": "The provider id ('yfinance').",
    "ingestion_run_id": (
        "The ingestion_runs row that wrote this row; cleared if that run is purged."
    ),
}
TABLE_COMMENTS = {
    "bars_daily": (
        "Settled daily bars, as traded, insert-only: the first settled value, never overwritten. "
        "Stored only after the settle point (available_at)."
    ),
    "bars_intraday": (
        "Settled intraday bars, as traded, insert-only: the first settled value, never "
        "overwritten. Stored only after the settle point (available_at). Not the provisional "
        "in-session table (IDEA-045)."
    ),
    "bar_total_return": (
        "Total return per daily bar, one row per revision, insert-only. The only home of total "
        "return; the current value is the highest revision."
    ),
    "splits": "Stock splits, insert-only. Adjusted prices are derived from these on read (D3 b).",
}

HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "28 days"}
INSERT_ONLY = ["bars_daily", "bars_intraday", "bar_total_return", "splits"]


def _run_fk() -> sa.ForeignKeyConstraint:
    # DECISION: ON DELETE SET NULL, as macro_metrics: a purged run takes only the pointer.
    return sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], ondelete="SET NULL")


def _bar_columns(comments: dict[str, str]) -> list[sa.Column[Any]]:
    def c(name: str, type_: Any, **kw: Any) -> sa.Column[Any]:
        return sa.Column(name, type_, comment=comments[name], **kw)

    return [
        c("o_raw", sa.Numeric(), nullable=False),
        c("h_raw", sa.Numeric(), nullable=False),
        c("l_raw", sa.Numeric(), nullable=False),
        c("c_raw", sa.Numeric(), nullable=False),
        c("v_raw", sa.BigInteger(), nullable=False),
        c("raw_basis", sa.Text(), nullable=False),
        c("source", sa.Text(), nullable=False),
        c("feed", sa.Text(), nullable=False),
        c("venue_scope", sa.Text(), nullable=False),
        c("available_at", TZ, nullable=False),
        c("ingested_at", TZ, nullable=False, server_default=sa.text("now()")),
        c("ingestion_run_id", sa.BigInteger(), nullable=True),
    ]


def _bar_checks() -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "raw_basis IN ('live', 'reconstructed', 'unreconstructed')", name="raw_basis"
        ),
        # DECISION: feed is the closed pair poll | backfill (Kyle: "every backfilled row carries
        # feed = backfill"), so a strict replay's `feed <> 'backfill'` cannot miss a misspelling.
        sa.CheckConstraint("feed IN ('poll', 'backfill')", name="feed"),
        sa.CheckConstraint("venue_scope IN ('provider_default')", name="venue_scope"),
        # DECISION: a bar's settle point is always after its open. Safe here (unlike macro, where a
        # value can be knowable before its period); it catches an in-session or ts-valued
        # available_at. It cannot express D+k (that needs the calendar); the ingest computes it.
        sa.CheckConstraint("available_at > effective_at", name="settled_after_open"),
    ]


def upgrade() -> None:
    op.create_table(
        "bars_daily",
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=BARS_DAILY["symbol_id"]),
        sa.Column("effective_at", TZ, nullable=False, comment=BARS_DAILY["effective_at"]),
        *_bar_columns(BARS_DAILY),
        sa.PrimaryKeyConstraint("symbol_id", "effective_at"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        _run_fk(),
        *_bar_checks(),
        comment=TABLE_COMMENTS["bars_daily"],
    )
    op.create_table(
        "bars_intraday",
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=BARS_INTRADAY["symbol_id"]),
        sa.Column("interval", sa.Text(), nullable=False, comment=BARS_INTRADAY["interval"]),
        sa.Column("effective_at", TZ, nullable=False, comment=BARS_INTRADAY["effective_at"]),
        *_bar_columns(BARS_INTRADAY),
        sa.PrimaryKeyConstraint("symbol_id", "interval", "effective_at"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        _run_fk(),
        *_bar_checks(),
        sa.CheckConstraint("interval IN ('15m')", name="interval"),
        comment=TABLE_COMMENTS["bars_intraday"],
    )
    for table, chunk in HYPERTABLES.items():
        # DECISION: create_default_indexes => false, then the time index by name. TimescaleDB's
        # default is a DESC index the models cannot declare for `alembic check`; a plain btree on
        # effective_at serves both directions and is what the cross-section read uses (measured).
        op.execute(
            f"SELECT create_hypertable('{table}', 'effective_at', "
            f"chunk_time_interval => INTERVAL '{chunk}', create_default_indexes => false)"
        )
        op.create_index(f"ix_{table}_effective_at", table, ["effective_at"])

    op.create_table(
        "bar_total_return",
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=TOTAL_RETURN["symbol_id"]),
        sa.Column("effective_at", TZ, nullable=False, comment=TOTAL_RETURN["effective_at"]),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=TOTAL_RETURN["revision"],
        ),
        sa.Column("value", sa.Numeric(), nullable=False, comment=TOTAL_RETURN["value"]),
        sa.Column("available_at", TZ, nullable=False, comment=TOTAL_RETURN["available_at"]),
        sa.Column(
            "ingested_at",
            TZ,
            nullable=False,
            server_default=sa.text("now()"),
            comment=TOTAL_RETURN["ingested_at"],
        ),
        sa.Column("source", sa.Text(), nullable=False, comment=TOTAL_RETURN["source"]),
        sa.Column(
            "ingestion_run_id",
            sa.BigInteger(),
            nullable=True,
            comment=TOTAL_RETURN["ingestion_run_id"],
        ),
        # DECISION: a plain table (at most about 48,000 rows a year, ASSUMED), as macro_metrics.
        # DECISION: no FK to bars_daily. The ingest writes both in one transaction (D2); a foreign
        # key into a hypertable would couple the two for a guarantee the ingest already gives.
        sa.PrimaryKeyConstraint("symbol_id", "effective_at", "revision"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        _run_fk(),
        sa.CheckConstraint("available_at > effective_at", name="settled_after_open"),
        comment=TABLE_COMMENTS["bar_total_return"],
    )
    op.create_table(
        "splits",
        sa.Column("symbol_id", sa.BigInteger(), nullable=False, comment=SPLITS["symbol_id"]),
        sa.Column("effective_at", sa.Date(), nullable=False, comment=SPLITS["effective_at"]),
        sa.Column("ratio", sa.Numeric(), nullable=False, comment=SPLITS["ratio"]),
        sa.Column("available_at", TZ, nullable=False, comment=SPLITS["available_at"]),
        sa.Column(
            "ingested_at",
            TZ,
            nullable=False,
            server_default=sa.text("now()"),
            comment=SPLITS["ingested_at"],
        ),
        sa.Column("source", sa.Text(), nullable=False, comment=SPLITS["source"]),
        sa.Column(
            "ingestion_run_id", sa.BigInteger(), nullable=True, comment=SPLITS["ingestion_run_id"]
        ),
        sa.PrimaryKeyConstraint("symbol_id", "effective_at"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),
        _run_fk(),
        sa.CheckConstraint("ratio > 0", name="ratio_positive"),
        comment=TABLE_COMMENTS["splits"],
    )

    # Insert-only, enforced by the database (the macro_metrics pattern, migration 0005).
    # DECISION: one function for the four tables, comparing the whole row as jsonb minus
    # ingestion_run_id, instead of 0005's hand-written column list: a column added later is covered
    # without anyone remembering to add it. The one UPDATE allowed is clearing ingestion_run_id to
    # NULL, which ON DELETE SET NULL needs. DELETE and TRUNCATE are refused.
    op.execute("""
        CREATE FUNCTION insert_only_observation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND (to_jsonb(NEW) - 'ingestion_run_id') = (to_jsonb(OLD) - 'ingestion_run_id')
               AND (NEW.ingestion_run_id IS NULL
                    OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                '% is insert-only and its rows are immutable (% refused). A changed value is a new '
                'revision or a validation failure, never an update (migration 0010)',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END
        $$
    """)
    for table in INSERT_ONLY:
        for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):
            op.execute(
                f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} "
                f"FOR EACH {scope} EXECUTE FUNCTION insert_only_observation()"
            )


def downgrade() -> None:
    # Dropping a table drops its triggers and, for a hypertable, its chunks.
    for table in reversed(INSERT_ONLY):
        op.drop_table(table)
    op.execute("DROP FUNCTION insert_only_observation()")

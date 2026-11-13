"""series_history_state: which series' history is fully captured (M2 slice 5, step 3, commit B)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-13

A table section 8 does not have (the same kind of departure as `providers_health.first_seen_at` in
0004), added for two jobs and recorded as a DECISION rather than slipped in:

  1. THE SKIP. `python -m app.backfill` skips a series whose row here has the current
     `rule_version`,
     so a re-run costs only the incomplete series. "Fully captured" is a stored fact, not an
     inference from row counts: the row is written in the SAME transaction as the observations, so
     it cannot say "captured" without the rows (and a refused series writes neither). This is NOT
     resume: an interrupted series starts again from its first request.
  2. THE POLL'S WINDOW. `captured_through` is the latest vintage date the history held when it was
     captured; the daily poll asks FRED only for what changed from there.

`rule_version` is `app.ingest.macro.FRED_RULE_VERSION`. A change that makes the rules stricter, or
that changes what is stored, bumps it, and every series is captured again on purpose; for a
relaxation the count is checked first (ADR-0003 addendum, F2).

The counts are the series' own summary and are kept APART, never summed (ADR-0003 addendum,
2026-11-13, "Two exclusions, two rules, two counts"): `no_vintage_excluded` and
`gaps_without_trace_skipped` are exclusion A (no publication date exists; not recoverable);
`revised_pre_vintage_excluded` is exclusion B (every revision has a real date; the chain has no
beginning; recoverable data we chose not to model). A reader of this table must not be able to
read B as a loss of A's kind.

DECISION: unlike `macro_metrics` this table is mutable (a re-capture updates its row) and has no
triggers: it is bookkeeping about the data, not the data.
DECISION: part C's planned `ingestion_runs.task_run_id` migration is unbuilt and was going to be
0006; whichever lands second takes the next number (it is now 0008; see 0007).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "series_history_state",
        sa.Column("series_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("captured_through", sa.Date(), nullable=False),
        sa.Column("completed_at", TZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column("first_prints", sa.Integer(), nullable=False),
        sa.Column("revisions", sa.Integer(), nullable=False),
        sa.Column("no_vintage_excluded", sa.Integer(), nullable=False),
        sa.Column("gaps_without_trace_skipped", sa.Integer(), nullable=False),
        sa.Column("revised_pre_vintage_excluded", sa.Integer(), nullable=False),
        sa.Column("duplicates_skipped", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("series_id", name="pk_series_history_state"),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name="fk_series_history_state_ingestion_run_id_ingestion_runs",
            ondelete="SET NULL",
        ),
    )


def downgrade() -> None:
    op.drop_table("series_history_state")

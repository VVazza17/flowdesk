"""series_history_state: name the gap count for what was observed, add the gap-first-print count

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-13

1. RENAME `gaps_without_trace_skipped` to `gaps_without_o4_first_print_skipped`. The old name
   asserted
   an inference ("no trace of a publication exists") that proved false: VIXCLS's 32 market-holiday
   dates, counted as traceless by the first-print-only backfill, were published as "." in the
   2014-02-05 vintage, visible in `output_type=3` and absent from `output_type=4`. The new name says
   only what is observed. 0006 is left as it was applied; a migration is never edited afterwards.
2. ADD `gap_first_prints_without_o4_witness`: chains stored although `output_type=4` does not report
   their "." first print (ADR-0003 addendum 2026-11-13, F2). Kept apart from everything else.

DECISION: part C's planned `ingestion_runs.task_run_id` migration is now 0008.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "series_history_state",
        "gaps_without_trace_skipped",
        new_column_name="gaps_without_o4_first_print_skipped",
    )
    op.add_column(
        "series_history_state",
        sa.Column(
            "gap_first_prints_without_o4_witness",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # The default only backfills existing rows (captured before the count existed: 0 is true for
    # them, they were refused otherwise); new rows always supply the value.
    op.alter_column(
        "series_history_state", "gap_first_prints_without_o4_witness", server_default=None
    )


def downgrade() -> None:
    op.drop_column("series_history_state", "gap_first_prints_without_o4_witness")
    op.alter_column(
        "series_history_state",
        "gaps_without_o4_first_print_skipped",
        new_column_name="gaps_without_trace_skipped",
    )

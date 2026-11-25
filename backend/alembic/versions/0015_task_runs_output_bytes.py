"""task_runs.output_bytes: the size a task produced (the B2 off-box copy, 2026-11-25)

Revision ID: 0015 Revises: 0014 Create Date: 2026-11-25

Kyle, 2026-11-25 (docs/BUILD-LOG.md, addendum third): record the real dump size weekly in health,
because the "1 GB a year" growth figure is ASSUMED (a 500-symbol projection) and the B2 free tier
holds only while (copies kept) x (dump size) stays under 10 GB.

Two writers, one meaning, "bytes this run measured":
- `ops.backup`: the encrypted backup's size, on success;
- `ops.backup_copy`: every version in the B2 bucket, hidden ones included, whenever the run got as
  far as listing the bucket. A failed run can carry it too: `OverBudget` is a failure that measured.
The copy also sets `output_watermark` (a column 0002 already has) to the newest backup the bucket
holds.

DECISION: a column on `task_runs`, not a new table. The backup scripts already write `task_runs`
with psql, `/health/data` already reads it, and a size per run is one number. §8 gives `task_runs`
no such column; this is a departure in the manner of `providers_health.first_seen_at` (0004).
NULLABLE: every other task has no size, and rows already written cannot have one.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# app/models/ops.py has the same literal; `alembic check` fails if they differ.
COMMENT = (
    "Bytes this run measured, NULL for tasks that measure nothing. ops.backup: the encrypted "
    "backup's size. ops.backup_copy: every version in the B2 bucket, hidden ones included, "
    "whenever the run listed the bucket (also on a failed run, e.g. OverBudget)."
)


def upgrade() -> None:
    op.add_column(
        "task_runs", sa.Column("output_bytes", sa.BigInteger(), nullable=True, comment=COMMENT)
    )


def downgrade() -> None:
    op.drop_column("task_runs", "output_bytes")

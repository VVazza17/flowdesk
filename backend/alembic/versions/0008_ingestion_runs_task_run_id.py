"""ingestion_runs.task_run_id: which task execution an ingestion run belongs to (M2 slice 5, part C)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13

The link the sweep needs. An `ingestion_runs` row that is `running` is alive only if the task that
started it is: `task_runs` carries the heartbeat, `ingestion_runs` does not. With this column
the sweep
has one rule for both tables (docs/BUILD-LOG.md, step 3 plan, part C): a `running` ingestion run
whose `task_run_id` does not point at a `running` task with a fresh heartbeat is orphaned. A NULL
fails that test by itself, so it needs no special case.

NULLABLE, and rows already written cannot be backfilled: fake-provider runs and smokes have no task
to link to. That is the reason to add it now, before the first real FRED ingestion rows are mixed in
with them. No row written after this migration may be NULL: `run_feed` refuses to run outside a
recorded task (`app.ingest.errors.NotRecorded`).

ON DELETE SET NULL, the same as `macro_metrics.ingestion_run_id`: a purged ledger row neither takes
the run with it nor is blocked by it. No index: the table is small and the sweep reads only
`running` rows; revisit if it is ever slow.

DECISION: `backfill_runs` gets no column here and is not swept. A resumable backfill spans several
task runs by design (slice 10), so "is the linked task alive" is the wrong question for it
(app/tasks/sweep.py states the exclusion and its reason).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_runs", sa.Column("task_run_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_ingestion_runs_task_run_id_task_runs",
        "ingestion_runs",
        "task_runs",
        ["task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ingestion_runs_task_run_id_task_runs", "ingestion_runs", type_="foreignkey"
    )
    op.drop_column("ingestion_runs", "task_run_id")

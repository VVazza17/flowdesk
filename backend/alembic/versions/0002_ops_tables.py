"""ops tables: providers_health, ingestion_runs, task_runs, backfill_runs (§8)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "providers_health",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("last_success_at", TZ),
        sa.Column("last_error_at", TZ),
        sa.Column("last_error", sa.Text()),
        sa.PrimaryKeyConstraint("provider", "feed", name="pk_providers_health"),
    )
    op.create_table(
        "ingestion_runs",
        _id(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("finished_at", TZ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_read", sa.Integer()),
        sa.Column("rows_written", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
    )
    op.create_table(
        "task_runs",
        _id(),
        sa.Column("task_name", sa.Text(), nullable=False),
        sa.Column("scheduled_for", TZ),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("heartbeat_at", TZ),
        sa.Column("completed_at", TZ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer()),
        sa.Column("error_class", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("input_watermark", TZ),
        sa.Column("output_watermark", TZ),
        sa.PrimaryKeyConstraint("id", name="pk_task_runs"),
    )
    op.create_table(
        "backfill_runs",
        _id(),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("total_units", sa.Integer()),
        sa.Column("completed_units", sa.Integer()),
        sa.Column("current_unit", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("updated_at", TZ),
        sa.PrimaryKeyConstraint("id", name="pk_backfill_runs"),
    )


def downgrade() -> None:
    # Unlike the baseline's no-op, dropping these four tables is safe: nothing references them.
    op.drop_table("backfill_runs")
    op.drop_table("task_runs")
    op.drop_table("ingestion_runs")
    op.drop_table("providers_health")

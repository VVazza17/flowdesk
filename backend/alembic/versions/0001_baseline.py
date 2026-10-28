"""baseline: timescaledb extension, no tables

Revision ID: 0001
Revises:
Create Date: 2026-10-28
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DECISION: the migration owns the extension rather than relying on the image's init
    # scripts, so a restore into a fresh database (slice 9) works on any Postgres that has
    # timescaledb installed. IF NOT EXISTS makes it a no-op on the compose image.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def downgrade() -> None:
    # DECISION: deliberately a no-op. Dropping the extension would cascade to every
    # hypertable added by later revisions; downgrade-to-base should never be destructive.
    pass

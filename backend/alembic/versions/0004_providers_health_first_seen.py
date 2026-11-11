"""providers_health.first_seen_at (§16 M2 slice 4, IDEA-026)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11

A departure from §8's column list, approved 2026-11-11 and recorded in the IDEA-026 addendum:
without it a feed that never succeeds has no age and can never be called stale. The table is empty
when this lands (no feed is registered before slice 4), so NOT NULL with a default is safe; the
default also covers a row inserted by hand.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "providers_health",
        sa.Column(
            "first_seen_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("providers_health", "first_seen_at")

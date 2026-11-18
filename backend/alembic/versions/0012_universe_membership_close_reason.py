"""universe_membership.close_reason (M2 slice 6, step 2; D4)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-18

D4 (Kyle, 2026-11-15): a symbol leaves a universe only by an operator command that sets valid_to,
never by a delete, and it must say why: delisted, merged or removed_by_operator. No "who" column
(one operator). The check is TWO-WAY (Kyle, 2026-11-15, addendum fourth): a reason on an open
membership is as wrong as a closed membership with no reason.

No backfill: the live table had 24 rows and none with valid_to set (MEASURED 2026-11-18, dev
database), and the seed never closes an interval, so both CHECKs hold on every existing row.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

COMMENT = (
    "Why the membership ended: delisted, merged or removed_by_operator. Set exactly when valid_to "
    "is set, by the operator command; NULL while the symbol is a member."
)


def upgrade() -> None:
    op.add_column(
        "universe_membership", sa.Column("close_reason", sa.Text(), nullable=True, comment=COMMENT)
    )
    op.create_check_constraint(
        "close_reason_iff_closed",
        "universe_membership",
        "(valid_to IS NULL) = (close_reason IS NULL)",
    )
    op.create_check_constraint(
        "close_reason_values",
        "universe_membership",
        "close_reason IN ('delisted', 'merged', 'removed_by_operator')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_universe_membership_close_reason_values"), "universe_membership")
    op.drop_constraint(
        op.f("ck_universe_membership_close_reason_iff_closed"), "universe_membership"
    )
    op.drop_column("universe_membership", "close_reason")

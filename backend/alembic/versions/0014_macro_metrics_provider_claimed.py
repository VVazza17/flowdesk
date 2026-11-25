"""macro_metrics.provider_claimed_available_at: the audit column of ADR-0003 addendum F3

Revision ID: 0014 Revises: 0013 Create Date: 2026-11-24

F3 (approved by Kyle 2026-11-20, rule (c) corrected the same day; built 2026-11-24): a period that
arrives after FlowDesk has captured past it takes `available_at` = when FlowDesk first observed it
(the accepting run's start), never FRED's earlier date. FRED's claimed date is kept HERE, for audit
only. No point-in-time read uses it (`app/services/macro_read.py` filters on `available_at`). The
ingest's own comparison with FRED (`macro_store.read_chains`) reads it back as the vintage, because
that comparison is with FRED's record, not with what was knowable.

NULL on every ordinary row. The CHECK says what a non-NULL value means: FRED claimed an EARLIER
instant than FlowDesk could observe. A claimed instant at or after `available_at` is not a late
arrival, and storing one would be a bug.

The immutability trigger function (0005) compares an explicit list of columns, so a column it does
not name could be UPDATEd freely. This migration replaces the function so the new column is in the
list; the downgrade restores 0005's text exactly. The triggers themselves are unchanged (they call
the function by name).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)

# app/models/macro.py has the same literal; `alembic check` fails if they differ.
COMMENT = (
    "AUDIT ONLY; no point-in-time read uses it. NULL on every ordinary row. Set only on an F3 late "
    "arrival (ADR-0003 addendum F3): a period FRED added to a vintage dated before FlowDesk could "
    "observe it there. Such a row's available_at is when FlowDesk first observed it (the accepting "
    "run's start); this column is the instant FRED's claimed vintage date would have given it "
    "(available_at_for(vintage)). The ingest's own comparison with FRED reads it back as that "
    "vintage; nothing else reads it."
)

# The naming convention (app/models/base.py) prefixes ck_macro_metrics_ to CHECK; the downgrade
# drops it by that full name.
CHECK = "provider_claimed_before_available"
CHECK_FULL = "ck_macro_metrics_provider_claimed_before_available"

_FUNCTION = """
    CREATE OR REPLACE FUNCTION macro_metrics_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE'
           AND ROW(NEW.metric, NEW.effective_at, NEW.revision, NEW.value, NEW.available_at,
                   NEW.ingested_at, NEW.source{new_extra})
               IS NOT DISTINCT FROM
               ROW(OLD.metric, OLD.effective_at, OLD.revision, OLD.value, OLD.available_at,
                   OLD.ingested_at, OLD.source{old_extra})
           AND (NEW.ingestion_run_id IS NULL
                OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION
            'macro_metrics vintages are immutable (% refused). A revision is a new row '
            '(ADR-0003); a genuine repair follows docs/RUNBOOK-VINTAGE-REPAIR.md', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END
    $$
"""


def upgrade() -> None:
    op.add_column(
        "macro_metrics",
        sa.Column("provider_claimed_available_at", TZ, nullable=True, comment=COMMENT),
    )
    op.create_check_constraint(
        CHECK,
        "macro_metrics",
        "provider_claimed_available_at IS NULL OR provider_claimed_available_at < available_at",
    )
    op.execute(
        _FUNCTION.format(
            new_extra=", NEW.provider_claimed_available_at",
            old_extra=", OLD.provider_claimed_available_at",
        )
    )


def downgrade() -> None:
    op.execute(_FUNCTION.format(new_extra="", old_extra=""))
    op.drop_constraint(op.f(CHECK_FULL), "macro_metrics", type_="check")
    op.drop_column("macro_metrics", "provider_claimed_available_at")

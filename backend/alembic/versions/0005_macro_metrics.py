"""macro_metrics: point-in-time macro observations (§16 M2 slice 5, step 1; ADR-0003; IDEA-034/038)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11

§8 gives `macro_metrics(metric, effective_ts, value, available_at, revision, source,
ingestion_run_id)` with PRIMARY KEY (metric, effective_ts, revision). This is that table, built to
the provenance vocabulary settled at slice 5 planning (Kyle, 2026-11-11), which binds every
observation table:

  effective_at      the period the value describes. DATE, not a midnight TIMESTAMPTZ: a quarterly
                    figure has no time of day, and 2025-01-01T00:00Z renders as 2024-12-31 in
                    Toronto. §8's `effective_ts` is superseded by this name (IDEA-038). The
                    vocabulary binds names and meanings, not types: a bar is an instant, a macro
                    observation is a period.
  available_at      when the value became knowable; the column every point-in-time read filters on.
  ingested_at       when we wrote the row. Never used for point-in-time filtering.
  revision          0 for the first print, +1 per detected change, per (metric, effective_at). Each
                    is a row; a vintage is never overwritten (ADR-0003 decision 1).
  source            the provider id ('fred').
  ingestion_run_id  the ingestion_runs row that wrote it (§8 has it; the settled list keeps it).

`value` is nullable on purpose: FRED's "." is a genuine gap (ADR-0003 decision 7). A gap is a stored
row with value NULL, so the period still exists and nothing can read it as 0.0 or forward-fill
over it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    # DECISION: a plain table, not a hypertable. §8 does not mark it with the hypertable symbol,
    # there are about 17 series with at most tens of thousands of rows each, and 0003 set the
    # precedent for small tables. Revisit only with a measured reason.
    # DECISION: no index beyond the primary key. A point-in-time read of one metric is a prefix
    # scan of the key over a few thousand rows. Add (metric, available_at) when a measured query
    # needs it.
    # DECISION: no CHECK on revision, following the ops tables' convention (0003 made one deliberate
    # exception, for a reason that does not apply here).
    op.create_table(
        "macro_metrics",
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Unconstrained NUMERIC: the values are exact decimals from the provider (23526.085).
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("available_at", TZ, nullable=False),
        sa.Column("ingested_at", TZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("metric", "effective_at", "revision", name="pk_macro_metrics"),
        # DECISION: ON DELETE SET NULL. A purged ledger row must neither take the observation
        # with it nor be blocked by it; only the pointer goes, the vintage is untouched.
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name="fk_macro_metrics_ingestion_run_id_ingestion_runs",
            ondelete="SET NULL",
        ),
    )

    # Vintages are immutable, and the database enforces it (Kyle, 2026-11-11). The primary key stops
    # a duplicate vintage but not an UPDATE, and an overwritten vintage is the one failure here that
    # is both catastrophic and undetectable: a replay would silently read the revised number as if
    # it had been known all along. So UPDATE, DELETE and TRUNCATE are refused.
    #
    # DECISION: the one change an UPDATE may make is clearing ingestion_run_id to NULL. The
    # foreign key's ON DELETE SET NULL is itself an UPDATE; without this carve-out a purged
    # ingestion_runs row could not be deleted while an observation pointed at it. Provenance
    # may be forgotten, never rewritten to claim a different run, and no value or timestamp
    # can change.
    # DECISION: DELETE and TRUNCATE are blocked too. Nothing legitimately deletes from this
    # table: re-ingesting is idempotent and appends, pg_restore loads with COPY into an empty
    # database, and DROP TABLE (this migration's downgrade) is not blocked by a row trigger.
    # The way around it, for a genuine repair that must leave a trace:
    # docs/RUNBOOK-VINTAGE-REPAIR.md.
    op.execute("""
        CREATE FUNCTION macro_metrics_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND ROW(NEW.metric, NEW.effective_at, NEW.revision, NEW.value, NEW.available_at,
                       NEW.ingested_at, NEW.source)
                   IS NOT DISTINCT FROM
                   ROW(OLD.metric, OLD.effective_at, OLD.revision, OLD.value, OLD.available_at,
                       OLD.ingested_at, OLD.source)
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
    """)
    op.execute(
        "CREATE TRIGGER macro_metrics_no_update BEFORE UPDATE ON macro_metrics "
        "FOR EACH ROW EXECUTE FUNCTION macro_metrics_immutable()"
    )
    op.execute(
        "CREATE TRIGGER macro_metrics_no_delete BEFORE DELETE ON macro_metrics "
        "FOR EACH ROW EXECUTE FUNCTION macro_metrics_immutable()"
    )
    op.execute(
        "CREATE TRIGGER macro_metrics_no_truncate BEFORE TRUNCATE ON macro_metrics "
        "FOR EACH STATEMENT EXECUTE FUNCTION macro_metrics_immutable()"
    )


def downgrade() -> None:
    # Dropping the table drops its triggers; the function is a separate object.
    op.drop_table("macro_metrics")
    op.execute("DROP FUNCTION macro_metrics_immutable()")

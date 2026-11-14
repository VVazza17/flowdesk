"""series_history_state: say on the columns which vintage of truth each one carries

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13

The row carries TWO vintages of truth, and a reader who sees one number next to another will read
them as contemporaneous:

  CURRENT (advances with every daily poll): captured_through, completed_at,
  ingestion_run_id, first_prints, revisions.

  AS OF THE LAST FULL CAPTURE (`python -m app.backfill`), NOT CURRENT: no_vintage_excluded,
  gaps_without_o4_first_print_skipped, gap_first_prints_without_o4_witness,
  revised_pre_vintage_excluded, duplicates_skipped. The poll never updates these, because its
  verdict sees pre-vintage and gap periods only where its window happens to touch them, and
  overwriting a full capture's counts with that partial view would be worse than leaving them.

So `captured_through` can be a week newer than the exclusion counts beside it. The comments are
on the database objects so that they show in `\\d+` and in any SQL client, where the numbers are
actually read. A test fails if a column has no comment, so a column added later must say
which vintage it belongs to.

Comments only: no schema change, and nothing to migrate.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLE = (
    "One row per FRED series. TWO vintages of truth live in it: captured_through, completed_at, "
    "ingestion_run_id, first_prints and revisions are CURRENT (advanced by every daily poll); the "
    "exclusion and duplicate counts are AS OF THE LAST FULL CAPTURE (python -m app.backfill) and "
    "the poll never updates them. Do not read them as contemporaneous."
)
NOT_CURRENT = (
    "AS OF THE LAST FULL CAPTURE, NOT CURRENT. The daily poll never updates this column (its "
    "window sees only the periods it touches), so after polls it can lag the true figure; "
    "captured_through is newer than this count. "
)
COLUMNS = {
    "series_id": "The FRED series id. Primary key.",
    "rule_version": (
        "The FRED_RULE_VERSION the history was captured under. The poll refuses a series whose "
        "version is not the code's (NoBaseline); python -m app.backfill skips a series whose "
        "version is."
    ),
    "captured_through": (
        "CURRENT. The latest vintage date this series' stored history holds. Advanced by every "
        "daily poll; the next poll asks FRED only for vintages from this date (inclusive)."
    ),
    "completed_at": "CURRENT. When the last capture OR daily poll of this series completed.",
    "ingestion_run_id": "CURRENT. The ingestion run of the last capture or poll.",
    "first_prints": (
        "CURRENT as of captured_through. Periods with a stored first print, over the whole stored "
        "history; refreshed by every poll."
    ),
    "revisions": (
        "CURRENT as of captured_through. Stored revisions after the first print, over the whole "
        "stored history; refreshed by every poll."
    ),
    "no_vintage_excluded": NOT_CURRENT
    + (
        "Exclusion A: periods before the first vintage that were never revised, so no publication "
        "date exists. Not recoverable."
    ),
    "gaps_without_o4_first_print_skipped": NOT_CURRENT
    + (
        "Exclusion A: a latest-value '.' found in neither output_type=3 nor output_type=4 at or "
        "after the first print. Not stored."
    ),
    "gap_first_prints_without_o4_witness": NOT_CURRENT
    + (
        "Chains stored although output_type=4 does not report their '.' first print (ADR-0003 "
        "addendum F2); no second source corroborates them."
    ),
    "revised_pre_vintage_excluded": NOT_CURRENT
    + (
        "Exclusion B: periods before the first vintage revised after the record began. Every "
        "revision has a real date but the chain has no beginning. Recoverable data we chose not "
        "to model."
    ),
    "duplicates_skipped": NOT_CURRENT
    + "Columns equal to the one before them, which are not revisions.",
}


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute(f"COMMENT ON TABLE series_history_state IS {_literal(TABLE)}")
    for column, text in COLUMNS.items():
        op.execute(f"COMMENT ON COLUMN series_history_state.{column} IS {_literal(text)}")


def downgrade() -> None:
    op.execute("COMMENT ON TABLE series_history_state IS NULL")
    for column in COLUMNS:
        op.execute(f"COMMENT ON COLUMN series_history_state.{column} IS NULL")

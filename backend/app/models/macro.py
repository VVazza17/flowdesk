"""macro_metrics (§8, ADR-0003, M2 slice 5): point-in-time macro observations, one row per vintage.

Migration 0005 is the schema and carries the reasoning; this module mirrors it for queries. It is
the first table built to the shared provenance vocabulary (docs/SPEC-DEFECTS.md, IDEA-034):
`effective_at`, `available_at`, `ingested_at`, `revision`, `source`, plus `ingestion_run_id`.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TZ = TIMESTAMP(timezone=True)

# Migration 0014 has the same literal, and `alembic check` fails if they differ.
PROVIDER_CLAIMED_COMMENT = (
    "AUDIT ONLY; no point-in-time read uses it. NULL on every ordinary row. Set only on an F3 late "
    "arrival (ADR-0003 addendum F3): a period FRED added to a vintage dated before FlowDesk could "
    "observe it there. Such a row's available_at is when FlowDesk first observed it (the accepting "
    "run's start); this column is the instant FRED's claimed vintage date would have given it "
    "(available_at_for(vintage)). The ingest's own comparison with FRED reads it back as that "
    "vintage; nothing else reads it."
)


class MacroMetric(Base):
    __tablename__ = "macro_metrics"

    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    # The period the value describes. A DATE, not an instant: it cannot shift with a time zone.
    effective_at: Mapped[date] = mapped_column(Date, primary_key=True)
    # 0 = first print; each later change is a new row, never an update (ADR-0003 decision 1).
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("0"))
    # NULL is a genuine gap (FRED's "."), stored as a row: never 0.0, never forward-filled.
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    # When it became knowable. Every point-in-time read filters on this, on row existence.
    available_at: Mapped[datetime] = mapped_column(TZ)
    # When we wrote it. Never used to filter point-in-time reads.
    ingested_at: Mapped[datetime] = mapped_column(TZ, server_default=text("now()"))
    source: Mapped[str] = mapped_column(Text)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_runs.id", ondelete="SET NULL")
    )
    # Migration 0014 (ADR-0003 addendum F3). AUDIT ONLY: no point-in-time read uses it.
    provider_claimed_available_at: Mapped[datetime | None] = mapped_column(
        TZ, comment=PROVIDER_CLAIMED_COMMENT
    )


# The words on the database objects. Migration 0009 has the same literals, and `alembic check` fails
# if they differ, which is the point: the semantics must not drift between model and database.
_STATE_TABLE_COMMENT = (
    "One row per FRED series. TWO vintages of truth live in it: captured_through, "
    "completed_at, ingestion_run_id, first_prints and revisions are CURRENT (advanced by "
    "every daily poll); the exclusion and duplicate counts are AS OF THE LAST FULL "
    "CAPTURE (python -m app.backfill) and the poll never updates them. Do not read them "
    "as contemporaneous."
)

_STATE_COMMENTS = {
    "series_id": ("The FRED series id. Primary key."),
    "rule_version": (
        "The FRED_RULE_VERSION the history was captured under. The poll refuses a series "
        "whose version is not the code's (NoBaseline); python -m app.backfill skips a series "
        "whose version is."
    ),
    "captured_through": (
        "CURRENT. The latest vintage date this series' stored history holds. Advanced by "
        "every daily poll; the next poll asks FRED only for vintages from this date "
        "(inclusive)."
    ),
    "completed_at": ("CURRENT. When the last capture OR daily poll of this series completed."),
    "ingestion_run_id": ("CURRENT. The ingestion run of the last capture or poll."),
    "first_prints": (
        "CURRENT as of captured_through. Periods with a stored first print, over the whole "
        "stored history; refreshed by every poll."
    ),
    "revisions": (
        "CURRENT as of captured_through. Stored revisions after the first print, over the "
        "whole stored history; refreshed by every poll."
    ),
    "no_vintage_excluded": (
        "AS OF THE LAST FULL CAPTURE, NOT CURRENT. The daily poll never updates this column "
        "(its window sees only the periods it touches), so after polls it can lag the true "
        "figure; captured_through is newer than this count. Exclusion A: periods before the "
        "first vintage that were never revised, so no publication date exists. Not "
        "recoverable."
    ),
    "gaps_without_o4_first_print_skipped": (
        "AS OF THE LAST FULL CAPTURE, NOT CURRENT. The daily poll never updates this column "
        "(its window sees only the periods it touches), so after polls it can lag the true "
        "figure; captured_through is newer than this count. Exclusion A: a latest-value '.' "
        "found in neither output_type=3 nor output_type=4 at or after the first print. Not "
        "stored."
    ),
    "gap_first_prints_without_o4_witness": (
        "AS OF THE LAST FULL CAPTURE, NOT CURRENT. The daily poll never updates this column "
        "(its window sees only the periods it touches), so after polls it can lag the true "
        "figure; captured_through is newer than this count. Chains stored although "
        "output_type=4 does not report their '.' first print (ADR-0003 addendum F2); no "
        "second source corroborates them."
    ),
    "revised_pre_vintage_excluded": (
        "AS OF THE LAST FULL CAPTURE, NOT CURRENT. The daily poll never updates this column "
        "(its window sees only the periods it touches), so after polls it can lag the true "
        "figure; captured_through is newer than this count. Exclusion B: periods before the "
        "first vintage revised after the record began. Every revision has a real date but the"
        " chain has no beginning. Recoverable data we chose not to model."
    ),
    "duplicates_skipped": (
        "AS OF THE LAST FULL CAPTURE, NOT CURRENT. The daily poll never updates this column "
        "(its window sees only the periods it touches), so after polls it can lag the true "
        "figure; captured_through is newer than this count. Columns equal to the one before "
        "them, which are not revisions."
    ),
}


class SeriesHistoryState(Base):
    """Which series' revision history is fully captured (migration 0006 carries the reasoning).

    A row exists only if the history was written, in the same transaction. The exclusion counts are
    separate columns on purpose: A (no vintage exists) is not B (revised but pre-vintage).

    TWO VINTAGES OF TRUTH LIVE IN THIS ROW (the same text is on the database columns, migration
    0009). `captured_through`, `completed_at`, `ingestion_run_id`, `first_prints` and `revisions`
    are CURRENT: the daily poll advances them. The five exclusion and duplicate counts
    (`no_vintage_excluded`, `gaps_without_o4_first_print_skipped`,
    `gap_first_prints_without_o4_witness`, `revised_pre_vintage_excluded`, `duplicates_skipped`) are
    AS OF THE LAST FULL CAPTURE (`python -m app.backfill`), and the poll never updates them
    (`commit_history(exclusion_counts=False)`): its window sees pre-vintage and gap periods only
    where it touches them. So `captured_through` can be a week newer than the counts beside it. Do
    not read them as contemporaneous.
    """

    __tablename__ = "series_history_state"
    __table_args__ = {"comment": _STATE_TABLE_COMMENT}

    series_id: Mapped[str] = mapped_column(
        Text, primary_key=True, comment=_STATE_COMMENTS["series_id"]
    )
    rule_version: Mapped[int] = mapped_column(Integer, comment=_STATE_COMMENTS["rule_version"])
    captured_through: Mapped[date] = mapped_column(
        Date, comment=_STATE_COMMENTS["captured_through"]
    )
    completed_at: Mapped[datetime] = mapped_column(
        TZ, server_default=text("now()"), comment=_STATE_COMMENTS["completed_at"]
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        comment=_STATE_COMMENTS["ingestion_run_id"],
    )
    first_prints: Mapped[int] = mapped_column(Integer, comment=_STATE_COMMENTS["first_prints"])
    revisions: Mapped[int] = mapped_column(Integer, comment=_STATE_COMMENTS["revisions"])
    no_vintage_excluded: Mapped[int] = mapped_column(
        Integer, comment=_STATE_COMMENTS["no_vintage_excluded"]
    )
    gaps_without_o4_first_print_skipped: Mapped[int] = mapped_column(
        Integer, comment=_STATE_COMMENTS["gaps_without_o4_first_print_skipped"]
    )
    gap_first_prints_without_o4_witness: Mapped[int] = mapped_column(
        Integer, comment=_STATE_COMMENTS["gap_first_prints_without_o4_witness"]
    )
    revised_pre_vintage_excluded: Mapped[int] = mapped_column(
        Integer, comment=_STATE_COMMENTS["revised_pre_vintage_excluded"]
    )
    duplicates_skipped: Mapped[int] = mapped_column(
        Integer, comment=_STATE_COMMENTS["duplicates_skipped"]
    )

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

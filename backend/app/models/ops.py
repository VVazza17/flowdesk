"""Ops tables (§8): provider health, ingestion/task/backfill run ledgers.

Columns are exactly the §8 lists. Everything §8 leaves open (types, nullability, status
vocabularies) is a stopgap logged as IDEA-022/023/024 in docs/SPEC-DEFECTS.md.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Identity, Integer, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# DECISION: every timestamp is TIMESTAMPTZ (matches bars.ts); §8 leaves them untyped.
TZ = TIMESTAMP(timezone=True)


class ProvidersHealth(Base):
    __tablename__ = "providers_health"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    feed: Mapped[str] = mapped_column(Text, primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(TZ)
    last_error_at: Mapped[datetime | None] = mapped_column(TZ)
    last_error: Mapped[str | None] = mapped_column(Text)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    # DECISION: BIGINT identity ids, no UUIDs.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    feed: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    # DECISION: TEXT, no CHECK — §8 gives no status vocabulary and we do not invent one.
    status: Mapped[str] = mapped_column(Text)
    rows_read: Mapped[int | None] = mapped_column(Integer)
    rows_written: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    task_name: Mapped[str] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(TZ)
    started_at: Mapped[datetime] = mapped_column(TZ)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TZ)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    # DECISION: TIMESTAMPTZ, matching source_watermarks.watermark_at (§8). This assumes every
    # feed watermarks temporally; a feed needing a sequence number or cursor breaks it (IDEA-022).
    input_watermark: Mapped[datetime | None] = mapped_column(TZ)
    output_watermark: Mapped[datetime | None] = mapped_column(TZ)


class BackfillRun(Base):
    __tablename__ = "backfill_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # DECISION: no provider column and no uniqueness on feed — §8 specifies neither (IDEA-022).
    feed: Mapped[str] = mapped_column(Text)
    total_units: Mapped[int | None] = mapped_column(Integer)
    completed_units: Mapped[int | None] = mapped_column(Integer)
    current_unit: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime | None] = mapped_column(TZ)

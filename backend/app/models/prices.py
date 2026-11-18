"""Price tables (M2 slice 6, step 2; IDEA-046): bars_daily, bars_intraday, bar_total_return,
splits, bar_gaps.

Migrations 0010 and 0011 are the schema and carry the reasoning; this module mirrors them for
queries and for `alembic check`. The comment literals are copied from the migrations, so
`alembic check` fails if the words on the database and here drift apart.

All five are insert-only, enforced by database triggers (0010, 0011): `bars_daily` and
`bars_intraday` hold the FIRST settled value, never overwritten. `available_at` on a bar is its
settle point; point-in-time reads filter on it, never on `effective_at`.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.models.base import Base

TZ = TIMESTAMP(timezone=True)

COMMON = {
    "symbol_id": "The symbol (symbols.id).",
    "o_raw": "Open, as traded (D3 b): split adjustment is applied on read from splits, "
    "never stored.",
    "h_raw": "High, as traded (D3 b).",
    "l_raw": "Low, as traded (D3 b).",
    "c_raw": "Close, as traded (D3 b).",
    "v_raw": "Volume, as traded: the provider's split-adjusted volume divided by later "
    "split ratios.",
    "raw_basis": "How the as-traded prices were obtained: live (no split after the bar "
    "when fetched), reconstructed (provider-adjusted values multiplied back "
    "by later split ratios), or unreconstructed (a split could not be "
    "applied).",
    "source": "The provider id ('yfinance').",
    "feed": "poll (the settled poll) or backfill (the recorded backfill command). A "
    "strict replay excludes backfill rows: their values were fetched after the "
    "settle point.",
    "venue_scope": "Which market view the volume covers. provider_default until "
    "consolidation is measured; never 'consolidated' before then (D1).",
    "ingested_at": "TIME COLUMN. When this row was written (the fetch that stored it). "
    "Never used for point-in-time filtering.",
    "ingestion_run_id": "The ingestion_runs row that wrote this row; cleared if that run "
    "is purged.",
}
BARS_DAILY = {
    **COMMON,
    "effective_at": (
        "TIME COLUMN. The instant the session opened (trading_sessions.opens_at), in UTC. The "
        "bar describes that whole session. Never cast it to a date: join trading_sessions."
    ),
    "available_at": (
        "TIME COLUMN. When the value became knowable: the settle point, 09:00 "
        "America/New_York on the second session after this one (D+2; S measured 2026-11-18). "
        "Point-in-time reads filter on this. For feed = backfill it is the same settle point, "
        "a recorded exception: the value was fetched later and may include corrections made "
        "after it."
    ),
}
BARS_INTRADAY = {
    **COMMON,
    "interval": "The bar length. '15m' only for now; another length arrives by migration.",
    "effective_at": (
        "TIME COLUMN. The instant the bar opened, in UTC (yfinance labels bars by their open, "
        "measured 2026-11-16). The bar covers [effective_at, effective_at + interval)."
    ),
    "available_at": (
        "TIME COLUMN. When the value became knowable: the settle point, 09:00 "
        "America/New_York on the next session after the bar's session (D+1; S measured "
        "2026-11-18). Point-in-time reads filter on this. For feed = backfill it is the same "
        "settle point, a recorded exception: the value was fetched later and may include "
        "corrections made after it."
    ),
}
TOTAL_RETURN = {
    "symbol_id": "The symbol (symbols.id).",
    "effective_at": "TIME COLUMN. The daily bar this total return belongs to: its "
    "session's open instant, as bars_daily.effective_at.",
    "revision": "0 for the first value seen, +1 per detected change, per (symbol_id, "
    "effective_at). Each is a row; the current total return is the highest "
    "revision.",
    "value": "The provider's total-return close (yfinance Adj Close, auto_adjust=False).",
    "available_at": "TIME COLUMN. When this revision became knowable. Revision 0: the "
    "bar's settle point. A later revision: the fetch that saw it (no "
    "provider vintage date exists).",
    "ingested_at": "TIME COLUMN. When this row was written. Never used for point-in-time "
    "filtering.",
    "source": "The provider id ('yfinance').",
    "ingestion_run_id": "The ingestion_runs row that wrote this row; cleared if that run "
    "is purged.",
}
SPLITS = {
    "symbol_id": "The symbol (symbols.id).",
    "effective_at": "TIME COLUMN. The exchange-local session date the split takes effect: "
    "bars before it are multiplied back by ratio to give as-traded "
    "prices.",
    "ratio": "New shares per old share (10 for a 10-for-1; below 1 for a reverse split).",
    "available_at": "TIME COLUMN. When the split became knowable: the open of its "
    "effective session (the announcement date is unknown). The adjusted "
    "read as of T applies splits with available_at <= T.",
    "ingested_at": "TIME COLUMN. When this row was written: when FlowDesk first saw the "
    "split. Never used for point-in-time filtering.",
    "source": "The provider id ('yfinance').",
    "ingestion_run_id": "The ingestion_runs row that wrote this row; cleared if that run "
    "is purged.",
}
GAPS = {
    "id": "Surrogate key.",
    "symbol_id": "The symbol (symbols.id).",
    "interval": "Which bars are missing: '1d' (bars_daily) or '15m' (bars_intraday).",
    "missing_from": "TIME COLUMN. The open instant of the first missing bar (inclusive). "
    "The range is half-open: [missing_from, missing_to).",
    "missing_to": "TIME COLUMN. The first bar open instant AFTER the gap (exclusive): the "
    "first bar that is not missing.",
    "recorded_at": "TIME COLUMN. When FlowDesk recorded the gap. Set once.",
    "ingestion_run_id": "The ingestion run that found the gap; cleared if that run is purged.",
    "resolved_at": "TIME COLUMN. When the gap was filled; NULL while it is still missing. "
    "Set once, never cleared; the row is never deleted.",
}
TABLE_COMMENTS = {
    "bars_daily": "Settled daily bars, as traded, insert-only: the first settled value, "
    "never overwritten. Stored only after the settle point (available_at).",
    "bars_intraday": "Settled intraday bars, as traded, insert-only: the first settled "
    "value, never overwritten. Stored only after the settle point "
    "(available_at). Not the provisional in-session table (IDEA-045).",
    "bar_total_return": "Total return per daily bar, one row per revision, insert-only. "
    "The only home of total return; the current value is the highest "
    "revision.",
    "splits": "Stock splits, insert-only. Adjusted prices are derived from these on read (D3 b).",
    "bar_gaps": "Unrecoverable gaps in the stored bars: ranges beyond the provider's "
    "look-back. Never deleted; a filled gap gets resolved_at.",
}


def _bar_checks() -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint(
            "raw_basis IN ('live', 'reconstructed', 'unreconstructed')", name="raw_basis"
        ),
        CheckConstraint("feed IN ('poll', 'backfill')", name="feed"),
        CheckConstraint("venue_scope IN ('provider_default')", name="venue_scope"),
        CheckConstraint("available_at > effective_at", name="settled_after_open"),
    )


class _BarColumns:
    """The columns both settled bar tables share: as traded (D3 b) and provenance (D1)."""

    o_raw: Mapped[Decimal] = mapped_column(Numeric, comment=COMMON["o_raw"])
    h_raw: Mapped[Decimal] = mapped_column(Numeric, comment=COMMON["h_raw"])
    l_raw: Mapped[Decimal] = mapped_column(Numeric, comment=COMMON["l_raw"])
    c_raw: Mapped[Decimal] = mapped_column(Numeric, comment=COMMON["c_raw"])
    v_raw: Mapped[int] = mapped_column(BigInteger, comment=COMMON["v_raw"])
    raw_basis: Mapped[str] = mapped_column(Text, comment=COMMON["raw_basis"])
    source: Mapped[str] = mapped_column(Text, comment=COMMON["source"])
    feed: Mapped[str] = mapped_column(Text, comment=COMMON["feed"])
    venue_scope: Mapped[str] = mapped_column(Text, comment=COMMON["venue_scope"])
    ingested_at: Mapped[datetime] = mapped_column(
        TZ, server_default=text("now()"), comment=COMMON["ingested_at"]
    )

    @declared_attr
    def ingestion_run_id(cls) -> Mapped[int | None]:
        # A ForeignKey on a mixin must be declared per class.
        return mapped_column(
            BigInteger,
            ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            comment=COMMON["ingestion_run_id"],
        )


class BarDaily(_BarColumns, Base):
    __tablename__ = "bars_daily"
    __table_args__ = (
        PrimaryKeyConstraint("symbol_id", "effective_at"),
        Index("ix_bars_daily_effective_at", "effective_at"),
        *_bar_checks(),
        {"comment": TABLE_COMMENTS["bars_daily"]},
    )

    symbol_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("symbols.id"), comment=BARS_DAILY["symbol_id"]
    )
    effective_at: Mapped[datetime] = mapped_column(TZ, comment=BARS_DAILY["effective_at"])
    available_at: Mapped[datetime] = mapped_column(TZ, comment=BARS_DAILY["available_at"])


class BarIntraday(_BarColumns, Base):
    __tablename__ = "bars_intraday"
    __table_args__ = (
        PrimaryKeyConstraint("symbol_id", "interval", "effective_at"),
        Index("ix_bars_intraday_effective_at", "effective_at"),
        *_bar_checks(),
        CheckConstraint("interval IN ('15m')", name="interval"),
        {"comment": TABLE_COMMENTS["bars_intraday"]},
    )

    symbol_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("symbols.id"), comment=BARS_INTRADAY["symbol_id"]
    )
    interval: Mapped[str] = mapped_column(Text, comment=BARS_INTRADAY["interval"])
    effective_at: Mapped[datetime] = mapped_column(TZ, comment=BARS_INTRADAY["effective_at"])
    available_at: Mapped[datetime] = mapped_column(TZ, comment=BARS_INTRADAY["available_at"])


class BarTotalReturn(Base):
    __tablename__ = "bar_total_return"
    __table_args__ = (
        PrimaryKeyConstraint("symbol_id", "effective_at", "revision"),
        CheckConstraint("available_at > effective_at", name="settled_after_open"),
        {"comment": TABLE_COMMENTS["bar_total_return"]},
    )

    symbol_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("symbols.id"), comment=TOTAL_RETURN["symbol_id"]
    )
    effective_at: Mapped[datetime] = mapped_column(TZ, comment=TOTAL_RETURN["effective_at"])
    revision: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), comment=TOTAL_RETURN["revision"]
    )
    value: Mapped[Decimal] = mapped_column(Numeric, comment=TOTAL_RETURN["value"])
    available_at: Mapped[datetime] = mapped_column(TZ, comment=TOTAL_RETURN["available_at"])
    ingested_at: Mapped[datetime] = mapped_column(
        TZ, server_default=text("now()"), comment=TOTAL_RETURN["ingested_at"]
    )
    source: Mapped[str] = mapped_column(Text, comment=TOTAL_RETURN["source"])
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        comment=TOTAL_RETURN["ingestion_run_id"],
    )


class Split(Base):
    __tablename__ = "splits"
    __table_args__ = (
        PrimaryKeyConstraint("symbol_id", "effective_at"),
        CheckConstraint("ratio > 0", name="ratio_positive"),
        {"comment": TABLE_COMMENTS["splits"]},
    )

    symbol_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("symbols.id"), comment=SPLITS["symbol_id"]
    )
    # A session DATE, like a macro period: a split takes effect for a whole session.
    effective_at: Mapped[date] = mapped_column(Date, comment=SPLITS["effective_at"])
    ratio: Mapped[Decimal] = mapped_column(Numeric, comment=SPLITS["ratio"])
    available_at: Mapped[datetime] = mapped_column(TZ, comment=SPLITS["available_at"])
    ingested_at: Mapped[datetime] = mapped_column(
        TZ, server_default=text("now()"), comment=SPLITS["ingested_at"]
    )
    source: Mapped[str] = mapped_column(Text, comment=SPLITS["source"])
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        comment=SPLITS["ingestion_run_id"],
    )


class BarGap(Base):
    __tablename__ = "bar_gaps"
    __table_args__ = (
        UniqueConstraint("symbol_id", "interval", "missing_from"),
        CheckConstraint("interval IN ('1d', '15m')", name="interval"),
        CheckConstraint("missing_to > missing_from", name="range_not_empty"),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= recorded_at", name="resolved_after_recorded"
        ),
        {"comment": TABLE_COMMENTS["bar_gaps"]},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, comment=GAPS["id"]
    )
    symbol_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("symbols.id"), comment=GAPS["symbol_id"]
    )
    interval: Mapped[str] = mapped_column(Text, comment=GAPS["interval"])
    # Half-open [missing_from, missing_to) over bar open instants.
    missing_from: Mapped[datetime] = mapped_column(TZ, comment=GAPS["missing_from"])
    missing_to: Mapped[datetime] = mapped_column(TZ, comment=GAPS["missing_to"])
    recorded_at: Mapped[datetime] = mapped_column(
        TZ, server_default=text("now()"), comment=GAPS["recorded_at"]
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        comment=GAPS["ingestion_run_id"],
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TZ, comment=GAPS["resolved_at"])

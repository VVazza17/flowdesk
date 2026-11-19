"""Storing settled daily bars (M2 slice 6, step 4): what the backfill (and later the poll) writes.

Every write is INSERT ... ON CONFLICT DO NOTHING into insert-only tables (migration 0010), so a
re-run writes 0 rows. One transaction per batch: a symbol's bars, the splits they were rebuilt with,
and its distributions (step 5b) land together or not at all. Total return is not stored (step 5b, D2
superseded): it is computed on read (app.services.bars_read).

**A re-fetch never overwrites (D3), and prices and volume are judged apart (Kyle, 2026-11-18).**
Before inserting, each symbol's re-fetched bars are compared with the stored ones on the same
instants. If a raw PRICE differs (most likely a missed split), NONE of that symbol's rows are
written and the symbol fails (`RefetchMismatch`). If only the VOLUME differs, that is a post-settle
revision: counted per symbol, the stored value kept, the symbol's new rows written. Either way the
stored first settled value stands. A distribution that disagrees with a stored one is caught by the
parser (`DistributionConflict`) and fails the symbol, as a disagreeing split does.

Session dates are found by a join to `trading_sessions` (`opens_at = effective_at`), never by
casting an instant to a date."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.ingest.bars import (
    DAILY,
    Calendar,
    DistributionEvent,
    Session,
    SettledBar,
    SplitEvent,
    SymbolFailure,
)
from app.ingest.errors import SchemaMismatch
from app.models.prices import BarDaily, BarGap, BarIntraday, Distribution, Split
from app.models.reference import ProviderMapping, Symbol, TradingSession

PROVIDER = "yfinance"
CALENDAR = "XNYS"  # every seeded symbol is a US ETF (reference_seed); exchanges are not yet tagged
# A re-fetched PRICE that differs from a stored one fails the symbol (most likely a missed split:
# an operator must look). A re-fetched VOLUME that differs is a post-settle revision, measured to
# happen (daily volume revised on night 2, 2026-11-18) and expected under the pending revision
# policy: counted per symbol, the stored value kept, the symbol's new rows written normally. Had
# it failed the symbol, the first revision would freeze that symbol for good, because a stored
# value never changes. Its handling (dated versions or a validation flag) is the two-week tail's.
PRICE_FIELDS = ("o_raw", "h_raw", "l_raw", "c_raw")
RAW_FIELDS = (*PRICE_FIELDS, "v_raw")


class RefetchMismatch(SchemaMismatch):
    """D3: a re-fetch rebuilt a different raw PRICE for a stored bar. A validation failure, never an
    overwrite: none of the symbol's rows from this batch are written. (A volume-only difference is
    not this: see PRICE_FIELDS.)

    Kind `schema_mismatch` until slice 7 adds the validation kind; it MOVES to that kind then
    (Kyle, 2026-11-18)."""


@dataclass(frozen=True)
class MappedSymbol:
    symbol_id: int
    ticker: str
    provider_symbol: str


@dataclass
class WriteCounts:
    bars: int = 0
    splits: int = 0
    distributions: int = 0
    # Per symbol: bars whose re-fetched volume differs from the stored one (prices equal). Counted,
    # never written: the stored first settled value stands.
    volume_revisions: dict[str, int] = field(default_factory=dict)
    mismatched: list[SymbolFailure] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        return self.bars + self.splits + self.distributions


async def load_calendar(engine: AsyncEngine, calendar_code: str = CALENDAR) -> Calendar:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    TradingSession.session_date, TradingSession.opens_at, TradingSession.closes_at
                ).where(TradingSession.calendar_code == calendar_code)
            )
        ).all()
    return Calendar(Session(r.session_date, r.opens_at, r.closes_at) for r in rows)


async def load_symbols(
    engine: AsyncEngine, tickers: Sequence[str] | None = None
) -> list[MappedSymbol]:
    """Active symbols with an active yfinance ticker mapping, in ticker order."""
    q = (
        select(Symbol.id, Symbol.ticker, ProviderMapping.external_id)
        .join(ProviderMapping, ProviderMapping.symbol_id == Symbol.id)
        .where(
            Symbol.active,
            ProviderMapping.provider == PROVIDER,
            ProviderMapping.id_kind == "ticker",
            ProviderMapping.retired_at.is_(None),
        )
        .order_by(Symbol.ticker)
    )
    if tickers is not None:
        q = q.where(Symbol.ticker.in_(tickers))
    async with engine.connect() as conn:
        found = [MappedSymbol(r.id, r.ticker, r.external_id) for r in (await conn.execute(q)).all()]
    if tickers is not None:
        missing = sorted(set(tickers) - {m.ticker for m in found})
        if missing:
            raise ValueError("no active yfinance mapping for: " + ", ".join(missing))
    return found


async def load_splits(
    engine: AsyncEngine, symbol_ids: Iterable[int]
) -> dict[int, list[SplitEvent]]:
    ids = list(symbol_ids)
    out: dict[int, list[SplitEvent]] = {i: [] for i in ids}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(Split.symbol_id, Split.effective_at, Split.ratio).where(
                    Split.symbol_id.in_(ids)
                )
            )
        ).all()
    for r in rows:
        out[r.symbol_id].append(SplitEvent(r.effective_at, r.ratio))
    return out


async def load_distributions(
    engine: AsyncEngine, symbol_ids: Iterable[int]
) -> dict[int, list[DistributionEvent]]:
    ids = list(symbol_ids)
    out: dict[int, list[DistributionEvent]] = {i: [] for i in ids}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    Distribution.symbol_id,
                    Distribution.effective_at,
                    Distribution.kind,
                    Distribution.amount,
                    Distribution.raw_basis,
                ).where(Distribution.symbol_id.in_(ids))
            )
        ).all()
    for r in rows:
        out[r.symbol_id].append(DistributionEvent(r.effective_at, r.kind, r.amount, r.raw_basis))
    return out


def _table(interval: str) -> Any:
    return BarDaily if interval == DAILY else BarIntraday


async def _stored(
    conn: AsyncConnection, interval: str, symbol_id: int, bars: Sequence[SettledBar]
) -> dict[object, tuple[object, ...]]:
    """The stored raw values of this symbol at these instants, in this interval's table."""
    t = _table(interval)
    instants = [b.effective_at for b in bars]
    query = select(t.effective_at, *(getattr(t, f) for f in RAW_FIELDS)).where(
        t.symbol_id == symbol_id, t.effective_at.in_(instants)
    )
    if t is BarIntraday:
        query = query.where(BarIntraday.interval == interval)
    return {
        r.effective_at: tuple(getattr(r, f) for f in RAW_FIELDS)
        for r in (await conn.execute(query)).all()
    }


async def write_daily(
    engine: AsyncEngine,
    bars: Mapping[str, Sequence[SettledBar]],
    new_splits: Mapping[str, Sequence[SplitEvent]],
    symbols: Mapping[str, int],
    new_distributions: Mapping[str, Sequence[DistributionEvent]],
    *,
    calendar: Calendar,
    feed: str,
    ingestion_run_id: int | None,
    source: str = PROVIDER,
    interval: str = DAILY,
) -> WriteCounts:
    """Write one parsed batch (keyed by provider symbol; `symbols` maps it to symbols.id) into
    `bars_daily` or, for `interval='15m'`, `bars_intraday`, with the same rules."""
    counts = WriteCounts()
    async with engine.begin() as conn:
        for provider_symbol, rows in bars.items():
            symbol_id = symbols[provider_symbol]
            stored_raw = await _stored(conn, interval, symbol_id, rows)
            n_price = len(PRICE_FIELDS)
            if any(
                b.effective_at in stored_raw
                and stored_raw[b.effective_at][:n_price]
                != tuple(getattr(b, f) for f in PRICE_FIELDS)
                for b in rows
            ):
                counts.mismatched.append(
                    SymbolFailure(
                        provider_symbol,
                        RefetchMismatch(
                            f"{provider_symbol}: a re-fetched price differs from a stored bar"
                        ),
                    )
                )
                continue
            revised = sum(
                1
                for b in rows
                if b.effective_at in stored_raw and stored_raw[b.effective_at][n_price] != b.v_raw
            )
            if revised:
                counts.volume_revisions[provider_symbol] = revised
            for dist in new_distributions.get(provider_symbol, ()):
                session = calendar.get(dist.effective_at)
                if session is None:
                    raise SchemaMismatch("an ex-date is not a session in trading_sessions")
                done = await conn.execute(
                    pg_insert(Distribution)
                    .values(
                        symbol_id=symbol_id,
                        effective_at=dist.effective_at,
                        kind=dist.kind,
                        amount=dist.amount,
                        raw_basis=dist.raw_basis,
                        feed=feed,
                        # Decided 2026-11-18: knowable at its ex-date's open (late, never early).
                        available_at=session.opens_at,
                        source=source,
                        ingestion_run_id=ingestion_run_id,
                    )
                    .on_conflict_do_nothing()
                    .returning(Distribution.symbol_id)
                )
                counts.distributions += len(done.all())
            for split in new_splits.get(provider_symbol, ()):
                session = calendar.get(split.effective_at)
                if session is None:
                    raise SchemaMismatch("a split's date is not a session in trading_sessions")
                done = await conn.execute(
                    pg_insert(Split)
                    .values(
                        symbol_id=symbol_id,
                        effective_at=split.effective_at,
                        ratio=split.ratio,
                        # Approved 2026-11-18: knowable at the open of its effective session.
                        available_at=session.opens_at,
                        source=source,
                        ingestion_run_id=ingestion_run_id,
                    )
                    .on_conflict_do_nothing()
                    .returning(Split.symbol_id)
                )
                counts.splits += len(done.all())
            if not rows:
                continue
            table = _table(interval)
            extra = {} if table is BarDaily else {"interval": interval}
            done = await conn.execute(
                pg_insert(table)
                .values(
                    [
                        {
                            **extra,
                            "symbol_id": symbol_id,
                            "effective_at": b.effective_at,
                            "o_raw": b.o_raw,
                            "h_raw": b.h_raw,
                            "l_raw": b.l_raw,
                            "c_raw": b.c_raw,
                            "v_raw": b.v_raw,
                            "raw_basis": b.raw_basis,
                            "source": source,
                            "feed": feed,
                            "venue_scope": "provider_default",
                            "available_at": b.available_at,
                            "ingestion_run_id": ingestion_run_id,
                        }
                        for b in rows
                    ]
                )
                .on_conflict_do_nothing()
                .returning(table.effective_at)
            )
            counts.bars += len(done.all())
    return counts


async def stored_sessions(engine: AsyncEngine, symbol_ids: Iterable[int]) -> dict[int, list[date]]:
    """Each symbol's stored daily sessions, by a join to trading_sessions (never a CAST)."""
    ids = list(symbol_ids)
    out: dict[int, list[date]] = {i: [] for i in ids}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(BarDaily.symbol_id, TradingSession.session_date)
                .join(
                    TradingSession,
                    (TradingSession.opens_at == BarDaily.effective_at)
                    & (TradingSession.calendar_code == CALENDAR),
                )
                .where(BarDaily.symbol_id.in_(ids))
                .order_by(BarDaily.symbol_id, TradingSession.session_date)
            )
        ).all()
    for r in rows:
        out[r.symbol_id].append(r.session_date)
    return out


@dataclass(frozen=True)
class Coverage:
    ticker: str
    bars: int
    first: date | None
    last: date | None
    younger_than_window: bool
    missing: tuple[date, ...]  # sessions inside the symbol's own span with no stored bar

    def describe(self) -> str:
        if self.bars == 0 or self.first is None or self.last is None:
            return f"{self.ticker}: no bars stored"
        text = f"{self.ticker}: {self.bars} bars, {self.first} to {self.last}"
        if self.younger_than_window:
            text += f"; younger than the window (first bar {self.first}), not a gap"
        if self.missing:
            shown = ", ".join(d.isoformat() for d in self.missing[:5])
            more = f" and {len(self.missing) - 5} more" if len(self.missing) > 5 else ""
            text += f"; MISSING {len(self.missing)} sessions inside its span: {shown}{more}"
        return text


def coverage(
    symbols: Sequence[MappedSymbol],
    sessions: Mapping[int, Sequence[date]],
    *,
    calendar: Calendar,
    window_first: date,
    last_settled: date,
) -> list[Coverage]:
    """Per symbol, over [window_first, last_settled]: what is stored, whether the symbol is younger
    than the window (reported as such, never as a gap), and any session missing INSIDE its own
    span."""
    expected = [
        s.session_date for s in calendar.sessions if window_first <= s.session_date <= last_settled
    ]
    out = []
    for m in symbols:
        have = [d for d in sessions.get(m.symbol_id, []) if window_first <= d <= last_settled]
        if not have:
            out.append(Coverage(m.ticker, 0, None, None, False, ()))
            continue
        first, last = have[0], have[-1]
        present = set(have)
        missing = tuple(d for d in expected if first <= d <= last and d not in present)
        out.append(Coverage(m.ticker, len(have), first, last, first > window_first, missing))
    return out


async def last_stored(
    engine: AsyncEngine, symbol_ids: Iterable[int], interval: str
) -> dict[int, tuple[date, datetime] | None]:
    """Each symbol's newest stored bar in `interval`: (its session date, its effective_at), or None.
    The poll's start (gap-driven, inclusive: decided 2026-11-15 and 2026-11-18). The session date
    comes from the calendar, never a CAST."""
    t = _table(interval)
    ids = list(symbol_ids)
    newest = (
        select(t.symbol_id, func.max(t.effective_at).label("at"))
        .where(t.symbol_id.in_(ids))
        .group_by(t.symbol_id)
    )
    if t is BarIntraday:
        newest = newest.where(BarIntraday.interval == interval)
    sub = newest.subquery()
    join = (
        (TradingSession.opens_at == sub.c.at)
        if t is BarDaily
        else (TradingSession.opens_at <= sub.c.at) & (sub.c.at < TradingSession.closes_at)
    )
    out: dict[int, tuple[date, datetime] | None] = {i: None for i in ids}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(sub.c.symbol_id, TradingSession.session_date, sub.c.at).join(
                    TradingSession, join & (TradingSession.calendar_code == CALENDAR)
                )
            )
        ).all()
    for r in rows:
        out[r.symbol_id] = (r.session_date, r.at)
    return out


async def record_gap(
    engine: AsyncEngine,
    symbol_id: int,
    interval: str,
    missing_from: datetime,
    missing_to: datetime,
    *,
    ingestion_run_id: int | None,
) -> int:
    """Record an unrecoverable gap, [missing_from, missing_to) over bar open instants (decided
    2026-11-15). Never deleted; a second run cannot record it twice. Returns rows written."""
    async with engine.begin() as conn:
        done = await conn.execute(
            pg_insert(BarGap)
            .values(
                symbol_id=symbol_id,
                interval=interval,
                missing_from=missing_from,
                missing_to=missing_to,
                ingestion_run_id=ingestion_run_id,
            )
            .on_conflict_do_nothing()
            .returning(BarGap.id)
        )
        return len(done.all())

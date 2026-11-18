"""Storing settled daily bars (M2 slice 6, step 4): what the backfill (and later the poll) writes.

Every write is INSERT ... ON CONFLICT DO NOTHING into insert-only tables (migration 0010), so a
re-run writes 0 rows. One transaction per batch: a bar, its total return (revision 0) and the splits
it was rebuilt with land together or not at all (D2).

**A re-fetch never overwrites (D3).** Before inserting, each symbol's re-fetched bars are compared
with the stored ones on the same instants. If any raw price or volume differs, NONE of that symbol's
rows are written and the symbol fails (`RefetchMismatch`); the stored first settled value stands. A
total return that differs from the stored revision 0 is NOT a failure (a dividend rewrites `Adj
Close` by design, measured 2026-11-16) and is only counted: later revisions are the periodic history
re-fetch's (decided 2026-11-18, built later).

Session dates are found by a join to `trading_sessions` (`opens_at = effective_at`), never by
casting an instant to a date."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.ingest.bars import Calendar, Session, SettledBar, SplitEvent, SymbolFailure
from app.ingest.errors import SchemaMismatch
from app.models.prices import BarDaily, BarTotalReturn, Split
from app.models.reference import ProviderMapping, Symbol, TradingSession

PROVIDER = "yfinance"
CALENDAR = "XNYS"  # every seeded symbol is a US ETF (reference_seed); exchanges are not yet tagged
RAW_FIELDS = ("o_raw", "h_raw", "l_raw", "c_raw", "v_raw")


class RefetchMismatch(SchemaMismatch):
    """D3: a re-fetch rebuilt different raw values for a stored bar. A validation failure, never an
    overwrite: none of the symbol's rows from this batch are written."""


@dataclass(frozen=True)
class MappedSymbol:
    symbol_id: int
    ticker: str
    provider_symbol: str


@dataclass
class WriteCounts:
    bars: int = 0
    total_returns: int = 0
    splits: int = 0
    total_return_differs: int = 0  # stored revision 0 differs from today's Adj Close: counted only
    mismatched: list[SymbolFailure] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        return self.bars + self.total_returns + self.splits


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


async def _stored(
    conn: AsyncConnection, symbol_id: int, bars: Sequence[SettledBar]
) -> tuple[dict[object, tuple[object, ...]], dict[object, object]]:
    """The stored raw values and revision-0 total returns of this symbol at these instants."""
    instants = [b.effective_at for b in bars]
    raw = {
        r.effective_at: tuple(getattr(r, f) for f in RAW_FIELDS)
        for r in (
            await conn.execute(
                select(BarDaily.effective_at, *(getattr(BarDaily, f) for f in RAW_FIELDS)).where(
                    BarDaily.symbol_id == symbol_id, BarDaily.effective_at.in_(instants)
                )
            )
        ).all()
    }
    tr = {
        r.effective_at: r.value
        for r in (
            await conn.execute(
                select(BarTotalReturn.effective_at, BarTotalReturn.value).where(
                    BarTotalReturn.symbol_id == symbol_id,
                    BarTotalReturn.revision == 0,
                    BarTotalReturn.effective_at.in_(instants),
                )
            )
        ).all()
    }
    return raw, tr


async def write_daily(
    engine: AsyncEngine,
    bars: Mapping[str, Sequence[SettledBar]],
    new_splits: Mapping[str, Sequence[SplitEvent]],
    symbols: Mapping[str, int],
    *,
    calendar: Calendar,
    feed: str,
    ingestion_run_id: int | None,
    source: str = PROVIDER,
) -> WriteCounts:
    """Write one parsed batch (keyed by provider symbol; `symbols` maps it to symbols.id)."""
    counts = WriteCounts()
    async with engine.begin() as conn:
        for provider_symbol, rows in bars.items():
            symbol_id = symbols[provider_symbol]
            stored_raw, stored_tr = await _stored(conn, symbol_id, rows)
            if any(
                b.effective_at in stored_raw
                and stored_raw[b.effective_at] != tuple(getattr(b, f) for f in RAW_FIELDS)
                for b in rows
            ):
                counts.mismatched.append(
                    SymbolFailure(
                        provider_symbol,
                        RefetchMismatch(f"{provider_symbol}: a re-fetch differs from a stored bar"),
                    )
                )
                continue
            counts.total_return_differs += sum(
                1
                for b in rows
                if b.effective_at in stored_tr and stored_tr[b.effective_at] != b.total_return
            )
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
            done = await conn.execute(
                pg_insert(BarDaily)
                .values(
                    [
                        {
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
                .returning(BarDaily.effective_at)
            )
            counts.bars += len(done.all())
            done = await conn.execute(
                pg_insert(BarTotalReturn)
                .values(
                    [
                        {
                            "symbol_id": symbol_id,
                            "effective_at": b.effective_at,
                            "revision": 0,
                            "value": b.total_return,
                            # Revision 0 is knowable at the bar's settle point (decided 2026-11-18).
                            "available_at": b.available_at,
                            "source": source,
                            "ingestion_run_id": ingestion_run_id,
                        }
                        for b in rows
                        if b.total_return is not None
                    ]
                )
                .on_conflict_do_nothing()
                .returning(BarTotalReturn.effective_at)
            )
            counts.total_returns += len(done.all())
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

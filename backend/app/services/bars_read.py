"""The point-in-time read of settled bars (M2 slice 6, step 5; mirrors app.services.macro_read).

The one place that answers "what did we know about symbol S's bar for session D, at instant T". Live
calculation and replay both read through it, so `as_of` is an ARGUMENT: nothing here reads a clock.

THE RULE. A bar is visible at T if and only if `available_at <= T` (inclusive), never by
`effective_at`: a bar's `available_at` is its settle point (09:00 New York on D+2 daily, D+1 15m;
S MEASURED 2026-11-18), so filtering on `effective_at <= T` would hand a replay bars that had not
settled. It is a filter on row EXISTENCE: a bar not yet settled at T is absent, never present with
a provisional number.

TWO REQUIRED KEYWORDS, AT EVERY CALL SITE, NEITHER WITH A DEFAULT:
  as_of             the instant T. Timezone-aware.
  include_backfill  whether rows with `feed = 'backfill'` are visible. Their `available_at` is the
                    settle point by a RECORDED EXCEPTION (Kyle, 2026-11-18): the trades were public
                    at the close, but the value was fetched later and may include provider
                    corrections made after the settle point. `include_backfill=False` is the STRICT
                    mode, which sees only what a live poll stored at the time. A default either way
                    would hide that choice; like `revisions_of(..., as_of=None)` in macro_read, it
                    has to be written out where it is made.

AS TRADED, AND ADJUSTED ON READ (D3 b). Stored prices are as traded. The adjusted values are
computed here: the raw price divided by, and the raw volume multiplied by, the ratios of every split
that takes effect AFTER the bar's session AND whose `available_at <= T`. A split not yet knowable at
T is not applied, so a replay at T sees the adjustment that was knowable at T and nothing later.

TOTAL RETURN is the latest `bar_total_return` revision with `available_at <= T` (daily only), or
None. "Current total return" is the same rule with T = now.

THE WAYS TO HAVE NO ANSWER are different answers (`Missing.reason`), decided in this order:

  UNKNOWN_SYMBOL      no symbol has this ticker.
  NOT_A_SESSION       the date is not a session in trading_sessions (a weekend, a holiday): there is
                      no such bar for any symbol.
  BEFORE_RECORD       T is earlier than the first instant any bar of this symbol (in this mode) was
                      knowable: no replay of T can see anything of it. A property of T.
  BACKFILL_EXCLUDED   strict mode, and the only stored bar for that session is a backfill row: it
                      exists, the caller chose not to see it. Decided before PREDATES_RECORD,
                      because in strict mode the record starts at the first POLL row.
  PREDATES_RECORD     the session is earlier than the symbol's first stored bar: upstream history
                      may exist, but it was never stored. A property of the session.
  NOT_SETTLED_AS_OF   the bar is stored but settled after T: at T it was not yet known.
  NOT_STORED          a session inside or after the symbol's record with no stored bar at any time
                      (a gap, or a session not yet ingested).

NOT HERE: any HTTP route (it needs the section 9 envelope and IDEA-019's route invariant), signals,
the provisional intraday table (IDEA-045), and corporate actions beyond splits (slice 8).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Row, Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.ingest.bars import DAILY, INTRADAY_15M
from app.models.prices import BarDaily, BarIntraday, BarTotalReturn, Split
from app.models.reference import Symbol, TradingSession

CALENDAR = "XNYS"
BACKFILL = "backfill"


class Reason(StrEnum):
    UNKNOWN_SYMBOL = "unknown_symbol"
    NOT_A_SESSION = "not_a_session"
    BEFORE_RECORD = "before_record"
    PREDATES_RECORD = "predates_record"
    BACKFILL_EXCLUDED = "backfill_excluded"
    NOT_SETTLED_AS_OF = "not_settled_as_of"
    NOT_STORED = "not_stored"


@dataclass(frozen=True)
class Bar:
    """A settled bar as known at the query's `as_of`: as traded, adjusted on read, total return."""

    ticker: str
    interval: str
    session_date: date
    effective_at: datetime
    o_raw: Decimal
    h_raw: Decimal
    l_raw: Decimal
    c_raw: Decimal
    v_raw: int
    raw_basis: str
    feed: str
    available_at: datetime
    # D3 b: the product of the ratios of splits effective after the session and knowable at as_of.
    split_factor: Decimal
    o_adj: Decimal
    h_adj: Decimal
    l_adj: Decimal
    c_adj: Decimal
    v_adj: Decimal
    total_return: Decimal | None  # daily: the latest revision knowable at as_of; intraday: None
    total_return_revision: int | None


@dataclass(frozen=True)
class Missing:
    """No answer, and why. A value, not an exception: the reason is the information."""

    reason: Reason
    detail: str


@dataclass(frozen=True)
class Snapshot:
    ticker: str
    interval: str
    as_of: datetime
    include_backfill: bool
    bars: tuple[Bar, ...]


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")


def _table(interval: str) -> type[BarDaily] | type[BarIntraday]:
    if interval == DAILY:
        return BarDaily
    if interval == INTRADAY_15M:
        return BarIntraday
    raise ValueError(f"unknown interval {interval!r}")


def _session_join(t: Any) -> Any:
    """The bar's session, by the calendar (never a CAST of an instant to a date)."""
    if t is BarDaily:
        return and_(
            TradingSession.opens_at == t.effective_at, TradingSession.calendar_code == CALENDAR
        )
    return and_(
        t.effective_at >= TradingSession.opens_at,
        t.effective_at < TradingSession.closes_at,
        TradingSession.calendar_code == CALENDAR,
    )


def _bars_query(t: Any, symbol_id: int, as_of: datetime, include_backfill: bool) -> Select[Any]:
    # Explicit columns: the read's shape is fixed here, never SELECT *.
    query = (
        select(
            TradingSession.session_date,
            t.effective_at,
            t.o_raw,
            t.h_raw,
            t.l_raw,
            t.c_raw,
            t.v_raw,
            t.raw_basis,
            t.feed,
            t.available_at,
        )
        .join(TradingSession, _session_join(t))
        .where(t.symbol_id == symbol_id, t.available_at <= as_of)
    )
    if t is BarIntraday:
        query = query.where(BarIntraday.interval == INTRADAY_15M)
    if not include_backfill:
        query = query.where(t.feed != BACKFILL)
    return query


async def _symbol_id(conn: AsyncConnection, ticker: str) -> int | None:
    return (
        await conn.execute(select(Symbol.id).where(Symbol.ticker == ticker).limit(1))
    ).scalar_one_or_none()


async def _splits(
    conn: AsyncConnection, symbol_id: int, as_of: datetime
) -> list[tuple[date, Decimal]]:
    rows = await conn.execute(
        select(Split.effective_at, Split.ratio).where(
            Split.symbol_id == symbol_id, Split.available_at <= as_of
        )
    )
    return [(r.effective_at, r.ratio) for r in rows]


async def _total_returns(
    conn: AsyncConnection, symbol_id: int, instants: Sequence[datetime], as_of: datetime
) -> dict[datetime, tuple[Decimal, int]]:
    if not instants:
        return {}
    rows = await conn.execute(
        select(BarTotalReturn.effective_at, BarTotalReturn.value, BarTotalReturn.revision)
        .where(
            BarTotalReturn.symbol_id == symbol_id,
            BarTotalReturn.available_at <= as_of,
            BarTotalReturn.effective_at.in_(list(instants)),
        )
        .distinct(BarTotalReturn.effective_at)
        .order_by(BarTotalReturn.effective_at, BarTotalReturn.revision.desc())
    )
    return {r.effective_at: (r.value, r.revision) for r in rows}


def _bar(
    ticker: str,
    interval: str,
    row: Row[Any],
    splits: Sequence[tuple[date, Decimal]],
    total_returns: dict[datetime, tuple[Decimal, int]],
) -> Bar:
    factor = Decimal(1)
    for effective, ratio in splits:
        if effective > row.session_date:
            factor *= ratio
    tr = total_returns.get(row.effective_at)
    return Bar(
        ticker=ticker,
        interval=interval,
        session_date=row.session_date,
        effective_at=row.effective_at,
        o_raw=row.o_raw,
        h_raw=row.h_raw,
        l_raw=row.l_raw,
        c_raw=row.c_raw,
        v_raw=row.v_raw,
        raw_basis=row.raw_basis,
        feed=row.feed,
        available_at=row.available_at,
        split_factor=factor,
        o_adj=row.o_raw / factor,
        h_adj=row.h_raw / factor,
        l_adj=row.l_raw / factor,
        c_adj=row.c_raw / factor,
        v_adj=Decimal(row.v_raw) * factor,
        total_return=tr[0] if tr else None,
        total_return_revision=tr[1] if tr else None,
    )


async def bars_as_of(
    engine: AsyncEngine,
    ticker: str,
    *,
    as_of: datetime,
    include_backfill: bool,
    interval: str = DAILY,
    start: date | None = None,
    end: date | None = None,
) -> Snapshot | Missing:
    """Every bar of `ticker` known at `as_of`, oldest first; `start`/`end` bound the SESSION dates
    (inclusive). An unknown ticker is `Missing(UNKNOWN_SYMBOL)`; an empty snapshot is not an
    error."""
    _require_aware(as_of)
    t = _table(interval)
    async with engine.connect() as conn:
        symbol_id = await _symbol_id(conn, ticker)
        if symbol_id is None:
            return Missing(Reason.UNKNOWN_SYMBOL, f"{ticker}: no such symbol")
        query = _bars_query(t, symbol_id, as_of, include_backfill)
        if start is not None:
            query = query.where(TradingSession.session_date >= start)
        if end is not None:
            query = query.where(TradingSession.session_date <= end)
        rows = (await conn.execute(query.order_by(t.effective_at))).all()
        splits = await _splits(conn, symbol_id, as_of)
        trs = (
            await _total_returns(conn, symbol_id, [r.effective_at for r in rows], as_of)
            if interval == DAILY
            else {}
        )
    bars = tuple(_bar(ticker, interval, r, splits, trs) for r in rows)
    return Snapshot(ticker, interval, as_of, include_backfill, bars)


async def latest_bar_as_of(
    engine: AsyncEngine,
    ticker: str,
    *,
    as_of: datetime,
    include_backfill: bool,
    interval: str = DAILY,
) -> Bar | Missing:
    """The newest bar of `ticker` known at `as_of`, or why there is none."""
    _require_aware(as_of)
    t = _table(interval)
    async with engine.connect() as conn:
        symbol_id = await _symbol_id(conn, ticker)
        if symbol_id is None:
            return Missing(Reason.UNKNOWN_SYMBOL, f"{ticker}: no such symbol")
        row = (
            await conn.execute(
                _bars_query(t, symbol_id, as_of, include_backfill)
                .order_by(t.effective_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return await _why_nothing(conn, t, ticker, symbol_id, as_of, include_backfill)
        splits = await _splits(conn, symbol_id, as_of)
        trs = (
            await _total_returns(conn, symbol_id, [row.effective_at], as_of)
            if interval == DAILY
            else {}
        )
    return _bar(ticker, interval, row, splits, trs)


async def bar_as_of(
    engine: AsyncEngine,
    ticker: str,
    session_date: date,
    *,
    as_of: datetime,
    include_backfill: bool,
) -> Bar | Missing:
    """`ticker`'s daily bar for `session_date` as known at `as_of`, or a `Missing` naming which of
    the different kinds of no it is (module docstring, in decision order)."""
    _require_aware(as_of)
    t = BarDaily
    async with engine.connect() as conn:
        symbol_id = await _symbol_id(conn, ticker)
        if symbol_id is None:
            return Missing(Reason.UNKNOWN_SYMBOL, f"{ticker}: no such symbol")
        row = (
            await conn.execute(
                _bars_query(t, symbol_id, as_of, include_backfill).where(
                    TradingSession.session_date == session_date
                )
            )
        ).first()
        if row is not None:
            splits = await _splits(conn, symbol_id, as_of)
            trs = await _total_returns(conn, symbol_id, [row.effective_at], as_of)
            return _bar(ticker, DAILY, row, splits, trs)
        session = (
            await conn.execute(
                select(TradingSession.opens_at).where(
                    TradingSession.calendar_code == CALENDAR,
                    TradingSession.session_date == session_date,
                )
            )
        ).scalar_one_or_none()
        if session is None:
            return Missing(Reason.NOT_A_SESSION, f"{session_date}: not a session in {CALENDAR}")
        stored = (
            await conn.execute(
                select(BarDaily.feed, BarDaily.available_at).where(
                    BarDaily.symbol_id == symbol_id, BarDaily.effective_at == session
                )
            )
        ).first()
        # The record in THIS mode. In strict mode a symbol with only backfill rows has none, and
        # its bars are then BACKFILL_EXCLUDED below, not "before the record".
        first = await _record_start(conn, t, symbol_id, include_backfill)
    if first is not None and as_of < first[1]:
        return Missing(
            Reason.BEFORE_RECORD,
            f"{ticker}: nothing about it was knowable at {as_of.isoformat()}",
        )
    # Before PREDATES: in strict mode the record starts at the first POLL row, and a session
    # older than that may still be stored, as backfill. "Never stored" would then be false.
    if stored is not None and not include_backfill and stored.feed == BACKFILL:
        return Missing(
            Reason.BACKFILL_EXCLUDED,
            f"{ticker} {session_date}: only a backfill row exists (strict mode excludes it)",
        )
    if first is not None and session_date < first[0]:
        return Missing(
            Reason.PREDATES_RECORD,
            f"{ticker} {session_date}: earlier than its first stored bar ({first[0]})",
        )
    if stored is None:
        return Missing(
            Reason.NOT_STORED, f"{ticker} {session_date}: no bar is stored for this session"
        )
    return Missing(
        Reason.NOT_SETTLED_AS_OF,
        f"{ticker} {session_date}: settled at {stored.available_at.isoformat()}, after "
        f"{as_of.isoformat()}",
    )


async def _record_start(
    conn: AsyncConnection, t: Any, symbol_id: int, include_backfill: bool
) -> tuple[date, datetime] | None:
    """The symbol's first stored session and the first instant any of its bars was knowable, in
    this mode (strict mode ignores backfill rows)."""
    query = (
        select(func.min(TradingSession.session_date), func.min(t.available_at))
        .select_from(t)
        .join(TradingSession, _session_join(t))
        .where(t.symbol_id == symbol_id)
    )
    if not include_backfill:
        query = query.where(t.feed != BACKFILL)
    row = (await conn.execute(query)).first()
    if row is None or row[0] is None:
        return None
    return row[0], row[1]


async def _why_nothing(
    conn: AsyncConnection,
    t: Any,
    ticker: str,
    symbol_id: int,
    as_of: datetime,
    include_backfill: bool,
) -> Missing:
    first = await _record_start(conn, t, symbol_id, include_backfill)
    if first is None:
        if not include_backfill and await _record_start(conn, t, symbol_id, True) is not None:
            return Missing(
                Reason.BACKFILL_EXCLUDED,
                f"{ticker}: only backfill rows exist (strict mode excludes them)",
            )
        return Missing(Reason.NOT_STORED, f"{ticker}: no bar is stored")
    return Missing(
        Reason.BEFORE_RECORD, f"{ticker}: nothing about it was knowable at {as_of.isoformat()}"
    )

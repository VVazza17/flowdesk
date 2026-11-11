"""Trading sessions as data (M2 slice 3, IDEA-033).

§6.12 forbids naive weekday math and asks for an exchange-calendar library per venue. `signals/`
may not call a library or a clock, so the sessions have to reach it as data; this module is
where the library is called, and `trading_sessions` is where its answer is kept. Two reasons for
the table: signals get sessions as input, and a past run can be shown the sessions it saw even if
the library's holiday data later changes. The dependency (exchange_calendars) exists with or
without the table; the table only moves where it is called.

# DECISION: insert-only, like the universe seeder. A session already stored is never rewritten. If
# the library now disagrees with a stored row (a special closure announced after the fact, a data
# fix), that is reported as drift and left for a person to resolve. Rewriting would make "what the
# run saw" depend on when the seeder last ran, which is what the table exists to prevent. The cost:
# a FUTURE session that the library later corrects stays wrong until someone fixes it by hand.

Session MATH (windows of N sessions, "last session before D") is a follow-on, not this slice.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.reference import TradingSession

_CHUNK = 1000


@dataclass(frozen=True)
class SessionRow:
    calendar_code: str
    session_date: date
    opens_at: datetime
    closes_at: datetime
    is_early_close: bool


@dataclass(frozen=True)
class SessionSyncResult:
    inserted: int
    drift: tuple[str, ...]


def compute_sessions(calendar_code: str, start: date, end: date) -> list[SessionRow]:
    """Every session of `calendar_code` from `start` to `end` inclusive, from the library.

    The library (and pandas with it) is imported here, not at module level: the API process must
    import and answer /health/live without it, as with the other heavy clients (app.deps).
    """
    # DECISION: inline ignores, not pandas-stubs. exchange_calendars ships no types, and stubs for
    # pandas would add a dev dependency to type two lines. Everything below is read-only use.
    import exchange_calendars as xcals  # type: ignore[import-untyped]
    import pandas as pd  # type: ignore[import-untyped]

    cal = xcals.get_calendar(calendar_code, start=pd.Timestamp(start), end=pd.Timestamp(end))
    early = set(cal.early_closes.date)
    rows: list[SessionRow] = []
    # opens/closes are tz-aware UTC timestamps indexed by session; the label is a naive date.
    for session, opened, closed in zip(cal.sessions, cal.opens, cal.closes, strict=True):
        rows.append(
            SessionRow(
                calendar_code=calendar_code,
                session_date=session.date(),
                opens_at=opened.to_pydatetime(),
                closes_at=closed.to_pydatetime(),
                is_early_close=session.date() in early,
            )
        )
    return rows


async def sync_sessions(
    engine: AsyncEngine, calendar_codes: Sequence[str], start: date, end: date
) -> SessionSyncResult:
    inserted = 0
    drift: list[str] = []
    for code in calendar_codes:
        computed = {r.session_date: r for r in compute_sessions(code, start, end)}
        async with engine.begin() as conn:
            stored = {
                r.session_date: r
                for r in (
                    await conn.execute(
                        select(TradingSession).where(
                            TradingSession.calendar_code == code,
                            TradingSession.session_date >= start,
                            TradingSession.session_date <= end,
                        )
                    )
                ).all()
            }
            missing = [r for d, r in computed.items() if d not in stored]
            for i in range(0, len(missing), _CHUNK):
                chunk = missing[i : i + _CHUNK]
                await conn.execute(
                    pg_insert(TradingSession)
                    .values(
                        [
                            {
                                "calendar_code": r.calendar_code,
                                "session_date": r.session_date,
                                "opens_at": r.opens_at,
                                "closes_at": r.closes_at,
                                "is_early_close": r.is_early_close,
                            }
                            for r in chunk
                        ]
                    )
                    .on_conflict_do_nothing()
                )
            inserted += len(missing)
            for d, row in stored.items():
                lib = computed.get(d)
                if lib is None:
                    drift.append(f"{code} {d}: stored, but the library no longer has this session")
                elif (row.opens_at, row.closes_at, row.is_early_close) != (
                    lib.opens_at,
                    lib.closes_at,
                    lib.is_early_close,
                ):
                    drift.append(f"{code} {d}: stored row differs from the library")
    return SessionSyncResult(inserted=inserted, drift=tuple(drift))

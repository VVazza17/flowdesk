"""Entry point: `python -m app.seed`. Seeds the reference tables and extends trading sessions.

Runs in the `migrate` one-shot right after `alembic upgrade head`, so a fresh clone has its
reference data from the one command. Idempotent and insert-only (see app.services.reference_seed).

# DECISION: drift is logged as warnings and does not fail the run. A seeder that exits non-zero
# would fail `migrate` and so hold up the stack over a disagreement a person has to settle anyway.
# A failure to seed (database down, library missing) does raise, and `migrate` fails loudly.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.logging import configure_logging
from app.services.reference_seed import EXCHANGES, seed_reference
from app.services.sessions import sync_sessions


async def run() -> None:
    log = structlog.get_logger("app.seed")
    url = settings.database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set (expected postgresql+asyncpg://...)")
    engine = create_async_engine(url)
    try:
        seeded = await seed_reference(engine)
        start = date(settings.SESSIONS_START_YEAR, 1, 1)
        end = datetime.now(UTC).date() + timedelta(days=settings.SESSIONS_HORIZON_DAYS)
        calendars = sorted({row[4] for row in EXCHANGES})
        sessions = await sync_sessions(engine, calendars, start, end)
    finally:
        await engine.dispose()
    log.info(
        "reference_seed_done",
        currencies=seeded.currencies,
        exchanges=seeded.exchanges,
        universes=seeded.universes,
        symbols=seeded.symbols,
        memberships=seeded.memberships,
        sessions_inserted=sessions.inserted,
        drift=len(seeded.drift) + len(sessions.drift),
    )
    for message in (*seeded.drift, *sessions.drift):
        log.warning("reference_seed_drift", detail=message)


def main() -> None:
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()

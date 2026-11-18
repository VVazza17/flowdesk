"""The shared 'is there real captured data to check against?' gate for live-data tests.

A live-data test checks the STORED data against independent evidence, so it needs the series
captured in the database named by DATABASE_URL. On a fresh database (GitHub's CI, a clean checkout)
there is nothing to check, and the test skips: LOUDLY (tests/liveskips.py), never silently.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.liveskips import live_reason


async def _captured(url: str, series_id: str) -> bool:
    engine = create_async_engine(url, connect_args={"timeout": 3, "command_timeout": 3})
    try:
        async with engine.connect() as conn:
            n = (
                await conn.execute(
                    text("SELECT count(*) FROM series_history_state WHERE series_id = :s"),
                    {"s": series_id},
                )
            ).scalar_one()
            return bool(n)
    finally:
        await engine.dispose()


def require_live_series(url: str, series_id: str) -> str:
    """The URL if `series_id` is captured there; otherwise skip with a loud reason."""
    if not url:
        pytest.skip(live_reason("DATABASE_URL is not set"))
    try:
        captured = asyncio.run(_captured(url, series_id))
    except Exception as exc:
        pytest.skip(
            live_reason(
                f"the database is unreachable or not migrated ({type(exc).__name__}), "
                f"so there is no stored {series_id} to check"
            )
        )
    if not captured:
        pytest.skip(
            live_reason(
                f"{series_id} has no captured history in this database "
                "(python -m app.backfill). A fresh database, such as CI's, never does"
            )
        )
    return url


async def _provider_seeded(url: str, provider: str) -> bool:
    engine = create_async_engine(url, connect_args={"timeout": 3, "command_timeout": 3})
    try:
        async with engine.connect() as conn:
            n = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM provider_mappings "
                        "WHERE provider = :p AND retired_at IS NULL"
                    ),
                    {"p": provider},
                )
            ).scalar_one()
            return bool(n)
    finally:
        await engine.dispose()


def require_dev_stack_provider(url: str, provider: str) -> None:
    """Skip LOUDLY unless DATABASE_URL is a seeded development stack (it has active `provider`
    mappings). CI's test job starts Postgres only and never seeds, so there this always skips,
    and a live call to the provider is never made from GitHub."""
    if not url:
        pytest.skip(live_reason("DATABASE_URL is not set"))
    try:
        seeded = asyncio.run(_provider_seeded(url, provider))
    except Exception as exc:
        pytest.skip(
            live_reason(
                f"the database is unreachable or not migrated ({type(exc).__name__}), so this is "
                "not a seeded development stack"
            )
        )
    if not seeded:
        pytest.skip(
            live_reason(
                f"no {provider} provider mappings in this database (not a seeded development "
                "stack; CI never seeds)"
            )
        )

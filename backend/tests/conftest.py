"""Shared fixtures. Older test files keep their own `scratch_url`; a module-level fixture of the
same name takes precedence, so adding this changes nothing for them."""

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


async def _admin(admin_url: str, sql: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture
def scratch_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh, fully migrated database for one test. Skips without DATABASE_URL."""
    admin_url = os.environ.get("DATABASE_URL", "")
    if not admin_url:
        pytest.skip("DATABASE_URL not set")
    name = f"flowdesk_t_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(admin_url, f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(admin_url, f'DROP DATABASE "{name}" WITH (FORCE)'))

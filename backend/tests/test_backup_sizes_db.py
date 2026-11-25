"""The weekly backup sizes on /health/data (2026-11-25), read from a real Postgres by the real
probe.

Rule 9: in the same week as the backup's own success there are rows whose numbers would WIN if
the query lost a filter: a later ops.backup_copy success with more bytes (task name), a later
FAILED ops.backup with more bytes
(status), and a later success with no bytes at all (output_bytes IS NOT NULL).
"""

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.health_probes import SqlRedisProbes


def instant(at: str) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def sizes(scratch_url: str, rows: list[tuple[str, str, str, int | None]]) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        try:
            async with engine.begin() as conn:
                for name, status, at, size in rows:
                    await conn.execute(
                        text(
                            "INSERT INTO task_runs (task_name, started_at, completed_at, status, "
                            "output_bytes) VALUES (:n, :at, :at, :s, :b)"
                        ),
                        {"n": name, "at": instant(at), "s": status, "b": size},
                    )
            return (await SqlRedisProbes(engine, None).data_state())["backup_sizes"]
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_the_newest_successful_backup_of_each_week_newest_week_first(scratch_url: str) -> None:
    got = sizes(
        scratch_url,
        [
            ("ops.backup", "succeeded", "2026-11-17 08:00Z", 1_700_000),
            (
                "ops.backup",
                "succeeded",
                "2026-11-19 08:00Z",
                1_750_000,
            ),  # the week's newest success
            ("ops.backup_copy", "succeeded", "2026-11-20 08:00Z", 9_000_000_000),  # another task
            ("ops.backup", "failed", "2026-11-21 08:00Z", 5_000_000),  # not a success
            ("ops.backup", "succeeded", "2026-11-22 08:00Z", None),  # measured nothing
            ("ops.backup", "succeeded", "2026-11-24 08:00Z", 1_800_000),  # next week
        ],
    )
    assert [(s["week_start"][:10], s["bytes"]) for s in got] == [
        ("2026-11-23", 1_800_000),
        ("2026-11-16", 1_750_000),
    ]


def test_at_most_eight_weeks(scratch_url: str) -> None:
    rows: list[tuple[str, str, str, int | None]] = [
        ("ops.backup", "succeeded", f"2026-{m:02d}-15 08:00Z", 1000 + m) for m in range(1, 13)
    ]
    got = sizes(scratch_url, rows)
    assert len(got) == 8
    assert got[0]["bytes"] == 1012

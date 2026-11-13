"""M2 slice 5, step 3, part C: the sweep, the ingestion-run link, and the busy probe.

Real Postgres (a scratch database per test). The sweep's clock is passed in, so "after 120 s" is a
number in the test, not a wait.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.errors import NotRecorded
from app.ingest.runner import FeedContext, FeedResult, RetryPolicy, run_feed
from app.services.health_probes import SqlRedisProbes
from app.tasks.liveness import HEARTBEAT_FRESH_SECONDS
from app.tasks.names import (
    BACKUP,
    BACKUP_COPY,
    CANARY,
    FRED_BACKFILL,
    INGEST_CANARY,
    INGEST_WORKER_TASKS,
    UNSWEPT_TASKS,
)
from app.tasks.runs import run_recorded, utcnow
from app.tasks.sweep import run_sweep, sweep
from tests.recorded import recorded

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
FRESH = timedelta(seconds=HEARTBEAT_FRESH_SECONDS)


class Limiter:
    async def acquire(self, provider: str) -> None:
        return None


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql), params)]


async def run(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


def go(scratch_url: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def task(
    engine: AsyncEngine, name: str, beat: datetime | None, status: str = "running"
) -> int:
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "INSERT INTO task_runs (task_name, started_at, heartbeat_at, status) "
                        "VALUES (:n, :s, :h, :st) RETURNING id"
                    ),
                    {"n": name, "s": NOW - timedelta(hours=1), "h": beat, "st": status},
                )
            ).scalar_one()
        )


async def ingestion(
    engine: AsyncEngine, feed: str, task_id: int | None, status: str = "running"
) -> int:
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "INSERT INTO ingestion_runs "
                        "(provider, feed, started_at, status, task_run_id) "
                        "VALUES ('fred', :f, :s, :st, :t) RETURNING id"
                    ),
                    {"f": feed, "s": NOW - timedelta(hours=1), "st": status, "t": task_id},
                )
            ).scalar_one()
        )


# ---- the rule on task_runs ----------------------------------------------------------------------


def test_a_running_task_is_failed_only_once_its_heartbeat_is_older_than_the_fresh_window(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> dict[str, str]:
        alive = await task(engine, "t.alive", NOW - FRESH)  # exactly at the boundary: fresh
        dead = await task(engine, "t.dead", NOW - FRESH - timedelta(seconds=1))
        never = await task(engine, "t.never_beat", None)
        done = await task(engine, "t.done", NOW - timedelta(hours=1), status="succeeded")
        failed = await task(engine, "t.failed", NOW - timedelta(hours=1), status="failed")
        result = await sweep(engine, NOW)
        got = {
            r["id"]: (r["status"], r["error_class"])
            for r in await rows(engine, "SELECT id, status, error_class FROM task_runs")
        }
        assert result.task_runs == 2
        return {
            "alive": got[alive][0],
            "dead": f"{got[dead][0]}/{got[dead][1]}",
            "never": f"{got[never][0]}/{got[never][1]}",
            "done": got[done][0],
            "failed": got[failed][0],
        }

    assert go(scratch_url, fn) == {
        "alive": "running",  # a live long task is never swept
        "dead": "failed/Orphaned",
        "never": "failed/Orphaned",  # a NULL heartbeat is never fresh
        "done": "succeeded",
        "failed": "failed",
    }


def test_a_swept_task_records_when_and_why(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        await task(engine, "t.dead", NOW - timedelta(hours=1))
        await sweep(engine, NOW)
        return (await rows(engine, "SELECT * FROM task_runs"))[0]

    row = go(scratch_url, fn)
    assert row["completed_at"] == NOW
    assert row["error_message"] == f"no heartbeat within {HEARTBEAT_FRESH_SECONDS} s; swept"


def test_the_sweep_is_idempotent(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        await task(engine, "t.dead", NOW - timedelta(hours=1))
        return await sweep(engine, NOW), await sweep(engine, NOW)

    first, second = go(scratch_url, fn)
    assert (first.task_runs, second.task_runs) == (1, 0)


# ---- the rule on ingestion_runs ----------------------------------------------------------------


def test_an_ingestion_run_is_swept_unless_its_task_is_alive(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> dict[str, str]:
        live = await task(engine, "t.live", NOW - timedelta(seconds=5))
        dead = await task(engine, "t.dead", NOW - timedelta(hours=1))
        finished = await task(engine, "t.finished", NOW - timedelta(seconds=5), status="succeeded")
        ids = {
            "on_live_task": await ingestion(engine, "A", live),
            "on_dead_task": await ingestion(engine, "B", dead),
            "on_finished_task": await ingestion(engine, "C", finished),  # task done, run left over
            "no_task": await ingestion(engine, "D", None),  # NULL fails the test by itself
            "already_done": await ingestion(engine, "E", dead, status="succeeded"),
        }
        result = await sweep(engine, NOW)
        got = {r["id"]: r for r in await rows(engine, "SELECT * FROM ingestion_runs")}
        assert result.ingestion_runs == 3
        return {k: got[v]["status"] for k, v in ids.items()} | {
            "error_dead": got[ids["on_dead_task"]]["error"],
            "finished_at": str(got[ids["no_task"]]["finished_at"] == NOW),
        }

    out = go(scratch_url, fn)
    assert out["on_live_task"] == "running"  # a live task's run is never swept
    assert out["on_dead_task"] == out["on_finished_task"] == out["no_task"] == "failed"
    assert out["already_done"] == "succeeded"
    assert out["error_dead"].startswith("Orphaned")
    assert out["finished_at"] == "True"


def test_a_task_and_its_ingestion_run_are_failed_in_the_same_pass(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, list[str], list[str]]:
        dead = await task(engine, "t.dead", NOW - timedelta(hours=1))
        await ingestion(engine, "A", dead)
        result = await sweep(engine, NOW)
        return (
            result,
            [r["status"] for r in await rows(engine, "SELECT status FROM task_runs")],
            [r["status"] for r in await rows(engine, "SELECT status FROM ingestion_runs")],
        )

    result, tasks, runs = go(scratch_url, fn)
    # One sweep fails both: the two tables never disagree about the same run for even one pass.
    assert (result.task_runs, result.ingestion_runs, tasks, runs) == (1, 1, ["failed"], ["failed"])


# ---- what the sweep deliberately leaves alone ---------------------------------------------------


def test_backfill_runs_is_never_swept_a_running_row_with_no_live_task_is_correct(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> list[dict[str, Any]]:
        await run(
            engine,
            "INSERT INTO backfill_runs (feed, total_units, completed_units, current_unit, status, "
            "started_at, updated_at) VALUES ('fred', 17, 3, 'DGS10', 'running', :s, :s)",
            s=NOW - timedelta(days=2),
        )
        await sweep(engine, NOW)
        return await rows(engine, "SELECT status, current_unit FROM backfill_runs")

    # Waiting to be resumed is not orphaned: the resume design (slice 10) decides what stale means.
    assert go(scratch_url, fn) == [{"status": "running", "current_unit": "DGS10"}]


@pytest.mark.parametrize("name", ["ops.backup", "ops.backup_copy"])
def test_the_backup_tasks_are_never_swept_nothing_heartbeats_them(
    scratch_url: str, name: str
) -> None:
    async def fn(engine: AsyncEngine) -> list[dict[str, Any]]:
        await task(engine, name, NOW - timedelta(hours=3))  # written by psql, started 3 h ago
        result = await sweep(engine, NOW)
        assert result.task_runs == 0
        return await rows(engine, "SELECT status FROM task_runs")

    assert go(scratch_url, fn) == [{"status": "running"}]


def test_a_task_with_an_untouched_name_next_to_a_backup_is_still_swept(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> Any:
        await task(engine, "ops.backup", NOW - timedelta(hours=3))
        await task(engine, "ingest.fred_backfill", NOW - timedelta(hours=3))
        return (await sweep(engine, NOW)).task_runs

    assert go(scratch_url, fn) == 1  # only the backfill's row


# ---- run_feed: the link, the guard, and a completion after the sweep ----------------------------


def test_run_feed_outside_a_recorded_task_raises_before_writing_anything(scratch_url: str) -> None:
    async def body(ctx: FeedContext) -> FeedResult:
        raise AssertionError("must not be reached")

    async def fn(engine: AsyncEngine) -> tuple[int, int]:
        with pytest.raises(NotRecorded):
            await run_feed(engine, "fake", "ticks", body, limiter=Limiter())
        return (
            len(await rows(engine, "SELECT 1 FROM ingestion_runs")),
            len(await rows(engine, "SELECT 1 FROM providers_health")),
        )

    assert go(scratch_url, fn) == (0, 0)


def test_run_feed_links_its_run_to_the_task_that_started_it(scratch_url: str) -> None:
    async def body(ctx: FeedContext) -> FeedResult:
        return FeedResult(rows_read=1, rows_written=1)

    async def fn(engine: AsyncEngine) -> tuple[int, Any]:
        async with recorded(engine) as task_id:
            await run_feed(engine, "fake", "ticks", body, limiter=Limiter())
        run = (await rows(engine, "SELECT task_run_id FROM ingestion_runs"))[0]
        return task_id, run["task_run_id"]

    task_id, linked = go(scratch_url, fn)
    assert linked == task_id


def test_a_feed_that_finishes_after_the_sweep_keeps_the_sweeps_verdict_and_a_note(
    scratch_url: str,
) -> None:
    async def body(ctx: FeedContext) -> FeedResult:
        await sweep(engine_box[0], utcnow() + timedelta(hours=1))  # beats stalled; swept mid-run
        return FeedResult(rows_read=9, rows_written=4)

    engine_box: list[AsyncEngine] = []

    async def fn(engine: AsyncEngine) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        engine_box.append(engine)

        async def work() -> None:
            await run_feed(engine, "fake", "ticks", body, limiter=Limiter(), retry=RetryPolicy())

        await run_recorded(engine, "t.feed", work, heartbeat_interval=0.1)
        return (
            (await rows(engine, "SELECT * FROM ingestion_runs"))[0],
            (await rows(engine, "SELECT * FROM task_runs"))[0],
            (await rows(engine, "SELECT * FROM providers_health"))[0],
        )

    run_row, task_row, health = go(scratch_url, fn)
    assert run_row["status"] == "failed" and run_row["error"].startswith("Orphaned")
    assert "completed after sweep at" in run_row["error"] and "rows_written=4" in run_row["error"]
    assert (task_row["status"], task_row["error_class"]) == ("failed", "Orphaned")
    # The source answered, so providers_health records it: that table is about the source.
    assert health["last_success_at"] is not None


# ---- the Celery body ----------------------------------------------------------------------------


def test_the_sweep_task_is_a_recorded_task_and_sweeps(scratch_url: str) -> None:
    async def seed(engine: AsyncEngine) -> None:
        await task(engine, "t.dead", utcnow() - timedelta(hours=2))

    go(scratch_url, seed)
    asyncio.run(run_sweep())  # DATABASE_URL is the scratch database (the fixture sets it)

    async def read(engine: AsyncEngine) -> list[dict[str, Any]]:
        return await rows(engine, "SELECT task_name, status FROM task_runs ORDER BY id")

    assert go(scratch_url, read) == [
        {"task_name": "t.dead", "status": "failed"},
        {"task_name": "ops.sweep", "status": "succeeded"},  # the sweep has its own ledger row
    ]


# ---- the busy probe -----------------------------------------------------------------------------


def test_the_busy_probe_sees_only_a_fresh_running_row_of_a_named_task(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> dict[str, str | None]:
        probes = SqlRedisProbes(engine, None)
        names = ["ingest.fake"]
        out: dict[str, str | None] = {"none": await probes.running_task_fresh(names, NOW)}
        await task(engine, "ingest.fake", NOW - FRESH - timedelta(seconds=1))  # stale
        out["stale"] = await probes.running_task_fresh(names, NOW)
        await task(engine, "ingest.fred_backfill", NOW - timedelta(seconds=5))  # not this worker's
        out["other_task"] = await probes.running_task_fresh(names, NOW)
        await task(engine, "ingest.fake", NOW - timedelta(seconds=5), status="succeeded")
        out["finished"] = await probes.running_task_fresh(names, NOW)
        await task(engine, "ingest.fake", NOW - timedelta(seconds=5))
        out["fresh"] = await probes.running_task_fresh(names, NOW)
        return out

    assert go(scratch_url, fn) == {
        "none": None,
        "stale": None,
        "other_task": None,  # a manual backfill's beats do not make the ingest worker look busy
        "finished": None,
        "fresh": "ingest.fake",
    }


def test_the_two_task_name_sets_carry_their_decisions() -> None:
    # `busy` means "a task of the INGEST WORKER is alive". A manual backfill is a separate process,
    # and the canaries run on the ops and ingest queues for their own checks: none may excuse a
    # wedged ingest worker.
    assert FRED_BACKFILL not in INGEST_WORKER_TASKS
    assert CANARY not in INGEST_WORKER_TASKS and INGEST_CANARY not in INGEST_WORKER_TASKS
    # The backup scripts write their rows with psql and never heartbeat: sweeping them would fail a
    # healthy dump or copy. The backfill, which does heartbeat, must be swept like any task.
    assert set(UNSWEPT_TASKS) == {BACKUP, BACKUP_COPY}
    assert FRED_BACKFILL not in UNSWEPT_TASKS

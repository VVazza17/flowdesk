"""M2 slice 5, step 3, part C: `run_recorded`'s heartbeat thread, its teardown, and the context
variable. Real Postgres (a scratch database per test); intervals are shortened so it runs fast.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.tasks import runs
from app.tasks.runs import current_task_run_id, run_recorded, utcnow
from app.tasks.sweep import sweep
from tests.clocks import stepless_clock

INTERVAL = 0.1


async def rows(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


def go(scratch_url: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


def heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "heartbeat" and t.is_alive()]


def test_a_task_longer_than_three_intervals_keeps_its_heartbeat_advancing(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> list[Any]:
        seen: list[Any] = []

        async def work() -> None:
            for _ in range(8):
                await asyncio.sleep(INTERVAL)
                seen.append((await rows(engine, "SELECT heartbeat_at FROM task_runs"))[0])

        # The clock is injected: what this test pins is the CODE's order (each beat is written from
        # the clock at write time, by one sequential thread), not the OS wall clock's, which steps
        # backwards on this machine (tests/clocks.py). With utcnow it failed 5 of 150 runs, each one
        # straddling a measured step (docs/evidence/heartbeat-repeat-before.txt).
        await run_recorded(
            engine, "t.long", work, heartbeat_interval=INTERVAL, clock=stepless_clock()
        )
        return [r["heartbeat_at"] for r in seen]

    beats = go(scratch_url, fn)
    assert beats == sorted(beats)
    assert len(set(beats)) >= 4  # written by the thread, while the body was not doing it
    assert heartbeat_threads() == []


def test_a_clock_step_backwards_is_written_as_is_never_held_at_the_maximum(
    scratch_url: str,
) -> None:
    # DECISION (2026-11-18): heartbeat_at follows the clock; no GREATEST(heartbeat_at, new). A
    # backward step makes the row look up to ~2.5 s older than it is, against a 120 s freshness
    # threshold judged on the same clock: harmless. GREATEST would turn a FORWARD step into a
    # sticky one: a clock that jumps an hour ahead and is corrected would keep a dead task looking
    # alive for that hour. This pins the choice: beats after a scripted 5 s step back are stored.
    real = stepless_clock()
    stepped = threading.Event()

    def clock() -> datetime:
        return real() - (timedelta(seconds=5) if stepped.is_set() else timedelta(0))

    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        async def work() -> None:
            await asyncio.sleep(INTERVAL * 3)
            before = (await rows(engine, "SELECT heartbeat_at FROM task_runs"))[0]["heartbeat_at"]
            stepped.set()
            await asyncio.sleep(INTERVAL * 5)  # room for several beats after the step, under load
            after = (await rows(engine, "SELECT heartbeat_at FROM task_runs"))[0]["heartbeat_at"]
            seen.extend([before, after])

        seen: list[Any] = []
        await run_recorded(engine, "t.step", work, heartbeat_interval=INTERVAL, clock=clock)
        return seen[0], seen[1]

    before, after = go(scratch_url, fn)
    assert after < before  # the stepped clock's value was written, not held at the old maximum
    assert before - after < timedelta(seconds=5)


@pytest.mark.parametrize(
    "path", ["returns", "raises_exception", "raises_system_exit", "final_write_raises"]
)
def test_the_heartbeat_thread_is_stopped_on_every_exit_path(
    scratch_url: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fn(engine: AsyncEngine) -> str:
        async def work() -> None:
            await asyncio.sleep(INTERVAL * 2)  # long enough for the thread to be really running
            assert len(heartbeat_threads()) == 1
            if path == "raises_exception":
                raise ValueError("boom")
            if path == "raises_system_exit":
                raise SystemExit(3)

        if path == "final_write_raises":

            async def broken(*a: Any, **k: Any) -> None:
                raise RuntimeError("the final write failed")

            monkeypatch.setattr(runs, "_finish_succeeded", broken)
        outcome = "ok"
        try:
            await run_recorded(engine, "t.paths", work, heartbeat_interval=INTERVAL)
        except (ValueError, RuntimeError, SystemExit) as exc:
            outcome = type(exc).__name__
        return outcome

    outcome = go(scratch_url, fn)
    assert (
        outcome
        == {
            "returns": "ok",
            "raises_exception": "ValueError",
            "raises_system_exit": "SystemExit",
            "final_write_raises": "RuntimeError",
        }[path]
    )
    assert heartbeat_threads() == []  # the `finally` stopped it, whichever way the body left


def test_a_body_that_leaves_by_system_exit_leaves_the_row_running_for_the_sweep(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> list[dict[str, Any]]:
        async def work() -> None:
            raise SystemExit(1)

        try:
            await run_recorded(engine, "t.exit", work, heartbeat_interval=INTERVAL)
        except SystemExit:
            pass
        return await rows(engine, "SELECT status, error_class FROM task_runs")

    # Not `failed`: nothing recorded a verdict, so the sweep will, once the beats have stopped.
    assert go(scratch_url, fn) == [{"status": "running", "error_class": None}]


def test_the_thread_of_a_swept_row_stops_beating_by_itself(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any, int]:
        seen: list[tuple[Any, Any, int]] = []

        async def work() -> None:
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE task_runs SET status = 'failed'"))
            before = (await rows(engine, "SELECT heartbeat_at FROM task_runs"))[0]["heartbeat_at"]
            await asyncio.sleep(INTERVAL * 6)
            after = (await rows(engine, "SELECT heartbeat_at FROM task_runs"))[0]["heartbeat_at"]
            seen.append((before, after, len(heartbeat_threads())))

        await run_recorded(engine, "t.swept", work, heartbeat_interval=INTERVAL)
        return seen[0]

    before, after, alive = go(scratch_url, fn)
    assert before == after  # it did not keep writing to a failed row
    assert alive == 0  # and it exited on its own, before the body even finished


def test_a_failing_heartbeat_write_never_reaches_the_body_and_is_retried(
    scratch_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    async def broken(*a: Any, **k: Any) -> bool:
        attempts.append(1)
        raise OSError("the connection dropped")

    monkeypatch.setattr(runs, "_beat_once", broken)

    async def fn(engine: AsyncEngine) -> list[dict[str, Any]]:
        async def work() -> None:
            await asyncio.sleep(INTERVAL * 6)

        await run_recorded(engine, "t.flaky", work, heartbeat_interval=INTERVAL)
        return await rows(engine, "SELECT status FROM task_runs")

    assert go(scratch_url, fn) == [{"status": "succeeded"}]  # the body was never disturbed
    assert len(attempts) >= 3  # the thread kept trying on every tick
    assert heartbeat_threads() == []


def test_the_row_id_is_published_while_the_body_runs_and_gone_after(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        seen: list[Any] = []

        async def work() -> None:
            seen.append(current_task_run_id.get())

        before = current_task_run_id.get()
        await run_recorded(engine, "t.ctx", work, heartbeat_interval=INTERVAL)
        row = (await rows(engine, "SELECT id FROM task_runs"))[0]["id"]
        return before, seen[0] == row, current_task_run_id.get()

    assert go(scratch_url, fn) == (None, True, None)


def test_a_completion_after_the_sweep_keeps_the_sweeps_verdict_and_adds_a_note(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        async def work() -> None:
            # The beats stalled (a sleeping laptop): the sweep, a while later, fails the row.
            await sweep(engine, utcnow() + timedelta(hours=1))

        await run_recorded(engine, "t.late", work, heartbeat_interval=INTERVAL)
        return (await rows(engine, "SELECT * FROM task_runs"))[0]

    row = go(scratch_url, fn)
    assert row["status"] == "failed" and row["error_class"] == "Orphaned"  # the verdict stands
    assert "completed after sweep at" in row["error_message"]  # and the fact is not lost
    assert row["error_message"].startswith("no heartbeat within")


def test_a_failure_after_the_sweep_is_also_a_note_not_a_status(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        async def work() -> None:
            await sweep(engine, utcnow() + timedelta(hours=1))
            raise ValueError("and then it failed")

        with pytest.raises(ValueError):
            await run_recorded(engine, "t.late_fail", work, heartbeat_interval=INTERVAL)
        return (await rows(engine, "SELECT * FROM task_runs"))[0]

    row = go(scratch_url, fn)
    assert (row["status"], row["error_class"]) == ("failed", "Orphaned")
    assert "failed after sweep at" in row["error_message"] and row["error_message"].endswith(
        ": ValueError"
    )

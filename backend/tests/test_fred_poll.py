"""M2 slice 5, step 3: the daily FRED poll, on a real Postgres and the shared fake FRED.

The poll asks FRED only for what changed since a series was last captured, merges that onto the
stored chains, runs the SAME six rules over the merged history, and appends only the new tail.
Every revision is dated by FRED's vintage, never by the poll (ADR-0003 addendum, F1).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.errors import NoBaseline, SchemaMismatch
from app.ingest.fred_feed import PROVIDER, history_body, poll_body
from app.ingest.macro import FredSeries, available_at_for
from app.ingest.runner import FeedResult, RetryPolicy, run_feed
from app.tasks.fred_ingest import PollIncomplete, poll_all
from app.tasks.runs import run_recorded
from tests.fakefred import START, FakeFred, Limiter, Routed, simple
from tests.recorded import recorded

KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
NO_RETRY = RetryPolicy(attempts=1)


def item(series_id: str = "TESTSER") -> FredSeries:
    return FredSeries(series_id, START + timedelta(days=1), START)


async def rows(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


def go(
    scratch_url: str, fn: Callable[[AsyncEngine], Awaitable[Any]], *, record: bool = True
) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            if not record:
                return await fn(engine)
            async with recorded(engine):
                return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def capture(
    engine: AsyncEngine, fred: FakeFred, sid: str = "TESTSER", **kw: Any
) -> FeedResult:
    lim = Limiter()

    async def pace() -> None:
        await lim.acquire(PROVIDER)

    return await run_feed(
        engine,
        PROVIDER,
        sid,
        history_body(
            item(sid),
            api_key=KEY,
            user_agent="FlowDesk-test",
            pace=pace,
            transport=httpx2.MockTransport(fred),
            engine=engine,
            **kw,
        ),
        limiter=lim,
        retry=NO_RETRY,
        secrets=[KEY],
    )


async def poll(engine: AsyncEngine, fred: Any, sid: str = "TESTSER", **kw: Any) -> FeedResult:
    lim = Limiter()

    async def pace() -> None:
        await lim.acquire(PROVIDER)

    return await run_feed(
        engine,
        PROVIDER,
        sid,
        poll_body(
            item(sid),
            api_key=KEY,
            user_agent="FlowDesk-test",
            pace=pace,
            transport=httpx2.MockTransport(fred),
            engine=engine,
            **kw,
        ),
        limiter=lim,
        retry=NO_RETRY,
        secrets=[KEY],
    )


def test_a_quiet_day_writes_nothing_and_leaves_the_state_alone(scratch_url: str) -> None:
    fred = FakeFred(simple(3, rev={0: [(2, "1.6")]}))

    async def fn(engine: AsyncEngine) -> tuple[FeedResult, dict[str, Any], dict[str, Any], int]:
        await capture(engine, fred)
        before = (await rows(engine, "SELECT * FROM series_history_state"))[0]
        result = await poll(engine, fred)
        after = (await rows(engine, "SELECT * FROM series_history_state"))[0]
        n = (await rows(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
        return result, before, after, int(n)

    result, before, after, n = go(scratch_url, fn)
    assert result.rows_written == 0  # the observable evidence that a re-read changes nothing
    assert n == 4
    for key in ("first_prints", "revisions", "captured_through", "rule_version"):
        assert before[key] == after[key]


def test_a_new_revision_is_dated_by_fred_not_by_the_poll(scratch_url: str) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[FeedResult, list[dict[str, Any]], dict[str, Any]]:
        await capture(engine, fred)
        # FRED revises period 0 five days after its first print, after the capture.
        fred.periods = simple(3, rev={0: [(5, "1.6")]})
        result = await poll(engine, fred)
        stored = await rows(
            engine,
            f"SELECT revision, value, available_at, ingested_at FROM macro_metrics "
            f"WHERE effective_at = '{START}' ORDER BY revision",
        )
        state = (await rows(engine, "SELECT * FROM series_history_state"))[0]
        return result, stored, state

    result, stored, state = go(scratch_url, fn)
    assert result.rows_written == 1
    vintage = START + timedelta(days=1 + 5)
    assert [(r["revision"], str(r["value"])) for r in stored] == [(0, "1.5"), (1, "1.6")]
    assert stored[1]["available_at"] == available_at_for(vintage)  # FRED's date, 2020-01-12
    assert stored[1]["available_at"] < stored[1]["ingested_at"]  # not the instant we polled
    assert state["captured_through"] == vintage  # the next poll starts here
    assert state["revisions"] == 1


def test_a_new_period_is_appended_as_a_witnessed_first_print(scratch_url: str) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[FeedResult, list[dict[str, Any]]]:
        await capture(engine, fred)
        fred.periods = simple(4)
        result = await poll(engine, fred)
        return result, await rows(
            engine,
            "SELECT revision, available_at FROM macro_metrics "
            f"WHERE effective_at = '{START + timedelta(days=3)}'",
        )

    result, new = go(scratch_url, fn)
    assert result.rows_written == 1
    assert [r["revision"] for r in new] == [0]
    assert new[0]["available_at"] == available_at_for(START + timedelta(days=4))


def test_a_gap_first_print_that_output_type_4_omits_is_stored_by_the_poll_too(
    scratch_url: str,
) -> None:
    fred = FakeFred(simple(3))
    holiday = START + timedelta(days=50)

    async def fn(engine: AsyncEngine) -> tuple[FeedResult, list[dict[str, Any]], dict[str, Any]]:
        await capture(engine, fred)
        fred.pre_revised = {
            holiday: [(START + timedelta(days=8), None)]
        }  # in o3 and latest, not o4
        result = await poll(engine, fred)
        return (
            result,
            await rows(
                engine,
                f"SELECT revision, value FROM macro_metrics WHERE effective_at = '{holiday}'",
            ),
            (await rows(engine, "SELECT * FROM series_history_state"))[0],
        )

    result, stored, state = go(scratch_url, fn)
    assert result.rows_written == 1 and stored == [{"revision": 0, "value": None}]
    # The poll sees the exclusion kinds only where its window touches them: it leaves those counts
    # as the last full capture left them rather than overwrite them with a partial view.
    assert state["gap_first_prints_without_o4_witness"] == 0


def test_a_revised_pre_vintage_period_is_excluded_not_stored_and_not_an_error(
    scratch_url: str,
) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[FeedResult, int]:
        await capture(engine, fred)
        fred.pre_revised = {START - timedelta(days=30): [(START + timedelta(days=8), "9.1")]}
        result = await poll(engine, fred)
        return result, int((await rows(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"])

    result, n = go(scratch_url, fn)
    assert result.rows_written == 0 and n == 3


def test_a_series_with_no_captured_history_is_refused_and_nothing_is_written(
    scratch_url: str,
) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[int, int, list[dict[str, Any]]]:
        with pytest.raises(NoBaseline, match="python -m app.backfill"):
            await poll(engine, fred)
        return (
            int((await rows(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]),
            int((await rows(engine, "SELECT count(*) AS n FROM series_history_state"))[0]["n"]),
            await rows(engine, "SELECT status, error FROM ingestion_runs"),
        )

    n, states, runs = go(scratch_url, fn)
    # Without this guard the poll would write the whole history with available_at set to today.
    assert (n, states) == (0, 0)
    assert runs[0]["status"] == "failed" and runs[0]["error"].startswith("permanent:NoBaseline")
    assert fred.calls == []  # it did not even ask FRED


def test_history_captured_under_older_rules_is_refused_too(scratch_url: str) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> None:
        await capture(engine, fred, rule_version=1)
        with pytest.raises(NoBaseline):
            await poll(engine, fred, rule_version=2)

    go(scratch_url, fn)


def test_fred_changing_a_value_on_the_date_already_captured_is_refused_not_missed(
    scratch_url: str,
) -> None:
    """A vintage is a date, not an instant. The poll re-reads the last captured vintage (inclusive)
    so that a second change on the same date shows up as a disagreement instead of vanishing."""
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[str, int]:
        await capture(engine, fred)
        fred.periods[START + timedelta(days=2)] = [(START + timedelta(days=3), "9.9")]
        with pytest.raises(SchemaMismatch) as info:
            await poll(engine, fred)
        n = (await rows(engine, "SELECT count(*) AS n FROM macro_metrics WHERE value = 9.9"))[0][
            "n"
        ]
        return str(info.value), int(n)

    message, n = go(scratch_url, fn)
    # Two values for one vintage (rule 5); what is stored is not a prefix of what was fetched (6).
    assert "rule 5" in message and "rule 6" in message
    assert n == 0  # nothing written


def test_a_chain_that_no_longer_ends_at_the_latest_value_is_refused(scratch_url: str) -> None:
    """A revision published between the poll's reads: latest is ahead of the history it fetched.
    Rule 3 refuses, and the next day's poll, which sees both, catches up."""

    class LatestAhead(FakeFred):
        def __call__(self, request: httpx2.Request) -> httpx2.Response:
            response = super().__call__(request)
            if request.url.params.get("output_type") == "1":
                body = response.json()
                body["observations"][0]["value"] = "7.7"
                return httpx2.Response(200, json=body)
            return response

    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[str, int]:
        await capture(engine, fred)
        ahead = LatestAhead(fred.periods)
        with pytest.raises(SchemaMismatch) as info:
            await poll(engine, ahead)
        return str(info.value), int(
            (await rows(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
        )

    message, n = go(scratch_url, fn)
    assert "rule 3" in message and n == 3


def test_after_a_long_gap_the_poll_catches_up_through_windows_that_split(scratch_url: str) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[FeedResult, int, FakeFred]:
        await capture(engine, fred)
        slow = FakeFred(simple(260, rev={5: [(3, "1.6")]}), o3_limit=30)  # FRED's gateway is slow
        result = await poll(engine, slow)
        n = (await rows(engine, "SELECT count(DISTINCT effective_at) AS n FROM macro_metrics"))[0][
            "n"
        ]
        return result, int(n), slow

    result, periods, slow = go(scratch_url, fn)
    assert result.rows_written == 258  # 257 new periods + one revision, none missed or repeated
    assert periods == 260
    assert slow.calls.count("o3") > 4  # more than one window, after the halvings


def test_the_poll_leaves_the_exclusion_counts_alone_and_refreshes_the_totals(
    scratch_url: str,
) -> None:
    before_pre = tuple(START - timedelta(days=40 + i) for i in range(3))
    fred = FakeFred(simple(3), pre_plain=before_pre)  # exclusion A: 3 unrevised pre-vintage periods

    async def fn(engine: AsyncEngine) -> tuple[dict[str, Any], dict[str, Any]]:
        await capture(engine, fred)
        first = (await rows(engine, "SELECT * FROM series_history_state"))[0]
        fred.periods = simple(4, rev={0: [(4, "1.6")]})
        await poll(engine, fred)
        return first, (await rows(engine, "SELECT * FROM series_history_state"))[0]

    first, after = go(scratch_url, fn)
    assert first["no_vintage_excluded"] == after["no_vintage_excluded"] == 3
    assert (first["first_prints"], first["revisions"]) == (3, 0)
    assert (after["first_prints"], after["revisions"]) == (4, 1)  # the totals are complete


def test_polling_one_series_never_touches_another_series_state(scratch_url: str) -> None:
    """With seventeen state rows an UPDATE that forgot its WHERE would advance all of them."""
    routed = Routed({"AAA": FakeFred(simple(3)), "BBB": FakeFred(simple(3))})

    async def fn(engine: AsyncEngine) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        await capture(engine, routed.fakes["AAA"], sid="AAA")
        await capture(engine, routed.fakes["BBB"], sid="BBB")
        before = (await rows(engine, "SELECT * FROM series_history_state WHERE series_id = 'BBB'"))[
            0
        ]
        routed.fakes["AAA"].periods = simple(4, rev={0: [(5, "1.6")]})  # only AAA changes
        await poll(engine, routed, sid="AAA")
        after_b = (
            await rows(engine, "SELECT * FROM series_history_state WHERE series_id = 'BBB'")
        )[0]
        after_a = (
            await rows(engine, "SELECT * FROM series_history_state WHERE series_id = 'AAA'")
        )[0]
        return before, after_b, after_a

    before, after_b, after_a = go(scratch_url, fn)
    assert after_b == before  # BBB is exactly as it was
    assert (after_a["first_prints"], after_a["revisions"]) == (4, 1)


def test_poll_all_continues_past_a_failed_series_and_names_it(scratch_url: str) -> None:
    routed = Routed({"AAA": FakeFred(simple(3)), "BBB": FakeFred(simple(3))})

    async def fn(engine: AsyncEngine) -> tuple[str, list[dict[str, Any]]]:
        await capture(engine, routed.fakes["AAA"], sid="AAA")  # BBB was never captured
        with pytest.raises(PollIncomplete) as info:
            await poll_all(
                engine,
                [item("BBB"), item("AAA")],
                api_key=KEY,
                user_agent="FlowDesk-test",
                limiter=Limiter(),
                base_url="https://example.invalid/fred",
                transport=httpx2.MockTransport(routed),
                retry=NO_RETRY,
            )
        return str(info.value), await rows(
            engine, "SELECT feed, status FROM ingestion_runs WHERE id > 1 ORDER BY id"
        )

    message, runs = go(scratch_url, fn)
    assert "BBB" in message and "AAA" not in message
    assert runs == [{"feed": "BBB", "status": "failed"}, {"feed": "AAA", "status": "succeeded"}]


def test_every_poll_run_is_linked_to_the_task_that_started_it(scratch_url: str) -> None:
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with recorded(engine):
            await capture(engine, fred)

        async def body() -> None:
            await poll_all(
                engine,
                [item()],
                api_key=KEY,
                user_agent="FlowDesk-test",
                limiter=Limiter(),
                base_url="https://example.invalid/fred",
                transport=httpx2.MockTransport(fred),
                retry=NO_RETRY,
            )

        await run_recorded(engine, "ingest.fred_poll", body, heartbeat_interval=0.1)
        return (
            await rows(
                engine,
                "SELECT id, task_name, status FROM task_runs WHERE task_name = 'ingest.fred_poll'",
            ),
            await rows(
                engine, "SELECT task_run_id, status FROM ingestion_runs ORDER BY id DESC LIMIT 1"
            ),
        )

    tasks, runs = go(scratch_url, fn, record=False)
    assert tasks[0]["status"] == "succeeded"
    assert runs == [{"task_run_id": tasks[0]["id"], "status": "succeeded"}]


def test_revision_dates_come_from_fred_so_the_polls_own_clock_cannot_move_them(
    scratch_url: str,
) -> None:
    fred = FakeFred(simple(3, rev={0: [(5, "1.6")]}))

    async def fn(engine: AsyncEngine) -> Any:
        await capture(engine, FakeFred(simple(3)))
        # A poll that "ran" in 2031 stores exactly the same available_at as one that ran today.
        await poll(engine, fred, clock=lambda: datetime(2031, 6, 1, tzinfo=UTC))
        return (await rows(engine, "SELECT available_at FROM macro_metrics WHERE revision = 1"))[0]

    row = go(scratch_url, fn)
    assert row["available_at"] == available_at_for(START + timedelta(days=6))


def test_the_poll_reads_the_vintage_dates_first_and_only_asks_for_windows_that_hold_one(
    scratch_url: str,
) -> None:
    """The invariant that keeps FRED's "No vintage dates exist" 400 away from the poll. The fake
    answers a vintage-less window with the real 400, so a poll that skipped the vintage-date read
    and asked for a bare range on a quiet day would fail here, not in production at 08:00."""
    fred = FakeFred(simple(3))

    async def fn(engine: AsyncEngine) -> None:
        await capture(engine, fred)
        fred.calls.clear()
        fred.windows_asked.clear()
        result = await poll(engine, fred)  # a quiet day: nothing new since the capture
        assert result.rows_written == 0

    go(scratch_url, fn)
    assert fred.calls[0] == "vintagedates"  # the list first, before any observations request
    known = fred.vintages()
    assert fred.windows_asked  # it did read the last captured vintage again (inclusive)
    for _kind, start, end in fred.windows_asked:
        assert any(start <= v <= end for v in known), "a window with no vintage date was requested"


def test_a_bare_range_after_the_last_vintage_is_the_400_the_poll_avoids() -> None:
    """What 'optimising' the vintage-date read away would bring back, shown directly."""
    from app.ingest.errors import NoVintageDates
    from app.ingest.fred import FredClient

    fred = FakeFred(simple(3))
    after_last = max(fred.vintages()) + timedelta(days=1)

    async def ask(flag: bool) -> list[Any]:
        async with FredClient(
            KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(fred)
        ) as client:
            return list(
                await client.observations(
                    "TESTSER",
                    output_type=3,
                    realtime_start=after_last,
                    realtime_end=date(9999, 12, 31),
                    no_vintages_is_empty=flag,
                )
            )

    with pytest.raises(NoVintageDates):
        asyncio.run(ask(False))  # a backfill-style caller: fatal
    assert asyncio.run(ask(True)) == []  # a caller that opts in reads it as a quiet day

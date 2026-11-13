"""M2 slice 5, step 3, commit B: the revision-history backfill into macro_metrics, on a real
Postgres and a fake FRED.

Needs DATABASE_URL (a scratch database per test). The fake FRED serves what the real one was
measured to serve: vintage dates, `output_type=4` first prints with the 2000-vintage cap,
`output_type=3` wide rows (bare rows included) with a gateway timeout past a window size, and the
latest values.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.backfill import BackfillIncomplete, backfill_all, execute
from app.ingest.errors import SchemaMismatch
from app.ingest.fred_feed import PROVIDER, history_body
from app.ingest.macro import FredSeries, available_at_for
from app.ingest.runner import RetryPolicy, run_feed

ADMIN_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
NO_RETRY = RetryPolicy(attempts=1)
START = date(2020, 1, 6)  # a Monday

Chain = list[tuple[date, str | None]]


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture
def scratch_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"flowdesk_fredbf_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


class Limiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self, provider: str) -> None:
        self.acquired += 1


def simple(
    days: int, *, lag: int = 1, rev: dict[int, list[tuple[int, str | None]]] | None = None
) -> dict[date, Chain]:
    """Period i is first printed 1.5 on START+i+lag. `rev[i]` lists (days after that first
    publication, value) for its revisions."""
    out: dict[date, Chain] = {}
    for i in range(days):
        first = START + timedelta(days=i + lag)
        chain: Chain = [(first, "1.5")]
        for after, value in (rev or {}).get(i, []):
            chain.append((first + timedelta(days=after), value))
        out[START + timedelta(days=i)] = chain
    return out


class FakeFred:
    """One series. `periods` are in `output_type=4`; `pre_revised` periods (before the record,
    revised after it) are in `output_type=3` and `latest` only; `pre_plain` and `gaps` are in
    `latest` only. `hide` drops a first print from `output_type=4` alone (a silent shortfall).
    `o3_limit`: a window holding more vintage dates than this gets the gateway's 504."""

    def __init__(
        self,
        periods: dict[date, Chain],
        *,
        pre_revised: dict[date, Chain] | None = None,
        pre_plain: tuple[date, ...] = (),
        gaps: tuple[date, ...] = (),
        hide: tuple[date, ...] = (),
        o3_limit: int = 100_000,
    ) -> None:
        self.periods = periods
        self.pre_revised = pre_revised or {}
        self.pre_plain, self.gaps, self.hide, self.o3_limit = pre_plain, gaps, set(hide), o3_limit
        self.calls: list[str] = []

    def chains(self) -> dict[date, Chain]:
        return {**self.pre_revised, **self.periods}

    def vintages(self) -> list[date]:
        return sorted({v for chain in self.chains().values() for v, _ in chain})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        q = request.url.params
        sid = q["series_id"]

        def text_of(value: str | None) -> str:
            return "." if value is None else value

        if request.url.path.endswith("/series/vintagedates"):
            self.calls.append("vintagedates")
            v = self.vintages()
            return httpx2.Response(
                200, json={"count": len(v), "vintage_dates": [d.isoformat() for d in v]}
            )
        output_type = q["output_type"]
        self.calls.append(f"o{output_type}")
        if output_type == "1":
            rows = {p: ch[-1][1] for p, ch in self.chains().items()}
            rows.update({p: "2.5" for p in self.pre_plain})
            rows.update({p: None for p in self.gaps})
            body = [
                {
                    "realtime_start": "2026-11-12",
                    "realtime_end": "2026-11-12",
                    "date": p.isoformat(),
                    "value": text_of(v),
                }
                for p, v in sorted(rows.items())
            ]
            return httpx2.Response(200, json={"count": len(body), "observations": body})
        start, end = date.fromisoformat(q["realtime_start"]), date.fromisoformat(q["realtime_end"])
        held = [v for v in self.vintages() if start <= v <= end]
        if len(held) > 2000:
            return httpx2.Response(
                400,
                json={
                    "error_code": 400,
                    "error_message": "This exceeds the maximum number of vintage dates (2000).",
                },
            )
        if output_type == "4":
            rows4 = [
                {
                    "realtime_start": ch[0][0].isoformat(),
                    "realtime_end": "9999-12-31",
                    "date": p.isoformat(),
                    "value": text_of(ch[0][1]),
                }
                for p, ch in sorted(self.periods.items())
                if p not in self.hide and start <= ch[0][0] <= end
            ]
            return httpx2.Response(200, json={"count": len(rows4), "observations": rows4})
        assert output_type == "3"
        if len(held) > self.o3_limit:
            return httpx2.Response(504, text="<html>504 Gateway Time-out</html>")
        rows3: list[dict[str, str]] = []
        for p, ch in sorted(self.chains().items()):
            cols = {f"{sid}_{v:%Y%m%d}": text_of(val) for v, val in ch if start <= v <= end}
            if cols:
                rows3.append({"date": p.isoformat(), **cols})
            elif ch[0][0] < start:
                rows3.append({"date": p.isoformat()})  # unchanged in this window: a bare row
        return httpx2.Response(200, json={"count": len(rows3), "observations": rows3})


def series(series_id: str = "TESTSER") -> FredSeries:
    return FredSeries(series_id, START + timedelta(days=1), START)


async def _all(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


async def _run(
    engine: AsyncEngine, fred: FakeFred, *, item: FredSeries | None = None, **kw: Any
) -> Any:
    item = item or series()
    lim = Limiter()

    async def pace() -> None:
        await lim.acquire(PROVIDER)

    return await run_feed(
        engine,
        PROVIDER,
        item.series_id,
        history_body(
            item,
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


def go(scratch_url: str, coro: Any) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            return await coro(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


def test_the_whole_history_is_stored_with_revision_numbers_and_vintage_availability(
    scratch_url: str,
) -> None:
    # Period 0: printed, revised to 1.6 three days later, then to a gap. Period 1: never revised.
    fred = FakeFred(simple(3, rev={0: [(3, "1.6"), (10, None)]}))

    async def body(engine: AsyncEngine) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        result = await _run(engine, fred)
        rows = await _all(engine, "SELECT * FROM macro_metrics ORDER BY effective_at, revision")
        runs = await _all(engine, "SELECT id, status FROM ingestion_runs")
        return result, rows, runs

    result, rows, runs = go(scratch_url, body)
    # 3 first prints + 2 revisions of period 0, all new; 5 columns read.
    assert (result.rows_read, result.rows_written) == (5, 5)
    by = [(r["effective_at"], r["revision"], r["value"]) for r in rows]
    p0, p1, p2 = START, START + timedelta(days=1), START + timedelta(days=2)
    assert by == [
        (p0, 0, Decimal("1.5")),
        (p0, 1, Decimal("1.6")),
        (p0, 2, None),  # a gap in a vintage is a stored NULL, never 0.0
        (p1, 0, Decimal("1.5")),
        (p2, 0, Decimal("1.5")),
    ]
    first = START + timedelta(days=1)  # first publication of period 0
    # Each revision is dated by FRED's vintage, end of that day in New York: not by the poll.
    assert rows[0]["available_at"] == available_at_for(first)
    assert rows[1]["available_at"] == available_at_for(first + timedelta(days=3))
    assert rows[1]["available_at"] == datetime(2020, 1, 11, 5, 0, tzinfo=UTC)
    assert {r["ingestion_run_id"] for r in rows} == {runs[0]["id"]}
    assert [r["status"] for r in runs] == ["succeeded"]


def test_a_rerun_writes_nothing_and_a_new_revision_is_appended_alone(scratch_url: str) -> None:
    fred = FakeFred(simple(3, rev={0: [(3, "1.6")]}))

    async def body(engine: AsyncEngine) -> tuple[int, int, int, list[dict[str, Any]]]:
        first = await _run(engine, fred)
        again = await _run(engine, fred)
        # FRED revises period 0 once more, and prints period 3.
        fred.periods = simple(4, rev={0: [(3, "1.6"), (20, "1.7")]})
        extended = await _run(engine, fred)
        state = await _all(engine, "SELECT * FROM series_history_state")
        return first.rows_written, again.rows_written, extended.rows_written, state

    first, again, extended, state = go(scratch_url, body)
    assert (first, again) == (4, 0)
    assert extended == 2  # period 0's new revision, and period 3's first print: nothing else
    assert state[0]["captured_through"] == max(v for ch in fred.chains().values() for v, _ in ch)


def test_the_two_exclusions_are_stored_apart_and_their_periods_are_not_stored(
    scratch_url: str,
) -> None:
    before = START - timedelta(days=30)
    pre_plain = tuple(before - timedelta(days=i) for i in range(3))
    revised: dict[date, Chain] = {
        before + timedelta(days=1): [(START + timedelta(days=20), "9.1")],
        before + timedelta(days=2): [
            (START + timedelta(days=21), "9.2"),
            (START + timedelta(days=22), "9.3"),
        ],
    }
    gap = START + timedelta(days=100)
    fred = FakeFred(simple(4), pre_revised=revised, pre_plain=pre_plain, gaps=(gap,))

    async def body(engine: AsyncEngine) -> tuple[dict[str, Any], int]:
        await _run(engine, fred)
        state = (await _all(engine, "SELECT * FROM series_history_state"))[0]
        n = (await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
        return state, int(n)

    state, stored = go(scratch_url, body)
    assert stored == 4  # the four first prints; no pre-vintage period, revised or not
    assert state["no_vintage_excluded"] == 3  # exclusion A: no publication date exists
    assert state["gaps_without_trace_skipped"] == 1  # exclusion A: a "." nobody published
    assert state["revised_pre_vintage_excluded"] == 2  # exclusion B: recoverable, not modelled
    assert (state["first_prints"], state["revisions"]) == (4, 0)


def test_a_failed_rule_writes_nothing_not_even_the_state_row(scratch_url: str) -> None:
    periods = simple(5)
    fred = FakeFred(periods, hide=(START + timedelta(days=2),))  # o4 loses one first print

    async def body(engine: AsyncEngine) -> tuple[str | None, int, int, str]:
        with pytest.raises(SchemaMismatch) as info:
            await _run(engine, fred)
        runs = await _all(engine, "SELECT status, error FROM ingestion_runs")
        n = (await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
        s = (await _all(engine, "SELECT count(*) AS n FROM series_history_state"))[0]["n"]
        return runs[0]["error"], int(n), int(s), str(info.value)

    error, stored, states, message = go(scratch_url, body)
    assert stored == 0 and states == 0  # a partial history must never look complete
    assert error is not None and "SchemaMismatch" in error
    assert "rule 2" in message and "1.5" not in message  # rules and dates; no provider value


def test_stored_history_that_disagrees_with_the_source_refuses_and_changes_nothing(
    scratch_url: str,
) -> None:
    async def body(engine: AsyncEngine) -> tuple[int, list[dict[str, Any]], str]:
        await _run(engine, FakeFred(simple(3)))
        before = await _all(engine, "SELECT * FROM macro_metrics ORDER BY effective_at")
        with pytest.raises(SchemaMismatch) as info:
            await _run(engine, FakeFred(simple(3, lag=2)))  # same periods published a day later
        after = await _all(engine, "SELECT * FROM macro_metrics ORDER BY effective_at")
        assert before == after
        state = await _all(engine, "SELECT * FROM series_history_state")
        return len(after), state, str(info.value)

    stored, state, message = go(scratch_url, body)
    assert stored == 3 and "rule 6" in message
    assert len(state) == 1


def test_the_first_prints_stored_by_the_old_backfill_stay_valid(scratch_url: str) -> None:
    """The 17 series already hold revision-0 rows from the first-print-only backfill (38,294 rows).
    The history backfill must treat them as the first element of each chain: add the revisions,
    rewrite nothing."""
    fred = FakeFred(simple(3, rev={1: [(4, "1.9")]}))

    async def body(engine: AsyncEngine) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        pre = datetime(2020, 1, 1, tzinfo=UTC)
        async with engine.begin() as conn:
            for p, ch in fred.periods.items():
                await conn.execute(
                    text(
                        "INSERT INTO macro_metrics (metric, effective_at, revision, value, "
                        "available_at, ingested_at, source) VALUES "
                        "('TESTSER', :p, 0, 1.5, :a, :i, 'fred')"
                    ),
                    {"p": p, "a": available_at_for(ch[0][0]), "i": pre},
                )
        result = await _run(engine, fred)
        rows = await _all(engine, "SELECT * FROM macro_metrics ORDER BY effective_at, revision")
        state = await _all(engine, "SELECT * FROM series_history_state")
        return result.rows_written, rows, state

    written, rows, state = go(scratch_url, body)
    assert written == 1  # only the revision
    zero = [r for r in rows if r["revision"] == 0]
    assert len(zero) == 3 and {r["ingested_at"] for r in zero} == {datetime(2020, 1, 1, tzinfo=UTC)}
    assert [r["revision"] for r in rows if r["effective_at"] == START + timedelta(days=1)] == [0, 1]
    assert len(state) == 1


def test_a_stored_chain_with_a_hole_in_its_revision_numbers_is_refused_not_extended(
    scratch_url: str,
) -> None:
    """The store never writes revision 0 and 2 without 1; finding that means the table was repaired
    by hand, and extending it would number new revisions on top of a lie."""
    fred = FakeFred(simple(2))

    async def body(engine: AsyncEngine) -> int:
        async with engine.begin() as conn:
            for revision in (0, 2):
                await conn.execute(
                    text(
                        "INSERT INTO macro_metrics (metric, effective_at, revision, value, "
                        "available_at, source) VALUES ('TESTSER', :p, :r, 1.5, :a, 'fred')"
                    ),
                    {"p": START, "r": revision, "a": available_at_for(START + timedelta(days=1))},
                )
        with pytest.raises(SchemaMismatch, match="not contiguous"):
            await _run(engine, fred)
        return int((await _all(engine, "SELECT count(*) AS n FROM series_history_state"))[0]["n"])

    assert go(scratch_url, body) == 0


def test_a_window_that_times_out_is_split_and_the_stored_history_is_identical(
    scratch_url: str,
) -> None:
    periods = simple(60, rev={5: [(3, "1.6")], 30: [(2, "1.7"), (9, "1.8")]})

    async def body(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        await _run(engine, FakeFred(periods))  # one window: all vintage dates fit in 100
        want = await _all(
            engine,
            "SELECT effective_at, revision, value, available_at FROM macro_metrics ORDER BY 1, 2",
        )
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM series_history_state"))
            await conn.execute(text("ALTER TABLE macro_metrics DISABLE TRIGGER USER"))
            await conn.execute(text("DELETE FROM macro_metrics"))
            await conn.execute(text("ALTER TABLE macro_metrics ENABLE TRIGGER USER"))
        slow = FakeFred(periods, o3_limit=7)
        await _run(engine, slow)
        got = await _all(
            engine,
            "SELECT effective_at, revision, value, available_at FROM macro_metrics ORDER BY 1, 2",
        )
        assert slow.calls.count("o3") > 8  # many small windows, after the halvings
        return want, got

    want, got = go(scratch_url, body)
    assert got == want and len(want) == 63  # 60 first prints + 3 revisions


# ---- the entry point: skip, force, rule version, ledger --------------------------------------


class Routed:
    """Routes by series_id to a FakeFred and counts the calls that reach each."""

    def __init__(self, fakes: dict[str, FakeFred]) -> None:
        self.fakes = fakes
        self.touched: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        sid = request.url.params["series_id"]
        self.touched.append(sid)
        if sid not in self.fakes:
            body = {"error_code": 400, "error_message": "Bad Request.  The series does not exist."}
            return httpx2.Response(400, json=body)
        return self.fakes[sid](request)


async def _backfill(engine: AsyncEngine, routed: Routed, ids: list[str], **kw: Any) -> None:
    await backfill_all(
        engine,
        [series(i) for i in ids],
        api_key=KEY,
        user_agent="FlowDesk-test",
        limiter=Limiter(),
        base_url="https://example.invalid/fred",
        transport=httpx2.MockTransport(routed),
        retry=NO_RETRY,
        **kw,
    )


def test_a_captured_series_is_skipped_and_an_incomplete_one_is_fetched(scratch_url: str) -> None:
    routed = Routed({"AAA": FakeFred(simple(3)), "BBB": FakeFred(simple(3))})

    async def body(engine: AsyncEngine) -> tuple[list[str], list[dict[str, Any]], int]:
        await _backfill(engine, routed, ["AAA"])  # BBB never started
        routed.touched.clear()
        runs_before = len(await _all(engine, "SELECT * FROM ingestion_runs"))
        await _backfill(engine, routed, ["AAA", "BBB"])
        touched = sorted(set(routed.touched))
        progress = await _all(engine, "SELECT * FROM backfill_runs ORDER BY id")
        runs_after = len(await _all(engine, "SELECT * FROM ingestion_runs"))
        return touched, progress, runs_after - runs_before

    touched, progress, new_runs = go(scratch_url, body)
    assert touched == ["BBB"]  # AAA cost nothing: not one request
    assert new_runs == 1  # a skip writes no ingestion_runs row
    assert (progress[-1]["total_units"], progress[-1]["completed_units"]) == (2, 2)
    assert progress[-1]["status"] == "succeeded"


def test_a_rule_version_bump_captures_every_series_again_and_writes_nothing_new(
    scratch_url: str,
) -> None:
    routed = Routed({"AAA": FakeFred(simple(3))})

    async def body(engine: AsyncEngine) -> tuple[list[str], list[dict[str, Any]], int]:
        await _backfill(engine, routed, ["AAA"], rule_version=1)
        routed.touched.clear()
        await _backfill(engine, routed, ["AAA"], rule_version=1)
        skipped = list(routed.touched)
        await _backfill(engine, routed, ["AAA"], rule_version=2)
        state = await _all(engine, "SELECT rule_version FROM series_history_state")
        n = (await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"]
        return skipped, state, int(n)

    skipped, state, rows = go(scratch_url, body)
    assert skipped == []  # same version: skipped
    assert state == [{"rule_version": 2}] and rows == 3  # recaptured under 2; no duplicate rows


def test_force_captures_a_named_series_again(scratch_url: str) -> None:
    routed = Routed({"AAA": FakeFred(simple(3))})

    async def body(engine: AsyncEngine) -> list[str]:
        await _backfill(engine, routed, ["AAA"])
        routed.touched.clear()
        await _backfill(engine, routed, ["AAA"], force=True)
        return list(routed.touched)

    assert go(scratch_url, body) != []


def test_an_interrupted_series_starts_again_from_its_first_request_it_is_not_resumed(
    scratch_url: str,
) -> None:
    fred = FakeFred(simple(3))
    calls_seen: list[list[str]] = []

    def dying(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("output_type") == "3":
            raise httpx2.ConnectError("boom")  # dies partway: after the vintage dates
        return fred(request)

    async def body(engine: AsyncEngine) -> int:
        with pytest.raises(BackfillIncomplete):
            await backfill_all(
                engine,
                [series("AAA")],
                api_key=KEY,
                user_agent="FlowDesk-test",
                limiter=Limiter(),
                base_url="https://example.invalid/fred",
                transport=httpx2.MockTransport(dying),
                retry=NO_RETRY,
            )
        assert (await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"] == 0
        assert (await _all(engine, "SELECT count(*) AS n FROM series_history_state"))[0]["n"] == 0
        fred.calls.clear()
        await _backfill(engine, Routed({"AAA": fred}), ["AAA"])
        calls_seen.append(list(fred.calls))
        return int((await _all(engine, "SELECT count(*) AS n FROM macro_metrics"))[0]["n"])

    assert go(scratch_url, body) == 3
    assert calls_seen[0][0] == "vintagedates"  # from the very first request, nothing carried over


def test_backfill_all_continues_past_a_failed_series_then_reports_it(scratch_url: str) -> None:
    routed = Routed({"GOODSER": FakeFred(simple(3))})  # BADSER is unknown to FRED: a 400

    async def body(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        with pytest.raises(BackfillIncomplete) as info:
            await _backfill(engine, routed, ["BADSER", "GOODSER"])
        progress = await _all(engine, "SELECT * FROM backfill_runs")
        runs = await _all(engine, "SELECT feed, status FROM ingestion_runs ORDER BY id")
        return progress, runs, str(info.value)

    progress, runs, message = go(scratch_url, body)
    assert "BADSER" in message and "GOODSER" not in message
    assert runs == [
        {"feed": "BADSER", "status": "failed"},
        {"feed": "GOODSER", "status": "succeeded"},
    ]
    assert (progress[0]["total_units"], progress[0]["completed_units"]) == (2, 1)
    assert progress[0]["status"] == "failed" and progress[0]["current_unit"] is None


def test_a_manual_backfill_is_a_task_execution_with_a_task_runs_row(scratch_url: str) -> None:
    """The entry point goes through run_recorded: a real ledger row, so the sweep's rule applies to
    it unchanged (no exception, no special case)."""
    routed = Routed({"AAA": FakeFred(simple(3))})

    async def body(engine: AsyncEngine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        kw = dict(
            api_key=KEY,
            user_agent="FlowDesk-test",
            limiter=Limiter(),
            base_url="https://example.invalid/fred",
            transport=httpx2.MockTransport(routed),
            retry=NO_RETRY,
        )
        await execute(engine, [series("AAA")], **kw)  # type: ignore[arg-type]
        ok = await _all(
            engine, "SELECT task_name, status, heartbeat_at, completed_at FROM task_runs"
        )
        with pytest.raises(BackfillIncomplete):
            await execute(engine, [series("NOPE")], **kw)  # type: ignore[arg-type]
        both = await _all(
            engine, "SELECT task_name, status, error_class FROM task_runs ORDER BY id"
        )
        return ok, both

    ok, both = go(scratch_url, body)
    assert ok[0]["task_name"] == "ingest.fred_backfill" and ok[0]["status"] == "succeeded"
    assert ok[0]["heartbeat_at"] is not None and ok[0]["completed_at"] is not None
    assert [(r["status"], r["error_class"]) for r in both] == [
        ("succeeded", None),
        ("failed", "BackfillIncomplete"),
    ]

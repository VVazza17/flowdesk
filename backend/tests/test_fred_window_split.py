"""M2 slice 5, step 3, commit A: the windowed `output_type=3` fetch and the 504.

Measured 2026-11-13: FRED spends about 0.16 s per vintage date in a window and its gateway answers
504 at 60 s. A window that times out can never succeed on retry, so it is halved, from the same
start, and the smaller size is kept for the rest of the series.
"""

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingest.errors import GatewayTimeout, NoVintageDates, is_retryable, kind_of
from app.ingest.fred import HISTORY_TIMEOUT, FredClient, RevisionHistory

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fred"
KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
START = date(2020, 1, 1)


class FakeFred:
    """Vintage dates START + 0..n-1. An observations request whose window holds more than
    `limit` of them gets FRED's gateway timeout; any other gets one wide row per vintage."""

    def __init__(self, n: int, limit: int) -> None:
        self.dates = [START + timedelta(days=i) for i in range(n)]
        self.limit = limit
        self.windows: list[tuple[date, date, bool]] = []
        self.timeouts: list[Any] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        q = request.url.params
        if request.url.path.endswith("vintagedates"):
            return httpx2.Response(
                200,
                json={
                    "count": len(self.dates),
                    "vintage_dates": [d.isoformat() for d in self.dates],
                },
            )
        start, end = date.fromisoformat(q["realtime_start"]), date.fromisoformat(q["realtime_end"])
        held = [d for d in self.dates if start <= d <= end]
        self.timeouts.append(request.extensions.get("timeout"))
        too_big = len(held) > self.limit
        self.windows.append((start, end, too_big))
        if too_big:
            return httpx2.Response(504, text="<html>504 Gateway Time-out</html>")
        rows = [{"date": d.isoformat(), f"S_{d:%Y%m%d}": "1.5"} for d in held]
        return httpx2.Response(200, json={"count": len(rows), "observations": rows})


def run(fake: FakeFred, **kw: Any) -> RevisionHistory:
    async def go() -> RevisionHistory:
        async with FredClient(
            KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(fake)
        ) as fred:
            return await fred.revision_history("S", **kw)

    return asyncio.run(go())


def covered(fake: FakeFred) -> list[date]:
    good = [(s, e) for s, e, bad in fake.windows if not bad]
    return sorted(d for d in fake.dates if any(s <= d <= e for s, e in good))


def test_windows_are_disjoint_and_together_cover_every_vintage_date() -> None:
    fake = FakeFred(250, limit=1000)
    result = run(fake, window=100)
    assert result.splits == 0 and result.final_window == 100
    assert [w[:2] for w in fake.windows] == [
        (START, START + timedelta(days=99)),
        (START + timedelta(days=100), START + timedelta(days=199)),
        (START + timedelta(days=200), START + timedelta(days=249)),
    ]
    assert covered(fake) == fake.dates
    assert len(result.rows) == 250  # one row per vintage here, none lost or repeated


def test_a_window_that_times_out_is_halved_and_the_smaller_size_is_kept() -> None:
    fake = FakeFred(400, limit=30)  # FRED can only finish 30 dates
    result = run(fake, window=100)
    # 100 -> 50 -> 25: two splits, then 25-date windows for the rest of the series.
    assert result.splits == 2 and result.final_window == 25
    assert covered(fake) == fake.dates  # nothing skipped
    assert len(result.rows) == 400  # nothing fetched twice
    bad = [w for w in fake.windows if w[2]]
    assert len(bad) == 2
    sizes_after = [(e - s).days + 1 for s, e, ok in fake.windows if not ok]
    assert set(sizes_after[:1]) == {25}  # the retry restarted from the same first date
    assert fake.windows[0][0] == fake.windows[1][0] == fake.windows[2][0] == START
    # A timeout costs a request, and every request is a rate-budget slot (before_next_request).
    assert len(fake.windows) == 2 + 16


def test_a_single_vintage_date_that_still_times_out_is_raised() -> None:
    fake = FakeFred(10, limit=0)
    with pytest.raises(GatewayTimeout):
        run(fake, window=4)
    assert [(e - s).days + 1 for s, e, _ in fake.windows] == [4, 2, 1]


def test_the_history_requests_use_the_long_read_timeout_and_the_default_ones_do_not() -> None:
    fake = FakeFred(5, limit=1000)
    run(fake)
    assert HISTORY_TIMEOUT.read == 70.0
    assert fake.timeouts and all(t is not None and t["read"] == 70.0 for t in fake.timeouts)


def test_the_recorded_504_is_a_transient_gateway_timeout() -> None:
    rec = json.loads(
        (FIXTURES / "error_gateway_timeout_o3_window.json").read_text(encoding="utf-8")
    )
    assert rec["status"] == 504 and "Gateway Time-out" in rec["body"]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(504, content=rec["body"].encode("utf-8"))

    async def go() -> None:
        async with FredClient(
            KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(handler)
        ) as fred:
            await fred.vintage_dates("S")

    with pytest.raises(GatewayTimeout) as info:
        asyncio.run(go())
    # Still the same kind and still retryable: nothing that handles Transient changes.
    assert kind_of(info.value) == "transient" and is_retryable(info.value)
    assert "api" not in str(info.value).lower().replace("gateway", "")


def test_no_vintage_dates_inside_a_window_built_from_vintage_dates_is_fatal() -> None:
    # A backfill's windows come from the series' own vintage dates, so FRED saying none exist is a
    # moved record or a bug, never a quiet day (ADR-0003 addendum).
    fake = FakeFred(5, limit=1000)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("vintagedates"):
            return fake(request)
        return httpx2.Response(
            400,
            json={
                "error_code": 400,
                "error_message": (
                    "Bad Request.  No vintage dates exist for the specified real-time period."
                ),
            },
        )

    async def go() -> None:
        async with FredClient(
            KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(handler)
        ) as fred:
            await fred.revision_history("S")

    with pytest.raises(NoVintageDates):
        asyncio.run(go())


@pytest.mark.parametrize("window", [0, 2001])
def test_a_window_outside_what_fred_accepts_is_refused_before_any_request(window: int) -> None:
    fake = FakeFred(5, limit=1000)
    with pytest.raises(ValueError, match="window"):
        run(fake, window=window)
    assert fake.windows == []


def test_revisions_since_asks_only_for_the_vintages_from_that_date_but_knows_them_all() -> None:
    fake = FakeFred(250, limit=1000)
    since = START + timedelta(days=200)
    result = asyncio.run(_since(fake, since))
    assert result.window_dates == tuple(d for d in fake.dates if d >= since)  # 50 of 250
    assert result.vintage_dates == tuple(fake.dates)  # rule 5 needs every known date
    assert all(s >= since for s, _e, _bad in fake.windows)  # nothing earlier was requested
    assert fake.windows[0][0] == since  # inclusive: the last captured vintage is re-read
    assert len(result.rows) == 50


def test_revisions_since_a_date_with_no_later_vintage_asks_for_nothing() -> None:
    fake = FakeFred(10, limit=1000)
    result = asyncio.run(_since(fake, START + timedelta(days=500)))
    assert result.window_dates == () and result.rows == () and fake.windows == []


def test_revisions_since_splits_a_window_that_times_out_like_the_full_history() -> None:
    fake = FakeFred(250, limit=10)
    result = asyncio.run(_since(fake, START + timedelta(days=100)))
    assert result.splits > 0 and result.final_window <= 10
    assert len(result.rows) == 150  # nothing skipped, nothing repeated


async def _since(fake: FakeFred, since: date) -> RevisionHistory:
    async with FredClient(
        KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(fake)
    ) as fred:
        return await fred.revisions_since("S", since)

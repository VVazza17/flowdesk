"""M2 slice 5, step 3: the FRED client's vintage dates and chunked first prints. No database.

The fake FRED here enforces the cap the way the real one does (an explicit 400 past 2000 vintage
dates in the window), so a client that sent one big window would fail these tests.
"""

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingest.errors import Permanent, SchemaMismatch
from app.ingest.fred import FredClient

KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fred"
CAP_BODY = json.loads(
    json.loads((FIXTURES / "error_vintage_cap_exceeded.json").read_text(encoding="utf-8"))["body"]
)


class FakeFred:
    """One series: a first print per day, each its own vintage. Counts what it was asked."""

    def __init__(self, days: int, *, start: date = date(2010, 1, 1)) -> None:
        self.days = [start + timedelta(days=i) for i in range(days)]
        self.windows: list[tuple[str, str]] = []
        self.paths: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        q = request.url.params
        if request.url.path.endswith("/series/vintagedates"):
            offset, limit = int(q["offset"]), int(q["limit"])
            page = [x.isoformat() for x in self.days][offset : offset + limit]
            return httpx2.Response(200, json={"count": len(self.days), "vintage_dates": page})
        if q.get("output_type") == "1":
            latest = [
                {
                    "realtime_start": "2026-11-12",
                    "realtime_end": "2026-11-12",
                    "date": x.isoformat(),
                    "value": "1.5",
                }
                for x in self.days
            ]
            return httpx2.Response(200, json={"count": len(latest), "observations": latest})
        start, end = date.fromisoformat(q["realtime_start"]), date.fromisoformat(q["realtime_end"])
        self.windows.append((q["realtime_start"], q["realtime_end"]))
        inside = [x for x in self.days if start <= x <= end]
        if len(inside) > 2000:
            return httpx2.Response(400, json=CAP_BODY)
        rows = [
            {
                "realtime_start": x.isoformat(),
                "realtime_end": "9999-12-31",
                "date": x.isoformat(),
                "value": "1.5",
            }
            for x in inside
        ]
        return httpx2.Response(200, json={"count": len(rows), "observations": rows})


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def client(handler: FakeFred, **kw: Any) -> FredClient:
    return FredClient(
        KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(handler), **kw
    )


def test_a_series_over_the_cap_is_fetched_in_windows_that_each_fit() -> None:
    fred = FakeFred(5148)  # DGS10's measured vintage count

    async def go() -> list[Any]:
        async with client(fred) as c:
            return await c.first_prints_chunked("DGS10")

    rows = run(go())
    assert len(rows) == 5148
    assert len({r.effective_at for r in rows}) == 5148  # none twice, none lost
    assert len(fred.windows) == 3
    # The windows tile the vintage record: each starts the day after the one before ended.
    for (_, end), (start, _) in zip(fred.windows, fred.windows[1:], strict=False):
        assert date.fromisoformat(start) == date.fromisoformat(end) + timedelta(days=1)


def test_one_big_window_is_refused_by_the_same_fake_as_by_the_real_api() -> None:
    fred = FakeFred(2100)

    async def go() -> None:
        async with client(fred) as c:
            await c.first_prints("DGS10")

    with pytest.raises(Permanent) as info:
        run(go())
    assert "split the window" in str(info.value)


def test_a_series_that_fits_is_still_chunked_by_the_same_path() -> None:
    fred = FakeFred(62)

    async def go() -> list[Any]:
        async with client(fred) as c:
            return await c.first_prints_chunked("DRCCLACBS")

    assert len(run(go())) == 62
    assert len(fred.windows) == 1


def test_every_request_after_the_first_asks_the_limiter() -> None:
    fred = FakeFred(3800)  # vintagedates, then two windows
    paced = 0

    async def pace() -> None:
        nonlocal paced
        paced += 1

    async def go() -> None:
        async with client(fred, before_next_request=pace) as c:
            await c.first_prints_chunked("DGS10")
            await c.latest("DGS10")

    run(go())
    # 1 vintagedates + 2 windows + 1 latest = 4 requests; the first is covered by the run's slot.
    assert len(fred.paths) == 4
    assert paced == 3


def test_vintage_dates_parse_the_real_response() -> None:
    rec = json.loads((FIXTURES / "ok_vintagedates_DRCCLACBS.json").read_text(encoding="utf-8"))
    body = json.loads(rec["body"])

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=rec["body"].encode(), headers={"content-type": rec["content_type"]}
        )

    async def go() -> list[date]:
        async with client(handler) as c:  # type: ignore[arg-type]
            return await c.vintage_dates("DRCCLACBS")

    dates = run(go())
    assert len(dates) == body["count"] == 62
    assert dates == sorted(dates)
    assert dates[0] == date(2011, 5, 24)


@pytest.mark.parametrize(
    "payload",
    [{"vintage_dates": ["2020-01-01"]}, {"count": 1}, {"count": 1, "vintage_dates": ["nope"]}, []],
)
def test_a_malformed_vintage_dates_response_is_a_schema_mismatch(payload: Any) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    async def go() -> None:
        async with client(handler) as c:  # type: ignore[arg-type]
            await c.vintage_dates("DGS10")

    with pytest.raises(SchemaMismatch):
        run(go())


def test_vintage_dates_refuses_a_bad_series_id_before_any_request() -> None:
    fred = FakeFred(1)

    async def go() -> None:
        async with client(fred) as c:
            await c.vintage_dates("../x")

    with pytest.raises(ValueError):
        run(go())
    assert fred.paths == []

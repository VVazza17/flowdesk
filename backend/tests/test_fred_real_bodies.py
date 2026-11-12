"""M2 slice 5, step 2b: the FRED client against responses FRED actually sent.

Every other FRED test builds its fixtures from documentation, and M0's most expensive lesson is that
provider docs lie. The files in tests/fixtures/fred/ (repo root) are LIVE responses, unmodified,
captured on the date in each file. They are the only part of the taxonomy so far grounded in
something FRED sent.

What they showed (2026-11-12): a missing, malformed or unregistered api_key is HTTP **400**, not
401 or 403, and every one of the three bodies names `api_key`. The client's 401/403 branch is for
statuses nobody has observed; the real key-failure path is the 400 branch below.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingest.errors import Permanent, is_retryable
from app.ingest.fred import FredClient

# The repo-root tests/fixtures/ (CLAUDE.md), two levels above backend/tests/.
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fred"
KEY_ERRORS = ["error_api_key_not_set", "error_api_key_malformed", "error_api_key_unregistered"]


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("name", KEY_ERRORS)
def test_the_captured_bodies_are_what_they_claim_to_be(name: str) -> None:
    rec = load(name)
    assert rec["status"] == 400
    assert rec["content_type"].startswith("application/json")
    body = json.loads(rec["body"])  # the body is itself JSON, as FRED sent it
    assert body["error_code"] == 400
    assert "api_key" in body["error_message"]
    # Captured with a throwaway key; nothing in a fixture may be a real credential.
    assert "0123456789abcdef0123456789abcdef" not in json.dumps(rec)


@pytest.mark.parametrize("name", KEY_ERRORS)
def test_each_real_key_error_is_permanent_and_names_the_key(name: str) -> None:
    rec = load(name)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            rec["status"],
            content=rec["body"].encode("utf-8"),
            headers={"content-type": rec["content_type"]},
        )

    async def go() -> None:
        async with FredClient(
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            user_agent="FlowDesk-test",
            transport=httpx2.MockTransport(handler),
        ) as fred:
            await fred.first_prints("GDPC1")

    with pytest.raises(Permanent) as info:
        asyncio.run(go())
    assert "api key" in str(info.value).lower()
    assert not is_retryable(info.value)
    # Nothing FRED said is copied into the exception, not even the help URL it appends.
    assert "fred.stlouisfed.org" not in str(info.value)


# --- the rest of the live bodies (2026-11-12, a real key) -----------------------------------------
def _client_for(rec: dict[str, Any]) -> FredClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            rec["status"],
            content=rec["body"].encode("utf-8"),
            headers={"content-type": rec["content_type"]},
        )

    return FredClient(
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        user_agent="FlowDesk-test",
        transport=httpx2.MockTransport(handler),
    )


def test_a_real_first_print_response_parses() -> None:
    rec = load("ok_first_prints_DRCCLACBS")

    async def go() -> list[Any]:
        async with _client_for(rec) as fred:
            return await fred.first_prints("DRCCLACBS")

    rows = asyncio.run(go())
    body = json.loads(rec["body"])
    assert len(rows) == body["count"] == 61
    assert rows[0].effective_at.isoformat() == "2011-04-01"
    assert rows[0].realtime_start.isoformat() == "2011-08-22"
    # One first print per observation date, in date order: output_type=4 is not one row per vintage.
    assert len({r.effective_at for r in rows}) == len(rows)
    # Every real value here is a plain decimal, and a first print is published after its period.
    assert all(r.value is not None and r.realtime_start > r.effective_at for r in rows)


def test_a_real_latest_response_parses() -> None:
    rec = load("ok_latest_DGS10")

    async def go() -> list[Any]:
        async with _client_for(rec) as fred:
            return await fred.latest("DGS10")

    rows = asyncio.run(go())
    assert len(rows) == json.loads(rec["body"])["count"]
    # The default window is "today": every row's real-time interval is the one day.
    assert {(r.realtime_start, r.realtime_end) for r in rows} == {(rows[0].realtime_start,) * 2}


def test_the_vintage_cap_is_an_explicit_permanent_error_that_says_to_split() -> None:
    # Measured: over 2000 vintage dates in the window FRED answers 400, it does not truncate.
    rec = load("error_vintage_cap_exceeded")
    assert "maximum number of vintage dates" in json.loads(rec["body"])["error_message"]

    async def go() -> None:
        async with _client_for(rec) as fred:
            await fred.first_prints("DGS10")

    with pytest.raises(Permanent) as info:
        asyncio.run(go())
    assert "split the window" in str(info.value)
    assert not is_retryable(info.value)


@pytest.mark.parametrize(
    ("name", "needle"),
    [
        ("error_no_vintage_dates", "no vintage dates exist"),
        ("error_series_not_found", "unknown series"),
        ("error_limit_too_large", "request was rejected"),
    ],
)
def test_the_other_real_400s_are_permanent(name: str, needle: str) -> None:
    rec = load(name)

    async def go() -> None:
        async with _client_for(rec) as fred:
            await fred.latest("GDPC1")

    with pytest.raises(Permanent) as info:
        asyncio.run(go())
    assert needle in str(info.value).lower()
    assert not is_retryable(info.value)

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

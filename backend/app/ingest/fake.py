"""The fake provider (M2 slice 4): scripted behaviour, a fixed payload, no network.

It exists for two reasons. It lets the framework (budget, retries, both ledgers, health states) be
tested without a real provider. And it gives the gate's "killing a provider" clause something to
kill (docs/HEALTH.md, "Killing a provider").

It deliberately defines no record types. FRED-shaped and bar-shaped records belong to slices 5 and
6, which know what those shapes need. The payload here is opaque rows with a stable digest, enough
to show that re-ingesting an identical payload writes nothing new.
"""

import hashlib
import json
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.ingest.errors import (
    EmptyResult,
    Permanent,
    RateLimited,
    SchemaMismatch,
    Transient,
)
from app.ingest.runner import Body, FeedContext, FeedResult

PAYLOAD_ROWS = 5


@dataclass(frozen=True)
class FakeBatch:
    rows: tuple[tuple[str, str], ...]  # (key, value)

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.rows).encode()).hexdigest()


def _payload() -> FakeBatch:
    # Fixed on purpose: identical on every call, so a second ingest is an identical payload.
    return FakeBatch(rows=tuple((f"k{i}", f"{i}.0") for i in range(PAYLOAD_ROWS)))


class FakeProvider:
    """`script` is consumed one entry per call and the last entry repeats, so ["transient",
    "transient", "ok"] fails twice and then works, and ["down"] fails forever."""

    def __init__(self, script: Sequence[str] = ("ok",)) -> None:
        if not script:
            raise ValueError("script must not be empty")
        self._script = list(script)
        self.calls = 0

    async def fetch(self, feed: str) -> FakeBatch:
        mode = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if mode == "ok":
            return _payload()
        if mode == "transient":
            raise Transient("simulated timeout")
        if mode == "down":
            raise Transient("simulated connection refused")
        if mode == "rate_limited":
            raise RateLimited("simulated 429")
        if mode == "permanent":
            raise Permanent("simulated 400: bad series")
        if mode == "empty":
            raise EmptyResult("simulated empty frame")
        if mode == "schema":
            raise SchemaMismatch("simulated unexpected columns")
        raise ValueError(f"unknown fake mode {mode!r}")


class FakeStore(Protocol):
    async def apply(self, rows: Sequence[tuple[str, str]]) -> int:
        """Make `rows` the stored state. Return how many were created or changed."""
        ...


class InMemoryFakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def apply(self, rows: Sequence[tuple[str, str]]) -> int:
        changed = 0
        for key, value in rows:
            if self.data.get(key) != value:
                self.data[key] = value
                changed += 1
        return changed


class RedisFakeStore:
    """The fake's 'canonical state' for a running stack, so that a second run really writes
    nothing. Redis is fine for a test double's state; no real data would ever live here."""

    KEY = "fake:canonical"

    def __init__(self, redis: "_HashRedis") -> None:
        self._redis = redis

    async def apply(self, rows: Sequence[tuple[str, str]]) -> int:
        stored = await self._redis.hgetall(self.KEY)
        changed = 0
        for key, value in rows:
            if stored.get(key) != value:
                await self._redis.hset(self.KEY, key, value)
                changed += 1
        return changed


class _HashRedis(Protocol):
    def hgetall(self, name: str) -> Awaitable[dict[str, str]]: ...
    def hset(self, name: str, key: str, value: str) -> Awaitable[object]: ...


def fake_body(provider: FakeProvider, store: FakeStore) -> Body:
    async def body(ctx: FeedContext) -> FeedResult:
        batch = await provider.fetch(ctx.feed)
        # rows_written is what changed, not what was attempted (app.ingest.runner docstring).
        written = await store.apply(batch.rows)
        return FeedResult(rows_read=len(batch.rows), rows_written=written)

    return body

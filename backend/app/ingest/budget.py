"""Rate budgets (M2 slice 4; §6.11 "per-provider Redis rate limiter", §6.12 rate budget).

One kind of limit only: a minimum spacing between requests, plus a cap on batch size that clients
obey. That is what the spec states for yfinance (batches of about 100, at least one second apart).
A windowed quota (N per minute) is left out on purpose: FRED's limit is in neither the spec nor an
ADR, and slice 5 adds the kind it actually needs once it has verified the number.

Every attempt takes a slot, retries included: a retry is a request. If a slot cannot be granted
(Redis unreachable, no budget declared for the provider, or the wait ran out) the call is refused
with BudgetUnavailable. The framework never calls a provider unthrottled to make progress.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.ingest.errors import BudgetUnavailable


@dataclass(frozen=True)
class RateBudget:
    min_interval_seconds: float
    max_batch: int


class Limiter(Protocol):
    async def acquire(self, provider: str) -> None:
        """Return when `provider` may be called; raise BudgetUnavailable if it cannot be."""
        ...


class SetPttlRedis(Protocol):
    """The two Redis calls the limiter uses (redis.asyncio.Redis satisfies this)."""

    def set(self, name: str, value: str, *, nx: bool, px: int) -> Awaitable[object]: ...
    def pttl(self, name: str) -> Awaitable[int]: ...


class RedisSpacingLimiter:
    """A slot is a key that expires after the minimum interval. Taking it is SET NX PX, which is
    atomic across worker processes. Losers sleep for what the key has left and try again.

    # DECISION: no fairness. Two waiting callers race for each slot. With one worker doing a few
    # feeds that cannot matter, and a queue would be machinery for an imagined need.
    """

    def __init__(
        self,
        redis: SetPttlRedis,
        budgets: Mapping[str, RateBudget],
        *,
        max_wait_seconds: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._redis = redis
        self._budgets = budgets
        self._max_wait = max_wait_seconds
        self._sleep = sleep

    async def acquire(self, provider: str) -> None:
        budget = self._budgets.get(provider)
        if budget is None:
            raise BudgetUnavailable(f"no rate budget declared for provider {provider!r}")
        px = max(1, round(budget.min_interval_seconds * 1000))
        waited = 0.0
        try:
            while True:
                if await self._redis.set(f"ratelimit:{provider}", "1", nx=True, px=px):
                    return
                remaining_ms = await self._redis.pttl(f"ratelimit:{provider}")
                # -2 means the key expired between the two calls; -1 would be a key with no expiry,
                # which this class never writes. Either way, a short sleep and another try.
                pause = max(remaining_ms, 10) / 1000
                if waited + pause > self._max_wait:
                    raise BudgetUnavailable(f"rate budget for {provider!r} not granted in time")
                await self._sleep(pause)
                waited += pause
        except BudgetUnavailable:
            raise
        except Exception as exc:  # Redis unreachable or erroring: refuse, do not call unthrottled
            raise BudgetUnavailable(f"rate limiter unavailable: {type(exc).__name__}") from exc


def batches[T](items: Sequence[T], budget: RateBudget) -> Iterator[Sequence[T]]:
    """Split `items` into chunks of at most `budget.max_batch`. Clients obey the cap with this."""
    for start in range(0, len(items), budget.max_batch):
        yield items[start : start + budget.max_batch]

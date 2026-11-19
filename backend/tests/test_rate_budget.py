"""The Redis spacing limiter (M2 slice 4), against an in-memory Redis stand-in and a fake clock."""

import asyncio

import pytest

from app.ingest.budget import RateBudget, RedisSpacingLimiter, batches, wait_bound
from app.ingest.errors import BudgetUnavailable


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRedis:
    """SET NX PX and PTTL with expiry measured on the fake clock."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.expiry: dict[str, float] = {}

    async def set(self, name: str, value: str, *, nx: bool, px: int) -> object:
        if self.expiry.get(name, -1.0) > self.clock.now:
            return None
        self.expiry[name] = self.clock.now + px / 1000
        return True

    async def pttl(self, name: str) -> int:
        left = self.expiry.get(name, 0.0) - self.clock.now
        return round(left * 1000) if left > 0 else -2


class BrokenRedis:
    async def set(self, name: str, value: str, *, nx: bool, px: int) -> object:
        raise ConnectionError("redis is down")

    async def pttl(self, name: str) -> int:
        raise ConnectionError("redis is down")


BUDGETS = {"p": RateBudget(min_interval_seconds=1.0, max_batch=100)}


def _limiter(clock: Clock, redis: object | None = None, **kw: float) -> RedisSpacingLimiter:
    return RedisSpacingLimiter(
        redis or FakeRedis(clock),  # type: ignore[arg-type]
        BUDGETS,
        sleep=clock.sleep,
        **kw,
    )


def test_the_first_call_is_immediate_and_the_next_waits_the_interval() -> None:
    clock = Clock()
    limiter = _limiter(clock)

    async def go() -> list[float]:
        stamps = []
        for _ in range(3):
            await limiter.acquire("p")
            stamps.append(clock.now)
        return stamps

    stamps = asyncio.run(go())
    assert stamps[0] == 0.0
    assert stamps[1] - stamps[0] >= 1.0
    assert stamps[2] - stamps[1] >= 1.0


def test_a_provider_with_no_budget_is_refused_not_run_unthrottled() -> None:
    with pytest.raises(BudgetUnavailable, match="no rate budget"):
        asyncio.run(_limiter(Clock()).acquire("unknown"))


def test_redis_down_refuses_the_call() -> None:
    with pytest.raises(BudgetUnavailable, match="ConnectionError"):
        asyncio.run(_limiter(Clock(), BrokenRedis()).acquire("p"))


def test_a_wait_longer_than_the_cap_is_refused() -> None:
    clock = Clock()
    limiter = _limiter(clock, max_wait_seconds=0.5)

    async def go() -> None:
        await limiter.acquire("p")
        await limiter.acquire("p")  # needs a 1 s wait; the cap is 0.5 s

    with pytest.raises(BudgetUnavailable, match="not granted in time"):
        asyncio.run(go())


def test_batches_obey_the_cap() -> None:
    sizes = [len(b) for b in batches(list(range(250)), BUDGETS["p"])]
    assert sizes == [100, 100, 50]
    assert list(batches([], BUDGETS["p"])) == []


class SlowTtlRedis(FakeRedis):
    """A key lives 8% longer than asked, as this machine's Redis TTL against the caller's sleep
    (IDEA-048, MEASURED about 8%)."""

    async def set(self, name: str, value: str, *, nx: bool, px: int) -> object:
        return await super().set(name, value, nx=nx, px=round(px * 1.08))


def test_a_second_slot_in_a_row_is_granted_within_the_wait_bound_despite_clock_drift() -> None:
    # MEASURED 2026-11-19: the 15m backfill's second request was refused with a 60 s bound at
    # yfinance's 60 s spacing. Reproduced here; then granted with wait_bound.
    yf = {"yfinance": RateBudget(min_interval_seconds=60.0, max_batch=24)}
    for bound, granted in ((60.0, False), (wait_bound(yf), True)):
        clock = Clock()
        limiter = RedisSpacingLimiter(
            SlowTtlRedis(clock),
            yf,
            sleep=clock.sleep,
            max_wait_seconds=bound,
        )
        asyncio.run(limiter.acquire("yfinance"))
        if granted:
            asyncio.run(limiter.acquire("yfinance"))
            assert clock.now == pytest.approx(64.8)  # waited out the whole (drifted) slot
        else:
            with pytest.raises(BudgetUnavailable):
                asyncio.run(limiter.acquire("yfinance"))


def test_the_wait_bound_is_at_least_two_spacings_of_every_declared_budget() -> None:
    from app import settings
    from app.ingest.bars_feed import yfinance_limiter

    bound = wait_bound(settings.RATE_BUDGETS)
    assert all(bound >= 2 * b.min_interval_seconds for b in settings.RATE_BUDGETS.values())
    assert wait_bound({"fred": RateBudget(min_interval_seconds=1.0, max_batch=1)}) == 60.0
    assert yfinance_limiter(FakeRedis(Clock()))._max_wait == bound

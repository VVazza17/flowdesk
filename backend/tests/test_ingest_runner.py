"""M2 slice 4: run_feed against a real, migrated database.

Needs DATABASE_URL (see test_migrations.py).

Asserts on what is STORED, not on what the code path appears to do: the rows are read back with SQL.
"""

import asyncio
import json
import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.errors import BudgetUnavailable, Transient
from app.ingest.fake import FakeProvider, InMemoryFakeStore, fake_body
from app.ingest.runner import FeedContext, FeedResult, RetryPolicy, register_feeds, run_feed
from app.services.health import assemble_providers
from app.services.health_probes import SqlRedisProbes
from tests.recorded import recorded

ADMIN_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="DATABASE_URL not set")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
KEY = "0123456789abcdef0123456789abcdef"
POLICY = RetryPolicy(attempts=4, initial_seconds=1.0, max_seconds=60.0)


async def _admin(sql: str) -> None:
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture
def scratch_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"flowdesk_ingest_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


class CountingLimiter:
    def __init__(self, fail: bool = False) -> None:
        self.acquired = 0
        self.fail = fail

    async def acquire(self, provider: str) -> None:
        if self.fail:
            raise BudgetUnavailable("rate limiter unavailable: ConnectionError")
        self.acquired += 1


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def _all(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


async def _run(
    engine: AsyncEngine,
    script: Sequence[str],
    *,
    store: InMemoryFakeStore | None = None,
    limiter: CountingLimiter | None = None,
    sleeps: Sleeps | None = None,
    provider: FakeProvider | None = None,
    secrets: Sequence[str] = (),
) -> FeedResult:
    fake = provider or FakeProvider(script)
    async with recorded(engine):
        return await run_feed(
            engine,
            "fake",
            "ticks",
            fake_body(fake, store or InMemoryFakeStore()),
            limiter=limiter or CountingLimiter(),
            retry=POLICY,
            sleep=sleeps or Sleeps(),
            secrets=secrets,
        )


def test_a_successful_run_writes_both_ledgers(scratch_url: str) -> None:
    async def go() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        try:
            result = await _run(engine, ["ok"])
            assert result == FeedResult(rows_read=5, rows_written=5)
            return (
                await _all(engine, "SELECT * FROM ingestion_runs"),
                await _all(engine, "SELECT * FROM providers_health"),
            )
        finally:
            await engine.dispose()

    runs, health = asyncio.run(go())
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    assert (runs[0]["rows_read"], runs[0]["rows_written"]) == (5, 5)
    assert runs[0]["finished_at"] is not None and runs[0]["error"] is None
    assert len(health) == 1
    assert health[0]["last_success_at"] is not None
    assert health[0]["last_error_at"] is None and health[0]["first_seen_at"] is not None


def test_reingesting_an_identical_payload_reads_everything_and_writes_nothing(
    scratch_url: str,
) -> None:
    # The verification signal for the gate's idempotency clause (app.ingest.runner docstring):
    # rows_written counts created-or-changed rows, so an identical second run reports 0.
    async def go() -> list[dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        store = InMemoryFakeStore()
        try:
            await _run(engine, ["ok"], store=store)
            second = await _run(engine, ["ok"], store=store)
            assert second == FeedResult(rows_read=5, rows_written=0)
            return await _all(
                engine, "SELECT rows_read, rows_written FROM ingestion_runs ORDER BY id"
            )
        finally:
            await engine.dispose()

    assert [(r["rows_read"], r["rows_written"]) for r in asyncio.run(go())] == [(5, 5), (5, 0)]


def test_transient_failures_are_retried_inside_one_run_and_one_row(scratch_url: str) -> None:
    async def go() -> tuple[CountingLimiter, Sleeps, list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        limiter, sleeps = CountingLimiter(), Sleeps()
        try:
            await _run(engine, ["transient", "transient", "ok"], limiter=limiter, sleeps=sleeps)
            return limiter, sleeps, await _all(engine, "SELECT status FROM ingestion_runs")
        finally:
            await engine.dispose()

    limiter, sleeps, runs = asyncio.run(go())
    assert limiter.acquired == 3  # every attempt takes a slot, retries included
    assert len(sleeps.waits) == 2  # waited between attempts
    assert [r["status"] for r in runs] == ["succeeded"]  # one logical run, one row


@pytest.mark.parametrize("mode", ["down", "transient", "rate_limited"])
def test_a_retryable_failure_exhausts_the_attempts_then_fails_the_run(
    scratch_url: str, mode: str
) -> None:
    async def go() -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        limiter = CountingLimiter()
        try:
            with pytest.raises(Exception):  # noqa: B017, PT011
                await _run(engine, [mode], limiter=limiter)
            return (
                limiter.acquired,
                await _all(engine, "SELECT * FROM ingestion_runs"),
                await _all(engine, "SELECT * FROM providers_health"),
            )
        finally:
            await engine.dispose()

    acquired, runs, health = asyncio.run(go())
    assert acquired == POLICY.attempts
    assert len(runs) == 1 and runs[0]["status"] == "failed"
    assert runs[0]["finished_at"] is not None and runs[0]["rows_written"] is None
    assert health[0]["last_error_at"] is not None
    assert health[0]["last_success_at"] is None


@pytest.mark.parametrize(
    ("mode", "kind"),
    [("permanent", "permanent"), ("empty", "empty_result"), ("schema", "schema_mismatch")],
)
def test_a_non_retryable_failure_is_tried_once(scratch_url: str, mode: str, kind: str) -> None:
    async def go() -> tuple[int, list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        limiter = CountingLimiter()
        try:
            with pytest.raises(Exception):  # noqa: B017, PT011
                await _run(engine, [mode], limiter=limiter)
            return limiter.acquired, await _all(engine, "SELECT last_error FROM providers_health")
        finally:
            await engine.dispose()

    acquired, health = asyncio.run(go())
    assert acquired == 1
    assert health[0]["last_error"].startswith(f"{kind}:")


def test_a_refused_budget_means_the_provider_is_never_called(scratch_url: str) -> None:
    async def go() -> tuple[int, list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        provider = FakeProvider(["ok"])
        try:
            with pytest.raises(BudgetUnavailable):
                await _run(engine, [], limiter=CountingLimiter(fail=True), provider=provider)
            return provider.calls, await _all(engine, "SELECT status, error FROM ingestion_runs")
        finally:
            await engine.dispose()

    calls, runs = asyncio.run(go())
    assert calls == 0
    assert runs[0]["status"] == "failed" and runs[0]["error"].startswith("budget_unavailable:")


def test_a_later_failure_does_not_erase_the_last_success(scratch_url: str) -> None:
    async def go() -> dict[str, Any]:
        engine = create_async_engine(scratch_url)
        try:
            await _run(engine, ["ok"])
            with pytest.raises(Exception):  # noqa: B017, PT011
                await _run(engine, ["permanent"])
            return (await _all(engine, "SELECT * FROM providers_health"))[0]
        finally:
            await engine.dispose()

    row = asyncio.run(go())
    assert row["last_success_at"] is not None and row["last_error_at"] is not None
    assert row["last_error_at"] >= row["last_success_at"]  # last_attempt_failed reads off these


def test_the_body_is_handed_the_ingestion_run_id(scratch_url: str) -> None:
    seen: list[int] = []

    async def body(ctx: FeedContext) -> FeedResult:
        seen.append(ctx.run_id)
        return FeedResult(rows_read=0, rows_written=0)

    async def go() -> list[dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        try:
            async with recorded(engine):
                await run_feed(
                    engine, "fake", "ticks", body, limiter=CountingLimiter(), sleep=Sleeps()
                )
            return await _all(engine, "SELECT id FROM ingestion_runs")
        finally:
            await engine.dispose()

    stored = [r["id"] for r in asyncio.run(go())]
    assert seen == stored


def test_register_feeds_creates_a_row_with_first_seen_and_is_idempotent(scratch_url: str) -> None:
    t0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

    async def go() -> tuple[int, int, list[dict[str, Any]]]:
        engine = create_async_engine(scratch_url)
        try:
            first = await register_feeds(engine, [("fake", "ticks")], clock=lambda: t0)
            second = await register_feeds(
                engine, [("fake", "ticks")], clock=lambda: t0 + timedelta(days=1)
            )
            return first, second, await _all(engine, "SELECT * FROM providers_health")
        finally:
            await engine.dispose()

    first, second, rows = asyncio.run(go())
    assert (first, second) == (1, 0)
    assert rows[0]["first_seen_at"] == t0  # the second call did not move it
    assert rows[0]["last_success_at"] is None


def test_a_registered_feed_that_never_runs_goes_stale_through_the_probe(scratch_url: str) -> None:
    # The path /health/providers takes: registered at t0, never run, judged against its own
    # threshold with the real probe and the real classifier.
    t0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        try:
            await register_feeds(engine, [("fake", "ticks")], clock=lambda: t0)
            rows = await SqlRedisProbes(engine, None).provider_rows()
            thresholds = {("fake", "ticks"): timedelta(minutes=3)}
            return (
                assemble_providers(rows, t0 + timedelta(minutes=2), thresholds),
                assemble_providers(rows, t0 + timedelta(minutes=4), thresholds),
            )
        finally:
            await engine.dispose()

    early, late = asyncio.run(go())
    assert early["status"] == "pending" and early["feeds"][0]["status"] == "never_succeeded"
    assert late["status"] == "stale"
    assert late["feeds"][0]["never_succeeded"] is True


# --- Credentials: assert on the STORED value --------------------------------------------------
# FRED authenticates with api_key= in the query string and an HTTP client's exception text carries
# the URL. providers_health.last_error is served by /health/providers: the public profile removes
# the key (IDEA-019), the private profile does not. Neither is relied on here: nothing that looks
# like a credential may be in the stored columns, which are read back with SQL.


@pytest.mark.parametrize(
    ("message", "secrets"),
    [
        (
            "GET https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=GDPC1&api_key={KEY}&file_type=json -> ReadTimeout",
            (),
        ),
        (f"Authorization: Bearer {KEY}", ()),
        # No recognisable pattern at all: only the exact-value layer can catch this one.
        (f"upstream rejected credential {KEY}", (KEY,)),
    ],
)
def test_no_credential_reaches_the_stored_error_columns(
    scratch_url: str, message: str, secrets: tuple[str, ...]
) -> None:
    async def body(ctx: FeedContext) -> FeedResult:
        raise Transient(message)

    async def go() -> tuple[str, str, str]:
        engine = create_async_engine(scratch_url)
        try:
            with pytest.raises(Transient):
                async with recorded(engine):
                    await run_feed(
                        engine,
                        "fred",
                        "GDPC1",
                        body,
                        limiter=CountingLimiter(),
                        retry=POLICY,
                        sleep=Sleeps(),
                        secrets=secrets,
                    )
            health = await _all(engine, "SELECT row_to_json(h)::text AS j FROM providers_health h")
            runs = await _all(engine, "SELECT row_to_json(r)::text AS j FROM ingestion_runs r")
            served = assemble_providers(
                await SqlRedisProbes(engine, None).provider_rows(),
                datetime.now(UTC),
                {("fred", "GDPC1"): timedelta(days=1)},
            )
            return health[0]["j"], runs[0]["j"], json.dumps(served)
        finally:
            await engine.dispose()

    health_row, run_row, served = asyncio.run(go())
    assert KEY not in health_row  # the whole stored providers_health row
    assert KEY not in run_row  # the whole stored ingestion_runs row
    assert KEY not in served  # and what the private profile would serve of it
    assert json.loads(health_row)["last_error"] == "transient:Transient"


def test_one_feeds_outcome_never_touches_another_feeds_health_row(scratch_url: str) -> None:
    """Every fixture above has ONE feed, so a `providers_health` UPDATE that lost its
    `WHERE provider, feed` would have stamped every feed with one feed's failure and no test would
    have noticed (found by mutation, 2026-11-13). Two feeds, each outcome in turn."""

    async def ok(ctx: FeedContext) -> FeedResult:
        return FeedResult(rows_read=1, rows_written=1)

    async def broken(ctx: FeedContext) -> FeedResult:
        raise Transient("boom")

    async def go() -> dict[str, dict[str, Any]]:
        engine = create_async_engine(scratch_url)
        try:
            async with recorded(engine):
                await register_feeds(engine, [("fake", "ticks"), ("fred", "GDPC1")])
                # A succeeds: B must still read "never succeeded".
                await run_feed(
                    engine, "fake", "ticks", ok, limiter=CountingLimiter(), sleep=Sleeps()
                )
                after_a_ok = {
                    r["feed"]: r for r in await _all(engine, "SELECT * FROM providers_health")
                }
                # B fails: A must keep its success and show no error.
                with pytest.raises(Transient):
                    await run_feed(
                        engine,
                        "fred",
                        "GDPC1",
                        broken,
                        limiter=CountingLimiter(),
                        retry=RetryPolicy(attempts=1),
                        sleep=Sleeps(),
                    )
                after_b_failed = {
                    r["feed"]: r for r in await _all(engine, "SELECT * FROM providers_health")
                }
            return {"a_ok": after_a_ok, "b_failed": after_b_failed}
        finally:
            await engine.dispose()

    out = asyncio.run(go())
    b_after_a = out["a_ok"]["GDPC1"]
    assert b_after_a["last_success_at"] is None and b_after_a["last_error_at"] is None
    a_after_b = out["b_failed"]["ticks"]
    assert a_after_b["last_success_at"] == out["a_ok"]["ticks"]["last_success_at"] is not None
    assert a_after_b["last_error_at"] is None and a_after_b["last_error"] is None
    b = out["b_failed"]["GDPC1"]
    assert b["last_error_at"] is not None and b["last_success_at"] is None

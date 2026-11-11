"""The run wrapper every ingest goes through (M2 slice 4): budget, retries, and the two ledgers.

`run_feed` is `app.tasks.runs.run_recorded` for providers. A task hands it a provider, a feed and a
`body`; the body does the provider-specific work and reports counts. What this module owns:

  1. rate budget: every attempt takes a slot first (app.ingest.budget)
  2. retries: tenacity, inside the run. One run is 1 try + 3 retries (§11's max_retries=3), spaced
     exponentially with jitter. Only `retryable` errors are retried (app.ingest.errors)
  3. ingestion_runs: ONE row per run, however many attempts it took
  4. providers_health: upserted once, from the run's final outcome

# DECISION: tenacity only; Celery retries are off for ingest tasks. Beat is the outer retry: if a
# run fails, the next scheduled firing tries again. A Celery retry would also write a second
# ingestion_runs row for one logical run, and that row count is what idempotency is measured
# against (the M2 gate). §6.11 (tenacity) and §11 (Celery max_retries=3) both speak and neither
# says how the two compose: IDEA-042.

`rows_written` is a VERIFICATION SIGNAL, not a statistic. It counts the rows this run created or
changed in canonical state, never the rows it attempted to write. Re-ingesting an identical payload
therefore returns 0, and that 0 is the observable evidence for the gate's clause "re-ingesting
identical payloads yields identical canonical rows". Do not "fix" it to count attempted writes:
that would make a correct idempotent re-run look like it wrote everything again.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import insert, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.ingest.budget import Limiter
from app.ingest.errors import health_error, is_retryable, kind_of, run_error
from app.models.ops import IngestionRun, ProvidersHealth
from app.tasks.names import STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED

log = structlog.get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class FeedContext:
    """What a body is handed. `run_id` is the ingestion_run_id that §16 lists as a provenance
    column on observations: the body writes it onto the rows it creates."""

    run_id: int
    provider: str
    feed: str


@dataclass(frozen=True)
class FeedResult:
    rows_read: int
    rows_written: int  # created or changed, never attempted: see the module docstring


Body = Callable[[FeedContext], Awaitable[FeedResult]]


@dataclass(frozen=True)
class RetryPolicy:
    # DECISION: placeholder numbers. 4 attempts is §11's max_retries=3 plus the first try. The
    # waits are not from any spec and no real provider has been observed yet; slice 5 and 6 tune
    # them against FRED and yfinance. A failed run is also retried by the next Beat firing.
    attempts: int = 4
    initial_seconds: float = 1.0
    max_seconds: float = 60.0


async def register_feeds(
    engine: AsyncEngine,
    feeds: Iterable[tuple[str, str]],
    clock: Callable[[], datetime] = utcnow,
) -> int:
    """Create a providers_health row, with first_seen_at, for each declared feed that has none.

    Insert-only and idempotent. This is what makes a feed that is scheduled but never runs visible:
    without a row it would be absent from /health/providers instead of "never succeeded, and
    overdue" (IDEA-026). Returns how many rows it created.
    """
    created = 0
    now = clock()
    async with engine.begin() as conn:
        for provider, feed in feeds:
            result = await conn.execute(
                pg_insert(ProvidersHealth)
                .values(provider=provider, feed=feed, first_seen_at=now)
                .on_conflict_do_nothing()
            )
            created += result.rowcount or 0
    return created


def _log_attempt_failure(provider: str, feed: str) -> Callable[[RetryCallState], None]:
    def hook(state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        # Class and kind only: the text can carry a credential (app.ingest.errors.health_error).
        log.warning(
            "ingest_attempt_failed",
            provider=provider,
            feed=feed,
            attempt=state.attempt_number,
            error=health_error(exc) if exc else None,
        )

    return hook


async def run_feed(
    engine: AsyncEngine,
    provider: str,
    feed: str,
    body: Body,
    *,
    limiter: Limiter,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    secrets: Sequence[str] = (),
    clock: Callable[[], datetime] = utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FeedResult:
    """Run `body` for one (provider, feed), recording how it went. Re-raises the final failure.

    `secrets` are this provider's credentials, passed so the stored error text can have them
    removed by exact value (app.ingest.errors.scrub). providers_health never gets the text at all.
    """
    started = clock()
    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(ProvidersHealth)
            .values(provider=provider, feed=feed, first_seen_at=started)
            .on_conflict_do_nothing()
        )
        run_id = (
            await conn.execute(
                insert(IngestionRun)
                .values(provider=provider, feed=feed, started_at=started, status=STATUS_RUNNING)
                .returning(IngestionRun.id)
            )
        ).scalar_one()
    ctx = FeedContext(run_id=run_id, provider=provider, feed=feed)

    async def attempt() -> FeedResult:
        await limiter.acquire(provider)
        return await body(ctx)

    retrying = AsyncRetrying(
        stop=stop_after_attempt(retry.attempts),
        wait=wait_exponential_jitter(initial=retry.initial_seconds, max=retry.max_seconds),
        retry=retry_if_exception(is_retryable),
        sleep=sleep,
        before_sleep=_log_attempt_failure(provider, feed),
        reraise=True,
    )
    try:
        result: FeedResult = await retrying(attempt)
    except Exception as exc:
        finished = clock()
        async with engine.begin() as conn:
            await conn.execute(
                update(IngestionRun)
                .where(IngestionRun.id == run_id)
                .values(status=STATUS_FAILED, finished_at=finished, error=run_error(exc, secrets))
            )
            await conn.execute(
                update(ProvidersHealth)
                .where(ProvidersHealth.provider == provider, ProvidersHealth.feed == feed)
                .values(last_error_at=finished, last_error=health_error(exc))
            )
        log.warning("ingest_run_failed", provider=provider, feed=feed, kind=kind_of(exc))
        raise
    finished = clock()
    async with engine.begin() as conn:
        await conn.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                status=STATUS_SUCCEEDED,
                finished_at=finished,
                rows_read=result.rows_read,
                rows_written=result.rows_written,
            )
        )
        await conn.execute(
            update(ProvidersHealth)
            .where(ProvidersHealth.provider == provider, ProvidersHealth.feed == feed)
            .values(last_success_at=finished)
        )
    return result

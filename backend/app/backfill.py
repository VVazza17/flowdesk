"""Entry point: `python -m app.backfill`. Captures every FRED series' whole revision history.

    docker compose run --rm worker python -m app.backfill             # every series not captured
    docker compose run --rm worker python -m app.backfill --series DGS10
    docker compose run --rm worker python -m app.backfill --series DGS10 --force   # again

Why this is an entry point and not a Celery task (Kyle, 2026-11-13; docs/BUILD-LOG.md): the history
backfill is one-time plus an occasional deliberate re-capture, and at about 90 minutes it must not
live under the task limit that the ingest canary's threshold is built from. The poll is the only
thing that runs forever, and its windows are short. The precedent is `python -m app.seed` in the
`migrate` service.

It is NOT a scratch script. It calls the same `run_feed` per series, so `ingestion_runs`,
`providers_health`, the Redis rate budget, the retries, the credential scrubbing and the provenance
chain (`macro_metrics.ingestion_run_id`) behave exactly as under Celery. And it goes through
`run_recorded`: **a manual backfill is a task execution and belongs in `task_runs` like any other**,
with a real row, so the sweep's rule (a running row that is not fresh is failed) applies to it
unchanged, with no exception and no special case.

The skip is not resume. A series whose `series_history_state` row has the current
`FRED_RULE_VERSION` is not fetched; one that was interrupted starts again from its first request.
A change to the rules bumps the version, which captures every series again, on purpose.

Exit status: 0 if every selected series is captured or skipped, 1 if any failed (the others
still run).
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx2
import structlog
from redis.asyncio import Redis
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.budget import Limiter, RedisSpacingLimiter
from app.ingest.fred_feed import PROVIDER, history_body
from app.ingest.macro import FRED_RULE_VERSION, FredSeries
from app.ingest.runner import RetryPolicy, run_feed
from app.logging import configure_logging
from app.models.ops import BackfillRun
from app.services.macro_store import read_state
from app.tasks.names import FRED_BACKFILL, STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED
from app.tasks.runs import run_recorded

log = structlog.get_logger(__name__)

BACKFILL_FEED = "fred"
# The stop condition Kyle set for the first real run (2026-11-13): a series that takes longer
# than this is reported loudly so the run can be stopped and brought to him with the numbers. It
# does not stop the process by itself.
SLOW_SERIES_SECONDS = 20 * 60


class BackfillIncomplete(Exception):
    """Some series failed. The message names them; the reasons are in ingestion_runs."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.backfill",
        description="Capture the whole revision history of each FRED series not yet captured.",
    )
    parser.add_argument(
        "--series",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None,
        help="comma-separated series ids (default: every series in settings.FRED_SERIES)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="capture the named series again even if already captured (needs --series)",
    )
    args = parser.parse_args(argv)
    if args.force and not args.series:
        # A re-capture of everything is about 90 minutes of FRED time: it must be asked for by name.
        parser.error("--force needs --series (re-capturing every series must be explicit)")
    known = {s.series_id for s in settings.FRED_SERIES}
    unknown = sorted(set(args.series or ()) - known)
    if unknown:
        parser.error(f"unknown series: {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    return args


def select_series(all_series: Sequence[FredSeries], ids: Sequence[str] | None) -> list[FredSeries]:
    """The configured series in configured order, narrowed to `ids` if given."""
    return [s for s in all_series if ids is None or s.series_id in ids]


async def backfill_all(
    engine: AsyncEngine,
    series: Sequence[FredSeries],
    *,
    api_key: str,
    user_agent: str,
    limiter: Limiter,
    base_url: str,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    force: bool = False,
    rule_version: int = FRED_RULE_VERSION,
) -> None:
    """Capture each series not already captured at `rule_version`, continuing past a failure and
    raising BackfillIncomplete at the end if any failed."""
    started = datetime.now(UTC)
    async with engine.begin() as conn:
        progress_id = (
            await conn.execute(
                insert(BackfillRun)
                .values(
                    feed=BACKFILL_FEED,
                    total_units=len(series),
                    completed_units=0,
                    status=STATUS_RUNNING,
                    started_at=started,
                    updated_at=started,
                )
                .returning(BackfillRun.id)
            )
        ).scalar_one()

    async def pace() -> None:
        await limiter.acquire(PROVIDER)

    async def progress(**values: object) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                update(BackfillRun)
                .where(BackfillRun.id == progress_id)
                .values(updated_at=datetime.now(UTC), **values)
            )

    done = 0
    failed: list[str] = []
    for item in series:
        state = await read_state(engine, item.series_id)
        if not force and state is not None and state.rule_version == rule_version:
            # Not resume: a captured series is not fetched at all; costs nothing, writes nothing.
            log.info(
                "fred_history_skipped",
                series=item.series_id,
                rule_version=rule_version,
                captured_through=state.captured_through.isoformat(),
            )
            done += 1
            await progress(completed_units=done)
            continue
        await progress(current_unit=item.series_id)
        t0 = time.monotonic()
        captured = False
        try:
            await run_feed(
                engine,
                PROVIDER,
                item.series_id,
                history_body(
                    item,
                    api_key=api_key,
                    user_agent=user_agent,
                    pace=pace,
                    base_url=base_url,
                    transport=transport,
                    engine=engine,
                    rule_version=rule_version,
                ),
                limiter=limiter,
                retry=retry,
                secrets=[api_key],
            )
            captured = True
            done += 1
            await progress(completed_units=done)
        except Exception:
            # run_feed has already written the failure (kind and class) to ingestion_runs.
            failed.append(item.series_id)
        seconds = round(time.monotonic() - t0, 1)
        # DECISION: two events, never one with a vague name. The first version logged
        # `fred_history_series_done` for a failed series too, and a refusal read as a success
        # twice in one day (2026-11-13). An event now says what happened.
        if captured:
            log.info("fred_history_series_captured", series=item.series_id, seconds=seconds)
        else:
            log.error("fred_history_series_failed", series=item.series_id, seconds=seconds)
        if seconds > SLOW_SERIES_SECONDS:
            log.warning(
                "fred_history_series_over_twenty_minutes", series=item.series_id, seconds=seconds
            )
    await progress(current_unit=None, status=STATUS_FAILED if failed else STATUS_SUCCEEDED)
    if failed:
        raise BackfillIncomplete(
            f"{len(failed)} of {len(series)} series failed: {', '.join(failed)}"
        )


async def execute(
    engine: AsyncEngine,
    series: Sequence[FredSeries],
    *,
    api_key: str,
    user_agent: str,
    limiter: Limiter,
    base_url: str,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    force: bool = False,
    rule_version: int = FRED_RULE_VERSION,
) -> None:
    """`backfill_all` inside `run_recorded`: the execution gets its `task_runs` row."""

    async def body() -> None:
        await backfill_all(
            engine,
            series,
            api_key=api_key,
            user_agent=user_agent,
            limiter=limiter,
            base_url=base_url,
            transport=transport,
            retry=retry,
            force=force,
            rule_version=rule_version,
        )

    await run_recorded(engine, FRED_BACKFILL, body)


async def run(ids: Sequence[str] | None, force: bool) -> None:
    url, redis_url = settings.database_url(), settings.redis_url()
    if url is None or redis_url is None:
        raise RuntimeError("NotConfigured")
    api_key = settings.fred_api_key()
    if api_key is None:
        raise RuntimeError("NotConfigured")
    engine = create_async_engine(url, connect_args={"timeout": 5, "command_timeout": 5})
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    try:
        await execute(
            engine,
            select_series(settings.FRED_SERIES, ids),
            api_key=api_key,
            user_agent=settings.fred_user_agent(),
            # DECISION: redis-py types hgetall as sync-or-async; ours is the async client.
            limiter=RedisSpacingLimiter(redis, settings.RATE_BUDGETS),
            base_url=settings.fred_base_url(),
            retry=RetryPolicy(initial_seconds=settings.ingest_retry_initial_seconds()),
            force=force,
        )
    finally:
        await redis.aclose()
        await engine.dispose()


KEEP_AWAKE_NOTICE = (
    "KEEP THIS MACHINE AWAKE until this finishes (about 45 minutes for every series). On "
    "2026-11-13 a laptop slept mid-request and the run sat for nine hours before the dead "
    "connection was retried. Disable sleep, or run it where the machine cannot sleep."
)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    # Plain text on stderr, ahead of the JSON logs, so that a person starting it sees it.
    print(KEEP_AWAKE_NOTICE, file=sys.stderr, flush=True)
    log.warning("fred_history_keep_awake", detail=KEEP_AWAKE_NOTICE)
    try:
        asyncio.run(run(args.series, args.force))
    except BackfillIncomplete as exc:
        log.error("fred_history_incomplete", detail=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

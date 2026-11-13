"""The FRED history feed body (M2 slice 5, step 3): one series' revision history into macro_metrics.

It is a `Body` for app.ingest.runner.run_feed: that supplies the rate-budget slot per attempt, the
retries, and the ingestion_runs and providers_health rows. The body builds a client PER ATTEMPT so
the client's request count restarts with the slot it was granted.

Four reads, then one judged write (ADR-0003 addendum, 2026-11-13):
  1. `output_type=3`, windowed: every revision with its true vintage date;
  2. `output_type=4`, chunked: the first prints, the INDEPENDENT witness that a chain begins at a
     real first print (rule 1);
  3. `output_type=1`, last: the latest values (rules 3 and 4). Last, so that a release landing
     during the long fetch can only make the history look stale (a refusal and a re-run), never the
     latest values look older than the history;
  4. the six rules, inside the store's transaction, then the write and the state row.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest.fred import BASE_URL, FredClient
from app.ingest.macro import (
    FRED_RULE_VERSION,
    FirstPrintRef,
    FredSeries,
    HistoryVerdict,
    check_history,
)
from app.ingest.runner import FeedContext, FeedResult
from app.services.macro_store import Chains, commit_history

log = structlog.get_logger(__name__)

PROVIDER = "fred"


def history_body(
    series: FredSeries,
    *,
    api_key: str,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
    base_url: str = BASE_URL,
    transport: httpx2.AsyncBaseTransport | None = None,
    engine: AsyncEngine,
    rule_version: int = FRED_RULE_VERSION,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[FeedContext], Awaitable[FeedResult]]:
    """Fetch the history, the first prints and the latest values, judge them, store.

    Nothing is written unless every rule holds: a partial history must not look complete.
    """

    async def body(ctx: FeedContext) -> FeedResult:
        async with FredClient(
            api_key,
            user_agent=user_agent,
            base_url=base_url,
            transport=transport,
            before_next_request=pace,
        ) as fred:
            history = await fred.revision_history(series.series_id)
            first_prints = await fred.first_prints_chunked(series.series_id, history.vintage_dates)
            latest = await fred.latest(series.series_id)

        first = {o.effective_at: FirstPrintRef(o.realtime_start, o.value) for o in first_prints}
        latest_values = {o.effective_at: o.value for o in latest}

        def judge(stored: Chains) -> HistoryVerdict:
            return check_history(
                rows=history.rows,
                vintage_dates=history.vintage_dates,
                first_prints=first,
                latest=latest_values,
                stored=stored,
            )

        now = clock() if clock is not None else datetime.now(UTC)
        committed = await commit_history(
            engine,
            series.series_id,
            judge=judge,
            captured_through=history.vintage_dates[-1] if history.vintage_dates else now.date(),
            rule_version=rule_version,
            ingestion_run_id=ctx.run_id,
            **({"clock": clock} if clock is not None else {}),
        )
        verdict = committed.verdict
        if first and min(first) != series.first_covered_period:
            # Measured values are config (FRED states neither boundary). A difference means FRED's
            # vintage record moved; the data is still accounted for, so this warns.
            log.warning(
                "fred_vintage_start_changed",
                series=series.series_id,
                configured=series.first_covered_period.isoformat(),
                observed=min(first).isoformat(),
            )
        # The exclusion counts are logged apart, never summed: A (no vintage exists) is not B
        # (revised but pre-vintage).
        log.info(
            "fred_history_series",
            series=series.series_id,
            first_prints=verdict.first_prints,
            revisions=verdict.revisions,
            created=committed.created,
            no_vintage_excluded=verdict.no_vintage_excluded,
            gaps_without_trace_skipped=verdict.gaps_without_trace_skipped,
            revised_pre_vintage_excluded=verdict.revised_pre_vintage_excluded,
            duplicates_skipped=verdict.duplicates_skipped,
            windows_split=history.splits,
            final_window=history.final_window,
        )
        return FeedResult(
            rows_read=sum(len(row.values) for row in history.rows), rows_written=committed.created
        )

    return body

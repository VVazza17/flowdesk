"""The FRED backfill feed body (M2 slice 5, step 3): one series, first prints into macro_metrics.

It is a `Body` for app.ingest.runner.run_feed: that supplies the rate-budget slot per attempt, the
retries, and the ingestion_runs and providers_health rows. The body builds a client PER ATTEMPT so
the client's request count restarts with the slot it was granted.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest.errors import SchemaMismatch
from app.ingest.fred import BASE_URL, FredClient
from app.ingest.macro import FredSeries, account_for, available_at_for
from app.ingest.runner import FeedContext, FeedResult
from app.services.macro_store import FirstPrint, insert_first_prints

log = structlog.get_logger(__name__)

PROVIDER = "fred"


def backfill_body(
    series: FredSeries,
    *,
    api_key: str,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
    base_url: str = BASE_URL,
    transport: httpx2.AsyncBaseTransport | None = None,
    engine: AsyncEngine,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[FeedContext], Awaitable[FeedResult]]:
    """Fetch the first prints and the latest values, check the first prints against them, store.

    Nothing is written unless the check passes (app.ingest.macro.account_for): a partial history
    must not look like a complete one.
    """

    async def body(ctx: FeedContext) -> FeedResult:
        async with FredClient(
            api_key,
            user_agent=user_agent,
            base_url=base_url,
            transport=transport,
            before_next_request=pace,
        ) as fred:
            first_prints = await fred.first_prints_chunked(series.series_id)
            latest = await fred.latest(series.series_id)
        accounting = account_for(
            {row.effective_at: row.value for row in latest},
            {row.effective_at for row in first_prints},
        )
        if not accounting.complete:
            # Counts only, and the first few dates: enough to look, nothing the provider said.
            raise SchemaMismatch(
                f"first prints of {series.series_id} do not account for the latest values: "
                f"{len(accounting.unaccounted)} values have no first print "
                f"(from {[d.isoformat() for d in accounting.unaccounted[:3]]}), "
                f"{len(accounting.not_in_latest)} first prints are not in the latest values"
            )
        if first_prints:
            # Measured values are config (FRED states neither boundary). A difference means
            # FRED's vintage record moved; the data is still accounted for, so this warns.
            earliest = min(row.effective_at for row in first_prints)
            if earliest != series.first_covered_period:
                log.warning(
                    "fred_vintage_start_changed",
                    series=series.series_id,
                    configured=series.first_covered_period.isoformat(),
                    observed=earliest.isoformat(),
                )
        created = await insert_first_prints(
            engine,
            series.series_id,
            [
                FirstPrint(row.effective_at, row.value, available_at_for(row.realtime_start))
                for row in first_prints
            ],
            ingestion_run_id=ctx.run_id,
            **({"clock": clock} if clock is not None else {}),
        )
        log.info(
            "fred_backfill_series",
            series=series.series_id,
            first_prints=len(first_prints),
            created=created,
            pre_vintage_excluded=accounting.pre_vintage,
            gaps_without_trace_skipped=accounting.gaps_without_trace,
        )
        return FeedResult(rows_read=len(first_prints), rows_written=created)

    return body

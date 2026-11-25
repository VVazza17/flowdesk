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

from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest.errors import NoBaseline
from app.ingest.fred import BASE_URL, FredClient
from app.ingest.macro import (
    FRED_RULE_VERSION,
    FirstPrintRef,
    FredSeries,
    FredVintageRow,
    HistoryVerdict,
    check_history,
    rows_from_chains,
)
from app.ingest.runner import FeedContext, FeedResult
from app.services.macro_store import Chains, commit_history, read_chains, read_state

log = structlog.get_logger(__name__)

PROVIDER = "fred"

# DECISION (2026-11-24): at most this many F3 late periods are fetched in one poll of one series.
# Each costs at least one request against FRED's budget; FRED back-dating many periods at once is
# not a late arrival to judge one by one but something to look at. Over the cap nothing is fetched
# and every one refuses under rule 4, named (fails closed). The number is ASSUMED, not measured:
# one late period has been seen (2026-11-10, F-6b).
LATE_FETCH_MAX = 10


def late_candidates(
    latest: Mapping[date, Decimal | None],
    *,
    stored: Chains,
    window_periods: Collection[date],
    captured_through: date,
    first_covered_period: date,
) -> list[date]:
    """The periods whose evidence the poll fetches: in the latest values with a number, on or before
    `captured_through`, not stored, nothing in the window, and inside the record (not before the
    earliest stored period, or the configured first covered period when nothing is stored).

    Only what is FETCHED. `check_history` decides what is a late arrival and refuses any without
    evidence, so a period this misses refuses instead of being accepted.
    """
    floor = min(stored) if stored else first_covered_period
    return sorted(
        period
        for period, value in latest.items()
        if value is not None
        and period <= captured_through
        and period >= floor
        and period not in stored
        and period not in window_periods
    )


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
            clock=clock if clock is not None else (lambda: datetime.now(UTC)),
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
            gaps_without_o4_first_print_skipped=verdict.gaps_without_o4_first_print_skipped,
            gap_first_prints_without_o4_witness=verdict.gap_first_prints_without_o4_witness,
            revised_pre_vintage_excluded=verdict.revised_pre_vintage_excluded,
            duplicates_skipped=verdict.duplicates_skipped,
            windows_split=history.splits,
            final_window=history.final_window,
        )
        return FeedResult(
            rows_read=sum(len(row.values) for row in history.rows), rows_written=committed.created
        )

    return body


def poll_body(
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
    """The daily poll of one series: what changed since it was last captured, judged and appended.

    It asks FRED only for the vintages from `series_history_state.captured_through` (inclusive),
    merges those columns onto the stored chains, and runs the SAME six rules (`check_history`) over
    the merged history, so the poll cannot drift from the backfill. Revisions are dated by FRED's
    vintage, never by the poll (ADR-0003 addendum, F1). `commit_history` writes only the tail
    that is new, so a quiet day writes nothing and says so (`rows_written` 0).

    It REFUSES a series with no captured history, or one captured under older rules
    (`NoBaseline`): polling an empty series would write its whole history with `available_at` set
    to today.

    INVARIANT (see `FredClient.revisions_since`): the poll reads the vintage-date list first and
    only requests windows that contain a vintage. That is what makes FRED's "No vintage dates exist"
    400 unreachable here, so a quiet day is "nothing new to merge", not an error. Do not replace
    that read with a direct date-range request: the 400 returns on every quiet day.

    Rule 3 is the poll's daily consistency check: after the merge every chain must end at FRED's
    latest value. A revision published between this run's reads (FRED updates during the day) makes
    it refuse; the next run, a day later, sees both. Known race, kept because the alternative is to
    store a chain that stops short.

    F3 LATE ARRIVALS (ADR-0003 addendum F3): a latest value for a period on or before
    `captured_through` that has no stored history and nothing in the window gets its own fetch
    (`FredClient.period_history`), after the latest values, and the rules accept it only on that
    evidence. Accepted, it is dated by this run's start (`ctx.started_at`), never by FRED's vintage.
    A rolling-window series (`FredSeries.rolling_window`) also has its rolled-off stored periods
    left as they are (`check_history`).
    """

    async def body(ctx: FeedContext) -> FeedResult:
        state = await read_state(engine, series.series_id)
        if state is None or state.rule_version != rule_version:
            raise NoBaseline(
                f"{series.series_id} has no history captured under rule version {rule_version}: "
                "run python -m app.backfill first"
            )
        # Read only, outside the store's transaction: which periods are stored, to know which
        # latest values are late. The judge re-reads under the lock; a period that changed between
        # the two reads has no evidence and refuses, never slips through.
        async with engine.connect() as conn:
            stored_now = await read_chains(conn, series.series_id)
        async with FredClient(
            api_key,
            user_agent=user_agent,
            base_url=base_url,
            transport=transport,
            before_next_request=pace,
        ) as fred:
            history = await fred.revisions_since(series.series_id, state.captured_through)
            window_first = await fred.first_prints_chunked(series.series_id, history.window_dates)
            latest = await fred.latest(series.series_id)
            latest_values = {o.effective_at: o.value for o in latest}
            candidates = late_candidates(
                latest_values,
                stored=stored_now,
                window_periods={r.effective_at for r in history.rows if r.values},
                captured_through=state.captured_through,
                first_covered_period=series.first_covered_period,
            )
            evidence: dict[date, FredVintageRow] = {}
            if len(candidates) > LATE_FETCH_MAX:
                # Nothing fetched: every one of them refuses under rule 4, named. Fails closed.
                log.warning(
                    "fred_late_arrivals_over_cap",
                    series=series.series_id,
                    late=len(candidates),
                    cap=LATE_FETCH_MAX,
                )
            else:
                for period in candidates:
                    evidence[period] = await fred.period_history(
                        series.series_id, period, history.vintage_dates
                    )

        window_first_refs = {
            o.effective_at: FirstPrintRef(o.realtime_start, o.value) for o in window_first
        }

        def judge(stored: Chains) -> HistoryVerdict:
            # A stored period is its own first-print witness; a period new in the window is
            # witnessed by output_type=4 over the same window (and a stored one that the window
            # also lists must agree with it, else rule 1 refuses).
            first = {
                p: FirstPrintRef(chain[0].vintage, chain[0].value) for p, chain in stored.items()
            } | window_first_refs
            return check_history(
                rows=[*rows_from_chains(stored), *history.rows],
                vintage_dates=history.vintage_dates,
                first_prints=first,
                latest=latest_values,
                stored=stored,
                rolling_window=series.rolling_window,
                late_since=state.captured_through,
                late_evidence=evidence,
            )

        now = clock() if clock is not None else datetime.now(UTC)
        committed = await commit_history(
            engine,
            series.series_id,
            judge=judge,
            captured_through=(
                history.vintage_dates[-1] if history.vintage_dates else state.captured_through
            ),
            rule_version=rule_version,
            ingestion_run_id=ctx.run_id,
            exclusion_counts=False,
            clock=clock if clock is not None else (lambda: now),
            observed_at=ctx.started_at,
        )
        verdict = committed.verdict
        for period, claimed in sorted(verdict.late_arrivals.items()):
            log.info(
                "fred_late_arrival_accepted",
                series=series.series_id,
                period=period.isoformat(),
                fred_claimed_vintage=claimed.isoformat(),
                available_at=ctx.started_at.isoformat(),
            )
        # The exclusion counts here are of what this window touched, not of the series: logged
        # apart from the state row, which keeps the last full capture's.
        log.info(
            "fred_poll_series",
            series=series.series_id,
            since=state.captured_through.isoformat(),
            vintages_fetched=len(history.window_dates),
            created=committed.created,
            first_prints=verdict.first_prints,
            revisions=verdict.revisions,
            window_revised_pre_vintage=verdict.revised_pre_vintage_excluded,
            window_gap_first_prints=verdict.gap_first_prints_without_o4_witness,
            windows_split=history.splits,
            rolled_off=verdict.rolled_off,
            late_arrivals=len(verdict.late_arrivals),
        )
        return FeedResult(
            rows_read=sum(len(row.values) for row in history.rows), rows_written=committed.created
        )

    return body

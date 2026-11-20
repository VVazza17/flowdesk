"""One settled-bars run: the backfill (`python -m app.bars_backfill`) and the polls (M2 slice 6,
step 6).

`run_bars(interval=..., mode=...)` fetches every mapped symbol in batches of at most `max_batch`,
each batch its own `run_feed` (its own ingestion run and rate-budget slot), parses with
`app.ingest.bars` and writes with `app.services.bars_store`. What differs by mode:

  backfill  daily: `period="2y"`; 15m: `start` = today - 58 calendar days (`intraday_floor`;
            decided 2026-11-16 as 59, changed to 58 by Kyle 2026-11-20, F-6a).
            `feed = 'backfill'`: the recorded exception (values fetched after their settle
            point). A `backfill_runs` progress row.
  poll      each symbol starts at ITS last stored session, INCLUSIVE (Kyle, 2026-11-18: re-checking
            it every morning counts every late volume revision as it happens; the counts are
            reported per run). A symbol with no stored bar fails as `NotBackfilled` (decided
            2026-11-15: the poll never fetches deep history). A 15m symbol whose last bar is more
            than 58 days back has the unreachable range recorded in `bar_gaps`, then is fetched
            from the limit. `feed = 'poll'`.

**No 15m bar is accepted until splits for its window are known in the same run** (Kyle, 2026-11-18,
the general rule for every 15m write). A 15m run first fetches its own daily `actions=True` batch
for the same symbols over its window (to now, so the next session's row when it exists), stores and
checks any new split and distribution under the usual rules, and only then fetches and parses the
15m bars. That second request takes its own rate-budget slot. A symbol whose split check failed
(a disagreeing split, say) gets no 15m bars in that run and is named.

**Today's row (Kyle, 2026-11-20; MEASURED that day: no row for the day at 09:05 ET for 0 of 24
symbols, present at 09:35 and 10:05 for 24 of 24).** On a session day, no bar is accepted for a
symbol unless this run's daily `actions=True` frame (the daily batch itself, or the 15m run's
split check) has today's row for that symbol: until it does, a split or distribution effective
at today's open is invisible. Otherwise the symbol is HELD: nothing is written for it (bars,
splits, distributions), the run still succeeds, and `Report.held` counts per symbol the settled
bars it did not accept. Polls, and both backfills. Today's row is read only for its split and
distribution values, never for its prices (its Open moved between 09:35 and 10:05 for 22 of 24
symbols, MEASURED). Residual risk, ASSUMED and accepted (Kyle): Yahoo adjusting history over a
weekend or holiday for a split dated the next session is seen by nothing before the open; the
next morning's inclusive re-check fails that symbol loudly (a re-fetched price differs).
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app import settings
from app.ingest.bars import (
    DAILY,
    INTRADAY_15M,
    NEW_YORK,
    BatchResult,
    NotBackfilled,
    SymbolFailure,
    classify_dead,
    parse_batch,
    run_failure,
)
from app.ingest.budget import Limiter, RedisSpacingLimiter, SetPttlRedis, wait_bound
from app.ingest.errors import ProviderError
from app.ingest.runner import FeedContext, FeedResult, RetryPolicy, run_feed
from app.ingest.yfinance_client import (
    Download,
    YFinanceClient,
    intraday_floor,
    yf_download,
)
from app.models.ops import BackfillRun
from app.services import bars_store
from app.services.bars_store import Coverage
from app.tasks.names import STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED

log = structlog.get_logger(__name__)

BACKFILL = "backfill"
POLL = "poll"
FEEDS = {DAILY: "bars_daily", INTRADAY_15M: "bars_15m"}  # the ingestion feed names
DAILY_BACKFILL_PERIOD = "2y"


@dataclass
class Report:
    rows_written: int = 0
    bars: int = 0
    distributions: int = 0
    splits: int = 0
    gaps: int = 0  # bar_gaps rows recorded (15m beyond the look-back)
    volume_revisions: dict[str, int] = field(default_factory=dict)  # per symbol, kept stored
    unsettled: int = 0
    coverage: list[Coverage] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Per symbol: settled bars not accepted because the run's daily frame had no row for today.
    held: dict[str, int] = field(default_factory=dict)


def without_today(result: BatchResult, tickers: Iterable[str], *, session_today: bool) -> set[str]:
    """The symbols to HOLD (Kyle, 2026-11-20): on a session day, those whose daily `actions=True`
    frame in this run has no row for today. A dead or failed symbol is not held: it is already
    named as what it is, and holding would hide it."""
    if not session_today:
        return set()
    excluded = set(result.dead) | {f.ticker for f in result.failures}
    return {t for t in tickers if t not in excluded and t not in result.today_rows}


def hold(result: BatchResult, held: Iterable[str], report: Report | None = None) -> None:
    """Nothing of a held symbol is written: its bars, splits and distributions are dropped from
    the batch. With `report`, its settled bars are counted in `report.held` (the run's own
    interval only: the 15m run's daily check passes none)."""
    for t in sorted(held):
        dropped = len(result.bars.pop(t, []))
        if report is not None:
            report.held[t] = report.held.get(t, 0) + dropped
        result.new_splits.pop(t, None)
        result.new_distributions.pop(t, None)


def yfinance_limiter(redis: SetPttlRedis) -> RedisSpacingLimiter:
    """The limiter every bars entry point uses: the declared budgets, and a wait bound of at least
    two spacings (a 15m run takes two slots in a row; `wait_bound`)."""
    return RedisSpacingLimiter(
        redis, settings.RATE_BUDGETS, max_wait_seconds=wait_bound(settings.RATE_BUDGETS)
    )


def _session(last: tuple[date, datetime] | None) -> date | None:
    return last[0] if last is not None else None


def _add(report: Report, counts: bars_store.WriteCounts) -> None:
    report.bars += counts.bars
    report.distributions += counts.distributions
    report.splits += counts.splits
    report.rows_written += counts.rows_written
    for ticker, n in counts.volume_revisions.items():
        report.volume_revisions[ticker] = report.volume_revisions.get(ticker, 0) + n


async def run_bars(
    engine: AsyncEngine,
    *,
    interval: str,
    mode: str,
    limiter: Limiter,
    clock: Callable[[], datetime],
    tickers: Sequence[str] | None = None,
    download: Download = yf_download,
    retry: RetryPolicy = RetryPolicy(),  # noqa: B008 - frozen dataclass, safe as a default
    report: Report | None = None,
) -> Report:
    """Fetch, check, parse, store and report every mapped symbol. Raises the run's failure after
    the good symbols' rows are written (D4); `report` (pass one in to read it after a failure) is
    filled either way. Must run inside `run_recorded`."""
    if interval not in FEEDS or mode not in (BACKFILL, POLL):
        raise ValueError("unknown interval or mode")
    calendar = await bars_store.load_calendar(engine)
    symbols = await bars_store.load_symbols(engine, tickers)
    if not symbols:
        raise ValueError("no symbol has an active yfinance mapping (run the seed)")
    max_batch = settings.RATE_BUDGETS[bars_store.PROVIDER].max_batch
    batches = [symbols[i : i + max_batch] for i in range(0, len(symbols), max_batch)]
    client = YFinanceClient(max_batch=max_batch, download=download)
    report = report if report is not None else Report()
    feed_value = BACKFILL if mode == BACKFILL else POLL
    window: list[date] = []
    progress_id: int | None = None
    if mode == BACKFILL:
        started = clock()
        async with engine.begin() as conn:
            progress_id = (
                await conn.execute(
                    insert(BackfillRun)
                    .values(
                        feed=f"yfinance.{FEEDS[interval]}",
                        total_units=len(batches),
                        completed_units=0,
                        status=STATUS_RUNNING,
                        started_at=started,
                        updated_at=started,
                    )
                    .returning(BackfillRun.id)
                )
            ).scalar_one()

    async def known(ids: dict[str, int]) -> tuple[dict[str, Any], dict[str, Any]]:
        sp = await bars_store.load_splits(engine, ids.values())
        di = await bars_store.load_distributions(engine, ids.values())
        return {p: sp[s] for p, s in ids.items()}, {p: di[s] for p, s in ids.items()}

    def body_for(chunk: Sequence[bars_store.MappedSymbol]) -> Callable[[FeedContext], Any]:
        by_provider = {m.provider_symbol: m.symbol_id for m in chunk}

        async def body(ctx: FeedContext) -> FeedResult:
            now = clock()
            today = now.astimezone(NEW_YORK).date()
            floor = intraday_floor(today)
            session_today = calendar.get(today) is not None
            no_today: set[str] = set()
            refused: list[SymbolFailure] = []
            written = 0  # this batch's rows (a verification signal, never attempts)
            start: date | None = None
            period: str | None = None
            fetch = dict(by_provider)
            if mode == BACKFILL:
                if interval == DAILY:
                    period = DAILY_BACKFILL_PERIOD
                else:
                    start = floor
            else:
                last = await bars_store.last_stored(engine, by_provider.values(), interval)
                starts: dict[str, date] = {}
                for p, sid in by_provider.items():
                    got = last[sid]
                    if got is None:
                        refused.append(
                            SymbolFailure(p, NotBackfilled(f"{p}: no stored {interval} bar"))
                        )
                        continue
                    own = got[0]  # inclusive: the last stored session is fetched again
                    if interval == INTRADAY_15M and own < floor:
                        # Beyond the look-back: record what cannot be fetched, never a partial
                        # window passing for a whole one (decided 2026-11-15).
                        reach = next(s for s in calendar.sessions if s.session_date >= floor)
                        gap = await bars_store.record_gap(
                            engine,
                            sid,
                            interval,
                            got[1] + timedelta(minutes=15),
                            reach.opens_at,
                            ingestion_run_id=ctx.run_id,
                        )
                        report.gaps += gap
                        report.rows_written += gap
                        written += gap
                        own = floor
                    starts[p] = own
                fetch = {p: by_provider[p] for p in starts}
                start = min(starts.values()) if starts else None

            if fetch and interval == INTRADAY_15M:
                # The split check: this run's own daily actions batch over the window.
                ksplits, kdists = await known(fetch)
                check = await client.fetch(list(fetch), DAILY, today=today, start=start)
                checked = parse_batch(
                    check.frame,
                    check.errors,
                    tickers=list(fetch),
                    interval=DAILY,
                    calendar=calendar,
                    now=now,
                    known_splits=ksplits,
                    known_distributions=kdists,
                )
                # Today's row (Kyle, 2026-11-20): a symbol without it is held, and so are the
                # split and distribution events this check found for it.
                no_today = without_today(checked, fetch, session_today=session_today)
                hold(checked, no_today)
                counts = await bars_store.write_daily(
                    engine,
                    # Events only: no daily bar is written here (the daily feed owns those).
                    # Keyed by EVERY fetched symbol: write_daily walks this mapping's keys.
                    {p: [] for p in fetch},
                    checked.new_splits,
                    fetch,
                    checked.new_distributions,
                    calendar=calendar,
                    feed=feed_value,
                    ingestion_run_id=ctx.run_id,
                )
                _add(report, counts)
                written += counts.rows_written
                refused.extend(checked.failures)
                refused.extend(counts.mismatched)
                # Only a symbol whose check FAILED is held back (named above). One with no settled
                # daily bar yet, or none at all, is still fetched: the 15m batch judges it.
                held = {f.ticker for f in [*checked.failures, *counts.mismatched]}
                fetch = {p: s for p, s in fetch.items() if p not in held}
                # The 15m fetch is a second request in this run: it takes its own slot.
                await limiter.acquire(bars_store.PROVIDER)

            parsed: list[date] = []
            failures: list[SymbolFailure] = list(refused)
            if fetch:
                ksplits, kdists = await known(fetch)
                batch = await client.fetch(
                    list(fetch), interval, today=today, start=start, period=period
                )
                result = parse_batch(
                    batch.frame,
                    batch.errors,
                    tickers=list(fetch),
                    interval=interval,
                    calendar=calendar,
                    now=now,
                    known_splits=ksplits,
                    known_distributions=kdists,
                )
                if interval == DAILY:
                    # The daily batch is itself the run's daily actions=True frame.
                    no_today = without_today(result, fetch, session_today=session_today)
                hold(result, no_today, report)
                counts = await bars_store.write_daily(
                    engine,
                    result.bars,
                    result.new_splits,
                    fetch,
                    result.new_distributions,
                    calendar=calendar,
                    feed=feed_value,
                    ingestion_run_id=ctx.run_id,
                    interval=interval,
                )
                _add(report, counts)
                written += counts.rows_written
                report.unsettled += result.unsettled
                if counts.volume_revisions:
                    # In the run's own record: a post-settle volume revision, not a failure,
                    # the stored value kept. Feeds the revision-policy decision.
                    log.warning(
                        "bars_volume_revised_after_settle",
                        ingestion_run_id=ctx.run_id,
                        interval=interval,
                        per_symbol=dict(sorted(counts.volume_revisions.items())),
                    )
                # D4: likely_gone against the newest session ANY mapped symbol has stored.
                every = await bars_store.last_stored(
                    engine, [m.symbol_id for m in symbols], interval
                )
                newest = max((v[0] for v in every.values() if v), default=None)
                dead = classify_dead(
                    result.dead,
                    last_stored={p: _session(every[s]) for p, s in fetch.items()},
                    newest_stored=newest,
                    calendar=calendar,
                )
                parsed = [b.session_date for rows in result.bars.values() for b in rows]
                if parsed:
                    window.extend([min(parsed), max(parsed)])
                failures += [*result.failures, *counts.mismatched, *dead]
            report.failed = sorted({*report.failed, *(f.ticker for f in failures)})
            error = run_failure(failures)
            if error is not None:
                raise error
            return FeedResult(rows_read=len(parsed), rows_written=written)

        return body

    errors: list[BaseException] = []
    done = 0
    for chunk in batches:
        try:
            await run_feed(
                engine,
                bars_store.PROVIDER,
                FEEDS[interval],
                body_for(chunk),
                limiter=limiter,
                retry=retry,
            )
            done += 1
        except ProviderError as exc:
            # run_feed has recorded it. A whole-batch failure names no symbol: name the batch's.
            if not any(m.provider_symbol in report.failed for m in chunk):
                report.failed = sorted({*report.failed, *(m.provider_symbol for m in chunk)})
            errors.append(exc)
        if progress_id is not None:
            async with engine.begin() as conn:
                await conn.execute(
                    update(BackfillRun)
                    .where(BackfillRun.id == progress_id)
                    .values(updated_at=clock(), completed_units=done)
                )

    if window and interval == DAILY:
        report.coverage = bars_store.coverage(
            symbols,
            await bars_store.stored_sessions(engine, [m.symbol_id for m in symbols]),
            calendar=calendar,
            window_first=min(window),
            last_settled=max(window),
        )
    if progress_id is not None:
        async with engine.begin() as conn:
            await conn.execute(
                update(BackfillRun)
                .where(BackfillRun.id == progress_id)
                .values(updated_at=clock(), status=STATUS_FAILED if errors else STATUS_SUCCEEDED)
            )
    if errors:
        # DECISION: one batch today (24 symbols). With several, the first failure is raised,
        # and every failed symbol is named in report.failed and in each batch's ingestion run.
        raise errors[0]
    return report

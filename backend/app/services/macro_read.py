"""The point-in-time read of macro observations (M2 slice 5, step 4; ADR-0003 decisions 2, 6 and 7).

The one place that answers "what did we know about metric M, about period P, at instant T". It is
the primitive live calculation and replay both read through (ADR-0004), so `as_of` is an
ARGUMENT: nothing here reads a clock.

THE RULE. A row is visible at T if and only if `available_at <= T` (inclusive). That is a filter on
row EXISTENCE, not on value (decision 2): a period first published after T is absent, not present
with a stale number, and a period revised after T shows the revision that was current at T.
`available_at` is the end of the publication day in New York (ADR-0003 addendum, 2026-11-11), so a
value published on day D is visible from 00:00 New York on D+1 and not before; that alone is
decision 6 (a signal at the open of D resolves to the prior session's value), with no extra code.

A STORED NULL IS A PUBLISHED GAP (decision 7). It comes back as an `Observation` with `value=None`:
"FRED said this period had no value." It is never forward-filled here and never reported as
`Missing`, which means something else.

THE FOUR WAYS TO HAVE NO ANSWER are four different answers, and conflating them is the failure this
module exists to prevent. `Missing.reason`, in the order they are decided:

  UNKNOWN_METRIC           nothing is configured or stored for this metric at all.
  BEFORE_VINTAGE_RECORD    `as_of` is earlier than the first instant anything could be known about
                           this series (its first vintage, end of day New York). No replay of that
                           instant is possible for ANY period. A property of T.
  PREDATES_VINTAGE_RECORD  the PERIOD is earlier than the series' `first_covered_period`. FRED's
                           latest values hold a number for it, but no publication date exists
                           (exclusion A, or exclusion B when it was revised later), so it was never
                           stored and cannot be placed on a timeline. "A value exists but predates
                           the vintage record", NOT "no value exists". A property of P.
  NOT_PUBLISHED_AS_OF      the period is known to us (some revision exists) but none was published
                           at or before `as_of`: at T, the world had not seen it yet.
  UNKNOWN_PERIOD           no revision of this period exists at ANY time: a date that is not one of
                           the series' observation dates, or a period beyond the newest we hold.

`first_covered_period` is what keeps PREDATES distinct from UNKNOWN_PERIOD: without it, "FRED has a
number but we cannot date it" and "there is no such observation" would be the same empty answer.
The boundaries come from `settings.FRED_SERIES` (measured, FRED states neither) unless a caller
passes `bounds` for a series that is not ingested (a test, or a recorded fixture).

ONE FUNCTION CAN LEAK POST-T KNOWLEDGE: `revisions_of(..., as_of=None)`. With `as_of=None` it
returns the WHOLE chain of a period, including every revision published AFTER any instant the
caller is reasoning about. That is deliberate and defensible (diagnosis; the vintage-repair
procedure, docs/RUNBOOK-VINTAGE-REPAIR.md, wants exactly the whole chain; M5's hindsight
questions), and it is the one path in this module that is NOT a point-in-time read.

Its `as_of` is a REQUIRED keyword that may be None, so the whole-chain read has to be spelled out,
`as_of=None`, at every call site, where it is visible in the diff that adds it. A default would
let `revisions_of(engine, m, p)` read like the other three functions. A signal, a score, a brief,
a replay, anything that computes a number from the past, must use `series_as_of`, `value_as_of` or
`latest_as_of`, or pass a real `as_of` to `revisions_of`. Writing `as_of=None` in such a path is
look-ahead.

PERFORMANCE WAS MEASURED ON THE SMALLEST LOAD THIS MODULE WILL EVER CARRY: about 5,600 rows of daily
macro per series (BUILD-LOG, step 4). "No index needed" is a conclusion about that, not about bars.
Slice 6 (prices) must re-measure; it must not inherit it.

NOT HERE: snapshots and watermarks (ADR-0004, M3), signals, bars, and any HTTP route. A route needs
the section 9 envelope and the public-profile route invariant (IDEA-019) and waits for a consumer.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app import settings
from app.ingest.macro import FredSeries, available_at_for
from app.models.macro import MacroMetric


class Reason(StrEnum):
    UNKNOWN_METRIC = "unknown_metric"
    BEFORE_VINTAGE_RECORD = "before_vintage_record"
    PREDATES_VINTAGE_RECORD = "predates_vintage_record"
    NOT_PUBLISHED_AS_OF = "not_published_as_of"
    UNKNOWN_PERIOD = "unknown_period"


@dataclass(frozen=True)
class Observation:
    """What was known: the revision of `effective_at` current at the query's `as_of`.

    `value` is None for a published gap (FRED's "."), which is an answer, not an absence.
    """

    metric: str
    effective_at: date
    value: Decimal | None
    revision: int
    available_at: datetime


@dataclass(frozen=True)
class Missing:
    """No answer, and why. A value, not an exception: the reason is the information."""

    reason: Reason
    detail: str


@dataclass(frozen=True)
class Coverage:
    """The series' record, so a caller sees the boundary instead of inferring it from silence.

    `known` is False for a metric with no configured bounds (then the boundary dates are None and
    only the two bounds-free reasons can be given).
    """

    known: bool
    first_vintage: date | None
    first_covered_period: date | None
    as_of_precedes_record: bool


@dataclass(frozen=True)
class Snapshot:
    metric: str
    as_of: datetime
    observations: tuple[Observation, ...]
    coverage: Coverage


def bounds_for(metric: str) -> FredSeries | None:
    """The configured record boundaries of an ingested series, or None."""
    return next((s for s in settings.FRED_SERIES if s.series_id == metric), None)


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        # A naive datetime is a guess at a zone, and a point-in-time read that guesses is wrong
        # for hours around every boundary.
        raise ValueError("as_of must be timezone-aware")


def _observation(row: Row[Any]) -> Observation:
    return Observation(
        metric=row.metric,
        effective_at=row.effective_at,
        value=row.value,
        revision=row.revision,
        available_at=row.available_at,
    )


_COLUMNS = (
    MacroMetric.metric,
    MacroMetric.effective_at,
    MacroMetric.value,
    MacroMetric.revision,
    MacroMetric.available_at,
)


def _coverage(bounds: FredSeries | None, as_of: datetime) -> Coverage:
    if bounds is None:
        return Coverage(False, None, None, False)
    return Coverage(
        known=True,
        first_vintage=bounds.first_vintage,
        first_covered_period=bounds.first_covered_period,
        as_of_precedes_record=as_of < available_at_for(bounds.first_vintage),
    )


async def series_as_of(
    engine: AsyncEngine,
    metric: str,
    as_of: datetime,
    *,
    start: date | None = None,
    end: date | None = None,
    bounds: FredSeries | None = None,
) -> Snapshot:
    """Every period of `metric` as it was known at `as_of`: the latest revision with
    `available_at <= as_of`, periods with none absent, oldest period first. `start` and `end`
    bound the PERIODS (inclusive). An empty snapshot is not an error; read `coverage` to see whether
    `as_of` precedes the record."""
    _require_aware(as_of)
    bounds = bounds or bounds_for(metric)
    query = (
        select(*_COLUMNS)
        .where(MacroMetric.metric == metric, MacroMetric.available_at <= as_of)
        .distinct(MacroMetric.effective_at)
        .order_by(MacroMetric.effective_at, MacroMetric.revision.desc())
    )
    if start is not None:
        query = query.where(MacroMetric.effective_at >= start)
    if end is not None:
        query = query.where(MacroMetric.effective_at <= end)
    async with engine.connect() as conn:
        rows = (await conn.execute(query)).all()
    return Snapshot(metric, as_of, tuple(_observation(r) for r in rows), _coverage(bounds, as_of))


async def value_as_of(
    engine: AsyncEngine,
    metric: str,
    period: date,
    as_of: datetime,
    *,
    bounds: FredSeries | None = None,
) -> Observation | Missing:
    """`period` of `metric` as it was known at `as_of`, or a `Missing` that says which of the
    different kinds of no it is (see the module docstring, in decision order)."""
    _require_aware(as_of)
    bounds = bounds or bounds_for(metric)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(*_COLUMNS)
                .where(
                    MacroMetric.metric == metric,
                    MacroMetric.effective_at == period,
                    MacroMetric.available_at <= as_of,
                )
                .order_by(MacroMetric.revision.desc())
                .limit(1)
            )
        ).first()
        if row is not None:
            return _observation(row)
        any_row = (
            await conn.execute(
                select(func.count())
                .select_from(MacroMetric)
                .where(MacroMetric.metric == metric, MacroMetric.effective_at == period)
            )
        ).scalar_one()
        metric_rows = (
            await conn.execute(
                select(func.count()).select_from(MacroMetric).where(MacroMetric.metric == metric)
            )
        ).scalar_one()

    if bounds is None and metric_rows == 0:
        return Missing(Reason.UNKNOWN_METRIC, f"{metric}: no bounds configured and nothing stored")
    if bounds is not None:
        if as_of < available_at_for(bounds.first_vintage):
            return Missing(
                Reason.BEFORE_VINTAGE_RECORD,
                f"{metric}: nothing could be known before {bounds.first_vintage}, "
                "its first vintage",
            )
        if period < bounds.first_covered_period:
            return Missing(
                Reason.PREDATES_VINTAGE_RECORD,
                f"{metric} {period}: earlier than {bounds.first_covered_period}, the first period "
                "with a publication date; a value exists upstream but cannot be dated",
            )
    if any_row:
        return Missing(
            Reason.NOT_PUBLISHED_AS_OF,
            f"{metric} {period}: known, but no revision was published at or before {as_of}",
        )
    return Missing(
        Reason.UNKNOWN_PERIOD,
        f"{metric} {period}: no revision of this period exists at any time",
    )


async def latest_as_of(
    engine: AsyncEngine, metric: str, as_of: datetime, *, bounds: FredSeries | None = None
) -> Observation | Missing:
    """The newest period known at `as_of`, at the revision current then. If that period is a
    published gap the gap is returned (`value=None`): the caller decides what a gap means, nothing
    here walks back to an older number."""
    _require_aware(as_of)
    bounds = bounds or bounds_for(metric)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(*_COLUMNS)
                .where(MacroMetric.metric == metric, MacroMetric.available_at <= as_of)
                .order_by(MacroMetric.effective_at.desc(), MacroMetric.revision.desc())
                .limit(1)
            )
        ).first()
        if row is not None:
            return _observation(row)
        metric_rows = (
            await conn.execute(
                select(func.count()).select_from(MacroMetric).where(MacroMetric.metric == metric)
            )
        ).scalar_one()
    if bounds is None and metric_rows == 0:
        return Missing(Reason.UNKNOWN_METRIC, f"{metric}: no bounds configured and nothing stored")
    if bounds is not None and as_of < available_at_for(bounds.first_vintage):
        return Missing(
            Reason.BEFORE_VINTAGE_RECORD,
            f"{metric}: nothing could be known before {bounds.first_vintage} (its first vintage)",
        )
    return Missing(Reason.NOT_PUBLISHED_AS_OF, f"{metric}: nothing published at or before {as_of}")


async def revisions_of(
    engine: AsyncEngine, metric: str, period: date, *, as_of: datetime | None
) -> tuple[Observation, ...]:
    """The revisions of one period in order (0 first). **`as_of=None` is NOT a point-in-time read.**

    `as_of` is a REQUIRED keyword that may be None, on purpose: the whole-chain read has to be
    written out, `revisions_of(..., as_of=None)`, at every call site, where it is visible in the
    diff that adds it. A default would let `revisions_of(engine, m, p)` read like the other three
    functions and quietly hand a signal revisions published after the instant it is reasoning about.

    With `as_of=None` this returns the WHOLE chain, including revisions published after any T the
    caller has in mind: it can leak post-T knowledge. That is deliberate (diagnosis; the
    vintage-repair runbook; M5's hindsight questions) and it is why this function is not named
    `_as_of`. With an `as_of` it returns only revisions published at or before it (the chain as it
    stood). Never pass `as_of=None` from a signal, a score, a brief or a replay: that is look-ahead
    (see the module docstring). Empty if there are none.
    """
    if as_of is not None:
        _require_aware(as_of)
    query = (
        select(*_COLUMNS)
        .where(MacroMetric.metric == metric, MacroMetric.effective_at == period)
        .order_by(MacroMetric.revision)
    )
    if as_of is not None:
        query = query.where(MacroMetric.available_at <= as_of)
    async with engine.connect() as conn:
        return tuple(_observation(r) for r in (await conn.execute(query)).all())

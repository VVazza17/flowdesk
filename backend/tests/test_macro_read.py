"""M2 slice 5, step 4: the point-in-time read (app/services/macro_read.py), on a real Postgres.

Synthetic chains with chosen `available_at` instants, so every boundary is a number in the test.
The recorded-data oracles are in test_macro_oracle.py; the live one in test_macro_oracle_live.py.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.macro import FredSeries, available_at_for
from app.services.macro_read import (
    Missing,
    Observation,
    Reason,
    latest_as_of,
    revisions_of,
    series_as_of,
    value_as_of,
)

NY = ZoneInfo("America/New_York")
D = Decimal
P1, P2, P3 = date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1)
BOUNDS = FredSeries("M", first_vintage=date(2020, 1, 20), first_covered_period=date(2020, 1, 1))


def go(scratch_url: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(scratch_url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def put(
    engine: AsyncEngine,
    metric: str,
    period: date,
    revision: int,
    value: Decimal | None,
    published: date,
) -> None:
    """A row published on `published`: available at the end of that day in New York."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO macro_metrics (metric, effective_at, revision, value, available_at, "
                "source) VALUES (:m, :p, :r, :v, :a, 'fred')"
            ),
            {"m": metric, "p": period, "r": revision, "v": value, "a": available_at_for(published)},
        )


def at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=NY)


def observed(result: Observation | Missing) -> Decimal | None:
    assert isinstance(result, Observation), result
    return result.value


def vals(snap: Any) -> dict[date, Decimal | None]:
    return {o.effective_at: o.value for o in snap.observations}


# ---- the rule: existence, not value ----------------------------------------------------------


def test_a_value_is_visible_from_the_instant_it_became_available_and_not_a_microsecond_before(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        # Published well after the series' first vintage (2020-01-20), so one microsecond before it
        # is inside the record: that is "not published yet", not "before the record".
        await put(engine, "M", P1, 0, D("1.5"), date(2020, 2, 10))
        t = available_at_for(date(2020, 2, 10))
        return (
            await value_as_of(engine, "M", P1, t - timedelta(microseconds=1), bounds=BOUNDS),
            await value_as_of(engine, "M", P1, t, bounds=BOUNDS),
            await value_as_of(engine, "M", P1, t + timedelta(microseconds=1), bounds=BOUNDS),
        )

    before, exact, after = go(scratch_url, fn)
    assert isinstance(before, Missing) and before.reason is Reason.NOT_PUBLISHED_AS_OF
    assert isinstance(exact, Observation) and exact.value == D("1.5")  # inclusive: <= as_of
    assert isinstance(after, Observation) and after.revision == 0


def test_existence_is_filtered_not_just_value_a_later_print_is_absent_not_stale(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> list[Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        await put(engine, "M", P1, 1, D("1.1"), date(2020, 3, 10))  # revised later
        await put(engine, "M", P2, 0, D("2.0"), date(2020, 3, 10))  # first printed later
        return [
            vals(await series_as_of(engine, "M", at(day), bounds=BOUNDS))
            for day in (date(2020, 1, 25), date(2020, 3, 9), date(2020, 3, 11))
        ]

    jan, early_march, late_march = go(scratch_url, fn)
    assert jan == {P1: D("1.0")}  # P2 does not exist yet: absent, not present with a stale number
    assert early_march == {P1: D("1.0")}  # the revision is not visible the day before it
    assert late_march == {P1: D("1.1"), P2: D("2.0")}  # the latest revision <= as_of, both periods


def test_a_stored_null_is_a_published_gap_an_answer_not_an_absence(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        await put(engine, "M", P1, 0, None, date(2020, 1, 20))  # FRED published "."
        return (
            await value_as_of(engine, "M", P1, at(date(2020, 1, 25)), bounds=BOUNDS),
            await value_as_of(engine, "M", P2, at(date(2020, 1, 25)), bounds=BOUNDS),
        )

    gap, never = go(scratch_url, fn)
    assert isinstance(gap, Observation) and gap.value is None  # "FRED said no value"
    assert isinstance(never, Missing)  # "we have nothing", a different statement


# ---- the different kinds of no ---------------------------------------------------------------


def test_the_three_kinds_of_no_answer_are_three_different_answers(scratch_url: str) -> None:
    """Unknown period, predates the vintage record, nothing published as of T: the point of
    `first_covered_period` is to keep the middle one distinct."""

    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        await put(engine, "M", P2, 0, D("2.0"), date(2020, 3, 10))
        t = at(date(2020, 2, 1))  # after the record begins (2020-01-20), before P2's print
        return {
            "not_yet": await value_as_of(engine, "M", P2, t, bounds=BOUNDS),
            "predates": await value_as_of(engine, "M", date(2019, 6, 1), t, bounds=BOUNDS),
            "unknown": await value_as_of(engine, "M", date(2020, 2, 15), t, bounds=BOUNDS),
        }

    out = go(scratch_url, fn)
    assert out["not_yet"].reason is Reason.NOT_PUBLISHED_AS_OF  # a period we know, at a T too early
    assert out["predates"].reason is Reason.PREDATES_VINTAGE_RECORD  # upstream has it; undatable
    assert out["unknown"].reason is Reason.UNKNOWN_PERIOD  # no revision of this date, ever
    assert len({m.reason for m in out.values()}) == 3


def test_without_first_covered_period_a_predating_period_would_read_as_unknown(
    scratch_url: str,
) -> None:
    """Why the boundary is config and not inferred: with no bounds the same question has only the
    bounds-free answer, so 'a number exists but cannot be dated' and 'there is no such date'
    collapse into one."""

    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        await put(engine, "M", P2, 0, D("2.0"), date(2020, 3, 10))
        t = at(date(2020, 4, 1))
        old = date(2019, 6, 1)
        return (
            await value_as_of(engine, "M", old, t, bounds=BOUNDS),
            await value_as_of(engine, "M", old, t, bounds=None),
        )

    with_bounds, without = go(scratch_url, fn)
    assert with_bounds.reason is Reason.PREDATES_VINTAGE_RECORD
    assert without.reason is Reason.UNKNOWN_PERIOD


def test_as_of_before_the_record_is_its_own_answer_for_any_period(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        before = at(date(2020, 1, 10))  # earlier than the series' first vintage (2020-01-20)
        return (
            await value_as_of(engine, "M", P1, before, bounds=BOUNDS),
            await value_as_of(engine, "M", date(2019, 6, 1), before, bounds=BOUNDS),
            await series_as_of(engine, "M", before, bounds=BOUNDS),
        )

    known_period, predating_period, snap = go(scratch_url, fn)
    assert known_period.reason is Reason.BEFORE_VINTAGE_RECORD
    # Decision order: a property of T is decided before one of P, so the more basic fact wins.
    assert predating_period.reason is Reason.BEFORE_VINTAGE_RECORD
    assert snap.observations == () and snap.coverage.as_of_precedes_record is True


def test_unknown_metric_is_distinct_from_a_known_metric_with_nothing_yet(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        return (
            await value_as_of(engine, "NOSUCH", P1, at(date(2020, 6, 1))),
            await latest_as_of(engine, "NOSUCH", at(date(2020, 6, 1))),
            await latest_as_of(engine, "M", at(date(2020, 1, 25)), bounds=BOUNDS),
        )

    unknown, unknown_latest, empty = go(scratch_url, fn)
    assert (
        unknown.reason is Reason.UNKNOWN_METRIC and unknown_latest.reason is Reason.UNKNOWN_METRIC
    )
    assert empty.reason is Reason.NOT_PUBLISHED_AS_OF  # bounds known, T inside the record, no rows


def test_coverage_tells_the_caller_where_the_record_starts(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        return (
            await series_as_of(engine, "M", at(date(2020, 2, 1)), bounds=BOUNDS),
            await series_as_of(engine, "UNBOUNDED", at(date(2020, 2, 1))),
        )

    known, unknown = go(scratch_url, fn)
    assert (known.coverage.first_vintage, known.coverage.first_covered_period) == (
        date(2020, 1, 20),
        date(2020, 1, 1),
    )
    assert known.coverage.known and not known.coverage.as_of_precedes_record
    assert not unknown.coverage.known and unknown.coverage.first_vintage is None


# ---- the series read, the latest read, the chain ---------------------------------------------


def test_series_as_of_is_ordered_and_start_and_end_bound_the_periods_inclusively(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        for period in (P3, P1, P2):  # inserted out of order on purpose
            await put(engine, "M", period, 0, D(period.month), date(2020, 3, 20))
        t = at(date(2020, 4, 1))
        return (
            await series_as_of(engine, "M", t, bounds=BOUNDS),
            await series_as_of(engine, "M", t, start=P2, end=P2, bounds=BOUNDS),
        )

    full, only_feb = go(scratch_url, fn)
    assert [o.effective_at for o in full.observations] == [P1, P2, P3]
    assert [o.effective_at for o in only_feb.observations] == [P2]


def test_latest_as_of_is_the_newest_period_known_then_at_the_revision_current_then(
    scratch_url: str,
) -> None:
    async def fn(engine: AsyncEngine) -> list[Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        await put(engine, "M", P2, 0, D("2.0"), date(2020, 2, 20))
        await put(engine, "M", P2, 1, D("2.5"), date(2020, 3, 20))
        await put(engine, "M", P3, 0, None, date(2020, 3, 25))  # newest period is a published gap
        return [
            await latest_as_of(engine, "M", at(day), bounds=BOUNDS)
            for day in (date(2020, 1, 25), date(2020, 3, 1), date(2020, 3, 21), date(2020, 3, 30))
        ]

    jan, march1, march21, march30 = go(scratch_url, fn)
    assert (jan.effective_at, jan.value) == (P1, D("1.0"))
    assert (march1.effective_at, march1.revision, march1.value) == (P2, 0, D("2.0"))
    assert (march21.effective_at, march21.revision, march21.value) == (P2, 1, D("2.5"))
    # The newest period is a gap and the gap is what comes back: nothing here walks back to an
    # older number for the caller.
    assert (march30.effective_at, march30.value) == (P3, None)


def test_a_daily_series_read_at_the_open_resolves_to_the_prior_sessions_value(
    scratch_url: str,
) -> None:
    """ADR-0003 decision 6, with no code of its own: available_at is the end of the publication
    day in New York, so 09:30 on day D cannot see D's own print."""
    monday, tuesday = date(2026, 11, 9), date(2026, 11, 10)

    async def fn(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        await put(engine, "M", monday, 0, D("4.10"), monday)  # Monday's reading, published Monday
        await put(engine, "M", tuesday, 0, D("4.20"), tuesday)
        return (
            await latest_as_of(engine, "M", at(tuesday, hour=9), bounds=None),
            await latest_as_of(engine, "M", at(date(2026, 11, 11), hour=9), bounds=None),
            await latest_as_of(engine, "M", at(monday, hour=9), bounds=None),
        )

    tue_open, wed_open, mon_open = go(scratch_url, fn)
    assert tue_open.effective_at == monday  # Tuesday 09:30 sees Monday, not Tuesday
    assert wed_open.effective_at == tuesday
    assert isinstance(mon_open, Missing)  # nothing published before Monday's own close


def test_revisions_of_returns_the_chain_in_order_optionally_as_it_stood(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        await put(engine, "M", P1, 1, D("1.1"), date(2020, 3, 10))
        await put(engine, "M", P1, 2, D("1.2"), date(2020, 5, 10))
        return (
            await revisions_of(engine, "M", P1, as_of=None),
            await revisions_of(engine, "M", P1, as_of=at(date(2020, 4, 1))),
            await revisions_of(engine, "M", P2, as_of=None),
        )

    full, as_it_stood, none = go(scratch_url, fn)
    assert [(o.revision, o.value) for o in full] == [(0, D("1.0")), (1, D("1.1")), (2, D("1.2"))]
    assert [o.revision for o in as_it_stood] == [0, 1]
    assert none == ()


def test_a_naive_as_of_is_refused_by_every_read(scratch_url: str) -> None:
    naive = datetime(2020, 6, 1, 12, 0)

    async def fn(engine: AsyncEngine) -> None:
        for call in (
            lambda: series_as_of(engine, "M", naive),
            lambda: value_as_of(engine, "M", P1, naive),
            lambda: latest_as_of(engine, "M", naive),
            lambda: revisions_of(engine, "M", P1, as_of=naive),
        ):
            with pytest.raises(ValueError, match="timezone-aware"):
                await call()

    go(scratch_url, fn)


def test_an_equal_instant_in_another_zone_is_the_same_instant(scratch_url: str) -> None:
    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        t = available_at_for(date(2020, 1, 20))  # 2020-01-21 05:00 UTC
        return (
            await value_as_of(engine, "M", P1, t.astimezone(UTC), bounds=BOUNDS),
            await value_as_of(engine, "M", P1, t.astimezone(ZoneInfo("Asia/Tokyo")), bounds=BOUNDS),
        )

    utc, tokyo = go(scratch_url, fn)
    assert isinstance(utc, Observation) and isinstance(tokyo, Observation)


# ---- CLAUDE.md rule 9: two keys ---------------------------------------------------------------


def test_one_metrics_reads_never_return_another_metrics_rows(scratch_url: str) -> None:
    """Two metrics over the SAME periods and instants, where the OTHER metric's rows would WIN every
    ordering a read applies: B has a higher revision of the same period and a newer period. A read
    that lost its `WHERE metric` would return B's numbers.

    A first version gave both metrics chains of the same shape, and mutation showed that
    `value_as_of` and `latest_as_of` could drop the filter and still pass: with ties the right
    answer arrives by luck. The other key's data must be able to WIN (CLAUDE.md rule 9).
    """

    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        await put(engine, "A", P1, 0, D("10"), date(2020, 1, 20))
        await put(engine, "A", P1, 1, D("11"), date(2020, 3, 10))
        await put(engine, "A", P2, 0, D("15"), date(2020, 3, 10))
        await put(engine, "B", P1, 0, D("20"), date(2020, 1, 20))
        await put(engine, "B", P1, 1, D("21"), date(2020, 3, 10))
        await put(
            engine, "B", P1, 2, D("22"), date(2020, 3, 20)
        )  # a higher revision of the same period
        await put(engine, "B", P2, 0, D("25"), date(2020, 3, 10))
        await put(engine, "B", P3, 0, D("30"), date(2020, 3, 20))  # a newer period
        t = at(date(2020, 4, 1))
        a = BOUNDS
        return {
            "series": vals(await series_as_of(engine, "A", t, bounds=a)),
            "value": observed(await value_as_of(engine, "A", P1, t, bounds=a)),
            "latest": observed(await latest_as_of(engine, "A", t, bounds=a)),
            "revisions": [o.value for o in await revisions_of(engine, "A", P1, as_of=None)],
            "b_series": vals(await series_as_of(engine, "B", t, bounds=a)),
            "b_value": observed(await value_as_of(engine, "B", P1, t, bounds=a)),
            "b_latest": observed(await latest_as_of(engine, "B", t, bounds=a)),
        }

    out = go(scratch_url, fn)
    assert out["series"] == {P1: D("11"), P2: D("15")}  # no P3, no revision 2
    assert out["value"] == D("11")  # not B's revision 2
    assert out["latest"] == D("15")  # not B's newer period
    assert out["revisions"] == [D("10"), D("11")]
    assert out["b_series"] == {P1: D("22"), P2: D("25"), P3: D("30")}
    assert out["b_value"] == D("22") and out["b_latest"] == D("30")


def test_revisions_of_without_as_of_is_the_whole_chain_and_says_so_where_it_is_read(
    scratch_url: str,
) -> None:
    """It is the one function that can hand a caller revisions published after the instant the
    caller is reasoning about. That is deliberate, so it is stated in the module docstring and in
    its own, and pinned here: both the leak and the warning must survive."""
    from app.services import macro_read

    async def fn(engine: AsyncEngine) -> tuple[Any, Any]:
        await put(engine, "M", P1, 0, D("1.0"), date(2020, 1, 20))
        await put(engine, "M", P1, 1, D("9.9"), date(2021, 1, 20))  # published long after T
        t = at(date(2020, 6, 1))
        return (
            await revisions_of(engine, "M", P1, as_of=None),
            await revisions_of(engine, "M", P1, as_of=t),
        )

    whole, as_it_stood = go(scratch_url, fn)
    assert [o.value for o in whole] == [D("1.0"), D("9.9")]  # the post-T revision IS returned
    assert [o.value for o in as_it_stood] == [D("1.0")]  # and is not, once as_of is passed
    assert "CAN LEAK POST-T KNOWLEDGE" in (macro_read.__doc__ or "")
    doc = macro_read.revisions_of.__doc__ or ""
    assert "NOT a point-in-time read" in doc and "look-ahead" in doc


def test_the_whole_chain_read_must_be_spelled_out_it_cannot_be_reached_by_omission(
    scratch_url: str,
) -> None:
    """`as_of` is a required keyword. `revisions_of(engine, m, p)` is a TypeError, not a quiet
    hindsight read that looks like the other three functions."""

    async def fn(engine: AsyncEngine) -> None:
        with pytest.raises(TypeError, match="as_of"):
            await revisions_of(engine, "M", P1)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            await revisions_of(engine, "M", P1, at(date(2020, 4, 1)))  # type: ignore[call-arg]

    go(scratch_url, fn)

"""The pure rules of the macro (FRED) ingest (M2 slice 5, step 3): no database, no HTTP, no clock.

Everything here is a function of its arguments so that the rules the point-in-time history depends
on can be tested exhaustively and cannot drift apart from the code that calls them.

THE RULE THESE FUNCTIONS EXIST TO ENFORCE: nothing enters a point-in-time table without a real
publication date. A value whose publication date nobody can state is not "known on some day we
chose"; it is not point-in-time data at all, and storing it with a guessed `available_at` would make
every replay that touches it show a value as knowable when it was not. (Settled 2026-11-12; slice 6
meets the same rule again with prices. docs/adr/0003, addendum.)
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# FRED refuses a real-time window holding more than 2000 vintage dates (HTTP 400, measured
# 2026-11-12). 1,900 leaves a margin: the error text counts one fewer than series/vintagedates does,
# and the series grow.
VINTAGE_DATE_CAP = 2000
VINTAGE_CHUNK = 1900


@dataclass(frozen=True)
class FredSeries:
    """One series of 6.4 and the two dates that bound its point-in-time record (MEASURED, not
    derived: FRED states neither).

    `first_vintage`: the earliest date FRED has a vintage for. No as-of date before it can be
    answered from FRED at all.
    `first_covered_period`: the earliest period (`effective_at`) that `output_type=4` returns. Every
    earlier period exists in FRED's latest values and has NO publication date, so it is deliberately
    not in macro_metrics. A point-in-time read that finds no row for a period earlier than this is
    looking at "a value exists but predates the vintage record", not at "no value exists".
    """

    series_id: str
    first_vintage: date
    first_covered_period: date


def available_at_for(realtime_start: date) -> datetime:
    """When a value published on `realtime_start` became knowable, as an instant in UTC.

    FRED gives a DATE and no time of day, so the rule (ADR-0003 addendum, 2026-11-11) is the end
    of the publication day in America/New_York, which is 00:00 of the next day there. That is
    never earlier than the truth, so a replay cannot be told about a value too soon; it can be
    told up to 24 hours late (a documented bias, first prints only). Built from local midnight
    so that the 23- and 25-hour days at the clock changes come out right.
    """
    next_day = realtime_start + timedelta(days=1)
    return datetime.combine(next_day, time(0, 0), tzinfo=NEW_YORK).astimezone(UTC)


def chunk_vintage_dates(
    dates: Sequence[date], size: int = VINTAGE_CHUNK
) -> list[tuple[date, date]]:
    """Split sorted vintage dates into [first, last] real-time windows of at most `size` dates.

    The windows are disjoint and together cover every vintage date, which is what makes the union
    of the per-window answers the whole answer. An empty series has no windows.
    """
    if size < 1 or size > VINTAGE_DATE_CAP:
        raise ValueError(f"size must be between 1 and {VINTAGE_DATE_CAP}")
    if list(dates) != sorted(set(dates)):
        raise ValueError("vintage dates must be sorted and distinct")
    return [
        (chunk[0], chunk[-1]) for chunk in (dates[i : i + size] for i in range(0, len(dates), size))
    ]


@dataclass(frozen=True)
class Accounting:
    """Where every date in the latest values went, and what could not be explained."""

    pre_vintage: int  # earlier than the first first-print: no publication date, left out
    gaps_without_trace: int  # "." in the latest values, never published as anything: left out
    unaccounted: tuple[date, ...]  # a real value with no first print: the backfill refuses
    not_in_latest: tuple[date, ...]  # a first print the latest values lack: the backfill refuses

    @property
    def complete(self) -> bool:
        return not self.unaccounted and not self.not_in_latest


def account_for(
    latest: Mapping[date, Decimal | None], first_print_dates: Collection[date]
) -> Accounting:
    """Compare the first prints with the latest values. This is the check that does not trust
    FRED's response to be complete.

    `output_type=4` does not say what it left out (measured 2026-11-12). A date in the latest
    values is explained if (a) it has a first print; (b) it is earlier than the earliest first
    print (pre-vintage history); or (c) its latest value is a gap ("."), which FRED never
    published as anything. Anything else is a value that should have a first print and does not.
    """
    have = set(first_print_dates)
    first = min(have) if have else None
    pre_vintage = gaps = 0
    unaccounted: list[date] = []
    for day, value in latest.items():
        if day in have:
            continue
        if first is not None and day < first:
            pre_vintage += 1
        elif value is None:
            gaps += 1
        else:
            unaccounted.append(day)
    return Accounting(
        pre_vintage=pre_vintage,
        gaps_without_trace=gaps,
        unaccounted=tuple(sorted(unaccounted)),
        not_in_latest=tuple(sorted(have - latest.keys())),
    )

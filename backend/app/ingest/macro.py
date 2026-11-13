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

# The window the revision-history fetch (output_type=3) asks for, in vintage dates. MEASURED
# 2026-11-13: FRED spends about 0.16 s per vintage date in the window (100 dates 17 s, 300 dates
# 48 s) and its gateway answers 504 at 60 s (600 dates and up, and the 1,900 above). 100 leaves a
# 3.5x margin for a slower FRED; a window that still times out is halved by the client
# (FredClient.revision_history). Not an operator setting: it is a property of FRED's gateway, and a
# knob would invite setting it to the cap.
HISTORY_WINDOW = 100

# Bumped whenever the rules in `check_history` change, so that every series captured under older
# rules is captured again (the backfill's skip compares it, commit B). Changing a rule without
# bumping this leaves history that no longer satisfies the rules marked as satisfying them.
FRED_RULE_VERSION = 1


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


def vintage_for(available_at: datetime) -> date:
    """The publication date a stored `available_at` came from: the inverse of `available_at_for`.

    `available_at_for(d)` is 00:00 New York the day after `d`, so one microsecond earlier is still
    day `d` there. This is how stored revisions are compared with fetched ones (rule 6).
    """
    return (available_at - timedelta(microseconds=1)).astimezone(NEW_YORK).date()


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


# --------------------------------------------------------------------------------------------
# The revision history (ADR-0003 addendum, 2026-11-13). Everything below is pure.
#
# This is the only accounting. `account_for`, the first-prints-only one it replaced, is gone: two
# accountings side by side would drift apart.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FredVintageRow:
    """One row of `output_type=3`: a period and every vintage in which it changed.

    FRED's wide format is `{"date": ..., "<SERIES>_<YYYYMMDD>": value, ...}`; `values` is that
    mapping with the keys turned into the vintage dates (publication dates) they name.
    """

    effective_at: date
    values: Mapping[date, Decimal | None]


@dataclass(frozen=True)
class Revision:
    """One step of a period's history: its value as of one vintage. Position in the chain is the
    revision number (0 is the first print)."""

    vintage: date
    value: Decimal | None


@dataclass(frozen=True)
class FirstPrintRef:
    """What `output_type=4` says a period's first print was: the independent witness of rule 1."""

    vintage: date
    value: Decimal | None


@dataclass(frozen=True)
class Refusal:
    """One rule that failed. Fixed text and dates only: nothing FRED sent is copied into it."""

    rule: int
    count: int
    sample: tuple[date, ...]
    reason: str


@dataclass(frozen=True)
class HistoryVerdict:
    """The outcome of the six rules for one series.

    `chains` holds only what may be stored. The counts are kept apart on purpose (ADR-0003
    addendum, "Two exclusions, two rules, two counts"): A, no vintage exists, is
    `no_vintage_excluded` and `gaps_without_trace_skipped`; B, revised but pre-vintage, is
    `revised_pre_vintage_excluded`. There is deliberately no field that adds them.
    """

    chains: Mapping[date, tuple[Revision, ...]]
    refusals: tuple[Refusal, ...]
    first_prints: int
    revisions: int
    no_vintage_excluded: int
    gaps_without_trace_skipped: int
    revised_pre_vintage_excluded: int
    duplicates_skipped: int

    @property
    def ok(self) -> bool:
        return not self.refusals

    def refusal_text(self) -> str:
        """For an exception message: rule numbers, counts and the first few dates."""
        return "; ".join(
            f"rule {r.rule} ({r.reason}): {r.count} periods, from "
            f"{[d.isoformat() for d in r.sample]}"
            for r in self.refusals
        )


_SAMPLE = 3


def _refusal(rule: int, reason: str, bad: Collection[date]) -> Refusal:
    ordered = sorted(set(bad))
    return Refusal(rule, len(ordered), tuple(ordered[:_SAMPLE]), reason)


def check_history(
    *,
    rows: Sequence[FredVintageRow],
    vintage_dates: Collection[date],
    first_prints: Mapping[date, FirstPrintRef],
    latest: Mapping[date, Decimal | None],
    stored: Mapping[date, Sequence[Revision]] | None = None,
) -> HistoryVerdict:
    """The six rules of the ADR-0003 addendum (2026-11-13). Nothing may be written unless `ok`.

    Rule 1  the chain's earliest column equals the `output_type=4` first print (date and value).
    Rule 2  a chain begins at a first print, or its period is before the earliest first print: it is
            pre-vintage and revised, left out and counted (exclusion B).
    Rule 3  a chain ends at the latest value.
    Rule 4  every latest value is explained: it has a chain, or it is pre-vintage and unrevised
            (exclusion A), or it is a "." that no vintage ever published (exclusion A).
    Rule 5  structure: every column is a known vintage date, one period never has two values for one
            vintage, and a column equal to the one before it is not a revision (skipped, counted).
    Rule 6  what is already stored is a prefix of what was fetched (`stored`, if given).

    FRED's response never says what it left out, so none of this trusts completeness: it checks the
    outputs against each other. What it cannot see is a revision missing from the MIDDLE of a chain.
    """
    known = set(vintage_dates)
    raw: dict[date, dict[date, Decimal | None]] = {}
    conflicts: set[date] = set()
    unknown_vintage: set[date] = set()
    for row in rows:
        if not row.values:
            continue  # a period listed without a change in this window: no information
        per = raw.setdefault(row.effective_at, {})
        for vintage, value in row.values.items():
            if vintage not in known:
                unknown_vintage.add(row.effective_at)
            if vintage in per and per[vintage] != value:
                conflicts.add(row.effective_at)
            per[vintage] = value

    chains: dict[date, tuple[Revision, ...]] = {}
    duplicates = 0
    for period, per in raw.items():
        built: list[Revision] = []
        for vintage, value in sorted(per.items()):
            if built and built[-1].value == value:
                duplicates += 1  # not a revision (ADR-0003 decision 3: only when the value differs)
                continue
            built.append(Revision(vintage, value))
        chains[period] = tuple(built)

    refusals: list[Refusal] = []
    if conflicts or unknown_vintage:
        refusals.append(
            _refusal(
                5,
                "a period has two values for one vintage, or a column is no vintage of the series",
                conflicts | unknown_vintage,
            )
        )

    # Rule 1.
    bad1 = [
        period
        for period, ref in first_prints.items()
        if period not in chains or chains[period][0] != Revision(ref.vintage, ref.value)
    ]
    if bad1:
        refusals.append(
            _refusal(1, "output_type=3 and output_type=4 disagree on the first print", bad1)
        )

    # Rule 2: split chains into storable and revised-but-pre-vintage.
    earliest = min(first_prints) if first_prints else None
    kept: dict[date, tuple[Revision, ...]] = {}
    revised_pre_vintage: set[date] = set()
    lost_first_print: list[date] = []
    for period, chain in chains.items():
        if period in first_prints:
            kept[period] = chain
        elif earliest is not None and period < earliest:
            revised_pre_vintage.add(period)
        else:
            lost_first_print.append(period)
    if lost_first_print:
        refusals.append(
            _refusal(
                2,
                "a chain has no first print although its period is inside the record",
                lost_first_print,
            )
        )

    # Rule 3.
    bad3 = [
        period
        for period, chain in kept.items()
        if period not in latest or chain[-1].value != latest[period]
    ]
    if bad3:
        refusals.append(_refusal(3, "a chain does not end at the latest value", bad3))

    # Rule 4.
    no_vintage = gaps = 0
    unexplained: list[date] = []
    for period, value in latest.items():
        if period in chains:
            continue
        if earliest is not None and period < earliest:
            no_vintage += 1
        elif value is None:
            gaps += 1
        else:
            unexplained.append(period)
    if unexplained:
        refusals.append(_refusal(4, "a latest value has no history", unexplained))

    # Rule 6.
    if stored:
        bad6 = [
            period
            for period, have in stored.items()
            if period not in kept
            or len(have) > len(kept[period])
            or tuple(kept[period][: len(have)]) != tuple(have)
        ]
        if bad6:
            refusals.append(
                _refusal(6, "stored history is not a prefix of the fetched history", bad6)
            )

    return HistoryVerdict(
        chains=kept,
        refusals=tuple(sorted(refusals, key=lambda r: r.rule)),
        first_prints=len(kept),
        revisions=sum(len(c) - 1 for c in kept.values()),
        no_vintage_excluded=no_vintage,
        gaps_without_trace_skipped=gaps,
        revised_pre_vintage_excluded=len(revised_pre_vintage),
        duplicates_skipped=duplicates,
    )

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
from dataclasses import dataclass, field
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

# Bump when a change to `check_history` makes the rules STRICTER or changes WHAT IS STORED, so that
# every series captured under the older rules is captured again (the backfill's skip compares it).
# For a RELAXATION, first count whether any captured series would now store differently, and bump
# if any would (ADR-0003 addendum, F2: the VIXCLS gap-first-print amendment changed nothing already
# stored, checked from data, and did not bump). Leaving a stricter rule unbumped leaves history that
# no longer satisfies the rules it is marked as satisfying.
#
# NOT bumped for the rolled-off exemption and F3 (2026-11-24), counted from data as the principle
# requires. Both are RELAXATIONS of what refused: rolled-off periods are stored rows left as they
# are, and a late arrival was always refused by rule 4, so no row of any of the 17 captured series
# was ever written as one. Nothing already stored would store differently. And a bump would make the
# backfill re-capture every series, which for BAMLC0A0CM and BAMLH0A0HYM2 would erase the history
# FRED no longer serves and the 2026-11-10 measurement (Kyle, 2026-11-24: never without his
# approval).
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
    # FRED serves this series only as a moving window: the oldest periods drop out of the latest
    # vintage, and their vintage dates out of series/vintagedates, as time passes (F-6b cause (i),
    # MEASURED 2026-11-20 and 2026-11-24: the earliest observation of BAMLC0A0CM and BAMLH0A0HYM2
    # moved from 2023-11-20 to 2023-11-24). Declared per series, never inferred from data: a series
    # not declared here that loses old periods still refuses. No window length is stated or used.
    # `first_vintage` and `first_covered_period` stay what FlowDesk STORES, which FRED no longer
    # serves (Kyle, 2026-11-24: that history exists only in FlowDesk).
    rolling_window: bool = False


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
    `no_vintage_excluded` and `gaps_without_o4_first_print_skipped`; B, revised but pre-vintage, is
    `revised_pre_vintage_excluded`. There is deliberately no field that adds them.
    """

    chains: Mapping[date, tuple[Revision, ...]]
    refusals: tuple[Refusal, ...]
    first_prints: int
    revisions: int
    no_vintage_excluded: int
    gaps_without_o4_first_print_skipped: int
    gap_first_prints_without_o4_witness: int  # stored; see rule 2
    revised_pre_vintage_excluded: int
    duplicates_skipped: int
    # Stored periods of a rolling-window series that FRED no longer serves: kept as stored, judged
    # by no rule that compares them with FRED's current record (rules 3 and 5), never extended.
    rolled_off: int = 0
    # F3 late arrivals accepted by this verdict: period -> the vintage FRED claims first printed it.
    # Their chain is that one value; the store dates it by FlowDesk's observation, never by this
    # vintage, which goes only to the audit column (ADR-0003 addendum F3).
    late_arrivals: Mapping[date, date] = field(default_factory=dict)

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
    rolling_window: bool = False,
    late_since: date | None = None,
    late_evidence: Mapping[date, FredVintageRow] | None = None,
) -> HistoryVerdict:
    """The six rules of the ADR-0003 addendum (2026-11-13). Nothing may be written unless `ok`.

    Rule 1  the chain's earliest column equals the `output_type=4` first print (date and value).
    Rule 2  a chain begins at a first print, or its period is before the earliest first print (it
            is pre-vintage and revised: left out and counted, exclusion B), or it begins with a
            "." that `output_type=4` does not report (a gap first print: stored, counted apart as
            `gap_first_prints_without_o4_witness`). A chain beginning with a number and missing
            from `output_type=4` is a lost first print and refuses.
    Rule 3  a chain ends at the latest value. Except, for a `rolling_window` series only, a stored
            period that has ROLLED OFF (below).
    Rule 4  every latest value is explained: it has a chain, or it is pre-vintage and unrevised
            (exclusion A), or it is a "." found in neither output (exclusion A), or it is a LATE
            ARRIVAL confirmed by its own fetch (F3, below; only when `late_since` is given).
    Rule 5  structure: every column is a known vintage date, one period never has two values for one
            vintage, and a column equal to the one before it is not a revision (skipped, counted).
            Except a rolled-off period's column whose vintage is older than FRED's earliest listed
            vintage date (rolling-window series only).
    Rule 6  what is already stored is a prefix of what was fetched (`stored`, if given).

    ROLLED OFF (F-6b cause (i); Kyle, 2026-11-24, rule 5 the same day). For a series declared
    `rolling_window`, a stored period older than the latest vintage's EARLIEST observation, whose
    merged chain is exactly the stored one, has rolled off FRED's window. Every such period is older
    than the earliest observation, so they are by construction one unbroken run at the old end; a
    stored period missing from the latest vintage anywhere else is a gap and still fails rule 3. The
    stored rows are untouched: nothing new is written for a rolled-off period, and one that the
    fetch tries to extend is not rolled off. No window length appears here.

    LATE ARRIVAL (ADR-0003 addendum F3, rule (c) as corrected by Kyle 2026-11-20). With `late_since`
    (the series' `captured_through`): a period in the latest values, with a number, on or before
    `late_since`, inside the record, and with no chain (no stored history and nothing in the
    window). It is accepted only if `late_evidence[period]` (that period's own fetch across the
    vintages from the period on) holds at least one column, every column is a known vintage, all
    agree on one value, and that value equals the latest. ABSENCE NEVER COUNTS AS AGREEMENT: no
    evidence, or an empty fetch, refuses under rule 4, named. Accepted, its chain is that one value
    at FRED's claimed vintage (the earliest column), reported in `late_arrivals`; the store dates it
    by FlowDesk's observation instead.

    FRED's response never says what it left out, so none of this trusts completeness: it checks the
    outputs against each other. What it cannot see is a revision missing from the MIDDLE of a chain.
    """
    known = set(vintage_dates)
    raw: dict[date, dict[date, Decimal | None]] = {}
    conflicts: set[date] = set()
    unknown_columns: set[tuple[date, date]] = set()  # (period, vintage): judged after roll-off
    for row in rows:
        if not row.values:
            continue  # a period listed without a change in this window: no information
        per = raw.setdefault(row.effective_at, {})
        for vintage, value in row.values.items():
            if vintage not in known:
                unknown_columns.add((row.effective_at, vintage))
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
    gap_first_unwitnessed: set[date] = set()
    lost_first_print: list[date] = []
    for period, chain in chains.items():
        if period in first_prints:
            kept[period] = chain
        elif earliest is not None and period < earliest:
            revised_pre_vintage.add(period)
        elif earliest is not None and chain[0].value is None:
            # A gap first print (ADR-0003 addendum 2026-11-13, F2): `output_type=4` does not always
            # report a first print whose value is ".", so no second source corroborates it. The
            # chain has a real publication date (a published gap is a stored NULL, decision 7), is
            # still held to rule 3, and is counted apart so that nobody mistakes it for a witnessed
            # first print. A chain that starts with a NUMBER and lacks an `output_type=4` row still
            # refuses below: that is a lost first print.
            kept[period] = chain
            gap_first_unwitnessed.add(period)
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

    # Rolled off (rolling-window series only; see the docstring). Stored, older than the latest
    # vintage's earliest observation, and not extended by the fetch.
    rolled: set[date] = set()
    if rolling_window and stored and latest:
        oldest = min(latest)
        rolled = {
            period
            for period, have in stored.items()
            if period < oldest and period in kept and tuple(kept[period]) == tuple(have)
        }

    # Rule 5. A rolled-off period's column may name a vintage FRED no longer lists, provided that
    # vintage is older than every date FRED does list (it rolled off with the period).
    floor = min(known) if known else None
    unknown_vintage = {
        period
        for period, vintage in unknown_columns
        if not (period in rolled and floor is not None and vintage < floor)
    }
    if conflicts or unknown_vintage:
        refusals.append(
            _refusal(
                5,
                "a period has two values for one vintage, or a column is no vintage of the series",
                conflicts | unknown_vintage,
            )
        )

    # Late arrivals (F3): recognised here, judged on their own fetch, refused in rule 4 below.
    late_accepted: dict[date, date] = {}
    late_refused: set[date] = set()
    if late_since is not None:
        evidence = late_evidence or {}
        for period, value in latest.items():
            if period in chains or period > late_since or value is None:
                continue
            if earliest is not None and period < earliest:
                continue  # pre-vintage and unrevised: exclusion A, as before
            found = evidence.get(period)
            columns = (
                dict(found.values) if found is not None and found.effective_at == period else {}
            )
            agreed = set(columns.values())
            if (
                columns
                and all(vintage in known for vintage in columns)
                and len(agreed) == 1
                and agreed == {value}
            ):
                claimed = min(columns)
                kept[period] = (Revision(claimed, value),)
                late_accepted[period] = claimed
            else:
                late_refused.add(period)

    # Rule 3.
    bad3 = [
        period
        for period, chain in kept.items()
        if period not in rolled and (period not in latest or chain[-1].value != latest[period])
    ]
    if bad3:
        refusals.append(_refusal(3, "a chain does not end at the latest value", bad3))

    # Rule 4.
    no_vintage = gaps = 0
    unexplained: list[date] = []
    for period, value in latest.items():
        if period in chains or period in late_accepted:
            continue
        if earliest is not None and period < earliest:
            no_vintage += 1
        elif value is None:
            gaps += 1
        elif period not in late_refused:
            unexplained.append(period)
    if unexplained:
        refusals.append(_refusal(4, "a latest value has no history", unexplained))
    if late_refused:
        refusals.append(
            _refusal(
                4,
                "a late arrival (on or before captured_through, no stored history) is not "
                "confirmed by its own fetch",
                late_refused,
            )
        )

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
        gaps_without_o4_first_print_skipped=gaps,
        gap_first_prints_without_o4_witness=len(gap_first_unwitnessed),
        revised_pre_vintage_excluded=len(revised_pre_vintage),
        duplicates_skipped=duplicates,
        rolled_off=len(rolled),
        late_arrivals=late_accepted,
    )


def rows_from_chains(chains: Mapping[date, Sequence[Revision]]) -> list[FredVintageRow]:
    """What is already stored, as the wide rows FRED would have sent for it.

    The poll merges these with the rows of a short window and runs the SAME six rules over the
    result, so the stored history and the new columns are judged together (a stored column that the
    window contradicts is rule 5 or rule 6, a chain that no longer ends at the latest value is rule
    3) and the poll cannot drift from the backfill's rules.
    """
    return [
        FredVintageRow(period, {rev.vintage: rev.value for rev in chain})
        for period, chain in chains.items()
    ]

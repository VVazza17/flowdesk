"""F-6b's two causes, built together (2026-11-24): the rolled-off exemption (cause (i), rules 3
and 5) and ADR-0003 addendum F3, late arrivals (cause (ii), rule 4 with its own fetch). Pure: no
database.

The verdicts are built the way the poll builds them: the stored chains as rows, plus the window's
rows, with the stored first prints as their own witnesses. Rule 9 (CLAUDE.md): every exemption is
tested next to a period whose data would WIN if the code lost its condition (a stored period
missing in the middle, a rolled-off period the fetch extends, a second late period whose evidence
disagrees), and each condition is checked by mutation (`tools/mutate.py`, suite `f3-rolling`).
"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

import httpx2

from app import settings
from app.ingest.errors import SchemaMismatch
from app.ingest.fred import FredClient
from app.ingest.fred_feed import late_candidates
from app.ingest.macro import (
    FirstPrintRef,
    FredVintageRow,
    HistoryVerdict,
    Revision,
    check_history,
    rows_from_chains,
)

D = Decimal
# Periods, oldest first. A1 and A2 roll off; B, M, C stay; N is new in the window.
A1, A2, B, M, C, N = (date(2023, 11, d) for d in (14, 15, 24, 27, 28, 29))
# Their first-print vintages (the day after each period). VA1 and VA2 have left FRED's list.
VA1, VA2, VB, VM, VC, VN = (date(2023, 11, d) for d in (15, 16, 25, 28, 29, 30))
NOW_VINTAGES = [VB, VM, VC, VN]

STORED: dict[date, tuple[Revision, ...]] = {
    A1: (Revision(VA1, D("0.93")),),
    A2: (Revision(VA2, D("0.92")),),
    B: (Revision(VB, D("0.90")),),
    M: (Revision(VM, D("0.91")),),
    C: (Revision(VC, D("0.89")),),
}
LATEST = {B: D("0.90"), M: D("0.91"), C: D("0.89"), N: D("0.88")}  # A1 and A2 are gone


def judge(
    *,
    stored: Mapping[date, Sequence[Revision]] = STORED,
    window: Sequence[FredVintageRow] = (FredVintageRow(N, {VN: D("0.88")}),),
    latest: Mapping[date, Decimal | None] = LATEST,
    vintages: Sequence[date] = NOW_VINTAGES,
    rolling: bool = True,
    late_since: date | None = None,
    evidence: Mapping[date, FredVintageRow] | None = None,
) -> HistoryVerdict:
    first = {p: FirstPrintRef(c[0].vintage, c[0].value) for p, c in stored.items()}
    first |= {r.effective_at: FirstPrintRef(min(r.values), r.values[min(r.values)]) for r in window}
    return check_history(
        rows=[*rows_from_chains(stored), *window],
        vintage_dates=vintages,
        first_prints=first,
        latest=latest,
        stored=stored,
        rolling_window=rolling,
        late_since=late_since,
        late_evidence=evidence,
    )


def rules(v: HistoryVerdict) -> set[int]:
    return {r.rule for r in v.refusals}


def refused(v: HistoryVerdict, rule: int) -> set[date]:
    return {d for r in v.refusals if r.rule == rule for d in r.sample}


# --- Rolled off (cause (i)) -------------------------------------------------------------------


def test_rolled_off_periods_of_a_rolling_series_pass_rules_3_and_5_and_stay_as_stored() -> None:
    v = judge()
    assert v.ok, v.refusal_text()
    assert v.rolled_off == 2
    assert v.chains[A1] == STORED[A1] and v.chains[A2] == STORED[A2]  # untouched, nothing new
    assert v.chains[N] == (Revision(VN, D("0.88")),)  # the window's new period still lands
    assert v.first_prints == 6  # the stored totals stay complete


def test_the_same_data_on_a_series_not_declared_rolling_refuses_rules_3_and_5() -> None:
    v = judge(rolling=False)
    assert rules(v) == {3, 5}
    assert refused(v, 3) == {A1, A2} and refused(v, 5) == {A1, A2}
    assert v.rolled_off == 0


def test_a_stored_period_missing_in_the_middle_is_a_gap_and_still_fails_rule_3() -> None:
    """M is newer than the latest vintage's earliest observation (B): not rolled off, a hole."""
    v = judge(latest={p: x for p, x in LATEST.items() if p != M})
    assert rules(v) == {3}
    assert refused(v, 3) == {M}  # A1 and A2 still roll off beside it
    assert v.rolled_off == 2


def test_a_rolled_off_period_the_fetch_would_extend_is_not_rolled_off() -> None:
    """The stored rows are untouched: a new column on an old period is not left out quietly."""
    v = judge(window=[FredVintageRow(N, {VN: D("0.88")}), FredVintageRow(A1, {VN: D("0.95")})])
    assert 3 in rules(v) and A1 in refused(v, 3)
    assert A2 not in refused(v, 3)  # its neighbour, unextended, still rolls off
    assert v.rolled_off == 1


def test_a_rolled_off_column_with_a_vintage_inside_freds_list_range_still_fails_rule_5() -> None:
    """Only a vintage OLDER than FRED's earliest listed date rolled off with its period. An unknown
    vintage within the listed range is a column FRED never had, rolled-off period or not."""
    odd = date(2023, 11, 26)  # after VB (FRED's earliest listed), and not in the list
    stored = {**STORED, A2: (Revision(VA2, D("0.92")), Revision(odd, D("0.94")))}
    v = judge(stored=stored)
    assert rules(v) == {5}
    assert refused(v, 5) == {A2}
    assert A1 not in refused(v, 5)


def test_an_unknown_vintage_on_a_period_that_did_not_roll_off_still_fails_rule_5() -> None:
    stored = {**STORED, C: (Revision(date(2023, 11, 1), D("0.89")),)}  # older than FRED's list
    v = judge(stored=stored)
    assert rules(v) == {5}
    assert C in refused(v, 5)
    assert not {A1, A2} & refused(v, 5)


def test_exactly_the_two_ice_bofa_series_are_declared_rolling() -> None:
    rolling = {s.series_id for s in settings.FRED_SERIES if s.rolling_window}
    assert rolling == {"BAMLC0A0CM", "BAMLH0A0HYM2"}


# --- F3 late arrivals (cause (ii)) ------------------------------------------------------------

# L1 and L2 are periods FRED added to past vintages after the capture (captured_through = VC).
L1, L2 = date(2023, 11, 25), date(2023, 11, 26)
VL1, VL2 = date(2023, 11, 25), date(2023, 11, 27)
LATE_LATEST = {**LATEST, L1: D("0.97"), L2: D("0.96")}
VINTAGES_WITH_LATE = sorted({*NOW_VINTAGES, VL1, VL2})


def late(**kw: object) -> HistoryVerdict:
    args: dict[str, object] = {
        "latest": LATE_LATEST,
        "vintages": VINTAGES_WITH_LATE,
        "late_since": VC,
        "evidence": {
            L1: FredVintageRow(L1, {VL1: D("0.97")}),
            L2: FredVintageRow(L2, {VL2: D("0.96")}),
        },
    }
    args.update(kw)
    return judge(**args)  # type: ignore[arg-type]


def test_a_late_arrival_confirmed_by_its_own_fetch_is_accepted_at_freds_claimed_vintage() -> None:
    v = late()
    assert v.ok, v.refusal_text()
    assert v.late_arrivals == {L1: VL1, L2: VL2}
    assert v.chains[L1] == (Revision(VL1, D("0.97")),)
    assert v.chains[L2] == (Revision(VL2, D("0.96")),)
    assert N not in v.late_arrivals  # the window's own new period is an ordinary first print


def test_absence_never_counts_as_agreement() -> None:
    """Kyle, 2026-11-20 (fourth addendum): no evidence, or an empty fetch, refuses."""
    missing = late(evidence={L1: FredVintageRow(L1, {VL1: D("0.97")})})  # nothing fetched for L2
    empty = late(evidence={L1: FredVintageRow(L1, {VL1: D("0.97")}), L2: FredVintageRow(L2, {})})
    for v in (missing, empty):
        assert rules(v) == {4}
        assert refused(v, 4) == {L2}  # L1, confirmed, is not named
        assert "late arrival" in v.refusal_text()


def test_a_late_arrival_whose_vintages_disagree_is_refused() -> None:
    v = late(
        evidence={
            L1: FredVintageRow(L1, {VL1: D("0.97")}),
            L2: FredVintageRow(L2, {VL2: D("0.96"), VN: D("0.95")}),
        }
    )
    assert rules(v) == {4} and refused(v, 4) == {L2}


def test_a_late_arrival_whose_value_is_not_the_latest_is_refused() -> None:
    v = late(
        evidence={
            L1: FredVintageRow(L1, {VL1: D("0.97")}),
            L2: FredVintageRow(L2, {VL2: D("0.50")}),
        }
    )
    assert rules(v) == {4} and refused(v, 4) == {L2}


def test_a_late_arrival_whose_evidence_names_an_unknown_vintage_is_refused() -> None:
    v = late(
        evidence={
            L1: FredVintageRow(L1, {VL1: D("0.97")}),
            L2: FredVintageRow(L2, {date(2023, 11, 20): D("0.96")}),
        }
    )
    assert rules(v) == {4} and refused(v, 4) == {L2}


def test_evidence_is_read_for_its_own_period_only() -> None:
    """Rule 9: L1's agreeing row filed under L2 does not confirm L2 (its value would win)."""
    v = late(
        evidence={
            L1: FredVintageRow(L1, {VL1: D("0.97")}),
            L2: FredVintageRow(L1, {VL1: D("0.96")}),
        }
    )
    assert rules(v) == {4} and refused(v, 4) == {L2}


def test_a_period_after_captured_through_is_not_a_late_arrival() -> None:
    """Periods after captured_through are unchanged (dated by FRED, F1): with no chain, rule 4 as
    before, whatever evidence is lying about."""
    after = date(2023, 12, 1)
    v = late(
        latest={**LATE_LATEST, after: D("0.80")},
        evidence={
            L1: FredVintageRow(L1, {VL1: D("0.97")}),
            L2: FredVintageRow(L2, {VL2: D("0.96")}),
            after: FredVintageRow(after, {VN: D("0.80")}),
        },
    )
    assert rules(v) == {4}
    assert refused(v, 4) == {after}
    assert "no history" in v.refusal_text() and after not in v.late_arrivals


def test_without_late_since_a_late_period_refuses_as_it_always_did() -> None:
    v = late(late_since=None)
    assert rules(v) == {4} and refused(v, 4) == {L1, L2}
    assert v.late_arrivals == {}


def test_late_candidates_are_the_unstored_unwindowed_numbers_up_to_captured_through() -> None:
    gap = date(2023, 11, 30)
    latest = {**LATE_LATEST, gap: None, date(2023, 12, 1): D("0.8"), date(2023, 1, 2): D("1")}
    got = late_candidates(
        latest,
        stored=STORED,
        window_periods={N},
        captured_through=VC,
        first_covered_period=date(2023, 11, 14),
    )
    # Not: a stored period, the window's N, a "." gap, one after captured_through, one before the
    # oldest stored period.
    assert got == [L1, L2]


# --- The per-period fetch (FredClient.period_history) -----------------------------------------


def _client_run(
    answer: dict[str, object], vintages: list[date]
) -> tuple[object, list[dict[str, str]]]:
    seen: list[dict[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(dict(request.url.params))
        return httpx2.Response(200, json=answer)

    async def run() -> object:
        async with FredClient(
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            user_agent="FlowDesk-test",
            transport=httpx2.MockTransport(handler),
        ) as fred:
            try:
                return await fred.period_history("BAMLC0A0CM", date(2026, 11, 10), vintages)
            except Exception as exc:  # returned, so the test can look at it
                return exc

    return asyncio.run(run()), seen


def test_the_own_fetch_asks_for_one_period_across_the_vintages_from_its_date() -> None:
    vintages = [date(2026, 11, 6), date(2026, 11, 10), date(2026, 11, 12), date(2026, 11, 20)]
    answer = {"count": 1, "observations": [{"date": "2026-11-10", "BAMLC0A0CM_20261110": "0.9"}]}
    got, seen = _client_run(answer, vintages)
    assert got == FredVintageRow(date(2026, 11, 10), {date(2026, 11, 10): D("0.9")})
    assert len(seen) == 1  # one request for a late period a few days old
    q = seen[0]
    assert (q["output_type"], q["observation_start"], q["observation_end"]) == (
        "3",
        "2026-11-10",
        "2026-11-10",
    )
    assert (q["realtime_start"], q["realtime_end"]) == ("2026-11-10", "2026-11-20")  # not 11-06


def test_the_own_fetch_refuses_an_answer_about_another_period() -> None:
    answer = {"count": 1, "observations": [{"date": "2026-11-11", "BAMLC0A0CM_20261111": "0.9"}]}
    got, _ = _client_run(answer, [date(2026, 11, 10), date(2026, 11, 12)])
    assert isinstance(got, SchemaMismatch)

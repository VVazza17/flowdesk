"""M2 slice 5, step 3, commit A: the six rules of the ADR-0003 addendum (2026-11-13).

One test per rule that fails when that rule is removed (the mutation habit), tests that the two
exclusions are counted apart, and the rules run over real recorded responses.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2

from app.ingest.fred import _parse_page, _parse_wide_page
from app.ingest.macro import (
    FirstPrintRef,
    FredVintageRow,
    HistoryVerdict,
    Revision,
    check_history,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fred"

D = Decimal
P1, P2, P3 = date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1)
V1, V2, V3, V4 = date(2020, 2, 10), date(2020, 3, 10), date(2020, 4, 10), date(2020, 5, 10)
EARLY = date(2019, 1, 1)  # before the earliest first print: pre-vintage
EARLIER = date(2018, 12, 1)
ALL_VINTAGES = [V1, V2, V3, V4]


def row(period: date, **cols: Decimal | None) -> FredVintageRow:
    """row(P1, V1=D(1), V2=D(2)) -> a wide row; the keyword names pick the vintage constants."""
    table = {"V1": V1, "V2": V2, "V3": V3, "V4": V4}
    return FredVintageRow(period, {table[k]: v for k, v in cols.items()})


def check(
    rows: list[FredVintageRow],
    first: dict[date, tuple[date, Decimal | None]],
    latest: dict[date, Decimal | None],
    **kw: Any,
) -> HistoryVerdict:
    return check_history(
        rows=rows,
        vintage_dates=kw.pop("vintage_dates", ALL_VINTAGES),
        first_prints={p: FirstPrintRef(*v) for p, v in first.items()},
        latest=latest,
        **kw,
    )


def good() -> HistoryVerdict:
    """P1 printed 1 at V1 and revised to 2 at V3; P2 printed 5 at V2 and never revised."""
    return check(
        [row(P1, V1=D(1), V3=D(2)), row(P2, V2=D(5))],
        {P1: (V1, D(1)), P2: (V2, D(5))},
        {P1: D(2), P2: D(5)},
    )


def rules(v: HistoryVerdict) -> set[int]:
    return {r.rule for r in v.refusals}


def test_a_consistent_history_is_accepted_with_revisions_numbered_by_position() -> None:
    v = good()
    assert v.ok and rules(v) == set()
    assert v.chains[P1] == (Revision(V1, D(1)), Revision(V3, D(2)))
    assert v.chains[P2] == (Revision(V2, D(5)),)
    assert (v.first_prints, v.revisions) == (2, 1)


def test_rule_1_the_first_print_of_the_two_outputs_must_agree() -> None:
    wrong_value = check([row(P1, V1=D(1))], {P1: (V1, D(9))}, {P1: D(1)})  # output_type=4 says 9
    wrong_date = check([row(P1, V2=D(1))], {P1: (V1, D(1))}, {P1: D(1)})
    missing = check([], {P1: (V1, D(1))}, {P1: D(1)})  # a first print with no chain at all
    for v in (wrong_value, wrong_date, missing):
        assert 1 in rules(v) and not v.ok


def test_rule_2_a_chain_inside_the_record_without_a_first_print_means_one_was_lost() -> None:
    # P2 is after the earliest first print (P1) but output_type=4 has nothing for it.
    v = check([row(P1, V1=D(1)), row(P2, V3=D(7))], {P1: (V1, D(1))}, {P1: D(1), P2: D(7)})
    assert rules(v) == {2}
    assert v.refusals[0].sample == (P2,)


def test_rule_2_a_chain_before_the_record_is_left_out_and_counted_not_refused() -> None:
    v = check(
        [row(P1, V1=D(1)), row(EARLY, V2=D(3), V3=D(4))],
        {P1: (V1, D(1))},
        {P1: D(1), EARLY: D(4)},
    )
    assert v.ok and EARLY not in v.chains
    assert v.revised_pre_vintage_excluded == 1


def test_rule_3_a_chain_must_end_at_the_latest_value() -> None:
    stale = check([row(P1, V1=D(1), V3=D(2))], {P1: (V1, D(1))}, {P1: D(3)})
    gone = check([row(P1, V1=D(1))], {P1: (V1, D(1))}, {})  # the latest values lack the period
    assert rules(stale) == {3} and rules(gone) == {3}


def test_rule_3_a_gap_may_be_the_end_of_a_chain_but_only_if_it_is_the_latest_too() -> None:
    ok = check([row(P1, V1=D(1), V3=None)], {P1: (V1, D(1))}, {P1: None})
    bad = check([row(P1, V1=D(1), V3=None)], {P1: (V1, D(1))}, {P1: D(1)})
    assert ok.ok and rules(bad) == {3}


def test_rule_4_a_latest_value_with_no_history_inside_the_record_is_refused() -> None:
    v = check([row(P1, V1=D(1))], {P1: (V1, D(1))}, {P1: D(1), P3: D(8)})
    assert rules(v) == {4} and v.refusals[0].sample == (P3,)


def test_rule_4_exclusion_a_no_vintage_exists_is_counted_and_not_refused() -> None:
    v = check(
        [row(P1, V1=D(1))],
        {P1: (V1, D(1))},
        {P1: D(1), EARLY: D(8), EARLIER: None, P3: None},  # P3: a "." that no vintage published
    )
    assert v.ok
    assert v.no_vintage_excluded == 2  # EARLY and EARLIER: before the record, in no vintage
    assert v.gaps_without_o4_first_print_skipped == 1  # P3
    assert v.revised_pre_vintage_excluded == 0


def test_the_two_exclusions_are_counted_apart_and_never_summed() -> None:
    v = check(
        [row(P1, V1=D(1)), row(EARLY, V2=D(3))],  # EARLY: revised but pre-vintage (B)
        {P1: (V1, D(1))},
        {P1: D(1), EARLY: D(3), EARLIER: D(9), date(2018, 11, 1): D(9), P3: None},
    )
    assert v.ok
    assert v.revised_pre_vintage_excluded == 1  # B
    assert v.no_vintage_excluded == 2  # A, pre-vintage and never revised
    assert v.gaps_without_o4_first_print_skipped == 1  # A, a "." nobody published
    # No field adds them: B must not be read as a loss of A's kind (ADR-0003 addendum).
    assert not any("pre_vintage" == n for n in vars(v)) and not hasattr(v, "pre_vintage")


def test_rule_5_one_vintage_with_two_values_is_refused() -> None:
    v = check([row(P1, V1=D(1)), row(P1, V1=D(2))], {P1: (V1, D(1))}, {P1: D(1)})
    assert 5 in rules(v)


def test_rule_5_a_column_that_is_not_a_vintage_of_the_series_is_refused() -> None:
    v = check([row(P1, V1=D(1))], {P1: (V1, D(1))}, {P1: D(1)}, vintage_dates=[V2, V3])
    assert 5 in rules(v)


def test_rule_5_a_repeated_value_is_not_a_revision_and_is_skipped_and_counted() -> None:
    v = check([row(P1, V1=D(1), V2=D(1), V3=D(2))], {P1: (V1, D(1))}, {P1: D(2)})
    assert v.ok and v.chains[P1] == (Revision(V1, D(1)), Revision(V3, D(2)))
    assert v.duplicates_skipped == 1 and v.revisions == 1


def test_rule_5_the_same_window_seen_twice_is_not_a_conflict() -> None:
    same = row(P1, V1=D(1))
    v = check([same, same], {P1: (V1, D(1))}, {P1: D(1)})
    assert v.ok and v.duplicates_skipped == 0


def test_rule_6_what_is_stored_must_be_a_prefix_of_what_was_fetched() -> None:
    stored_ok = {P1: [Revision(V1, D(1))], P2: [Revision(V2, D(5))]}
    assert good().ok
    base = check(
        [row(P1, V1=D(1), V3=D(2)), row(P2, V2=D(5))],
        {P1: (V1, D(1)), P2: (V2, D(5))},
        {P1: D(2), P2: D(5)},
        stored=stored_ok,
    )
    assert base.ok  # a shorter stored chain is extended, which is also how the poll appends

    changed = {P1: [Revision(V1, D(7))]}
    longer = {P2: [Revision(V2, D(5)), Revision(V4, D(6))]}
    absent = {date(2020, 4, 1): [Revision(V1, D(1))]}
    for stored in (changed, longer, absent):
        v = check(
            [row(P1, V1=D(1), V3=D(2)), row(P2, V2=D(5))],
            {P1: (V1, D(1)), P2: (V2, D(5))},
            {P1: D(2), P2: D(5)},
            stored=stored,
        )
        assert 6 in rules(v) and not v.ok


def test_a_refusal_carries_counts_and_dates_only() -> None:
    v = check([row(P1, V1=D(1))], {P1: (V1, D(1))}, {P1: D(1), P3: D(8), P2: D(8)})
    text = v.refusal_text()
    assert "rule 4" in text and "2 periods" in text and "2020-02-01" in text
    assert "8" not in text.replace("2020", "").replace("-0", "")  # no value copied into it


# ---- the rules over real recorded responses -------------------------------------------------


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def test_drcclacbs_real_history_passes_all_rules_and_splits_into_both_kinds() -> None:
    """DRCCLACBS recorded 2026-11-12 (first prints, vintage dates) and 2026-11-13 (history): two
    different FRED outputs of one series must agree on every first print (rule 1)."""
    hist_rows, _ = _parse_wide_page(
        httpx2.Response(200, json=json.loads(load("ok_revisions_DRCCLACBS")["body"])), "DRCCLACBS"
    )
    first_obs, _ = _parse_page(
        httpx2.Response(200, json=json.loads(load("ok_first_prints_DRCCLACBS")["body"])),
        "DRCCLACBS",
    )
    vintages = json.loads(load("ok_vintagedates_DRCCLACBS")["body"])["vintage_dates"]
    first = {o.effective_at: FirstPrintRef(o.realtime_start, o.value) for o in first_obs}
    # The latest value of a period is where its chain ends (there is no latest-values fixture for
    # this series, so rule 3 passes by construction here; its force is tested above).
    latest = {r.effective_at: r.values[max(r.values)] for r in hist_rows}
    v = check_history(
        rows=hist_rows,
        vintage_dates=[date.fromisoformat(d) for d in vintages] + [date(2026, 8, 25)],
        first_prints=first,
        latest=latest,
    )
    assert v.ok, v.refusal_text()
    assert v.first_prints == 61  # one per first print
    assert v.revised_pre_vintage_excluded == 81  # exclusion B, as measured 2026-11-13
    assert v.no_vintage_excluded == 0 and v.gaps_without_o4_first_print_skipped == 0
    assert v.duplicates_skipped == 0
    assert v.revisions > 0
    # Rule 1 has teeth on real data: one corrupted witness is caught.
    broken = dict(first)
    p = next(iter(broken))
    broken[p] = FirstPrintRef(broken[p].vintage, (broken[p].value or D(0)) + D(1))
    bad = check_history(
        rows=hist_rows,
        vintage_dates=[date.fromisoformat(d) for d in vintages] + [date(2026, 8, 25)],
        first_prints=broken,
        latest=latest,
    )
    assert rules(bad) == {1}


def test_the_recorded_withdrawn_values_are_pre_vintage_revisions_not_first_prints() -> None:
    """CSUSHPINSA 2018-01-30: FRED turned 144 values of 1975-1986 into ".". Their series' record
    begins in 2014, so each is a revised-but-pre-vintage chain with no beginning (exclusion B)."""
    rows, _ = _parse_wide_page(
        httpx2.Response(
            200,
            json=json.loads(load("ok_revisions_CSUSHPINSA_one_vintage_with_gaps")["body"]),
        ),
        "CSUSHPINSA",
    )
    gaps = {r.effective_at for r in rows if None in r.values.values()}
    earliest_first_print = date(2014, 10, 1)
    first = {earliest_first_print: FirstPrintRef(date(2014, 11, 25), D("1"))}
    v = check_history(
        rows=[r for r in rows if r.effective_at in gaps],
        vintage_dates=[date(2018, 1, 30)],
        first_prints=first,
        latest={p: None for p in gaps},
    )
    # The first print of the witness period has no chain in THIS window, so rule 1 fires for it;
    # what matters here is that the 144 withdrawn periods are all counted as exclusion B.
    assert v.revised_pre_vintage_excluded == 144
    assert v.no_vintage_excluded == 0 and 2 not in rules(v)


def test_a_bare_row_is_ignored_and_cannot_become_an_empty_chain() -> None:
    v = check(
        [row(P1, V1=D(1)), FredVintageRow(P2, {})],  # P2: listed, unchanged in this window
        {P1: (V1, D(1))},
        {P1: D(1)},
    )
    assert v.ok and set(v.chains) == {P1}


def test_with_no_first_prints_at_all_every_real_latest_value_is_unexplained() -> None:
    v = check([], {}, {P1: D(1), P2: None})
    assert rules(v) == {4} and v.gaps_without_o4_first_print_skipped == 1


# ---- gap first prints that output_type=4 does not report (ADR-0003 addendum 2026-11-13, F2) ----

P_GAP = date(2020, 2, 15)  # inside the record (after P1), "." from its first vintage on


def test_a_chain_that_starts_with_a_gap_and_has_no_o4_row_is_a_stored_gap_first_print() -> None:
    v = check(
        [row(P1, V1=D(1)), row(P_GAP, V3=None)],
        {P1: (V1, D(1))},
        {P1: D(1), P_GAP: None},
    )
    assert v.ok, v.refusal_text()
    assert v.chains[P_GAP] == (Revision(V3, None),)  # revision 0, NULL, dated by its vintage
    assert v.gap_first_prints_without_o4_witness == 1
    # Counted apart: it is neither exclusion, and the old count is untouched.
    assert (v.no_vintage_excluded, v.gaps_without_o4_first_print_skipped) == (0, 0)
    assert v.revised_pre_vintage_excluded == 0
    assert v.first_prints == 2


def test_a_chain_that_starts_with_a_number_and_has_no_o4_row_still_refuses() -> None:
    v = check([row(P1, V1=D(1)), row(P_GAP, V3=D(7))], {P1: (V1, D(1))}, {P1: D(1), P_GAP: D(7)})
    assert rules(v) == {2} and v.gap_first_prints_without_o4_witness == 0


def test_a_gap_first_print_before_the_record_is_still_exclusion_b_not_stored() -> None:
    v = check([row(P1, V1=D(1)), row(EARLY, V2=None)], {P1: (V1, D(1))}, {P1: D(1), EARLY: None})
    assert v.ok and EARLY not in v.chains
    assert v.revised_pre_vintage_excluded == 1 and v.gap_first_prints_without_o4_witness == 0


def test_a_gap_first_print_is_still_held_to_rule_3() -> None:
    # It starts with "." but the latest value for the period is a number: a chain that stops short.
    v = check([row(P1, V1=D(1)), row(P_GAP, V3=None)], {P1: (V1, D(1))}, {P1: D(1), P_GAP: D(4)})
    assert rules(v) == {3}


def test_a_gap_first_print_whose_period_is_missing_from_the_latest_values_refuses() -> None:
    v = check([row(P1, V1=D(1)), row(P_GAP, V3=None)], {P1: (V1, D(1))}, {P1: D(1)})
    assert rules(v) == {3}


def test_without_any_first_print_a_gap_chain_is_not_accepted() -> None:
    # No witness at all and nothing to place the period against: refuse rather than guess.
    v = check([row(P_GAP, V3=None)], {}, {P_GAP: None})
    assert 2 in rules(v)


def test_a_latest_gap_found_in_neither_output_is_the_renamed_exclusion_a_count() -> None:
    v = check([row(P1, V1=D(1))], {P1: (V1, D(1))}, {P1: D(1), P3: None})
    assert v.ok and v.gaps_without_o4_first_print_skipped == 1


def test_vixcls_holidays_published_as_gaps_in_2014_are_stored_not_refused() -> None:
    """LIVE responses (tests/fixtures/fred/, 2026-11-13). On the 2014-02-05 vintage FRED added "."
    rows for market holidays. output_type=3 shows each with that publication date; output_type=4
    for the same vintage returns one row and none of them. Under the first version of rule 2 this
    refused the whole series; the 32 holidays inside VIXCLS's record are gap first prints."""
    o3 = json.loads(load("ok_revisions_VIXCLS_holiday_gaps_2014-02-05")["body"])
    o4 = json.loads(load("ok_first_prints_VIXCLS_2014-02-05")["body"])
    rows, _ = _parse_wide_page(httpx2.Response(200, json=o3), "VIXCLS")
    first_obs, _ = _parse_page(httpx2.Response(200, json=o4), "VIXCLS")
    gaps = {r.effective_at for r in rows if None in r.values.values()}
    assert len(gaps) == 93 and not gaps & {o.effective_at for o in first_obs}  # o4 omits every one
    earliest = date(2010, 11, 22)  # VIXCLS's first covered period (settings.FRED_SERIES)
    inside = {p for p in gaps if p >= earliest}
    assert len(inside) == 32
    first = {o.effective_at: FirstPrintRef(o.realtime_start, o.value) for o in first_obs}
    first[earliest] = FirstPrintRef(date(2010, 11, 23), D("1"))  # anchor the record's start
    v = check_history(
        rows=[
            r
            for r in rows
            if r.effective_at in gaps or r.effective_at in {o.effective_at for o in first_obs}
        ],
        vintage_dates=[date(2014, 2, 5)],
        first_prints={
            p: ref for p, ref in first.items() if p in {o.effective_at for o in first_obs}
        }
        | {earliest: first[earliest]},
        latest={p: None for p in gaps} | {o.effective_at: o.value for o in first_obs},
    )
    # The anchor period has no chain in this single window, so rule 1 names it; every other rule is
    # satisfied by what the holidays do.
    assert rules(v) <= {1}
    assert v.gap_first_prints_without_o4_witness == 32
    assert (
        v.revised_pre_vintage_excluded == 61
    )  # the 93 minus the 32 inside the record: exclusion B
    assert all(v.chains[p] == (Revision(date(2014, 2, 5), None),) for p in inside)

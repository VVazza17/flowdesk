"""M2 slice 5, step 3, commit A: the wide `output_type=3` format, parsed.

The two fixtures are LIVE responses (tests/fixtures/fred/, captured 2026-11-13), unmodified:
a series' whole revision history (DRCCLACBS, 142 periods, 62 vintages) and one vintage of
CSUSHPINSA in which FRED turned 144 old values into ".".
"""

import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.ingest.errors import SchemaMismatch
from app.ingest.fred import FredClient, _parse_wide_page

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fred"
KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def response(payload: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json=payload)


def page(items: list[dict[str, str]], count: int | None = None) -> dict[str, Any]:
    return {"count": len(items) if count is None else count, "observations": items}


def test_the_recorded_history_parses_into_periods_and_vintage_dates() -> None:
    body = json.loads(fixture("ok_revisions_DRCCLACBS")["body"])
    rows, count = _parse_wide_page(response(body), "DRCCLACBS")
    assert count == len(rows) == 142
    first = rows[0]
    assert first.effective_at == date(1991, 1, 1)
    # A pre-vintage period revised after the record began: its earliest column is a REVISION date.
    assert min(first.values) == date(2017, 2, 21)
    assert first.values[date(2017, 2, 21)] == Decimal("5.26")
    assert first.values[date(2022, 2, 18)] == Decimal("5.31")
    # Column names are series-prefixed vintage dates; the keys we hold are real dates.
    assert all(isinstance(v, date) for r in rows for v in r.values)


def test_the_recorded_gaps_become_none_and_never_zero() -> None:
    body = json.loads(fixture("ok_revisions_CSUSHPINSA_one_vintage_with_gaps")["body"])
    rows, _ = _parse_wide_page(response(body), "CSUSHPINSA")
    gaps = [r for r in rows if None in r.values.values()]
    assert len(gaps) == 144
    assert all(r.values == {date(2018, 1, 30): None} for r in gaps)
    assert {r.effective_at.year for r in gaps} <= set(range(1975, 1987))
    # ADR-0003 decision 7: a "." is a NULL, never 0.0.
    assert not any(v == 0 for r in rows for v in r.values.values())


def test_revision_history_fetches_the_recorded_series_in_one_window() -> None:
    vint = json.loads(fixture("ok_vintagedates_DRCCLACBS")["body"])
    hist = json.loads(fixture("ok_revisions_DRCCLACBS")["body"])
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return response(vint if request.url.path.endswith("vintagedates") else hist)

    async def go() -> Any:
        async with FredClient(
            KEY, user_agent="FlowDesk-test", transport=httpx2.MockTransport(handler)
        ) as fred:
            return await fred.revision_history("DRCCLACBS")

    result = asyncio.run(go())
    assert len(result.rows) == 142
    assert len(result.vintage_dates) == vint["count"]
    assert result.splits == 0
    assert sum(path.endswith("/series/observations") for path in seen) == 1


@pytest.mark.parametrize(
    "item",
    [
        {"date": "2020-01-01", "OTHER_20200101": "1.0"},  # a column of another series
        {"date": "2020-01-01", "DRCCLACBS_2020": "1.0"},  # not eight digits
        {"date": "2020-01-01", "DRCCLACBS_20201399": "1.0"},  # impossible date
        {"date": "2020-13-01", "DRCCLACBS_20200101": "1.0"},  # bad period date
        {"DRCCLACBS_20200101": "1.0"},  # no period
        {"date": "2020-01-01", "DRCCLACBS_20200101": "NaN"},  # not a number
        {"date": "2020-01-01", "DRCCLACBS_20200101": "1e5"},  # not a plain decimal
    ],
)
def test_a_malformed_row_is_rejected_not_guessed_at(item: dict[str, str]) -> None:
    with pytest.raises(SchemaMismatch):
        _parse_wide_page(response(page([item])), "DRCCLACBS")


@pytest.mark.parametrize("payload", [[], {"count": "3", "observations": []}, {"count": 1}])
def test_a_body_that_is_not_the_known_shape_is_rejected(payload: Any) -> None:
    with pytest.raises(SchemaMismatch):
        _parse_wide_page(response(payload), "DRCCLACBS")


def test_a_period_may_carry_several_vintages_in_any_key_order() -> None:
    item = {"DRCCLACBS_20220101": "2", "date": "2020-01-01", "DRCCLACBS_20210101": "1"}
    rows, _ = _parse_wide_page(response(page([item])), "DRCCLACBS")
    assert rows[0].values == {date(2022, 1, 1): Decimal("2"), date(2021, 1, 1): Decimal("1")}


def test_a_bare_row_is_a_period_unchanged_in_the_window_and_is_not_an_error() -> None:
    # Recorded 2026-11-13: 32 of the 515 rows of one CSUSHPINSA window are {"date": ...} alone.
    body = json.loads(fixture("ok_revisions_CSUSHPINSA_one_vintage_with_gaps")["body"])
    rows, count = _parse_wide_page(response(body), "CSUSHPINSA")
    assert count == len(rows) == 515
    bare = [r for r in rows if not r.values]
    assert len(bare) == 32 and bare[0].effective_at == date(1987, 9, 1)

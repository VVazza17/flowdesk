"""Load RECORDED FRED responses (tests/fixtures/fred/) into a database through the real rules.

The point-in-time read is tested against data it did not produce: live FRED responses, judged by
`check_history` and written by `commit_history` exactly as the backfill does, then read back. The
expectations are computed separately, from the fixture's own columns (tests/test_macro_oracle.py).
"""

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest.fred import _parse_page, _parse_wide_page
from app.ingest.macro import (
    FirstPrintRef,
    FredSeries,
    FredVintageRow,
    HistoryVerdict,
    check_history,
)
from app.services.macro_store import Chains, commit_history

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fred"


def body(name: str) -> dict[str, Any]:
    rec = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    parsed: dict[str, Any] = json.loads(rec["body"])
    return parsed


class Recorded:
    """One series' recorded responses, parsed."""

    def __init__(self, series: str, *, extra_vintages: Sequence[date] = (), latest: bool) -> None:
        self.series = series
        self.rows: list[FredVintageRow] = _parse_wide_page(
            httpx2.Response(200, json=body(f"ok_revisions_{series}")), series
        )[0]
        first_obs = _parse_page(
            httpx2.Response(200, json=body(f"ok_first_prints_{series}")), series
        )[0]
        self.first = {o.effective_at: FirstPrintRef(o.realtime_start, o.value) for o in first_obs}
        vint = body(f"ok_vintagedates_{series}")["vintage_dates"]
        self.vintages = sorted({date.fromisoformat(d) for d in vint} | set(extra_vintages))
        if latest:
            latest_obs = _parse_page(
                httpx2.Response(200, json=body(f"ok_latest_{series}")), series
            )[0]
            self.latest = {o.effective_at: o.value for o in latest_obs}
        else:
            # No latest-values fixture: where each period's chain ends. Rule 3 then holds by
            # construction here (its force is tested in test_macro_history_rules.py).
            self.latest = {r.effective_at: r.values[max(r.values)] for r in self.rows}

    def bounds(self) -> FredSeries:
        """The record's boundaries as the config states them: the first vintage, and the first
        period that has a publication date (the earliest `output_type=4` period)."""
        return FredSeries(self.series, min(self.vintages), min(self.first))

    def columns(self) -> dict[date, dict[date, Any]]:
        """Each period's raw columns straight from the fixture: the oracle's only input."""
        out: dict[date, dict[date, Any]] = {}
        for row in self.rows:
            out.setdefault(row.effective_at, {}).update(row.values)
        return out


async def seed(engine: AsyncEngine, metric: str, rec: Recorded) -> HistoryVerdict:
    def judge(stored: Chains) -> HistoryVerdict:
        return check_history(
            rows=rec.rows,
            vintage_dates=rec.vintages,
            first_prints=rec.first,
            latest=rec.latest,
            stored=stored,
        )

    committed = await commit_history(
        engine,
        metric,
        judge=judge,
        captured_through=rec.vintages[-1],
        rule_version=1,
        ingestion_run_id=None,
    )
    return committed.verdict

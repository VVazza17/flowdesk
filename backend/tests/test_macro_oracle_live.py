"""M2 slice 5, step 4: the stored data against INDEPENDENT evidence, in a development stack.

M0 spike 3b (spikes/spike3b_evidence.json, recorded 2026-07-31 through `output_type=4`, a
different path from the `output_type=3` history that was stored) measured, for each of twelve
CPIAUCSL periods, the first print, its publication date, and the value then current. The stored
history must reproduce all of it, including the one period that is a published gap: this is the
check that the backfill's DATA, not just its code, is right.

It reads the MAIN database read-only (DATABASE_URL) and SKIPS when CPIAUCSL has not been captured
there. `ci-checks.sh test` uses the development stack's own Postgres, so on a development machine
with the series captured it RUNS there too; on a fresh database (GitHub's CI, a clean checkout) it
skips, permanently. A silent permanent skip is an absent test, so the skip reason carries the
live-data marker, `tests/conftest.py` prints a banner for it on every run (tests/liveskips.py), and
the test gate allows exactly this kind of skip and prints it (tools/junit_skips.py). By hand:

    python -m pytest tests/test_macro_oracle_live.py -rs
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.macro import available_at_for
from app.services.macro_read import Missing, Observation, Reason, revisions_of, value_as_of
from tests.livedb import require_live_series

SPIKE = Path(__file__).resolve().parents[2] / "spikes" / "spike3b_evidence.json"
NY = ZoneInfo("America/New_York")


@pytest.fixture
def live_url() -> Iterator[str]:
    yield require_live_series(os.environ.get("DATABASE_URL", ""), "CPIAUCSL")


def go(url: str, fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    async def wrapper() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


def number(raw: str) -> Decimal | None:
    """M0 recorded FRED's own strings; "." is a published gap (2025-10-01 was never collected: the
    US government shutdown), which the store holds as a NULL and the read returns as value=None."""
    return None if raw == "." else Decimal(raw)


def spike_points() -> tuple[datetime, list[dict[str, Any]]]:
    evidence = json.loads(SPIKE.read_text(encoding="utf-8"))
    run_at = datetime.fromisoformat(evidence["run_at_utc"].replace("Z", "+00:00"))
    return run_at, evidence["CPIAUCSL"]


def test_the_stored_cpi_reproduces_m0_spike_3b_at_both_instants(live_url: str) -> None:
    run_at, points = spike_points()
    assert len(points) >= 10  # not vacuous

    async def fn(engine: AsyncEngine) -> list[tuple[dict[str, Any], Any, Any]]:
        out = []
        for point in points:
            period = date.fromisoformat(point["period"])
            published = date.fromisoformat(point["available_at"])
            out.append(
                (
                    point,
                    await value_as_of(engine, "CPIAUCSL", period, available_at_for(published)),
                    await value_as_of(engine, "CPIAUCSL", period, run_at),
                )
            )
        return out

    for point, at_publication, at_run in go(live_url, fn):
        who = point["period"]
        assert isinstance(at_publication, Observation), who
        assert at_publication.value == number(point["first_print"]), who  # the first print
        assert at_publication.revision == 0, who
        assert isinstance(at_run, Observation), who
        assert at_run.value == number(point["current"]), who  # what M0 saw as current that day
        if point["revised"]:
            assert at_run.value != at_publication.value, who


def test_a_value_is_invisible_the_instant_before_its_publication_day_ends(live_url: str) -> None:
    _run_at, points = spike_points()
    point = points[0]
    period = date.fromisoformat(point["period"])
    published = available_at_for(date.fromisoformat(point["available_at"]))

    async def fn(engine: AsyncEngine) -> Any:
        return await value_as_of(engine, "CPIAUCSL", period, published - timedelta(microseconds=1))

    before = go(live_url, fn)
    assert isinstance(before, Missing) and before.reason is Reason.NOT_PUBLISHED_AS_OF


def test_cpi_2025_01_was_revised_on_fred_s_vintage_not_on_a_polls(live_url: str) -> None:
    """The annual seasonal-factor revision of 2026-02-13. The earlier design would have dated it by
    whichever poll first saw it; the stored revision carries FRED's own date, and a reader standing
    on either side of it gets the number that was believed then."""
    period = date(2025, 1, 1)

    async def fn(engine: AsyncEngine) -> dict[str, Any]:
        return {
            "chain": await revisions_of(engine, "CPIAUCSL", period),
            "day_before": await value_as_of(
                engine, "CPIAUCSL", period, datetime(2026, 2, 13, 23, 59, 59, tzinfo=NY)
            ),
            "day_of": await value_as_of(
                engine, "CPIAUCSL", period, datetime(2026, 2, 14, 0, 0, tzinfo=NY)
            ),
        }

    out = go(live_url, fn)
    assert [(o.revision, o.value) for o in out["chain"]][:2] == [
        (0, Decimal("319.086")),
        (1, Decimal("318.961")),
    ]
    assert out["chain"][1].available_at == available_at_for(date(2026, 2, 13))
    assert out["day_before"].value == Decimal("319.086")
    assert out["day_of"].value == Decimal("318.961")

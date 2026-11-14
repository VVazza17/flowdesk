"""M2 slice 5, step 5: do the configured thresholds still describe the real publication cadence?

A LIVE-DATA check (tests/livedb.py, tests/liveskips.py): it measures the cadence from the stored
history with the same function that produced the thresholds, and fails if a configured threshold has
drifted outside the band the rule intends. It skips LOUDLY on a fresh database. A release-schedule
change is not predicted by history; this is how a development stack finds out that one happened.

    python -m pytest tests/test_data_age_live.py -rs
"""

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.ingest.cadence import Cadence, suggest_stale_after
from app.measure_cadence import measure
from tests.livedb import require_live_series


@pytest.fixture
def live_url() -> Iterator[str]:
    yield require_live_series(os.environ.get("DATABASE_URL", ""), "DGS10")


def measured(url: str) -> dict[str, Cadence | None]:
    async def fn() -> dict[str, Cadence | None]:
        engine = create_async_engine(url)
        try:
            return await measure(engine, datetime.now(UTC))
        finally:
            await engine.dispose()

    return asyncio.run(fn())


def test_every_configured_threshold_is_still_a_generous_multiple_of_its_measured_cadence(
    live_url: str,
) -> None:
    got = measured(live_url)
    configured = settings.data_stale_after_all()
    drifted = []
    checked = 0
    for metric, threshold in configured.items():
        c = got.get(metric)
        if c is None:
            continue  # not captured in this database: nothing to compare
        checked += 1
        days = threshold.days
        # Generous, never tight: at least 2 x p95 so ordinary gaps never read as stale; at most
        # 6 x p95 so a stalled series does not take months to show. The rule picks 3 x.
        if not (2 * c.p95 <= days <= 6 * c.p95):
            drifted.append(
                f"{metric}: configured {days} d, p95 gap {c.p95:.1f} d "
                f"(rule now says {suggest_stale_after(c).days} d)"
            )
    assert checked >= 10  # not vacuous
    assert drifted == [], "thresholds drifted; run python -m app.measure_cadence: " + "; ".join(
        drifted
    )

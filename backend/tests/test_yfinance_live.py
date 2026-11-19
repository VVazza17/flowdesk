"""M2 slice 6: the private yfinance API's BEHAVIOUR, live, on the development stack.

`test_the_real_call_shape_against_yfinance_1_8_0` pins `_download_impl`'s PARAMETERS. It cannot
prove that `_DownloadCtx` still captures per-ticker errors, and the replayed fixtures would not
notice if it stopped: they hold errors recorded when it worked. If it stopped, a rate-limited symbol
would look dead again (app/ingest/yfinance_client.py). So this calls Yahoo for real, once, with one
live ticker and one known-dead ticker, and asserts that the dead ticker's error was captured in the
context and classified as "no data", and that the live ticker's rows came back and parse.

**THE CHECK TO RUN AFTER ANY yfinance UPGRADE** (Kyle, 2026-11-18), on the development stack:

    python -m pytest tests/test_yfinance_live.py -rs      (or: bash infra/ci-checks.sh test)

It runs only against a SEEDED development stack (the database has active yfinance mappings) and
SKIPS LOUDLY everywhere else: CI's test job starts Postgres only and never seeds, so no live call is
ever made from GitHub (tests/liveskips.py, tools/junit_skips.py). One batch of two tickers per run,
far inside the budget (one 24-ticker batch a minute).
"""

import asyncio
import os
from datetime import UTC, datetime

import pytest

from app import settings
from app.ingest.bars import DAILY, parse_batch
from app.ingest.yfinance_client import YFinanceClient
from tests.livedb import require_dev_stack_provider
from tests.yfixtures import nyse

LIVE = "SPY"
DEAD = "ZZZQXFAKE"  # not a listed symbol (step 1(b) used it; Yahoo answers 404 for it)


@pytest.fixture
def dev_stack() -> None:
    require_dev_stack_provider(os.environ.get("DATABASE_URL", ""), "yfinance")


def test_the_download_context_captures_a_dead_tickers_error_live(dev_stack: None) -> None:
    client = YFinanceClient(max_batch=settings.RATE_BUDGETS["yfinance"].max_batch)
    now = datetime.now(UTC)
    batch = asyncio.run(client.fetch([LIVE, DEAD], DAILY, today=now.date(), period="1mo"))

    # The private context still captures per-ticker errors: the dead ticker's is there, the live
    # ticker's is not. (Its TEXT is the library's: read here, never stored.)
    assert DEAD in batch.errors and batch.errors[DEAD].strip()
    assert LIVE not in batch.errors

    # Classified as "no data" (not a rate limit, not transient): parse_batch does not raise, the
    # dead ticker is dead, and the live ticker's rows came back and settle.
    result = parse_batch(
        batch.frame,
        batch.errors,
        tickers=[LIVE, DEAD],
        interval=DAILY,
        calendar=nyse(),
        now=now,
        known_splits={},
        known_distributions={},
    )
    assert result.dead == [DEAD]
    assert result.failures == []
    assert len(result.bars[LIVE]) >= 10  # a month of sessions, minus the two not yet settled
    assert all(b.available_at <= now for b in result.bars[LIVE])

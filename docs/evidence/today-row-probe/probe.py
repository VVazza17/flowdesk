"""Today-row probe, Fri 2026-11-20 (Kyle's item 3; the same-run split-check gap, question b).

Read-only. One daily `actions=True` batch of the 24 mapped symbols through the PRODUCTION client
(`app.ingest.yfinance_client.YFinanceClient`, the call the 15m split check makes), run inside the worker
image. No database connection is opened and nothing is written anywhere: the report goes to stdout, and
the runner appends it to docs/evidence/2026-11-20-today-row-probe.txt. It does NOT take a rate-budget
slot (that needs Redis and is run_bars' job); the bars polls are off (BARS_POLL_ENABLED=false), so no
other yfinance caller shares the minute.

For each symbol: is there a row labelled 2026-11-20, and its Open, Close and Stock Splits as returned
(repr, no rounding; NaN printed as NaN). Also the last row's date, so "absent" is not confused with a
dead symbol.
"""

import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd  # type: ignore[import-untyped]
import yfinance  # type: ignore[import-untyped]

from app import settings
from app.ingest.bars import DAILY
from app.ingest.yfinance_client import YFinanceClient

NEW_YORK = ZoneInfo("America/New_York")
TARGET = date(2026, 11, 20)
TICKERS = [
    *settings.ROTATION_UNIVERSE,
    *settings.BENCHMARK,
    *settings.STYLE_EXTRAS,
    *settings.COMMODITY_PROXIES,
]


def _day(label: pd.Timestamp) -> date:
    if label.tzinfo is None:
        return label.date()  # F-c: a naive daily label is the exchange-local session date
    return label.tz_convert(NEW_YORK).date()


def _v(x: object) -> str:
    return "NaN" if x is None or (isinstance(x, float) and pd.isna(x)) else repr(float(x))  # type: ignore[arg-type]


async def main() -> int:
    if len(TICKERS) != 24 or len(set(TICKERS)) != 24:
        print(f"ABORT: expected 24 distinct symbols, have {len(TICKERS)}")
        return 2
    now = datetime.now(UTC)
    today = now.astimezone(NEW_YORK).date()
    print(f"=== run {now.isoformat(timespec='seconds')} UTC = {now.astimezone(NEW_YORK):%Y-%m-%d %H:%M:%S %Z}")
    print(f"yfinance {yfinance.__version__}; start={today - timedelta(days=7)}; interval=1d; actions=True")
    client = YFinanceClient(max_batch=24)
    try:
        batch = await client.fetch(TICKERS, DAILY, today=today, start=today - timedelta(days=7))
    except Exception as exc:  # report the class only: library text is never kept (house rule)
        print(f"FAILED: fetch raised {type(exc).__module__}.{type(exc).__name__}")
        return 3
    frame = batch.frame
    print(f"per-ticker errors: {len(batch.errors)} ({', '.join(sorted(batch.errors)) or 'none'}); rows in frame: {len(frame)}")
    present = 0
    print(f"{'symbol':6} {'2026-11-20 row':14} {'Open':>22} {'Close':>22} {'Stock Splits':>12}  last row")
    for t in TICKERS:
        if frame.empty or t not in frame.columns.get_level_values(0):
            print(f"{t:6} {'NO GROUP':14}")
            continue
        g = frame[t]
        g = g[~g[["Open", "High", "Low", "Close"]].isna().all(axis=1) | (g.get("Stock Splits", 0) != 0)]
        days = [_day(i) for i in g.index]
        last = max(days).isoformat() if days else "none"
        if TARGET in days:
            row = g.iloc[days.index(TARGET)]
            present += 1
            print(
                f"{t:6} {'present':14} {_v(row.get('Open')):>22} {_v(row.get('Close')):>22} "
                f"{_v(row.get('Stock Splits')):>12}  {last}"
            )
        else:
            print(f"{t:6} {'absent':14} {'':>22} {'':>22} {'':>12}  {last}")
    print(f"summary: 2026-11-20 row present for {present} of 24")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

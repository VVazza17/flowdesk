"""Load the recorded yfinance fixtures (tests/fixtures/yfinance/, recorded 2026-11-18 with yfinance
1.8.0 by record.py.txt) back into the DataFrame and per-ticker errors the client received.

The frame is rebuilt exactly: floats from their JSON repr, null as NaN, the ticker/field
MultiIndex, and the index with the timezone it had (daily naive, intraday America/New_York).
"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from app.ingest.bars import ACTION_COLUMNS, Calendar, Session
from app.services.sessions import compute_sessions

DIR = Path(__file__).resolve().parent / "fixtures" / "yfinance"


def load(name: str) -> tuple[Any, dict[str, str], datetime]:
    """(frame, errors, fetched_at) of one recorded fixture."""
    d = json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))
    index = pd.DatetimeIndex(pd.to_datetime(d["index"]))
    if d["index_tz"] is not None:
        index = index.tz_convert(d["index_tz"])
    columns = pd.MultiIndex.from_tuples([tuple(c) for c in d["columns"]])
    data = [[float("nan") if v is None else v for v in row] for row in d["data"]]
    frame = pd.DataFrame(data, index=index, columns=columns)
    fetched = datetime.fromisoformat(d["meta"]["fetched_at"]).astimezone(UTC)
    return frame, dict(d["errors"]), fetched


def with_today_row(frame: Any, day: date, *, skip: Sequence[str] = ()) -> Any:
    """A DAILY fixture frame with a row labelled `day` for every ticker that has data and none there
    yet: prices copied from its last row, every split and distribution value 0. Since Kyle's rule of
    2026-11-20 no bar is accepted on a session day unless the run's daily frame has TODAY's row, and
    the recorded fixtures end before most tests' "now". A ticker in `skip` gets no row (it is held);
    an all-NaN (dead) ticker gets none either."""
    label = pd.Timestamp(day)
    out = frame.copy()
    if label not in out.index:
        blank = pd.DataFrame(float("nan"), index=pd.DatetimeIndex([label]), columns=out.columns)
        out = pd.concat([out, blank]).sort_index()
    for ticker in dict.fromkeys(c[0] for c in out.columns):
        if ticker in skip or not out.loc[label, ticker].isna().all():
            continue
        before = out[ticker].drop(index=label).dropna(how="all")
        if before.empty:
            continue
        last = before.iloc[-1]
        for col in out[ticker].columns:
            out.loc[label, (ticker, col)] = 0.0 if col in ACTION_COLUMNS else last[col]
    return out


@cache
def nyse() -> Calendar:
    """XNYS from the same library and code the seed uses for trading_sessions."""
    rows = compute_sessions("XNYS", date(2024, 1, 1), date(2027, 1, 31))
    return Calendar(Session(r.session_date, r.opens_at, r.closes_at) for r in rows)

"""F-6b measurement (Kyle's item 5, day 1 = 2026-11-20; repeat on Monday's run with the same command).

READ-ONLY. Through the production FredClient: the latest vintage (output_type=1, as the poll's
`latest` reads it) of BAMLC0A0CM and BAMLH0A0HYM2, and what the poll's own window would see for the
period rule 4 named (2026-11-10). From the database, SELECT only: the stored history's extent and the
state row. Nothing is written anywhere; output goes to stdout. No URL, header or key is printed, and a
failure prints only the exception's class.

    docker compose run --rm --no-deps -T -e PYTHONPATH=/srv -v <this dir>:/probe:ro worker python /probe/probe.py

The question it answers: is each series' EARLIEST observation in the latest vintage moving forward day
by day? Only that shows a rolling window (ASSUMED until then; secondary sources: SUPPORTED, not MEASURED).
"""

import asyncio
import sys
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.ingest.fred import FredClient

SERIES = ("BAMLC0A0CM", "BAMLH0A0HYM2")
RULE4_PERIOD = date(2026, 11, 10)


async def pace() -> None:
    await asyncio.sleep(1.0)  # well inside FRED's budget; this probe is a handful of requests


async def stored(series: str) -> dict[str, object]:
    url = settings.database_url()
    assert url, "DATABASE_URL"
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            ext = (
                await conn.execute(
                    text(
                        "SELECT min(effective_at), max(effective_at), count(DISTINCT effective_at), "
                        "count(*) FROM macro_metrics WHERE metric = :m"
                    ),
                    {"m": series},
                )
            ).one()
            st = (
                await conn.execute(
                    text("SELECT captured_through, rule_version FROM series_history_state WHERE series_id = :m"),
                    {"m": series},
                )
            ).one_or_none()
            p = (
                await conn.execute(
                    text(
                        "SELECT revision, value, available_at FROM macro_metrics "
                        "WHERE metric = :m AND effective_at = :d ORDER BY revision"
                    ),
                    {"m": series, "d": RULE4_PERIOD},
                )
            ).all()
    finally:
        await engine.dispose()
    return {"extent": tuple(ext), "state": tuple(st) if st else None, "rule4_rows": [tuple(r) for r in p]}


async def main() -> int:
    key = settings.fred_api_key()
    if not key:
        print("FAILED: no FRED api key in the worker's environment")
        return 2
    now = datetime.now(UTC)
    print(f"=== F-6b run {now.isoformat(timespec='seconds')} (read-only)")
    rc = 0
    async with FredClient(key, user_agent=settings.fred_user_agent(), before_next_request=pace) as fred:
        for s in SERIES:
            print(f"--- {s}")
            try:
                db = await stored(s)
                print(f"stored: periods {db['extent'][0]} .. {db['extent'][1]}, {db['extent'][2]} periods, "
                      f"{db['extent'][3]} rows; state (captured_through, rule_version) = {db['state']}")
                print(f"stored rows for {RULE4_PERIOD}: {db['rule4_rows'] or 'none'}")
                latest = await fred.latest(s)
                vals = [o for o in latest]
                periods = sorted(o.effective_at for o in vals)
                print(f"latest vintage: {len(vals)} observations; EARLIEST {periods[0]}; latest {periods[-1]}")
                at = [o for o in vals if o.effective_at == RULE4_PERIOD]
                print(f"latest vintage at {RULE4_PERIOD}: "
                      + (", ".join(f"value={o.value} realtime_start={o.realtime_start}" for o in at) or "absent"))
                dates = await fred.vintage_dates(s)
                print(f"vintage dates: {len(dates)} total; last 6: {[d.isoformat() for d in dates[-6:]]}")
                state = db["state"]
                if state:
                    since = state[0]
                    hist = await fred.revisions_since(s, since)
                    in_window = [r for r in hist.rows if r.effective_at == RULE4_PERIOD and r.values]
                    print(f"poll window (vintages >= {since}): {len(hist.window_dates)} vintages "
                          f"{[d.isoformat() for d in hist.window_dates]}; rows {len(hist.rows)}; "
                          f"rows for {RULE4_PERIOD} with values: "
                          + (", ".join(str({k.isoformat(): v for k, v in r.values.items()}) for r in in_window) or "none"))
                    o4 = await fred.first_prints_chunked(s, hist.window_dates)
                    fp = [o for o in o4 if o.effective_at == RULE4_PERIOD]
                    print(f"output_type=4 over the window: {len(o4)} first prints; at {RULE4_PERIOD}: "
                          + (", ".join(f"value={o.value} realtime_start={o.realtime_start}" for o in fp) or "absent"))
                    old = sorted({r.effective_at for r in hist.rows})
                    print(f"window rows' periods: {old[0] if old else '-'} .. {old[-1] if old else '-'}")
            except Exception as exc:
                print(f"FAILED: {type(exc).__module__}.{type(exc).__name__}")
                rc = 3
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

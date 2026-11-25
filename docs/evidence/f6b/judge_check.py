"""F-6b, 2026-11-24: which rules refuse the two ICE BofA series today, and on which periods. READ-ONLY.

The same fetches and the same judge as `app.ingest.fred_feed.poll_body`, but it never calls
`commit_history`: the stored chains are read on a plain connection (SELECT only) and the verdict is
printed with EVERY refused period, not the three-date sample of `refusal_text`. No URL, header or
key is printed; a failure prints only the exception's class.

    docker compose run --rm --no-deps -T -e PYTHONPATH=/srv -v <this dir>:/probe:ro worker python /probe/judge_check.py
"""

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.ingest.fred import FredClient
from app.ingest.macro import FirstPrintRef, check_history, rows_from_chains
from app.services.macro_store import read_chains, read_state

SERIES = ("BAMLC0A0CM", "BAMLH0A0HYM2")


async def pace() -> None:
    await asyncio.sleep(1.0)


async def main() -> int:
    engine = create_async_engine(settings.database_url())
    print(f"=== F-6b judge check {datetime.now(UTC).isoformat(timespec='seconds')} (read-only)")
    rc = 0
    try:
        async with FredClient(
            settings.fred_api_key(), user_agent=settings.fred_user_agent(), before_next_request=pace
        ) as fred:
            for s in SERIES:
                print(f"--- {s}")
                try:
                    state = await read_state(engine, s)
                    assert state is not None
                    async with engine.connect() as conn:
                        stored = await read_chains(conn, s)
                    history = await fred.revisions_since(s, state.captured_through)
                    window_first = await fred.first_prints_chunked(s, history.window_dates)
                    latest = await fred.latest(s)
                    latest_values = {o.effective_at: o.value for o in latest}
                    first = {p: FirstPrintRef(c[0].vintage, c[0].value) for p, c in stored.items()} | {
                        o.effective_at: FirstPrintRef(o.realtime_start, o.value) for o in window_first
                    }
                    verdict = check_history(
                        rows=[*rows_from_chains(stored), *history.rows],
                        vintage_dates=history.vintage_dates,
                        first_prints=first,
                        latest=latest_values,
                        stored=stored,
                    )
                    known = set(history.vintage_dates)
                    stored_vintages = sorted({r.vintage for c in stored.values() for r in c})
                    unknown = [v for v in stored_vintages if v not in known]
                    print(f"FRED vintage dates: {len(known)}, earliest {min(known)}; latest-vintage periods "
                          f"{min(latest_values)} .. {max(latest_values)}")
                    print(f"stored: {len(stored)} periods {min(stored)} .. {max(stored)}; stored vintages not in "
                          f"FRED's list now: {len(unknown)} {[d.isoformat() for d in unknown]}")
                    missing = sorted(p for p in stored if p not in latest_values)
                    print(f"stored periods absent from the latest vintage: {len(missing)} {[d.isoformat() for d in missing]}")
                    print(f"ok={verdict.ok}")
                    for r in verdict.refusals:
                        print(f"  rule {r.rule} ({r.reason}): {r.count} periods")
                    # Every period per rule, recomputed the way check_history does (sample is only 3).
                    rule5 = sorted(p for p, c in stored.items() if any(x.vintage not in known for x in c))
                    rule3 = sorted(p for p, c in verdict.chains.items() if p not in latest_values
                                   or c[-1].value != latest_values[p]) if verdict.chains else []
                    print(f"  rule 5 periods (stored column with a vintage FRED no longer lists): {[d.isoformat() for d in rule5]}")
                    print(f"  rule 3 periods (kept chain absent from / not ending at latest): {[d.isoformat() for d in rule3]}")
                except Exception as exc:
                    print(f"FAILED: {type(exc).__module__}.{type(exc).__name__}")
                    rc = 3
    finally:
        await engine.dispose()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

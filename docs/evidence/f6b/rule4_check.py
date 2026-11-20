import asyncio
from datetime import date
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app import settings
from app.ingest.fred import FredClient
P = date(2026, 11, 10)
async def pace(): await asyncio.sleep(1.0)
async def main():
    eng = create_async_engine(settings.database_url())
    async with eng.connect() as c:
        for s in ("BAMLC0A0CM", "BAMLH0A0HYM2"):
            r = (await c.execute(text("SELECT effective_at, revision, value, available_at FROM macro_metrics WHERE metric=:m AND effective_at BETWEEN '2026-11-05' AND '2026-11-13' ORDER BY 1,2"), {"m": s})).all()
            print(s, "stored 11-05..11-13:", [(str(x[0]), x[1], str(x[2]), x[3].date().isoformat()) for x in r])
    await eng.dispose()
    async with FredClient(settings.fred_api_key(), user_agent=settings.fred_user_agent(), before_next_request=pace) as f:
        for s in ("BAMLC0A0CM", "BAMLH0A0HYM2"):
            try:
                o4 = await f.first_prints(s)
                hit = [o for o in o4 if o.effective_at == P]
                print(s, "output_type=4 (whole record):", len(o4), "first prints; at 11-10:", [(str(o.value), o.realtime_start.isoformat()) for o in hit] or "absent",
                      "; earliest", min(o.effective_at for o in o4).isoformat())
                vd = await f.vintage_dates(s)
                win = [d for d in vd if date(2026, 11, 6) <= d <= date(2026, 11, 13)]
                h = await f._history_over(s, vd, win, 100)
                rows = [r for r in h.rows if r.effective_at == P]
                print(s, "output_type=3 over vintages", [d.isoformat() for d in win], ": rows for 11-10:", [{k.isoformat(): str(v) for k, v in r.values.items()} for r in rows] or "none",
                      "; periods in window:", sorted({r.effective_at.isoformat() for r in h.rows if r.values})[-6:])
            except Exception as e:
                print(s, "FAILED", type(e).__name__)
asyncio.run(main())

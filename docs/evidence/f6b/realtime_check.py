import asyncio
from collections import Counter
from datetime import date
from app import settings
from app.ingest.fred import FredClient
P = date(2026, 11, 10)
async def pace(): await asyncio.sleep(1.0)
async def main():
    async with FredClient(settings.fred_api_key(), user_agent=settings.fred_user_agent(), before_next_request=pace) as f:
        for s in ("BAMLC0A0CM", "BAMLH0A0HYM2"):
            print("---", s)
            latest = await f.latest(s)
            rs = Counter(o.realtime_start for o in latest)
            print("latest (output_type=1, no real-time window):", len(latest), "obs; realtime_start values:", {k.isoformat(): v for k, v in sorted(rs.items())},
                  "; realtime_end values:", sorted({o.realtime_end.isoformat() for o in latest}))
            for o in latest:
                if o.effective_at >= date(2026, 11, 6):
                    print(f"  period {o.effective_at} value {o.value} realtime {o.realtime_start}..{o.realtime_end}")
            vd = await f.vintage_dates(s)
            print("vintage dates, last 4:", [d.isoformat() for d in vd[-4:]])
            for v in (date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)):
                try:
                    obs = await f.observations(s, output_type=1, realtime_start=v, realtime_end=v, observation_start=date(2026, 11, 5), no_vintages_is_empty=True)
                    print(f"  as of vintage {v}: periods", [(o.effective_at.isoformat()[5:], str(o.value), o.realtime_start.isoformat()[5:]) for o in obs])
                except Exception as e:
                    print(f"  as of vintage {v}: FAILED {type(e).__name__}")
asyncio.run(main())

"""F3 point-in-time check (Kyle, 2026-11-25). READ-ONLY: app/services/macro_read.py only (SELECTs).

For BAMLH0A0HYM2 (3.3) and BAMLC0A0CM (0.9), period 2026-11-10, read as of the END of 2026-11-20 and of
2026-11-25 in America/New_York (23:59:59.999999). As of 11-20 the period must be absent; as of 11-25 it must
be present with its value. The late row's available_at is 2026-11-24 23:55Z, so 11-20 is before it and
11-25 after it. Prints each read and PASS/FAIL per expectation; exits non-zero on any FAIL.

    docker compose run --rm --no-deps -T -e PYTHONPATH=/srv -v <this dir>:/probe:ro worker python /probe/pit_check.py
"""

import asyncio
import sys
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.services.macro_read import Missing, series_as_of, value_as_of

NY = ZoneInfo("America/New_York")
PERIOD = date(2026, 11, 10)
EXPECT = {"BAMLH0A0HYM2": Decimal("3.3"), "BAMLC0A0CM": Decimal("0.9")}
AS_OF = [datetime.combine(d, time(23, 59, 59, 999999), tzinfo=NY) for d in (date(2026, 11, 20), date(2026, 11, 25))]


async def main() -> int:
    engine = create_async_engine(settings.database_url())
    fails = 0
    try:
        for metric, expected in EXPECT.items():
            for as_of in AS_OF:
                print(f"--- {metric} as of {as_of.isoformat()}")
                snap = await series_as_of(engine, metric, as_of, start=date(2026, 11, 6), end=date(2026, 11, 13))
                print("  series_as_of 11-06..11-13: "
                      + ", ".join(f"{o.effective_at}={o.value} (rev {o.revision}, available {o.available_at.isoformat()})"
                                  for o in snap.observations))
                got = await value_as_of(engine, metric, PERIOD, as_of)
                print(f"  value_as_of {PERIOD}: {got}")
                in_series = any(o.effective_at == PERIOD for o in snap.observations)
                if as_of.date() == date(2026, 11, 20):
                    ok = isinstance(got, Missing) and not in_series
                    want = "absent"
                else:
                    ok = (not isinstance(got, Missing)) and got.value == expected and in_series
                    want = f"present, value {expected}"
                fails += not ok
                print(f"  {'PASS' if ok else 'FAIL'}: {PERIOD} {want}")
    finally:
        await engine.dispose()
    print("RESULT:", "PASS" if not fails else f"FAIL ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

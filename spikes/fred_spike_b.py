"""
FlowDesk M0 — Spike 3b: first-print values and publication lag
==============================================================
Fixes the bug in spike 3: output_type=4 ("initial release only") needs a WIDE
real-time window. Defaulting realtime_start/realtime_end to today asks FRED
"which vintages were created today?" — usually none — hence the 400.

What this adds:
  - the FIRST PUBLISHED value of each observation (vs today's value)
  - realtime_start per observation = the date it became public = available_at
  - publication lag in days (how stale a value is when it first appears)

Run:  python m0_fred_spike_b.py
Saves: spike3b_evidence.json
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

BASE = "https://api.stlouisfed.org/fred"
WIDE = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}  # all vintages

SERIES = ["GDPC1", "CPIAUCSL", "PAYEMS"]   # GDP, CPI, nonfarm payrolls


def call(endpoint, key, **params):
    params.update({"api_key": key, "file_type": "json"})
    url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return {"ok": True, "data": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    key = os.environ.get("FRED_API_KEY") or input("Paste your FRED API key: ").strip()
    ev = {"run_at_utc": datetime.now(timezone.utc).isoformat()}

    for sid in SERIES:
        print("\n" + "=" * 70)
        print(f"{sid}: first print vs today, and publication lag")
        print("=" * 70)

        first = call("series/observations", key, series_id=sid, output_type=4,
                     observation_start="2025-01-01", observation_end="2025-12-31",
                     **WIDE)
        cur = call("series/observations", key, series_id=sid,
                   observation_start="2025-01-01", observation_end="2025-12-31")

        if not first["ok"]:
            print("  FAILED:", first.get("body") or first.get("error"))
            ev[sid] = first
            continue

        cur_map = ({o["date"]: o["value"] for o in cur["data"]["observations"]}
                   if cur["ok"] else {})

        print(f"  {'period':12} {'first print':>13} {'today':>13} {'rev?':>5} "
              f"{'published':>12} {'lag':>5}")
        rows = []
        for o in first["data"]["observations"]:
            per, v0 = o["date"], o["value"]
            v1 = cur_map.get(per, "n/a")
            pub = o.get("realtime_start")
            lag = (date.fromisoformat(pub) - date.fromisoformat(per)).days if pub else None
            rev = "YES" if v0 != v1 else "no"
            print(f"  {per:12} {v0:>13} {v1:>13} {rev:>5} {str(pub):>12} {str(lag):>5}")
            rows.append({"period": per, "first_print": v0, "current": v1,
                         "revised": rev == "YES", "available_at": pub,
                         "lag_days": lag})
        ev[sid] = rows

    print("\n" + "-" * 70)
    print("READ THIS:")
    print("  'published' is the available_at field the architecture requires.")
    print("  'lag' is how many days AFTER the period a value first appeared —")
    print("  a backtest standing inside that window must not see the value.")
    print("  Any 'YES' in rev? proves the stored value would change under you.")

    with open("spike3b_evidence.json", "w") as f:
        json.dump(ev, f, indent=2)
    print("\nSaved -> spike3b_evidence.json")


if __name__ == "__main__":
    main()

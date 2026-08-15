"""
FlowDesk M0 — Proof Spike 3: FRED vintages, revisions, and availability
======================================================================
Purpose: decision 26 says backtests must filter on `available_at` (when a value
became PUBLIC), never on `effective_at` (the period it describes). That is only
implementable if FRED actually exposes publication dates and revision history.
This spike finds out.

Assumptions under test:
  1. Series metadata (units, frequency, seasonal adjustment) is retrievable.
  2. Macro values ARE revised after first publication.
  3. The originally published ("first print") value is retrievable.
  4. A publication date per observation is retrievable  -> this becomes available_at.
  5. Publication lag can be measured (how stale is a value on any given day).
  6. Bad series IDs fail loudly rather than silently.

Setup:  get a free key at https://fredaccount.stlouisfed.org/apikeys
        then EITHER set an env var:   $env:FRED_API_KEY="yourkey"
        OR just run it and paste the key when prompted.

Run:    python m0_fred_spike.py
Saves:  spike3_evidence.json   (the API key is NEVER written to this file)
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

BASE = "https://api.stlouisfed.org/fred"

# DGS10  = 10y Treasury yield  (daily market rate — expect NO revisions)
# GDPC1  = real GDP            (advance -> second -> third estimate; big revisions)
# CPIAUCSL = CPI, seas. adj.   (annual seasonal-factor revisions)
DAILY_SERIES = "DGS10"
REVISED_SERIES = "GDPC1"
CPI_SERIES = "CPIAUCSL"
BAD_SERIES = "NOTAREALSERIES"


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
    today = date.today().isoformat()

    # ---- 1. metadata ----
    print("=" * 62); print("1. SERIES METADATA"); print("=" * 62)
    meta = call("series", key, series_id=DAILY_SERIES)
    if meta["ok"]:
        srs = meta["data"]["seriess"][0]
        keep = {k: srs.get(k) for k in
                ("id", "title", "frequency", "units", "seasonal_adjustment",
                 "last_updated", "observation_start", "observation_end")}
        ev["metadata"] = keep
        for k, v in keep.items():
            print(f"  {k:22} {v}")
    else:
        print("  FAILED:", meta); ev["metadata"] = meta
        print("\n*** If this failed with 400, the API key is wrong. Stop here. ***")
        return

    # ---- 2 & 3. current value vs FIRST PRINT ----
    print("\n" + "=" * 62); print("2. REVISIONS: current value vs originally published")
    print("=" * 62)
    for sid in (REVISED_SERIES, CPI_SERIES):
        cur = call("series/observations", key, series_id=sid,
                   observation_start="2025-01-01", observation_end="2025-12-31")
        # output_type=4 -> INITIAL RELEASE ONLY (the value as first published)
        first = call("series/observations", key, series_id=sid, output_type=4,
                     observation_start="2025-01-01", observation_end="2025-12-31")
        if not (cur["ok"] and first["ok"]):
            print(f"  {sid}: FAILED", cur if not cur['ok'] else first); continue

        cur_map = {o["date"]: o["value"] for o in cur["data"]["observations"]}
        first_obs = first["data"]["observations"]
        rows = []
        print(f"\n  {sid}")
        print(f"  {'period':12} {'first print':>14} {'today':>14} {'revised?':>10}")
        for o in first_obs[:6]:
            d, v0 = o["date"], o["value"]
            v1 = cur_map.get(d, "n/a")
            changed = "YES" if v0 != v1 else "no"
            print(f"  {d:12} {v0:>14} {v1:>14} {changed:>10}")
            rows.append({"period": d, "first_print": v0, "current": v1,
                         "revised": changed == "YES",
                         "realtime_start": o.get("realtime_start")})
        ev[f"revisions_{sid}"] = rows

    print("\n  --- READ THIS ---")
    print("  Any 'YES' proves macro values change after publication.")
    print("  Storing only the latest value would silently rewrite history.")

    # ---- 4 & 5. availability / publication lag ----
    print("\n" + "=" * 62); print("4. AVAILABILITY: when did each value become public?")
    print("=" * 62)
    first = call("series/observations", key, series_id=REVISED_SERIES, output_type=4,
                 observation_start="2025-01-01", observation_end="2025-12-31")
    if first["ok"]:
        lags = []
        print(f"  {'period':12} {'published (realtime_start)':>28} {'lag days':>10}")
        for o in first["data"]["observations"][:6]:
            per = date.fromisoformat(o["date"])
            pub = o.get("realtime_start")
            lag = (date.fromisoformat(pub) - per).days if pub else None
            print(f"  {o['date']:12} {str(pub):>28} {str(lag):>10}")
            lags.append({"period": o["date"], "published": pub, "lag_days": lag})
        ev["publication_lag"] = lags
        print("\n  --- READ THIS ---")
        print("  realtime_start IS the available_at field decision 26 requires.")
        print("  A backtest on 2025-04-15 must NOT see a value published later.")

    # ---- point-in-time proof: what did the world look like on a past date? ----
    print("\n" + "=" * 62); print("5. POINT-IN-TIME PROOF")
    print("=" * 62)
    asof = "2025-05-01"
    pit = call("series/observations", key, series_id=REVISED_SERIES,
               realtime_start=asof, realtime_end=asof,
               observation_start="2025-01-01", observation_end="2025-12-31")
    now = call("series/observations", key, series_id=REVISED_SERIES,
               realtime_start=today, realtime_end=today,
               observation_start="2025-01-01", observation_end="2025-12-31")
    if pit["ok"] and now["ok"]:
        p = [(o["date"], o["value"]) for o in pit["data"]["observations"]]
        n = [(o["date"], o["value"]) for o in now["data"]["observations"]]
        ev["as_of_past"] = {"as_of": asof, "observations": p}
        ev["as_of_today"] = {"as_of": today, "observations": n}
        print(f"  {REVISED_SERIES} as known on {asof}: {p}")
        print(f"  {REVISED_SERIES} as known today   : {n}")
        print("\n  --- READ THIS ---")
        print("  Different lists = true point-in-time replay is possible.")
        print("  Same list = check whether this series simply had no revisions.")

    # ---- 6. bad series ----
    print("\n" + "=" * 62); print("6. BAD SERIES ID"); print("=" * 62)
    bad = call("series/observations", key, series_id=BAD_SERIES)
    ev["bad_series"] = {"ok": bad["ok"], "status": bad.get("status"),
                        "body": bad.get("body", "")[:200]}
    print("  ok:", bad["ok"], "| status:", bad.get("status"))
    print("  body:", bad.get("body", bad.get("error", ""))[:200])
    print("  --- READ THIS ---")
    print("  Loud HTTP error = good. Compare with yfinance's silent empty frame.")

    with open("spike3_evidence.json", "w") as f:
        json.dump(ev, f, indent=2)
    print("\nSaved -> spike3_evidence.json  (no API key inside)")


if __name__ == "__main__":
    main()

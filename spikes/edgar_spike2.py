"""
FlowDesk M0 — Proof Spike 4: SEC EDGAR access
=============================================
Purpose: confirm the smart-money feeds (§6.5) are actually reachable and learn
their real behaviour before M10 depends on them.

Assumptions under test:
  1. A proper User-Agent is REQUIRED (SEC blocks generic clients).
  2. Ticker -> CIK mapping is retrievable.
  3. A company's filing index is retrievable and filterable by form type.
  4. Form 4 (insider trades) and 8-K (earnings releases) are both present.
  5. Filings expose BOTH a transaction/period date and an acceptance/filing
     date -> the disclosure-lag fields §7.3 requires.
  6. Rate limiting behaves as documented (~10 requests/sec cap).

EDIT THIS BEFORE RUNNING — the SEC requires a real contact address:
"""
USER_AGENT = "FlowDesk Research kyle.deng01@gmail.com"   # <-- put YOUR email here
"""
Run:  python m0_edgar_spike.py
Saves: spike4_evidence.json
"""

import json
import time
import urllib.request
from datetime import datetime, timezone

TICKER = "NVDA"
HEADERS = {"User-Agent": USER_AGENT}   # no Accept-Encoding: urllib won't decompress


def get(url, headers=None, label=""):
    req = urllib.request.Request(url, headers=headers if headers is not None else HEADERS)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            body = raw.decode("utf-8", errors="replace")
            return {"ok": True, "status": r.status, "ms": int((time.time() - t0) * 1000),
                    "len": len(body), "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": int((time.time() - t0) * 1000),
                "body": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    ev = {"run_at_utc": datetime.now(timezone.utc).isoformat(),
          "user_agent_used": USER_AGENT}

    # ---- 1. Does the User-Agent actually matter? ----
    print("=" * 66)
    print("1. USER-AGENT REQUIREMENT")
    print("=" * 66)
    url = "https://www.sec.gov/files/company_tickers.json"
    bare = get(url, headers={})           # no UA at all
    good = get(url)                       # proper UA
    print(f"  without User-Agent : ok={bare['ok']} status={bare.get('status')}")
    print(f"  with User-Agent    : ok={good['ok']} status={good.get('status')} "
          f"({good.get('len', 0):,} bytes)")
    ev["ua_test"] = {"without": {k: bare.get(k) for k in ('ok', 'status')},
                     "with": {k: good.get(k) for k in ('ok', 'status', 'len')}}
    print("  --- If 'without' failed and 'with' succeeded, the UA is mandatory.")
    if not good["ok"]:
        print("\n*** Could not reach SEC. Did you set a real email in USER_AGENT? ***")
        return

    # ---- 2. ticker -> CIK ----
    print("\n" + "=" * 66)
    print("2. TICKER -> CIK MAPPING")
    print("=" * 66)
    tickers = json.loads(good["body"])
    cik = None
    for row in tickers.values():
        if row["ticker"] == TICKER:
            cik = str(row["cik_str"]).zfill(10)
            print(f"  {TICKER} -> CIK {cik}  ({row['title']})")
            break
    ev["cik"] = {"ticker": TICKER, "cik": cik, "total_tickers_in_file": len(tickers)}
    print(f"  file contains {len(tickers):,} ticker->CIK rows")
    print("  --- NOTE: tickers are reused/reassigned over time; store CIK, not ticker.")
    if not cik:
        print("  *** ticker not found ***"); return

    time.sleep(0.2)

    # ---- 3-5. submissions: forms, dates, lag ----
    print("\n" + "=" * 66)
    print("3. RECENT FILINGS + DISCLOSURE DATES")
    print("=" * 66)
    sub = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not sub["ok"]:
        print("  FAILED:", sub.get("status"), sub.get("body", "")[:200])
        ev["submissions"] = sub
        return

    data = json.loads(sub["body"])
    recent = data["filings"]["recent"]
    fields = list(recent.keys())
    print(f"  fields available: {fields}")
    ev["submission_fields"] = fields

    counts = {}
    for f in recent["form"]:
        counts[f] = counts.get(f, 0) + 1
    top = sorted(counts.items(), key=lambda x: -x[1])[:8]
    print(f"  form types in recent batch: {top}")
    ev["form_counts"] = counts

    for want in ("4", "8-K"):
        print(f"\n  --- {want} filings ---")
        rows = []
        for i, form in enumerate(recent["form"]):
            if form != want:
                continue
            row = {
                "form": form,
                "filingDate": recent["filingDate"][i],
                "reportDate": recent.get("reportDate", [None] * len(recent["form"]))[i],
                "acceptanceDateTime": recent.get("acceptanceDateTime", [None] * len(recent["form"]))[i],
                "accessionNumber": recent["accessionNumber"][i],
                "primaryDocument": recent["primaryDocument"][i],
            }
            rows.append(row)
            if len(rows) >= 3:
                break
        for r in rows:
            print(f"    filed {r['filingDate']}  period {r['reportDate']}  "
                  f"accepted {r['acceptanceDateTime']}")
            print(f"      doc: {r['primaryDocument']}")
        ev[f"filings_{want}"] = rows
        if not rows:
            print("    (none in the recent batch — older filings live in the")
            print("     'files' array of the submissions JSON)")

    print("\n  --- READ THIS ---")
    print("  reportDate = period covered; filingDate/acceptanceDateTime = when it")
    print("  became public. Their difference IS the disclosure lag in §7.3.")
    print("  For Form 4 the actual TRANSACTION date lives inside the XML document,")
    print("  not in this index — confirm that before M10 planning.")

    # ---- 6. rate limiting ----
    print("\n" + "=" * 66)
    print("4. RATE LIMIT BEHAVIOUR (12 rapid requests)")
    print("=" * 66)
    statuses = []
    for i in range(12):
        r = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        statuses.append(r.get("status") or r.get("error"))
    print("  statuses:", statuses)
    blocked = sum(1 for s in statuses if s in (403, 429))
    print(f"  blocked/throttled: {blocked}/12")
    ev["rate_limit_test"] = {"statuses": [str(s) for s in statuses], "blocked": blocked}
    print("  --- SEC documents ~10 req/sec. If you see 403/429 here, ingestion")
    print("      needs explicit throttling, not just retries.")

    with open("spike4_evidence.json", "w") as f:
        json.dump(ev, f, indent=2)
    print("\nSaved -> spike4_evidence.json")


if __name__ == "__main__":
    main()

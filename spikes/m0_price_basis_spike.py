"""
FlowDesk M0 — Proof Spike 2: yfinance price basis
=================================================
Purpose: find out what yfinance ACTUALLY returns, so §6.12's price-basis rule
(raw for premiums/gaps, split-adjusted for momentum) can be implemented on
facts instead of assumptions.

Assumptions under test:
  1. Does history() return raw or adjusted prices by default?
  2. Does an 'Adj Close' column exist, and when?
  3. Are splits already applied to the Close series?
  4. Are dividends baked into the adjusted series?
  5. What comes back for a bad/delisted ticker?
  6. What happens on a market holiday — missing row, or a filled one?

Run:  python m0_price_basis_spike.py
Saves: spike2_evidence.json   (commit it — raw evidence beats conclusions)

Nothing here is production code. It exists to be read, run once, and recorded.
"""

import json
from datetime import datetime, timezone

import yfinance as yf

# NVDA did a 10:1 split on 2024-06-10. If splits are applied to the series,
# prices BEFORE that date will look ~10x smaller than they traded at the time.
SPLIT_TICKER = "NVDA"
SPLIT_WINDOW = ("2024-06-05", "2024-06-14")

DIV_TICKER = "KO"                     # reliable dividend payer
DIV_WINDOW = ("2025-01-01", "2025-06-30")

HOLIDAY_TICKER = "SPY"
HOLIDAY_WINDOW = ("2025-07-01", "2025-07-08")   # contains July 4 (market closed)

BAD_TICKER = "ZZZZFAKE"


def frame(ticker, start, end, auto_adjust):
    df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=auto_adjust)
    if df.empty:
        return {"empty": True, "columns": [], "rows": []}
    return {
        "empty": False,
        "columns": list(df.columns),
        "rows": [
            {
                "date": str(idx.date()),
                **{c: (float(r[c]) if c in df.columns else None)
                   for c in ("Open", "Close", "Adj Close", "Volume",
                             "Dividends", "Stock Splits") if c in df.columns},
            }
            for idx, r in df.iterrows()
        ],
    }


def show(title, data):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)
    if data["empty"]:
        print("  *** EMPTY RESULT ***")
        return
    print("columns:", data["columns"])
    for r in data["rows"]:
        bits = [f"{r['date']}"]
        for k in ("Open", "Close", "Adj Close"):
            if k in r and r[k] is not None:
                bits.append(f"{k}={r[k]:,.4f}")
        if r.get("Stock Splits"):
            bits.append(f"SPLIT={r['Stock Splits']}")
        if r.get("Dividends"):
            bits.append(f"DIV={r['Dividends']}")
        print("  " + "  ".join(bits))


def main():
    ev = {"run_at_utc": datetime.now(timezone.utc).isoformat()}

    # --- 1 & 3: split handling, both modes ---
    adj = frame(SPLIT_TICKER, *SPLIT_WINDOW, auto_adjust=True)
    raw = frame(SPLIT_TICKER, *SPLIT_WINDOW, auto_adjust=False)
    ev["split_auto_adjust_true"] = adj
    ev["split_auto_adjust_false"] = raw
    show(f"{SPLIT_TICKER} around 10:1 split — auto_adjust=True", adj)
    show(f"{SPLIT_TICKER} around 10:1 split — auto_adjust=False", raw)

    print("\n--- READ THIS ---")
    print("If auto_adjust=True prices before 2024-06-10 are ~1/10 of the")
    print("auto_adjust=False prices, then splits ARE applied to Close.")
    print("If 'Adj Close' appears only in the auto_adjust=False frame, that")
    print("confirms auto_adjust=True folds adjustment into OHLC silently.")

    # --- 4: dividends ---
    div = frame(DIV_TICKER, *DIV_WINDOW, auto_adjust=False)
    if not div["empty"]:
        div["rows"] = [r for r in div["rows"] if r.get("Dividends")] or div["rows"][:3]
    ev["dividend_rows"] = div
    show(f"{DIV_TICKER} dividend rows (auto_adjust=False)", div)
    print("\n--- READ THIS ---")
    print("Compare Close vs Adj Close on a dividend date. A gap means the")
    print("adjusted series is TOTAL RETURN, not just split-adjusted.")

    # --- 6: holiday ---
    hol = frame(HOLIDAY_TICKER, *HOLIDAY_WINDOW, auto_adjust=False)
    ev["holiday_window"] = hol
    show(f"{HOLIDAY_TICKER} across July 4 2025", hol)
    dates = [r["date"] for r in hol["rows"]]
    print("\n--- READ THIS ---")
    print("Dates returned:", dates)
    print("Is 2025-07-04 present? ->", "2025-07-04" in dates)
    print("Absent = gaps are implicit. Never assume row N-1 is 'yesterday'.")

    # --- 5: bad ticker ---
    print("\n" + "=" * 62)
    print(f"BAD TICKER: {BAD_TICKER}")
    print("=" * 62)
    try:
        bad = frame(BAD_TICKER, "2025-01-01", "2025-01-10", auto_adjust=False)
        ev["bad_ticker"] = {"raised": False, **bad}
        print("empty result:", bad["empty"], "| no exception raised")
    except Exception as e:
        ev["bad_ticker"] = {"raised": True, "error": f"{type(e).__name__}: {e}"}
        print("raised:", type(e).__name__, e)
    print("--- READ THIS ---")
    print("Silent-empty is more dangerous than an exception: ingestion must")
    print("treat an empty frame as a FAILURE, not as 'no new data'.")

    with open("spike2_evidence.json", "w") as f:
        json.dump(ev, f, indent=2)
    print("\nSaved -> spike2_evidence.json")


if __name__ == "__main__":
    main()

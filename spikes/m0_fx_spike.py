"""
FlowDesk M0 — Proof Spike 1: Foreign listing + FX + premium
============================================================
Purpose: test the most dangerous assumption in the whole system before any
app code exists — can we get a Korean price and an FX rate, do we know which
direction the FX rate points, and does the premium reproduce by hand?

Run:  pip install yfinance
      python m0_fx_spike.py

Output: prints raw evidence to the screen and saves it to
        spike1_evidence.json  (commit this file — raw evidence matters more
        than conclusions in six months)

NOTE: this prints what the API ACTUALLY returns. Do not trust the premium
number it prints. You will recompute it yourself in a spreadsheet and compare.
That is the entire point of the exercise.
"""

import json
from datetime import datetime, timezone

import yfinance as yf

US_TICKER = "SKHY"        # Nasdaq ADR
KR_TICKER = "000660.KS"   # SK Hynix on KRX
FX_TICKER = "KRW=X"       # convention check: is this KRW per USD, or USD per KRW?
ADR_RATIO = 0.1           # ordinary shares per ADR — VERIFY from the depositary
                          # bank's own documentation, not from a forum post


def last_close(ticker: str):
    """Return the most recent daily close plus the raw row, so we can see
    exactly which fields exist and what they are called."""
    hist = yf.Ticker(ticker).history(period="5d", interval="1d")
    if hist.empty:
        return None
    row = hist.iloc[-1]
    return {
        "ticker": ticker,
        "timestamp": str(hist.index[-1]),
        "close": float(row["Close"]),
        "columns_returned": list(hist.columns),   # is there an 'Adj Close'?
        "last_5_closes": [float(x) for x in hist["Close"].tolist()],
    }


def main():
    evidence = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "assumptions_under_test": [
            "foreign ticker retrievable",
            "price field + timestamp understood",
            "FX direction explicit and verifiable",
            "ADR ratio convention unambiguous",
            "premium reproducible by hand",
        ],
    }

    us = last_close(US_TICKER)
    kr = last_close(KR_TICKER)
    fx = last_close(FX_TICKER)
    evidence["us_leg"] = us
    evidence["foreign_leg"] = kr
    evidence["fx_raw"] = fx

    print("=" * 60)
    print("RAW EVIDENCE")
    print("=" * 60)
    for label, data in (("US leg", us), ("Foreign leg", kr), ("FX", fx)):
        print(f"\n{label}:")
        print(json.dumps(data, indent=2) if data else "  *** NO DATA RETURNED ***")

    # ---- The direction check: this is the whole spike ----
    if fx:
        rate = fx["close"]
        print("\n" + "=" * 60)
        print("FX DIRECTION CHECK")
        print("=" * 60)
        print(f"KRW=X returned: {rate}")
        print("A number near 1,400 means KRW per USD  -> usd_per_krw = 1/rate")
        print("A number near 0.0007 means USD per KRW -> usd_per_krw = rate")
        usd_per_krw = (1.0 / rate) if rate > 1 else rate
        print(f"Therefore usd_per_krw = {usd_per_krw:.10f}")
        evidence["fx_interpretation"] = {
            "raw_rate": rate,
            "inferred_usd_per_krw": usd_per_krw,
        }

        if kr and us:
            implied = kr["close"] * usd_per_krw * ADR_RATIO
            premium = (us["close"] / implied - 1.0) * 100.0
            print("\n" + "=" * 60)
            print("PREMIUM (script's answer — VERIFY THIS BY HAND)")
            print("=" * 60)
            print(f"foreign close      = {kr['close']:,.0f} KRW  @ {kr['timestamp']}")
            print(f"usd_per_krw        = {usd_per_krw:.10f}     @ {fx['timestamp']}")
            print(f"adr_ratio          = {ADR_RATIO}")
            print(f"implied US price   = {implied:.6f}")
            print(f"actual US price    = {us['close']:.4f}      @ {us['timestamp']}")
            print(f"premium            = {premium:+.6f}%")
            print("\nSESSION CHECK: are those three timestamps the same moment?")
            print("(They will not be. Record how far apart, and how you will label it.)")
            evidence["computed"] = {
                "implied_us_price": implied,
                "premium_pct": premium,
                "adr_ratio_used": ADR_RATIO,
            }

    with open("spike1_evidence.json", "w") as f:
        json.dump(evidence, f, indent=2)
    print("\nSaved raw evidence -> spike1_evidence.json")
    print("\nNEXT: recompute the premium in a spreadsheet from the raw numbers")
    print("above. If your number and the script's number disagree, STOP and")
    print("find out why before writing a single line of FlowDesk.")


if __name__ == "__main__":
    main()

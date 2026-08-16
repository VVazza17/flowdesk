# ADR-0002: Raw-vs-Adjusted Price Policy

**Status:** Accepted
**Date:** 2026-08-15
**Source:** M0 spike 2 (2026-07-31), §6.12

## Context

Different signals need different price bases: momentum needs split-adjusted
(or total-return) series; intraday moves, gaps, and cross-listing premiums
need raw as-traded prices at their observed timestamp; dilution needs split-
normalized share counts. "Adjusted everywhere" silently breaks the premium
monitor and any raw-timestamp comparison. M0 spike 2 had to settle what
yfinance's `auto_adjust=False` actually returns, and whether it can stand in
for raw as-traded prices.

## Decision

1. Bars store raw and split-adjusted variants side by side (§8); each signal
   declares which basis it consumes — never a single "the" price column.
2. Ingestion always passes `auto_adjust` explicitly (`False`, per decision 3)
   and never relies on the library default, because the default's value is a
   yfinance version detail, not something spike 2 tested or this ADR pins.
3. `c_raw` is not read directly from the provider. It is **reconstructed** —
   `c_split_adj` (False-mode `Close`) × cumulative split factor from `Stock
   Splits` for bars before each ex-date — except bars captured live on their
   own date, which are as-traded at capture and frozen at that basis; §6.12's
   corporate-actions workflow, not a later split, is what revises them, and
   raw history is never rewritten.
4. Idempotency (M2 gate) is defined only on `c_raw`/`c_split_adj`.
   `c_total_return` (False-mode `Adj Close`) is revision-prone and versioned
   like a FRED vintage, never treated as a fixed historical fact.

## Evidence (M0 spike 2, 2026-07-31)

Tested against NVDA's 10:1 split (2024-06-10) and KO dividends, comparing the
two explicit modes (the library default was not tested). `auto_adjust=False`
`Close` is **split-adjusted, not raw**: NVDA 2024-06-07 returned 120.888
against ~1,208.88 as actually traded — exactly 1/10. `auto_adjust=True`
silently becomes the total-return series and drops `Adj Close` (True-mode
`Close` 122.2285 == False-mode `Adj Close` 122.2285, identical) — so true
as-traded prices are obtainable from **neither** explicit mode; raw must be
reconstructed. Total-return values are also unstable across re-fetches: KO
2025-03-14 showed Close 69.16 vs Adj Close 66.7805, a $2.38 gap equal to ~5
quarters of accumulated dividends, not that date's $0.51 payout — `Adj Close`
is indexed to present and rewritten by every later dividend. Raw evidence:
`spikes/spike2_evidence.json`.

## Consequences

- A backfilled bar spanning a split is split-adjusted, not raw, until the
  reconstruction step runs; a premium computed from an unreconstructed bar
  against an effective-dated ADR ratio would be wrong by the split factor.
- Column mapping is fixed for §8: `c_split_adj` ← False-mode `Close`,
  `c_total_return` ← False-mode `Adj Close`, `c_raw` ← reconstructed/live.
- `c_total_return` needs its own revision-tracking treatment in ingestion,
  the same machinery already required for revision-prone macro series.
- The provider returns empty frames (no exception) for unknown/delisted
  tickers, and varies its column set by security type; ingestion must treat
  an empty frame as a provider-health degradation, not "no new data."
- The premium engine (§7.5) and any raw-timestamp comparison must read
  `c_raw`, never `c_split_adj`/`c_total_return`, or this ADR's bug returns.

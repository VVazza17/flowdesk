# ADR-0001: FX Base/Quote Convention

**Status:** Accepted
**Date:** 2026-08-15
**Source:** M0 spike 1 (2026-07-31), §6.10, §7.5

## Context

The Premium Monitor (§7.5) converts a foreign-listed price into USD to compare
against a US ADR price. That conversion depends on an FX rate whose direction
(currency per USD, or USD per currency) is easy to get backwards. A bare
`fx_rate` scalar is silently valid either way: multiplying by an inverted rate
still produces a plausible-looking number, not an error. For KRW/USD the two
directions differ by a factor of ~1,428, so an inversion bug would not fail
loudly — it would produce a confidently wrong premium.

M0 spike 1 pulled a live FX quote to determine, empirically, which direction
yfinance's FX tickers return, since this cannot be safely assumed from the
ticker name alone.

## Decision

1. FX rates are never stored or passed as ambiguously named scalars. They live
   in a typed `fx_rates` table (`base_currency`, `quote_currency`, `rate`,
   `observed_at`, `source`), and all conversion goes through one function,
   `convert(amount, from_currency, to_currency, observed_at)`.
2. Downstream code receives a named quantity — `usd_per_foreign_currency` —
   never a bare `fx_rate`. A bare `fx_rate` identifier is forbidden in the
   premium-calculation code path.
3. Ingestion asserts the expected magnitude range per pair and quarantines
   out-of-range values — a per-pair drift check, not a way to infer direction.

## Evidence (M0 spike 1, 2026-07-31)

yfinance ticker `KRW=X` returned **1428.60**, i.e. **KRW per USD**, so
`usd_per_krw = 1 / rate`. The verified finding is about yfinance's `XXX=X`
ticker convention — it returns foreign-currency-per-USD, confirmed for
`KRW=X` — not about magnitude. Magnitude does not generalize: `CAD=X` (~1.37)
and `EURUSD=X` (~1.08) are both >1 but quoted in opposite directions, which is
why decision 3 uses magnitude only for per-pair drift detection. Raw evidence:
`spikes/spike1_evidence.json`.

The §7.5 worked example uses a separate, illustrative USD/KRW rate of 1,390
from the spec text, not this live pull — unrelated to the 1428.60 spot rate
above, and the two are not expected to match. That example depends only on
this ADR's direction convention holding, and is the canonical regression
fixture for a reintroduced inversion bug.

## Consequences

- Every FX-consuming code path names its quantity explicitly
  (`usd_per_foreign_currency`), making an inversion bug a naming-level review
  catch instead of a silent numeric error.
- Ingestion-time magnitude assertions catch provider-side direction changes or
  outages; the premium engine refuses to compute (a config error, not a
  guess) when currency metadata is missing or ambiguous.
- The §7.5 worked example and the KRW=X finding above become mandatory unit
  test fixtures, including a non-0.1 ADR ratio, so a regression cannot pass
  CI silently.

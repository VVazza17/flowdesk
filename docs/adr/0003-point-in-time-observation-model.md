# ADR-0003: Point-in-Time Observation Model

**Status:** Accepted
**Date:** 2026-08-15
**Source:** M0 spike 3 and 3b (2026-07-31), §6.4

## Context

Macro and credit series (FRED) are revision-prone: published values change
after the fact, and — the larger trap — the *set* of observations that
existed as of a past date is smaller than what exists today. A backtest or
replay that queries "today's" store while simulating a past date can see
data that had not been published yet, an availability-based look-ahead that
filtering on the observation's own timestamp (`effective_at`) cannot catch,
because that field describes what period the data is *about*, not when it
became knowable. M0 spikes 3 and 3b had to establish whether FRED's API
actually supports reconstructing "the world as known on date X," and how
large and how irregular the revision/lag behavior is in practice.

## Decision

1. Every FRED-sourced observation is stored as a vintage with an explicit
   `available_at` (publication date), separate from `effective_at` (the
   period it describes). Revisions never overwrite an earlier vintage —
   each revised value is a new row.
2. Point-in-time queries (replay, backtest, any "as of date X" read) filter
   on `available_at`, and that filter applies to **row existence, not just
   row value** — a period that had not been published as of the simulated
   date must not appear at all, not appear with a stale value.
3. Historical backfill uses FRED's `output_type=4` (first-published value per
   observation, with `realtime_start` giving its true `available_at`) with an
   explicitly widened real-time window to seed each period's *first* vintage
   and its true publication date. This seeds initial history only — it is
   not the mechanism for capturing later revisions. Going forward, ongoing
   ingestion polls each series on its own cadence and appends a **new**
   vintage row only when the returned value differs from the last stored
   one, with `available_at` set to the poll date the change was first
   observed. Vintage storage therefore combines both: `output_type=4` for
   the historical first-print baseline, incremental polling for every
   revision after that. This makes `available_at` **exact for first prints
   only**: a revision's `available_at` is the poll date on which it was
   first detected, not FRED's true publication date, so it is accurate to
   within one polling interval. A revision published between two polls gets
   an `available_at` up to one interval late (e.g. a Tuesday revision under
   weekly polling can date as much as six days later than actually true).
   This is conservative in the safe direction — replay sees a revision no
   earlier than it could have in reality, never earlier — but it means
   replay is exact on first prints and approximate, not exact, on
   revisions.
4. The widened window (`realtime_start=1776-07-04&realtime_end=9999-12-31`
   in the spike) is required because the default (today→today) window
   returns HTTP 400 "No vintage dates exist for the specified real-time
   period" — a wrong-parameter error, not a data-availability signal, so
   ingestion must treat that specific 400 as a configuration bug, never as
   "no data yet." The exact start date is arbitrary — any date predating the
   series' first vintage works; `1776-07-04` is not itself significant.
5. Freshness/staleness thresholds (§6.11) are per-series and tolerant of
   each series' own irregular publication cadence, never a single global
   constant, and never a hardcoded lag value for any one series.
6. Daily series carry a one-session reporting lag on top of the
   `available_at` discipline above: `available_at` for a "today" daily
   reading is not actually today until the provider has published it, so a
   signal computed at market open must resolve to the prior session's
   value rather than assume same-day publication.
7. The literal string `"."` from FRED means a genuine missing observation.
   Parsers must preserve it as a gap — never coerce to `0.0`, never crash on
   it, never silently forward-fill it into a signal input.
8. An unknown/invalid FRED series ID is a loud HTTP 400 with an explicit
   message — parsers may treat that failure mode as unambiguous, unlike the
   silent-empty-frame behavior recorded for yfinance (§6.12, ADR-0002).

## Evidence (M0 spikes 3 and 3b, 2026-07-31)

**Spike 3, tested against `GDPC1` (real GDP) and `DGS10` (10Y yield):** Q1-2025
GDP read 23,526.085 to an observer standing on 2025-05-01; the same period
reads 23,548.210 today (+0.094%) — confirming values are revised. On
2025-05-01 exactly one 2025 quarter existed in the series; today four do —
confirming the observation *set* grows over time, independent of any value
change. `DGS10` reported `last_updated` 2026-07-31 against `observation_end`
2026-07-30, confirming the one-session lag on daily series that decision 6
addresses.

**Spike 3b, revision magnitude and publication lag across three series:**

| Series | Publication lag (days) | Revised? | Revision magnitude |
|---|---|---|---|
| `GDPC1` (real GDP) | 119–175 | 4 of 4 | up to ±0.4% |
| `CPIAUCSL` (CPI) | 39–78 | 11 of 12 | ~±0.04% |
| `PAYEMS` (payrolls) | 31–80 | 12 of 12 | −801k to −1,246k jobs (−0.5% to −0.8%) |

27 of the 28 observations across these three series changed after first
publication — revision is the norm, not the exception. `PAYEMS` is the
extreme case: every month revised downward by roughly a million jobs, large
enough to invert the sign of a "labour market strengthening" reading if
computed from stored latest values instead of the vintage available at the
time. Lag is not constant even within one series: `PAYEMS` ranged 31–80 days,
`CPIAUCSL` 39–78 days — the basis for decision 5's per-series thresholds.
Raw evidence: `spikes/spike3_evidence.json` and `spikes/spike3b_evidence.json`.

## Consequences

- Replay and live calculation can share one point-in-time snapshot interface
  (§16 operating principle) only because `available_at` is stored per
  observation and enforced on existence, not just value — this is the
  mechanism that makes that principle actually implementable rather than
  aspirational.
- `GDPC1`'s 119–175-day lag means quarterly macro is necessarily a
  slow-moving context input; any signal reacting to a GDP print within a
  quarter of the period it describes is reacting to data that did not exist
  yet, and must be treated as a design error if it happens.
- Provider-health checks must compare each series against its own historical
  cadence, not a shared staleness threshold, or a normally-lagged series
  (e.g. `PAYEMS` at 80 days) will falsely alarm as dead.
- Ingestion code must special-case the specific "No vintage dates exist" 400
  response so a widened-window misconfiguration is never misclassified as a
  genuine absence of data.
- A signal that reads a daily macro series at market open must resolve to
  the prior session's `available_at`-valid value, not the current calendar
  date's — the same filtering discipline as monthly/quarterly series,
  applied at a one-session granularity instead of a multi-week one.
- Because revision is the norm rather than the exception, vintage storage is
  not an optimization to defer — treating it as a Phase-2 hardening item
  would mean early signals and their outcome-tracking history are built on
  a model this evidence already falsifies.

## Addendum, 2026-11-11 (M2 slice 5; Kyle)

Decisions 1 to 8 above stand unchanged. These two notes settle what they left open when `macro_metrics` was built
(migration 0005).

### `available_at` for a date-granular publication is the end of that day in America/New_York

FRED's `realtime_start` is a date, but `available_at` is a timestamp and every point-in-time read filters
`available_at <= simulated_clock`. Decision 3 calls a first print's `available_at` "exact"; a date cannot be
exact to the instant, so the rule is:

> For a first print (`revision = 0`), `available_at` is **00:00 America/New_York at the start of the day after
> FRED's `realtime_start`** (the end of the publication day, DST-aware). For a revision it is the instant the
> poll first saw the change, which is a real timestamp.

**This is a documented bias, with a direction and a size.**

- *Direction:* always **late**, never early. A replay never sees a first print before the live system could
  have, so the look-ahead that decision 2 exists to prevent cannot come from this rule. (Midnight UTC of the
  publication date would have been *early*: an 08:30 ET release is 13:30 UTC, and a replay clock between the two
  would read a value that had not been published yet.)
- *Size:* between 0 and 24 hours, for first prints only. The lateness is 24 hours minus the release's time of day
  in New York: a release at 08:30 ET is stored about 15.5 hours late, one at 16:15 ET about 7.75 hours late. It is
  roughly constant per series (a series has a release schedule) and not stored.
- *Who it affects:* anything that replays on the publication day itself. A daily series read at the next
  session's open is unaffected, because 00:00 ET precedes any open. A replay clocked at the close of the
  publication day cannot use that day's print, though the live system could have used it from the morning.
- *Not a bias on revisions,* whose `available_at` is the poll instant. Those are late by up to one polling
  interval (decision 3), a separate and already documented bias.

**M5's outcome tracking must carry this.** Any signal-to-outcome lag, reaction time, or "how early was the
signal" measured from a FRED-sourced `available_at` overstates the delay by up to 24 hours for first prints.
Left unaccounted for, that lag would be attributed to the strategy. M5 must either report the bias beside
every such number or measure FRED-sourced inputs from the publication date (recoverable for a first print as
`date_in_New_York(available_at - 1 microsecond)`), never from `available_at` as stored.

**Assumption to verify when the backfill meets real data.** FRED states its times in US Central, so
`realtime_start` is presumably a Central date, and the end of a Central day is 01:00 ET the next day, an hour
after the 00:00 ET this rule uses. That could only matter for a release after 23:00 Central, and FRED releases
are daytime events, but it is an assumption until `realtime_start` is compared against known release times for a
handful of series.

### Vintages are immutable in the database, not only in the ingest code

Decision 1 says an earlier vintage is never overwritten. The primary key `(metric, effective_at, revision)`
stops a duplicate vintage but not an `UPDATE`, and an overwritten vintage is both catastrophic and undetectable:
a replay would read the revised value as always known, with nothing red anywhere. So `BEFORE UPDATE`,
`BEFORE DELETE` and `BEFORE TRUNCATE` triggers refuse, with one carve-out: `ingestion_run_id` may be cleared to
NULL (the foreign key's `ON DELETE SET NULL`). The sanctioned way around them, for a genuine repair that must
leave a trace, is `docs/RUNBOOK-VINTAGE-REPAIR.md`.

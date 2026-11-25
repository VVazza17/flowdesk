# ADR-0003: Point-in-Time Observation Model

**Status:** Accepted. **Decision 3's rationale is falsified** (Addendum 2026-11-13, F1); decision 4 is amended by the same addendum; **a claim of the 2026-11-12 addendum about `"."` observations is corrected** (Addendum 2026-11-13, F2); **decision 3 as rewritten by F1 is amended for late arrivals, and rules 3 and 5 exempt rolled-off periods of a declared rolling-window series** (Addendum 2026-11-24, F3). Decisions 1, 2 and 5 to 8 stand.
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
3. **[Falsified: Addendum 2026-11-13, F1: the parts saying revisions are dated by the poll, "initial history only" and "not the mechanism for capturing later revisions" no longer bind. `output_type=3` dates revisions.]**
   Historical backfill uses FRED's `output_type=4` (first-published value per
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
4. **[Amended: Addendum 2026-11-13: the 400 below is fatal for a backfill and a quiet day for a poll.]**
   The widened window (`realtime_start=1776-07-04&realtime_end=9999-12-31`
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

## Addendum, 2026-11-12 (M2 slice 5, step 2c and 3; Kyle)

Settled with the first real FRED measurements (`docs/BUILD-LOG.md`, "step 2c").

### The rule: nothing enters a point-in-time table without a real publication date

This is the rule behind the decisions below, recorded once so that it does not have to be rediscovered. A
point-in-time table answers "what was knowable on day D". A value whose publication date nobody can state was not
knowable on any particular day; giving it one (the first vintage's date, the ingest date, midnight of anything)
makes every replay that reaches it show a value as known when it may not have been. So such a value is **left out**,
not stored with a guessed `available_at`, and what is left out is **recorded**, so its absence is a fact and not a
surprise. **Slice 6 meets this again with prices** (a bar's `c_total_return` as it stood on a given day cannot be
reconstructed after the fact); it applies there unchanged.

Applied to FRED:

- **Pre-vintage observations stay out of `macro_metrics`.** `output_type=4` returns observations only from a
  series' first stored vintage onward, and says nothing about the earlier ones (GDPC1: 179 of 319 observations,
  1947 to 1991). Those exist in FRED's latest values and have no publication date. Not stored.
- **[Corrected: Addendum 2026-11-13, F2: the 32 VIXCLS dates below WERE published, as gaps, in the 2014-02-05 vintage. They are stored, and the count is renamed.]** **A `"."` with no vintage trace is skipped, with a logged count.** VIXCLS has 32 market-holiday dates inside its
  covered range that are `"."` in the latest values and appear in no vintage. No one published them as anything, so
  there is no `available_at` to give them. (A `"."` that FRED *did* publish in a vintage stays a stored NULL
  row: decision 7.) The daily poll applies the same skip.
- **The measured start of each series' record is stored beside the series list** (`settings.FRED_SERIES`:
  `first_vintage` and `first_covered_period`, measured 2026-11-12, FRED states neither). A point-in-time read must
  tell "no value exists" from "a value exists but predates the vintage record"; the boundary differs by decades
  between series (UNRATE 1960, DGS10 2005, BAMLH0A0HYM2 2023), so it cannot be a constant. A period earlier than a
  series' `first_covered_period` is the second case. Step 4's read is where this is used.

### `available_at < effective_at` is legitimate and unconstrained

IORB's first prints are published 1 to 6 days before their own period (1,932 of 1,932 rows). A value can be known
before the period it describes. No `CHECK (available_at >= effective_at)` may be added, and no ingest may clamp the
date; the migration says so beside the table so that it is read by whoever would add it in good faith.

### The backfill refuses rather than trusts

FRED signals the vintage-date cap (2000) with an explicit 400, but `output_type=4` is silent about what it leaves
out. The backfill therefore compares the first prints with the latest values and **writes nothing for a series in
which a real value has no first print and is not explained by either rule above** (`app/ingest/macro.py`,
`account_for`). A partial history must not look complete.

## Addendum, 2026-11-13 (M2 slice 5, step 3; Kyle): the rationale of decision 3 is falsified

This is the first time an ADR's rationale has been disproved by a later measurement, so the way it is recorded
is itself a decision, made once here and followed after.

### How an ADR is falsified (the mechanism)

An ADR is **binding where it interprets the spec**, and it is also a record of what was measured. A later
measurement can show that an ADR's *reasoning* rested on an assumption its own evidence never tested. That is not
a **spec defect** (`docs/SPEC-DEFECTS.md` is for a spec that contradicts itself or cannot be built), and it is not
an ordinary amendment. It gets an addendum with exactly these parts, and nothing is deleted:

1. **The assumption, quoted** from the ADR, with the decision number that relies on it.
2. **What the original evidence covered**, so that the gap is visible (here: spikes 3 and 3b exercised
   `output_type=4` only).
3. **The measurement that falsified it**: date, requests made, result. The raw numbers live in
   `docs/BUILD-LOG.md`; this addendum carries what a reader needs to trust the conclusion.
4. **What survives and what is superseded**, decision by decision. A superseded sentence stays where it is,
   carries an inline `[Falsified: Addendum 2026-11-13, F1]` marker, and is not binding any longer. A reader who
   stops at the original text is sent here.
5. **The ADR's `Status` line** names the addendum, so the state is visible from the top of the file.

Anything an ADR said that the measurement did not touch stays binding.

### F1. "A revision's true publication date can only be known to within one polling interval"

**The assumption.** Decision 3: `output_type=4` "is not the mechanism for capturing later revisions", so revisions
are caught by polling and dated by "the poll date the change was first observed", "accurate to within one polling
interval". The ADR never states that no other source of a revision's true date exists, but its design is only
necessary if that is so. (It was not chosen for request cost: decision 3 never mentions cost. Asked and answered
2026-11-12.)

**What the evidence covered.** Spikes 3 and 3b (2026-07-31) used `output_type=4` and the default window. No other
vintage output type was requested.

**The measurement.** 2026-11-12 and 2026-11-13, against the real API, several hundred requests in all:

- `output_type=3` over a real-time window returns, per observation date, one column for **each vintage in which
  that period changed**, named `<SERIES>_<YYYYMMDD>`, so a revision carries its true publication date.
- **For every one of the 17 series in 6.4 the earliest `output_type=3` column of a period equals that period's
  `output_type=4` first print, in date and in value: 0 differences.** Eleven series were compared in full (the seven that carry revisions: CPIAUCSL, UNRATE, HOUST, CSUSHPINSA,
  DRCCLACBS, MORTGAGE30US, DTWEXBGS; and BAMLH0A0HYM2, BAMLC0A0CM, SOFR, IORB); **the six
  long daily series (DGS10, DGS2, T10Y2Y, VIXCLS, DFII10, T10YIE) were compared over three windows each (first,
  middle, last), not in full.** Their sampled chains held one revision at most.
- **For every series compared in full, the last column of each period equals the latest value: 0 differences.**
- No chain held two equal consecutive values (the union of windows adds no carried-over column).
- Revisions are common, as the ADR said: of the periods `output_type=3` returned, 658 of 958 (CPIAUCSL), 657 of 919
  (UNRATE) and 812 of 813 (HOUST) were revised at least once, with up to 54 vintages on one period (CSUSHPINSA).

**What the first column holds for a period whose first print predates the vintage record.** Checked on the seven
revision-bearing series. `output_type=3` does return such periods, **but only those revised after the record
began, and their earliest column is the date of a *revision*, never the series' first vintage and never a first
print** (CPIAUCSL: 306 such periods, earliest columns 1972-08-22 to 1994-02-17). Every one of them is earlier than
the series' `first_covered_period`; none lies inside the covered range. A period never revised since the record began has no
column in `output_type=3` (a window may list it as a bare row holding only its date, which says nothing and is
ignored), and is in no `output_type=4` row. So the chain of a pre-vintage period starts at an unobserved base value.

**Cost, measured, because it decides the design.** Request time grows with the number of vintage dates in the
window, about 0.16 s each: 100 dates took 17 s, 300 took 48 s, and **600 or more (and the 1,900 the backfill uses
today) get an HTTP 504 from FRED's gateway at 60 s.** All 17 series hold 34,244 vintage dates, so a full revision
backfill is about 90 minutes of server time in windows of about 100 dates. This is a one-off; the daily poll uses a
short window and is cheap (`realtime_start=2026-10-01` on GDPC1 returned one row).

### What changes

| Decision | Status |
|---|---|
| 1 (vintage per observation, never overwritten) | stands |
| 2 (point-in-time filter on `available_at`, on existence) | stands |
| 3, "initial history only", "not the mechanism for capturing later revisions", "a revision's `available_at` is the poll date" | **Falsified. Superseded.** |
| 3, first prints from `output_type=4` | stands, and becomes a cross-check (below) |
| 4 (widened window needed; the 400 is a configuration bug) | **Amended**: the 400 is context-dependent |
| 5 to 8 | stand |

**Decision 3 becomes:** the backfill stores the **whole revision history** of every series: revision 0 is the first
print, and each later column is revision 1, 2, ... in vintage-date order, each with
`available_at = available_at_for(that vintage's date)`, the same end-of-day-in-New-York rule as a first print. The
daily poll fetches `output_type=3` over a short window since the last successful poll and appends the revisions it
finds with the same rule. **A revision is therefore dated by FRED, not by the poll, and a rebuild from FRED
reproduces the table exactly.** **[Amended: Addendum 2026-11-24, F3: a period FRED adds at a vintage dated before FlowDesk had
captured past it is dated by FlowDesk's observation (the accepting run's start), FRED's date kept for audit only; a rebuild
from FRED no longer reproduces such rows.]**

**Superseded in the 2026-11-11 addendum:** "For a revision it is the instant the poll first saw the change", and
the bullet "Not a bias on revisions, whose `available_at` is the poll instant". A revision now carries the same
0-to-24-hour lateness as a first print and no poll-interval lateness. M5 must carry that one bias for all
FRED-sourced `available_at`, not for first prints only.

**Amended decision 4: "No vintage dates exist" means two things.** For a **backfill**, whose windows are built from
the series' own vintage dates, the 400 is a bug or a moved record and **fails the run** (`permanent`, never
retried). For a **poll** over a short window it is **a normal quiet day**: nothing was published, and the poll
returns no rows. The caller says which (`FredClient.observations(..., no_vintages_is_empty=True)`); the default is
the fatal reading, and no other 400 is affected. Built and tested 2026-11-13 (`NoVintageDates`, a `Permanent`
subclass, so every existing classification is unchanged). Consequence 4 of the original text ("special-case the
specific 400 so it is never misclassified as absence of data") is superseded by this rule.

**Two exclusions, two rules, two counts (Kyle, 2026-11-13).** They look alike (a pre-vintage period is absent from
`macro_metrics`) and they are not the same thing. Each has its own reason, its own count, and its own way back.

*Exclusion A: no vintage exists.* A value in FRED's latest values that appears in **no** vintage: a period earlier
than the series' first vintage that was never revised since the record began, or a `"."` that no vintage ever
published. **Nobody can state when it was published, so it has no `available_at`**; the rule of the 2026-11-12
addendum applies exactly as written (nothing enters a point-in-time table without a real publication date). It is
**not recoverable from FRED**: the information does not exist there. Counted as `no_vintage_excluded` (never-revised
pre-vintage periods) and `gaps_without_o4_first_print_skipped` (a `"."` in neither output; formerly `gaps_without_trace_skipped`, renamed by F2 because "trace" asserted something that was not observed), as before.

*Exclusion B: revised but pre-vintage.* A period earlier than the first vintage **whose value was revised after the
record began** (measured: CPIAUCSL 306, UNRATE 119, HOUST 18, CSUSHPINSA 475, DRCCLACBS 81, MORTGAGE30US 266,
DTWEXBGS 3,415). **Every one of its revisions has a real publication date.** Its chain has no beginning: the value
it was revised *from* is unobserved. **This is recoverable data that we choose not to model, and A's reason does
not apply to it.** The reason is narrower and different: the schema's `revision` counts from a first print, so
storing the chain means either inventing a revision number for the first column we see or calling a revision a first
print, and a read "as of" a date before that column would answer "no value" for a period that had one. Counted as
`revised_pre_vintage_excluded`. **The way back:** these chains are fetched anyway (they arrive in the same
`output_type=3` responses) and then discarded, so modelling them later needs a decision on how to represent a chain
with no beginning, not a new fetch. **Revisit when** a read needs a value that was revised before the first vintage
of its own series, for example M5 outcome tracking over a pre-2005 yield period. *A recommendation Kyle approved,
not a measurement; reversing it costs a re-run of the backfill.*

The two are never summed into one "pre-vintage" number in logs, in `series_history_state`, or in the BUILD-LOG, so
that B cannot be read as a loss of the kind A is.

**A 504 is not transient.** The client maps 5xx to `transient`, and the runner retries it. For a window that is too
large, a retry can never succeed. The vintage-window fetch must split a window that times out and try the halves
(down to one vintage date), and must treat a single date that still times out as a failure.

### The refusal check is redesigned, not carried over

The check of 2026-11-12 ("the backfill refuses rather than trusts") compares first prints with latest values. It
was sound for a table holding one row per period. With full history that accounting no longer holds: a latest
value can be explained by a revision, a first print can differ from the latest value for good reasons, and the
old rule would either refuse every revised period or, loosened, accept a chain with its history cut off. The new
check is per period, over the chain, and writes **nothing** for a series unless every rule passes:

1. **Two outputs agree on the first print.** For every period `output_type=4` returns, the chain's earliest column
   has the same date and value. (Measured: no violation in 17 series.) `output_type=4` stays in the backfill for
   this one job; it is the independent witness that a chain begins at a real first print.
2. **A chain begins at a first print or is pre-vintage.** A chain whose earliest column is not an `output_type=4`
   first print and whose period is *at or after* the earliest first print means a first print was lost: refuse.
   A chain of a period *before* it is pre-vintage and revised: leave it out and count it as
   `revised_pre_vintage_excluded` (exclusion B above, never merged with exclusion A). **Amended by F2:** a chain at or after the earliest first print that begins with a `"."` which `output_type=4` does not report is accepted as a gap first print (below); one that begins with a number still refuses.
3. **A chain ends at the latest value.** The last column of every stored chain equals the series' latest value for
   that period (`output_type=1`, fetched last). A mismatch refuses. A release that lands during a backfill can
   cause one honestly; the cost is a re-run, which is idempotent, and it is preferred to storing a chain that stops
   short. (Measured: no mismatch in the eleven series compared in full; the six sampled ones were not tested for it.)
4. **Every real latest value is explained**: it has a stored chain, or it is pre-vintage and unrevised (absent from
   both outputs, counted as `no_vintage_excluded`, exclusion A), or it is a `"."` found in neither output (skipped, counted as `gaps_without_o4_first_print_skipped`). Anything
   else is a value with no history: refuse. A first print the latest values lack still refuses.
5. **Chain structure.** Columns are strictly increasing in vintage date and each date is in `series/vintagedates`.
   A column equal in value to the one before it is not a revision (decision 3, "only when the value differs") and
   is skipped and counted. (Measured: none.) A `"."` column is stored as a NULL value (decision 7), including one
   that follows or precedes a number.
6. **Stored history is a prefix of fetched history.** Against rows already stored, every stored revision must equal
   the fetched one in value and date. A stored revision that differs, or a stored chain longer than the fetched
   one, refuses and overwrites nothing. A fetched chain longer than the stored one is extended, which is also how
   the poll appends.

**What this check cannot see, stated so that nobody assumes otherwise.** A revision missing from the *middle* of a
chain is invisible to every rule above: the first print is witnessed (1), the end is witnessed (3), nothing
witnesses the interior. The defence is structural, not a check: a window's `count` must equal the rows returned,
and the union of windows must be checked for the vintage dates it covers (each date in `series/vintagedates` lies
in exactly one window). Past that, the point-in-time read is only as good as FRED's record.

### Not done by this addendum

Only the 400 rule is built. The wide-format parser, the chain-aware store, the windowed `output_type=3` fetch with
504 splitting, the redesigned check and the poll are the next step and need a plan (`docs/BUILD-LOG.md`). The
17 series' stored `revision = 0` rows from the 2026-11-12 backfill stay valid: rule 6 treats them as the first
element of each chain.

**Built since (2026-11-13, `docs/BUILD-LOG.md`):** the 400 rule (A of this addendum's build), then the parser, the windowed
fetch with 504 splitting and the six rules (commit A), then the history store, `series_history_state`, the skip and
`python -m app.backfill` (commit B). The poll (commit C) is not built. The rules above are the ones in
`app/ingest/macro.py` (`check_history`, `FRED_RULE_VERSION = 1`).

### F2. "A `"."` that appears in no vintage was never published"

The second falsification, and the first to correct a claim this ADR's own addendum made. Same mechanism as F1.

**The assumption, quoted** (addendum of 2026-11-12, "The rule: nothing enters a point-in-time table without a real
publication date", applied to FRED): "VIXCLS has 32 market-holiday dates inside its covered range that are `"."` in the
latest values and appear in no vintage. No one published them as anything, so there is no `available_at` to give them."
The count carrying the claim was named `gaps_without_trace_skipped`.

**What the evidence covered.** That conclusion was drawn from `output_type=4` and the latest values only. The record
that produced it already noted, unexplained, that some `"."` first prints *do* appear in `output_type=4` (BUILD-LOG,
step 2c: "why FRED reports those and not these was not determined"). "Appears in no vintage" was true of
`output_type=4`; it was never established for the vintages themselves.

**The measurement that falsified it** (2026-11-13). The first real run of the history backfill refused VIXCLS under
rule 2: 32 periods had an `output_type=3` chain and no `output_type=4` first print. Asked for the whole record,
2010-11-25 and 2010-12-24 each carry one column, `VIXCLS_20140205 = "."`. The 2014-02-05 vintage itself, from both
outputs (fixtures `ok_revisions_VIXCLS_holiday_gaps_2014-02-05` and `ok_first_prints_VIXCLS_2014-02-05`):
`output_type=3` shows **93 periods that became `"."`** (32 inside VIXCLS's record, 61 before it); `output_type=4`
for the same vintage returns **one row and none of the 93**. The dates were published, as gaps, with a publication
date. `output_type=4` does not always report a `"."` first print (DGS10's, DGS2's and T10Y2Y's market holidays are
reported, and are stored: 229, 229 and 138 NULL periods).

**What survives.** The rule itself: nothing enters without a real publication date. These chains have one, so the rule
admits them. Decision 7 (a published `"."` is a stored NULL row) covers them as written.

**What is superseded.** The claim that these dates have no publication date, and the name that asserted it.
- `gaps_without_trace_skipped` is renamed **`gaps_without_o4_first_print_skipped`** (code, `series_history_state`
  by migration 0007): a latest-value `"."` found in neither output, which is all that is observed.
- Rule 2 is amended (above, and `app/ingest/macro.py`): a chain at or after the earliest first print whose earliest
  column is `"."` and that `output_type=4` does not report is stored as a **gap first print** (revision 0, NULL,
  dated by its vintage) and counted apart as **`gap_first_prints_without_o4_witness`**. It is held to rule 3 like any
  chain, and it has no second source, which is why it is counted rather than folded into the first-print count.
  A chain that begins with a number and is missing from `output_type=4` still refuses: that is a lost first print.
  A gap first print *before* the record is still exclusion B.

**Did the sixteen captured series lose rows? Settled from data, 2026-11-13.** `gaps_without_o4_first_print_skipped`
is 0 in all sixteen `series_history_state` rows (the only way the old rules left a `"."` unstored without refusing the
series), and a chain starting with an unwitnessed `"."` would have refused its series, so none is among the sixteen.
Independently, FRED's own latest values for DGS10, DGS2 and T10Y2Y were compared with the table: every `"."` inside
the record is stored (229 of 229, 229 of 229, 138 of 138). So the new rule changes what *can* be stored for a series
(VIXCLS), and changes nothing already stored. **`FRED_RULE_VERSION` is therefore not bumped**, and the principle is
recorded with the evidence: *bump the version when a change makes the rules stricter or changes what is stored;
for a relaxation, count whether any captured series would store differently, and bump if any would.* Here none would.

### Decision 4 as built: the poll avoids the 400 instead of absorbing it (2026-11-13)

The amended decision 4 above says the "No vintage dates exist" 400 is a quiet day for a poll. As built, the poll never receives it, and the
reason is an **invariant**: it reads `series/vintagedates` first and only requests real-time windows that contain a vintage date, and its
window starts at the last captured vintage (inclusive), which is itself one. A quiet day is therefore "no vintage after
`captured_through`", which merges to zero new rows. The context-dependent handling stays built and tested for any caller that does ask for a
bare date range (`no_vintages_is_empty=True`); for the poll's own windows, a 400 would mean FRED's record moved and is fatal. **Whoever removes
the vintage-date read as an optimisation brings the 400 back on every quiet day, as a permanent failure.** Pinned by
`tests/test_fred_poll.py::test_the_poll_reads_the_vintage_dates_first_and_only_asks_for_windows_that_hold_one`, against a fake that answers a
vintage-less window with FRED's real 400.

### The Evidence section's GDPC1 assertion is now verified (2026-11-14)

The Evidence section states that Q1-2025 GDP read 23,526.085 to an observer on 2025-05-01 and 23,548.210 on 2026-07-31 (+0.094%), and that one
2025 quarter existed on the first date and four on the second. Until M2 slice 5 step 4, nothing re-checked that. `tests/test_macro_oracle.py::
test_adr_0003_gdpc1_assertion_as_measured_in_m0_spike_3` now runs it against a recorded live GDPC1 history, loaded through the real rules and the real
store and read through `app/services/macro_read.py`. GDPC1 is deliberately not one of the ingested series; the data is a fixture. **"Today" in that
sentence is the spike's own date**: the same period now reads 23,650.213, revised by the 2026-09-30 annual update, so the assertion is pinned to
2026-07-31 and not to the present. The end-of-day rule shows too: the print was published on 2025-04-30 and is invisible at 23:59:59 New York that day,
visible at 00:00 on 2025-05-01.

## Addendum, 2026-11-24 (M2 slice 6, F-6b; Kyle): F3, and rolled-off periods

Drafted 2026-11-20 (`docs/BUILD-LOG.md`, addendum 2026-11-20 second, item 3(ii)), approved in principle the same day with answers (a) to (c)
(addendum third), rule (c) corrected the same day (addendum fourth: absence never counts as agreement), built 2026-11-24 together with the
rolling-window fix (addendum 2026-11-24). The draft file is deleted; this is its final text. It follows the falsification mechanism of the
2026-11-13 addendum.

### F3. "A revision is dated by FRED, and a rebuild from FRED reproduces the table exactly"

**The assumption, quoted** (addendum of 2026-11-13, F1, "Decision 3 becomes"): each column is stored "with
`available_at = available_at_for(that vintage's date)`", and "a revision is therefore dated by FRED, not by the poll, and a rebuild from FRED
reproduces the table exactly." This holds only if **FRED's vintage record does not change after it is read**: a vintage date says when a value
became public only if that value was in FRED's record from that date on. The ADR never stated this, but the design depends on it.

**What the evidence covered.** F1's measurements (2026-11-12 and 11-13) compared `output_type=3` with `output_type=4` and the latest values *at
one moment*. Nothing re-read the same vintages later to see whether FRED's past record moves.

**The measurement** (BAMLC0A0CM and BAMLH0A0HYM2; evidence `docs/evidence/2026-11-20-f6b-ice-bofa-window.txt`):
- FlowDesk captured both series' history on 2026-11-12 07:00Z (`captured_through` 2026-11-12). The poll then succeeded at 2026-11-13 08:06Z,
  11-13 23:22Z, 11-14 18:34Z and **2026-11-15 13:00Z**, each time running the six rules against FRED's latest values. Rule 4 refuses any latest
  value with no chain, so (DERIVED) FRED's record at each of those times held **no period 2026-11-10**. The stored history goes 11-09, 11-11,
  11-12.
- The first poll to refuse, at **2026-11-16 15:51Z**, did so under rule 4: "a latest value has no history: 2026-11-10".
- On 2026-11-20 (MEASURED, read-only), FRED's latest values, `output_type=4` (whole record) and `output_type=3` (vintages 11-06 to 11-13) all
  hold 2026-11-10: 0.9 and 3.3, **first printed in vintage 2026-11-10**.
- So FRED added a period to a vintage dated **5 to 6 days before** anyone could read it there: between 11-15 13:00Z and 11-16 15:51Z. Dated by
  FRED, the value would be visible to a replay at the end of 2026-11-10 New York time. Nobody could have seen it before 2026-11-15 13:00Z.
- Side finding (MEASURED 2026-11-20): the latest values' `realtime_start` for that period is **not a publication date**. With no real-time
  window, `output_type=1` stamps every observation with the request's real-time date.

**Rule 4 refusing was correct.** Storing the period under FRED's date would have put a value into the past that no live observer saw.

**What survives.** Decisions 1 and 2. F1's decision 3 for the normal case: a column whose vintage FlowDesk had not yet captured past when it
first read it is dated by FRED's vintage (`available_at_for(vintage)`). That is what nearly every revision is, and it is unchanged.

**What is superseded: the rule (Kyle, 2026-11-20; built 2026-11-24).** *A period that arrives after FlowDesk has already captured past it
takes `available_at` = when FlowDesk first observed it, never FRED's earlier date.*
- **(a) "First observed" is an instant: the start of the ingestion run that accepts it** (`ingestion_runs.started_at`; Kyle, 2026-11-24).
  FlowDesk's own refusals show it saw such a value earlier (for 2026-11-10, from the 11-16 15:51Z refusal), but that instant comes only from
  parsing error text. The accepting run's start is later, so more cautious, and needs no parsing. Both are recorded in `docs/BUILD-LOG.md`.
- **(b) FRED's claimed date is kept in `macro_metrics.provider_claimed_available_at`** (migration 0014), for audit only: `available_at_for`
  of the claimed vintage, NULL on every ordinary row, with a CHECK that it is earlier than `available_at`. **No point-in-time read uses it**
  (`app/services/macro_read.py` filters on `available_at`). The store's own comparison with FRED (`macro_store.read_chains`, rules 1, 5 and 6)
  reads it back as the vintage, because that comparison is with FRED's record; read from `available_at`, the next poll would refuse the row
  under rule 5. The immutability trigger covers the column.
- **(c) Recognition (rule 4). Absence never counts as agreement.** A **late arrival** is a new period whose `effective_at` is on or before the
  series' `captured_through`, with a number, inside the record, and with no stored history and nothing in the poll's window. It **triggers its
  own fetch** (`FredClient.period_history`): `output_type=3` with `observation_start = observation_end` = the period, across every vintage from
  the period's own date on. The claimed vintage is not known until it answers. ASSUMED: no value of a period is published before the
  period's date (true of all 17 series). It is windowed and 504-halved like the history fetch, it is one request for a period a few days
  old, and every request takes a rate-budget slot. It is **accepted only if** that fetch returns at least one column, every column is a
  known vintage, all agree on one value, and that value equals the latest. Otherwise it is **refused under rule 4, named** ("a late arrival
  ... is not confirmed by its own fetch"). More than `LATE_FETCH_MAX` (10, ASSUMED) late periods in one poll of one series: nothing is
  fetched and all refuse.
- Periods after `captured_through` are unchanged (dated by FRED, F1).
- "A rebuild from FRED reproduces the table exactly" no longer holds for such rows. A rebuild reproduces FRED's record. Only FlowDesk's own
  observations reproduce what was knowable.

**A re-capture erases the evidence.** `python -m app.backfill --force`, or a `FRED_RULE_VERSION` bump, rebuilds from FRED's current record,
which dates 2026-11-10 to vintage 2026-11-10. **Evidence protection (Kyle, 2026-11-20, tightened 2026-11-24, CLAUDE.md): no re-capture of
BAMLC0A0CM or BAMLH0A0HYM2, ever, without Kyle's explicit approval.** `FRED_RULE_VERSION` was NOT bumped for this addendum. Counted from data,
nothing stored would store differently: late arrivals were always refused, so none was ever written. How a later re-capture would keep
FlowDesk-observed dates is not built; until it is, the protection is the only safeguard.

**Known limit, not built:** a later revision of a late period, published in a vintage dated before the late row's `available_at`, is dated
by FRED (F1) and so would read as knowable before its revision 0. Not seen; it is the same back-dating as F3, on a revision.

### Rolled-off periods (F-6b cause (i)): an exemption from rules 3 and 5

**The assumption:** rule 3 ("a chain ends at the latest value") and rule 5 ("every column is a known vintage date") assumed that FRED keeps
every period, and every vintage date, it has ever served. **MEASURED false for BAMLC0A0CM and BAMLH0A0HYM2.** The earliest observation in
their latest vintage moved from 2023-11-20 (read 2026-11-20) to 2023-11-24 (read 2026-11-24) in both series (Kyle: cause (i) MEASURED
2026-11-24). FRED serves them as a moving window, and the vintage dates leave `series/vintagedates` with them (MEASURED 2026-11-24 by
`docs/evidence/f6b/judge_check.py`: the 8 stored periods 2023-11-14 to 11-23 failed rule 3 AND rule 5, their vintages no longer listed).
"Exactly three years by calendar date" fits both measurements and stays ASSUMED. It is not used anywhere.

**The rule (Kyle, 2026-11-24; rule 5 the same day).** For a series **declared** `rolling_window` (`FredSeries`; BAMLC0A0CM and BAMLH0A0HYM2
only, never inferred from data), a stored period older than the latest vintage's **earliest observation**, whose merged chain is exactly the
stored one, has **rolled off**. Such periods are by construction one unbroken run at the old end.
- A rolled-off period is not a rule 3 failure.
- A rolled-off period's column whose vintage is older than FRED's earliest listed vintage date is not a rule 5 failure.
- **The stored rows are untouched.** Nothing is written for a rolled-off period, and one the fetch would extend is not rolled off.
- A stored period missing anywhere else still fails rule 3. An unknown vintage anywhere else still fails rule 5. An undeclared series that
  loses old periods still refuses.
- **No window length appears in code.**

**What this means for the data.** The stored history of the two series before FRED's current earliest observation **exists only in
FlowDesk** (Kyle, 2026-11-24), and that span grows every day. `first_vintage` and `first_covered_period` in `settings.FRED_SERIES` stay what
FlowDesk stores. A backfill re-capture would also refuse (rule 6: the stored history is not a prefix of what FRED now serves), but the
protection above is the rule regardless.

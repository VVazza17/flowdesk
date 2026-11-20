# DRAFT, NOT BINDING: proposed ADR-0003 addendum (F3). APPROVED IN PRINCIPLE by Kyle 2026-11-20, with answers (revised below)

Drafted 2026-11-20 at Kyle's request (`docs/BUILD-LOG.md`, addendum 2026-11-20 second, item 3(ii)); approved in principle
the same day with answers (a) to (c) (addendum 2026-11-20 third); **(c) corrected by Kyle the same day** (addendum
2026-11-20 fourth: absence never counts as agreement). **To be built together with the rolling-window fix (F-6b
cause (i)), after Monday's F-6b rerun; neither alone unblocks the two series.** It is appended to the ADR when it is built,
with the final text. Nothing is built.
`docs/adr/0003-point-in-time-observation-model.md` is unchanged until F3 is built. Then the text below is
appended there as "Addendum, 2026-11-2x (F3)", the Status line names it, decision 3's rewritten text (F1, "What changes")
gets an inline `[Amended: F3]` marker, and this file is deleted. It follows the falsification mechanism of the 2026-11-13
addendum, part by part.

---

### F3. "A revision is dated by FRED, and a rebuild from FRED reproduces the table exactly"

**The assumption, quoted** (addendum of 2026-11-13, F1, "Decision 3 becomes"): each column is stored "with
`available_at = available_at_for(that vintage's date)`", and "a revision is therefore dated by FRED, not by the poll,
and a rebuild from FRED reproduces the table exactly." This holds only if **FRED's vintage record does not change after
it is read**: a vintage date says when a value became public only if that value was in FRED's record from that date on.
The ADR never states this, but the design depends on it.

**What the evidence covered.** F1's measurements (2026-11-12 and 11-13) compared `output_type=3` with `output_type=4` and
the latest values *at one moment*. Nothing re-read the same vintages later to see whether FRED's past record moves.

**The measurement** (BAMLC0A0CM and BAMLH0A0HYM2; evidence `docs/evidence/2026-11-20-f6b-ice-bofa-window.txt`):
- FlowDesk captured both series' history on 2026-11-12 07:00Z (`captured_through` 2026-11-12). The poll then succeeded at
  2026-11-13 08:06Z, 11-13 23:22Z, 11-14 18:34Z and **2026-11-15 13:00Z**, each time running the six rules against
  FRED's latest values. Rule 4 refuses any latest value with no chain, so (DERIVED) FRED's record at each of those times
  held **no period 2026-11-10**. The stored history goes 11-09, 11-11, 11-12.
- The first poll to refuse, at **2026-11-16 15:51Z**, did so under rule 4: "a latest value has no history: 2026-11-10".
- On 2026-11-20 (MEASURED, read-only), FRED's latest values, `output_type=4` (whole record) and `output_type=3`
  (vintages 11-06 to 11-13) all hold 2026-11-10: 0.9 and 3.3, **first printed in vintage 2026-11-10**.
- So FRED added a period to a vintage dated **5 to 6 days before** anyone could read it there: between 11-15 13:00Z and
  11-16 15:51Z. Dated by FRED, the value would be visible to a replay at the end of 2026-11-10 New York time. Nobody
  could have seen it before 2026-11-15 13:00Z.
- Side finding, explained (MEASURED 2026-11-20): the latest values' `realtime_start` of 11-19 or 11-20 for that period is
  **not a publication date**. With no real-time window, `output_type=1` stamps every observation with the request's
  real-time date (all 794 rows of each series carried 2026-11-20 on the afternoon read). The 11-19 on one of the two
  early-morning reads is ASSUMED to be a cached response or FRED's date boundary; it was not measured.

This is the same kind of finding as F1: a point-in-time date that FRED reports and that FlowDesk could not have
observed. **Rule 4 refusing was correct.** Storing the period under FRED's date would have put a value into the past
that no live observer saw.

**What survives.** Decisions 1 and 2. F1's decision 3 for the normal case: a column whose vintage FlowDesk had not
yet captured past when it first read it is dated by FRED's vintage (`available_at_for(vintage)`). That is what nearly
every revision is, and it is unchanged. The six rules stand; rule 4 did its job.

**What is superseded: the rule (Kyle, 2026-11-20; decided).** *A period that arrives after FlowDesk has already captured
past it takes `available_at` = when FlowDesk first observed it, never FRED's earlier date.*
- **(a) "First observed" is an instant:** the `ingested_at` time of the first FlowDesk run whose response contained the
  period. Not the end-of-day rule: that rule exists because a vintage is a date, and this observation is not.
- **(b) FRED's claimed date is kept in a separate column, for audit only.** `available_at` is the first-observed
  instant, and **no read uses the claimed date** (`app/services/macro_read.py` filters on `available_at`, as now).
- **(c) Recognition (rule 4), corrected 2026-11-20 (Kyle): absence never counts as agreement.** A late period that
  appears in no vintage of the poll's window has no evidence there. (The first version accepted a late period when
  "every vintage in the poll's window agrees"; for 2026-11-10 the window held no such vintage, MEASURED, so it would
  have accepted the period on no evidence.) The rule:
  - A **late arrival** is a new period whose `effective_at` is on or before the series' `captured_through`, with no
    stored history. It **triggers its own fetch**: that period's values across every vintage from its claimed vintage
    date to today. That is one extra request per late period, and it takes its rate-budget slot.
  - It is **accepted only if** that fetch returns at least one vintage, every returned vintage agrees on a single
    value, and that value equals the latest vintage's.
  - Otherwise it is **refused as now, named.** Accepted, it is stored with `available_at` = the first-observed
    instant (a), and FRED's claimed date in the audit column (b).
  - Periods after `captured_through` are unchanged (dated by FRED, F1).
- "A rebuild from FRED reproduces the table exactly" no longer holds for such rows. A rebuild reproduces FRED's
  record. Only FlowDesk's own observations reproduce what was knowable.

**Answered 2026-11-20:** questions 1 (an instant: (a) above), 2 (a separate audit column: (b)) and 4 (recognition: (c),
as corrected) of the first draft. The second version's open question, whether "no column in the window" counts as
agreement, is settled by the correction: it never does, and the late period gets its own fetch. Still to note:
1. **The audit column needs a migration on `macro_metrics`, and is built with F3** (name and type to be chosen at the
   build, for example `provider_claimed_available_at`, NULL for every ordinary row). The model, `alembic check` and
   the rule 9 tests follow it.
2. **The per-period fetch** is a new request shape for the poll: one period (`observation_start` = `observation_end`
   = the period) across the real-time window from its claimed vintage to today. Its windowing and 504 handling,
   and whether it reuses `revisions_since`'s machinery, are chosen at the build. It takes a FRED rate-budget slot.
3. **A re-capture erases the evidence.** `python -m app.backfill --force`, or a `FRED_RULE_VERSION` bump (which the
   recorded principle requires when what is stored changes), rebuilds from FRED's current record. That record now
   shows 2026-11-10 in vintage 11-10. A re-capture would therefore store it with FRED's date, and the knowledge that
   it arrived late would be lost. **Protected meanwhile (Kyle, 2026-11-20, CLAUDE.md):** no `--force` re-capture
   and no `FRED_RULE_VERSION` bump touching the two series without Kyle's approval. The build still has to say
   how a later re-capture keeps FlowDesk-observed dates.
4. **Scope.** The two ICE BofA series are also refused under rule 3 (the rolling window, cause (i), still being
   measured). F3 alone does not unblock them.

**Not done by this draft:** no rule, code, migration or `FRED_RULE_VERSION` change. The two series stay refused (they
fail closed, as rule 4 intends) until F3 and cause (i) are both decided.

# DRAFT, NOT BINDING: proposed ADR-0003 addendum (F3), for Kyle's approval

Drafted 2026-11-20 at Kyle's request (`docs/BUILD-LOG.md`, addendum 2026-11-20 second, item 3(ii)). Nothing is built.
`docs/adr/0003-point-in-time-observation-model.md` is unchanged until this is approved. On approval, the text below is
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

**What is superseded: proposed rule (Kyle's, 2026-11-20).** *A period, or a revision, that arrives after FlowDesk has
already captured past its vintage takes `available_at` = when FlowDesk first observed it, never FRED's earlier date.*
- "Captured past it": its FRED vintage date is on or before the `captured_through` FlowDesk held when it last read
  that vintage without the column.
- Equivalently `available_at = max(available_at_for(FRED vintage), FlowDesk's first observation)`, applied only where
  FlowDesk has a recorded read that lacked the column.
- "A rebuild from FRED reproduces the table exactly" no longer holds for such rows. A rebuild reproduces FRED's
  record. Only FlowDesk's own observations reproduce what was knowable.

**Open questions for approval (each changes what gets built):**
1. **"First observed" as an instant or a day.** Proposed: the observing ingestion run's start instant. FlowDesk
   knows that exactly, and the end-of-day rule of 2026-11-11 exists only because a vintage is a *date*. The
   alternative is `available_at_for(observation date)`, for one rule across the table.
2. **How such a row is marked.** The table has no column saying an `available_at` came from FlowDesk rather than
   FRED, and `source` is the provider. A row would be indistinguishable from a FRED-dated one, except by comparing
   with FRED. Options: a counted exception in `series_history_state` (like F2's counts), or a column (a migration).
3. **A re-capture erases the evidence.** `python -m app.backfill --force`, or a `FRED_RULE_VERSION` bump (which the
   recorded principle requires when what is stored changes), rebuilds from FRED's current record. That record now
   shows 2026-11-10 in vintage 11-10. A re-capture would therefore store it with FRED's date, and the knowledge that
   it arrived late would be lost. The rule needs either a re-capture that keeps FlowDesk-observed dates, or a
   recorded statement that a re-capture may only make dates later, never earlier.
4. **How the poll recognises it.** Today such a period refuses under rule 4. Proposed: rule 4 admits a latest value
   with no chain when its first print's vintage is at or before `captured_through` and the series' state shows a
   successful read at or after that vintage. It is stored with the observed date and counted apart, and every other
   unexplained latest value still refuses. It needs the first print from `output_type=4` over the period's own
   vintage, which the poll does not fetch today: it asks only for vintages from `captured_through` on.
5. **Scope.** The two ICE BofA series are also refused under rule 3 (the rolling window, cause (i), still being
   measured). F3 alone does not unblock them.

**Not done by this draft:** no rule, code, migration or `FRED_RULE_VERSION` change. The two series stay refused (they
fail closed, as rule 4 intends) until F3 and cause (i) are both decided.

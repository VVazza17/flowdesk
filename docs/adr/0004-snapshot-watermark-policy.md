# ADR-0004: Snapshot/Watermark Policy

**Status:** Accepted
**Date:** 2026-08-15
**Source:** No M0 spike. Transcribed from §8 (Data Model), §16 (M3 gate and
operating principles), §2 decision 26, and §7.16 (live-vs-replay rule). Where
the spec is silent or ambiguous, this ADR says so explicitly rather than
inventing detail.

## Context

Decision 26 (§2) requires that "every calculation and event stamps its
snapshot, signal version, config version, and code SHA" so that any number
shown in the UI or a brief traces back to exactly what data and code produced
it. §16's operating principles go further: "historical replay and live
calculation consume the same point-in-time snapshot interface" — the same
mechanism must serve both a live compute cycle and a backtest simulating a
past date, or replay and live results are not comparable. ADR-0003
established the underlying primitive this depends on: `available_at`-filtered
existence, not just value, on revision-prone feeds. This ADR records what a
"snapshot" and a "watermark" are as concrete tables (§8), and what the M3
gate (§16) actually requires of them.

There is no spike behind this ADR because there is nothing to measure —
`source_watermarks` and `data_snapshots` are schema the spec already
specifies verbatim (§8); M3 has not been built yet, so there is no running
system to verify behavior against. This ADR is a transcription of the schema
and the M3 gate, with an explicit list of what neither actually pins down.

## Decision

1. **A watermark is per (provider, feed).** `source_watermarks(provider,
   feed, watermark_at, status, PRIMARY KEY(provider, feed))` (§8) tracks,
   for each provider/feed pair independently, how far ingestion has
   progressed. There is one row per feed, continuously updated — not a
   history of watermark values. (§11's `ingest_bars_daily`,
   `ingest_foreign_bars`, `ingest_fx`, `ingest_fred`, etc. are the tasks that
   advance it, one per feed.)

2. **A snapshot is a frozen watermark vector across feeds, with its own
   identity.** `data_snapshots(id PK, cutoff_at, status, components JSONB)`
   (§8) is explicitly commented "frozen watermark vector per feed" — i.e., a
   `data_snapshot` row captures the state of (some or all of)
   `source_watermarks` as of `cutoff_at` and freezes it under a stable `id`,
   so that later reads against that `id` always see the same data regardless
   of what ingestion does afterward. This is the mechanism that makes §16's
   "same point-in-time snapshot interface" principle concrete: live
   calculation takes the current watermark vector and freezes it into a new
   snapshot; replay constructs a snapshot for a simulated past `cutoff_at`
   using the same `available_at`-filtered existence rule ADR-0003
   established, and both then read through the identical interface.

3. **Every compute run references exactly one snapshot, by FK, not by
   re-deriving one.** `calculation_runs(id PK, signal_definition_id FK,
   snapshot_id FK, config_version TEXT, code_commit_sha TEXT, ran_at)` (§8)
   ties a specific signal version, config version, and code revision to a
   specific frozen snapshot. The M3 gate states this positively as "brief
   and every compute run reference one" (§16) — no compute path is allowed
   to read `source_watermarks` directly and skip snapshot freezing, because
   that would reintroduce the look-ahead risk ADR-0003 closed (the live
   watermark vector keeps moving; an unfrozen read is not point-in-time).

4. **Events denormalize lineage rather than requiring a join.** `events`
   (§8) itself carries `signal_definition_id`, `calculation_run_id`,
   `snapshot_id`, `config_version`, and `code_commit_sha` directly, in
   addition to the fact that `calculation_runs` already has `snapshot_id`,
   `config_version`, and `code_commit_sha`. This is confirmed independently
   at §7.16: "every event additionally stamps `signal_version`,
   `code_commit_sha`, and `snapshot_id` alongside `config_version`." The
   redundancy is deliberate, not an oversight to normalize away — an event's
   lineage must be readable without joining through `calculation_runs`, so
   provenance survives even if that convention changes later.

5. **Snapshot/calculation/event creation is one atomic transaction, with the
   snapshot itself created beforehand.** The M3 gate: "one Postgres
   transaction creates result + event + outbox row" and "killing Redis
   after commit loses nothing" (§16). Read together with decision 26
   ("Postgres owns all durable state") and the §4 architectural rule that
   Redis is never the source of truth, this means: the calculation result,
   the `events` row, and the `outbox_messages` row all commit together or
   not at all, entirely within Postgres, with no dependency on Redis being
   up at commit time. The snapshot itself (`data_snapshots`) is logically
   prior to this transaction — a `calculation_run` reads an already-frozen
   `snapshot_id`, it doesn't freeze one as part of committing its result —
   but the spec does not state this ordering explicitly; it is inferred
   from "brief and every compute run **reference** one" (reference implies
   the snapshot already exists) rather than "compute run creates one."

6. **Delivery is decoupled from computation via the outbox, and is
   watermark-like in its own right.** `outbox_messages(id PK, event_id FK,
   created_at, dispatched_at, status)` and `notification_deliveries(id PK,
   outbox_id FK, channel, status, attempts INT, last_attempt_at, response
   TEXT)` (§8) give per-channel delivery state. The M3 gate requires
   "dispatcher restart delivers pending exactly once" — the dispatcher's own
   progress through `outbox_messages` is a watermark in effect (which
   messages have been dispatched), even though the spec does not name it as
   one or give it a `source_watermarks`-style row; it is tracked via
   `outbox_messages.status`/`dispatched_at` and
   `notification_deliveries.status` instead.

7. **Payload validity is enforced before any of the above commits.** "invalid
   payloads fail before insert" (§16 M3 gate) — `events.payload` is
   "validated by Pydantic model keyed (kind, schema_version)" (§8 comment),
   and that validation must run before the transaction in decision 5 is
   opened (or at least before it commits), since a rejected payload must
   produce zero rows across `events`, `outbox_messages`, and any result
   table — not a partial write to be cleaned up later.

## Open questions for M3 (not resolved by this ADR)

- **`source_watermarks.status` and `data_snapshots.status` have no
  enumerated values in §8.** ADR-0002's `corporate_actions.confirmed` and
  ADR-0003's vintage model both got concrete state machines from their
  spikes; there is no equivalent evidence here. `providers_health` (§8) and
  the `stale: true` API convention (§6.11) suggest a fresh/stale-style
  status is plausible for `source_watermarks`, and `data_snapshots.status`
  plausibly distinguishes something like building/complete/failed given
  "brief and every compute run reference one" implies a snapshot must be
  known-complete before anything reads it — but neither is stated in the
  spec, and this ADR does not invent the literal values. That belongs in
  the M3 migration design.

- **`data_snapshots.components` (JSONB) has no defined shape.** "frozen
  watermark vector per feed" tells us it holds a per-(provider, feed) map,
  presumably keyed similarly to `source_watermarks`' composite key, but the
  spec gives no JSON schema. Undefined until M3.

- **Whether a snapshot covers all feeds or only the feeds a given
  calculation needs is not stated.** §16's "frozen watermark vector per
  feed" phrasing suggests system-wide, all-feeds scope (one snapshot
  serving every calculation run in a cycle), but M4's "calculation service
  on snapshots" (§16) does not clarify whether Rotation gets its own
  snapshot per compute tick or shares one with Breadth/Premiums/etc.
  computed around the same time. This matters for cost (snapshot creation
  overhead) and for whether two signals computed "at the same time" are
  guaranteed to see identical data — left open for M3/M4 design.

- **The relationship between `task_runs.input_watermark`/`output_watermark`
  and `source_watermarks.watermark_at` is not stated.** §8 gives
  `task_runs` its own `input_watermark`/`output_watermark` columns,
  separate from `source_watermarks`. The plausible reading — `task_runs` is
  a per-execution audit log of one ingestion job's watermark advancement,
  while `source_watermarks` holds only the current committed value — is
  inference, not spec text. Not confirmed here.

- **Snapshot/calculation_run retention is not addressed.** §8's retention
  note covers only time-series (⏱) tables (bars, macro, scores) and says
  nothing about `data_snapshots` or `calculation_runs` growth over time.
  Given every compute run creates a new row referencing a snapshot, and
  compute cadences run from every 30 minutes (rotation, themes, pairs) to
  every 5 minutes (scanner, P2) per §11, this table will grow fast; no
  pruning or archival policy exists in the spec.

## Consequences

- M3's actual migration work has three genuinely open design questions
  (status enums, `components` shape, snapshot scope-per-cycle) that this
  ADR does not resolve — they are implementation decisions for that
  milestone, not architecture-freeze violations, per §17.1's "when
  ambiguous, choose the simpler option and leave a `# DECISION:` comment."
- Decision 5's ordering (snapshot exists before a calculation_run references
  it, rather than being created as part of the same transaction) should be
  treated as binding for M3 unless a `# DECISION:` comment records a
  deviation, since it is the only reading consistent with both "reference
  one" (§16) and the atomic result+event+outbox transaction the gate
  requires — freezing a snapshot inside that same transaction would couple
  snapshot cadence to every individual calculation run, which contradicts
  "brief **and every compute run** reference one" implying shared reuse.
- Because `events` denormalizes lineage fields independently of
  `calculation_runs` (decision 4), a future schema change to
  `calculation_runs` must not silently break event-level provenance — the
  two are redundant by design, not by accident, and should be kept in sync
  deliberately if either changes.

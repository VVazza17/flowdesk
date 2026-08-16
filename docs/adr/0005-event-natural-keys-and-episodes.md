# ADR-0005: Event Natural Keys & Episodes

**Status:** Accepted
**Date:** 2026-08-16
**Source:** No M0 spike. Transcribed from §8 (`events`, `event_subjects`), §16
(M3 and M5 gates), §2 decision 26, and §7.16 (Alert Engine & Signal Outcome
Tracking — episodes are defined there, not in §7.15; see the note under
decision 4). Where the spec is silent or ambiguous, this ADR says so
explicitly rather than inventing detail.

## Context

Decision 26 (§2) states that "events deduplicate on rich natural keys and
group into episodes," as part of the same lineage discipline ADR-0004
records for snapshots and calculation runs. Two separate problems are being
solved under one heading: **natural keys** stop the same real-world
occurrence from being inserted twice (idempotent re-runs, replay re-processing
a window already live-computed), while **episodes** stop one *persistent*
occurrence from being counted many times in a scorecard (§7.16: "a premium
at z>2 for five straight days is one episode, and a scorecard that counts it
as five is lying with its own n"). Both mechanisms live on the single
`events` table (§8) that every module writes into (§4: "One event stream").
This ADR records what §8's columns and §16's M3/M5 gates actually commit to
for each mechanism, and flags what neither commits to.

There is no spike behind this ADR because there is nothing to measure —
`events` and `event_subjects` are schema the spec already specifies verbatim
(§8); M3 (natural-key + episode dedup framework) and M5 (episode-aware
scorecards) have not been built yet. This is a transcription of the schema
and the two gates, not a design document.

## Decision

1. **The natural key is `kind + specific subjects + trigger state +
   config_version`, enforced as a UNIQUE column.** `events.natural_key TEXT
   UNIQUE` (§8) carries this exact composition in its inline comment, and
   decision 26 states the purpose directly: dedup. The §7.2 pairwise
   rotation example shows why "subjects" must be directional, not just a
   set — rotation events are emitted per **ordered pair** (loser, winner);
   `rotation_event(SMH, IGV)` and a hypothetical `rotation_event(IGV, SMH)`
   are different occurrences even though they name the same two tickers, so
   the natural key must encode which role each subject plays, not merely
   which subjects are involved.

2. **`event_subjects` is the complete, authoritative subject list;
   `primary_subject_type`/`primary_subject_id` on `events` is a second,
   denormalized field carried directly on the row.** `event_subjects(event_id
   FK, subject_type TEXT, subject_id TEXT, role TEXT, PRIMARY KEY(event_id,
   subject_type, subject_id, role))` (§8) allows one event to name multiple
   subjects, each tagged with a role (the rotation example needs at least a
   `loser` role and a `winner` role) — the `role` column being part of the
   primary key means the same subject could in principle appear more than
   once under different roles on one event. `events.primary_subject_type`/
   `primary_subject_id` (§8) exist as columns on the event row itself,
   independent of this join table. The spec states both exist; it does not
   state how `primary_subject` is chosen when an event names more than one
   subject (deferred to Open Questions).

3. **Payload shape validation is a separate axis from natural-key dedup, and
   gates insertion first.** `events.payload` is "validated by Pydantic
   model keyed (kind, schema_version)" (§8 comment), and the M3 gate states
   "invalid payloads fail before insert" (§16). This is orthogonal to
   decision 1: a payload can be well-formed and still collide on
   `natural_key` (a duplicate), or it can be malformed regardless of
   whether the natural key would have been novel — validation is a
   precondition on the row existing at all, dedup is a precondition on it
   being distinct.

4. **An episode is a condition-activation window, not a firing.** §7.16
   states it directly: "An episode opens when a condition first activates
   and closes when it clears; intra-episode events share an `episode_id`
   and count once." The clearest worked example in the spec is the §7.10
   Supply-Bottleneck state machine — `Emerging` (score crosses 60),
   `Confirmed` (score ≥ 60 for 15 sessions), `Easing` (score < 40, or
   breadth < 50% for 10 sessions), `None` — where "state transitions emit
   `bottleneck_event`" (§7.10): the episode's open is the transition into
   `Emerging`/`Confirmed`, its close is the transition into `Easing`/`None`,
   and any `bottleneck_event`s fired while the segment stays in an active
   state belong to one episode.
   **Note on citation:** `events.episode_id`'s inline comment (§8) reads
   `-- §7.15 sample independence`, but the actual definition of episodes and
   sample independence is in §7.16 ("Episodes (sample independence)");
   §7.15 is Risk Lens (Scenario Replay & Signal Crowding), an unrelated
   module. The changelog records that §7.15 Risk Lens was inserted in v4.7
   and "subsequent module sections renumbered" — this comment most likely
   predates that renumbering and was never updated. Flagging per the
   project's "tell me when the spec is wrong, don't silently reinterpret it"
   rule; registered as IDEA-014 in `docs/IDEAS.md`. This ADR cites §7.16 as
   the substantive source.

5. **Natural-key dedup and episode grouping are two different mechanisms at
   two different grains, both scoped to M3, both exercised by M5.** The M3
   gate lists them as separate deliverables — "natural-key + episode dedup
   framework" (§16) — and M4's gate separately requires "reprocessing
   creates no duplicates," which is the natural-key mechanism doing its
   job (decision 1). M5's gate requires "overlapping events group into one
   episode" and scorecards to show "raw count, episode count, hit rate,
   mean/median, bootstrap CI, overlap %, regime breakdown" (§16) — episode
   count only means something distinct from raw count if multiple *distinct*
   rows (i.e., rows that already passed natural-key dedup as non-duplicates)
   can still belong to one episode. The two mechanisms are therefore not
   redundant with each other by design: natural-key dedup prevents one
   occurrence from being stored twice; episode grouping prevents a string of
   distinct occurrences that share one underlying condition from being
   scored as independent samples.

6. **Supersession exists as schema, named as a capability, not given
   operational rules in the body text.** `events.supersedes_event_id BIGINT
   NULL, status TEXT DEFAULT 'active'` (§8) are present columns. The only
   prose mention of the concept is the changelog line introducing "events
   v2 (schema-versioned payloads, event_subjects, rich natural keys,
   episodes, **supersession**)" — the word appears once, as a feature name,
   with no accompanying rule for what triggers it. Recorded as a decision
   only to the extent that the columns exist and are part of the events-v2
   lineage set; what populates them is deferred to Open Questions.

7. **Every event carries two timestamps, `occurred_at` and `detected_at`**
   (§8), with no prose definition of either anywhere else in the document
   (confirmed by search — the schema line is the only occurrence of
   `detected_at`). Recorded as a decision only to the extent that both
   columns exist and are distinct; their filtering semantics for replay are
   deferred to Open Questions rather than assumed from the
   `effective_at`/`available_at` pattern ADR-0003 established for a
   different table.

## Open questions for M3 (not resolved by this ADR)

- **Does the natural key's "trigger state" component have enough
  granularity to let a persistent condition generate new rows across
  multiple days, or would the UNIQUE constraint silently dedupe them?** §8's
  formula (`kind + specific subjects + trigger state + config_version`) has
  no explicit date/timestamp component. §7.16's own example — "a premium at
  z>2 for five straight days is one episode, and a scorecard that counts it
  as five is lying with its own n" — only makes sense if five distinct rows
  *can* exist (otherwise there is nothing for a scorecard to miscount as
  five). If "trigger state" for a threshold-crossing condition like
  `premium_event` is something coarse like "above z>2" rather than a
  day-specific value, day 2's natural key would be identical to day 1's and
  the row would never be inserted — making the episode mechanism moot for
  that case rather than load-bearing. This is a genuine contradiction
  between the §8 column comment and the §7.16 example, not just an
  unspecified detail: they cannot both hold unless "trigger state" is
  day-specific, and if it is, "dedup" is doing something narrower than the
  word implies (deduplicating exact re-processing of the same day, not
  deduplicating a persistent condition). One of the two sections needs
  correcting, and M3 cannot be built until that is decided — registered as
  IDEA-013 in `docs/IDEAS.md` so it isn't rediscovered from scratch when M3
  starts.

- **What triggers a supersede, and what does it do to the superseded row's
  `natural_key`/`episode_id`/`status`?** Neither the trigger condition
  (recomputation with corrected data? a confirmed corporate action rewriting
  a premium event? a later-arriving revision per ADR-0003?) nor the
  resulting state (does `status` move to something other than `'active'` on
  the *old* row, does the *new* row get a fresh `natural_key` or reuse the
  old one, does `episode_id` carry forward) is stated anywhere in the spec
  outside the bare column definitions.

- **What is the replay/point-in-time filtering rule for `occurred_at` vs
  `detected_at`?** ADR-0003 established that point-in-time queries filter on
  `available_at` (publication time) for row *existence*, not `effective_at`
  (what period the data describes). `occurred_at`/`detected_at` look
  structurally analogous — plausibly `occurred_at` is the domain time a
  condition became true and `detected_at` is when the compute run found it
  — but the spec never states this, and never states which one (if either
  alone) governs what a replay run at a simulated past date is allowed to
  see.

- **How is `primary_subject_type`/`primary_subject_id` populated when an
  event names multiple subjects via `event_subjects`?** For a
  `rotation_event` naming both a `loser` and a `winner`, nothing in §8 or
  §7.2 says which one (if either) becomes the "primary" subject, or whether
  the choice is per-`kind` (e.g., always the winner for rotation, always the
  segment for a bottleneck event) or left to the emitting module.

- **Does a code-only signal-definition version bump necessarily change
  `config_version`, and if not, can a redefined signal collide with its
  predecessor's natural-key space?** The natural key includes
  `config_version` (§8) but not `signal_definition_id`. §7.16 separately
  defines `config_version` as "a hash of the signal thresholds/weights
  active at emission," while `signal_definitions(id, kind, version INT,
  definition JSONB, ...)` (§8) is a distinct table, and the M4 gate says "a
  threshold **or code** change creates a new signal version" (§16) — folding
  both kinds of change into one version concept. If a code change (with no
  threshold change) bumps `signal_definitions.version` but leaves the
  threshold hash — and therefore `config_version` — unchanged, a
  redefined signal's events could, in principle, land on the same
  `natural_key` as the version it replaced. Not addressed in the spec.

## Consequences

- The first open question is the one that actually blocks M3: until
  "trigger state" granularity is pinned down for at least one persistent-
  condition event type (`premium_event` is the concrete example the spec
  itself uses), the dedup framework cannot be built without a guess, and
  guessing wrong either silently suppresses the daily rows the episode
  mechanism is supposed to collapse, or never collapses anything because
  every day already produces a colliding key. This needs a `# DECISION:` in
  the M3 migration, not a default assumption carried in application code.
- Supersession (open question 2) should be treated as present-but-unused
  schema until a concrete trigger and resulting state transition are
  designed — nothing in M3 through M5's gates requires it to function, and
  inventing semantics now would violate the same discipline ADR-0004 applied
  to `data_snapshots.status`.
- `event_subjects` is the only complete, authoritative subject record;
  `primary_subject_type`/`primary_subject_id` should not be treated as a
  substitute for it anywhere in code (e.g., outcome-tracking or alert
  matching) until a selection rule is defined, since two different
  `kind`s could reasonably want different rules and the spec picks neither.

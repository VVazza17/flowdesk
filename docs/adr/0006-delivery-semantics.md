# ADR-0006: Delivery Semantics

**Status:** Accepted
**Date:** 2026-08-16
**Source:** No M0 spike. Transcribed from §8 (`outbox_messages`,
`notification_deliveries`, `alert_rules`, `alerts`), §16 (M3 and M8 gates),
§11 (Background Jobs), §7.16 (Alert Engine), §4 (architectural rules), §2
decision 26, and §15 (Testing & Acceptance item 8). Where the spec is silent
or ambiguous, this ADR says so explicitly rather than inventing detail, and
registers genuine spec defects as new `docs/IDEAS.md` entries per the
pattern set in ADR-0005.

## Context

Decision 26 (§2) states plainly: "delivery runs through a transactional
outbox." The M3 gate builds the mechanism (`outbox_messages` +
`notification_deliveries`) and requires it to survive a crash without losing
or duplicating a delivery. The M8 gate then routes a second, older
subsystem — `alert_rules`/`alerts` (§7.16, present in §8 since before the
events-v2 lineage redesign the v4.0 changelog describes) — through that same
outbox, and adds a per-channel "retry view." This ADR records what the
schema and the two gates actually commit to about how a computed result
becomes a delivered notification, and where either the `alerts` subsystem's
older shape or the gate wording itself doesn't fully line up with the rest
of the lineage model ADR-0004 and ADR-0005 already documented.

There is no spike behind this ADR because there is nothing to measure —
`outbox_messages`, `notification_deliveries`, `alert_rules`, and `alerts`
are schema the spec already specifies verbatim (§8); M3 and M8 have not
been built yet. This is a transcription of the schema and the two gates,
not a design document.

## Decision

1. **The outbox is the transactional bridge from "an event was written" to
   "something should be delivered," committed atomically with the event.**
   `outbox_messages(id PK, event_id FK, created_at, dispatched_at, status)`
   (§8) keys directly off `events.id`. The M3 gate: "one Postgres
   transaction creates result + event + outbox row" and "killing Redis
   after commit loses nothing" (§16). Read with decision 26 ("Postgres owns
   all durable state," "delivery runs through a transactional outbox") and
   the §4 rule that Redis is never the source of truth: the durable fact
   "this needs delivering" is committed in Postgres in the same transaction
   as the event itself, so a crash between computing a result and notifying
   about it cannot silently lose the notification — a separate dispatcher
   reads `outbox_messages` afterward and does the actual sending.

2. **Delivery fans out per channel below the outbox row, with each channel
   tracked independently.** `notification_deliveries(id PK, outbox_id FK,
   channel, status, attempts INT, last_attempt_at, response TEXT)` (§8)
   means one `outbox_messages` row can produce multiple
   `notification_deliveries` rows — one per channel (`discord|websocket|
   inapp|email`, per the column comment). This is what makes the M8 gate's
   "killing Discord loses nothing" meaningful: Discord's delivery state is
   one row among several, not a property of the outbox row itself, so a
   Discord outage cannot corrupt or block the in-app delivery record.

3. **`notification_deliveries.status` is a fully enumerated value set,
   stated directly in the schema.** The column comment gives all four
   values explicitly: `pending|delivered|retrying|permanently_failed`
   (§8). This is unlike `source_watermarks.status` and
   `data_snapshots.status` (ADR-0004), which have no enumerated values
   anywhere in the spec — delivery status is one of the few status-typed
   columns in the schema that is actually fully specified.

4. **Three separate dedup/idempotency mechanisms operate at three different
   layers of the pipeline, and none substitutes for another.**
   (a) `events.natural_key` (§8; ADR-0005) — is this *occurrence* new.
   (b) `alerts.fingerprint = sha1(rule_id + entity + date)` (§7.16, §8) —
   has this *rule* already fired for this *entity* today; directly tested
   by MVP acceptance item 8: "`congress_ticker=NVDA` rule fires at most
   once per qualifying day, delivered in-app + Discord" (§15), and §7.16's
   own framing: "one fire per day per entity per rule."
   (c) `notification_deliveries` per-channel `status`/`attempts` (§8) — has
   *this specific channel* already been notified for *this outbox row*.
   A duplicate at one layer is not a duplicate at another: the same alert
   could legitimately retry a failed Discord send (layer c) without
   re-firing the rule (layer b), and the same rule could fire once per day
   for two different entities (layer b) from two different events (layer
   a) without either being a "duplicate" of the other.

5. **As of M8, alert delivery is explicitly routed through the outbox
   mechanism, not a parallel delivery path.** The M8 gate: "typed alert
   rules; in-app + Discord via outbox with per-channel delivery states and
   a retry view" (§16). This decision item is paired with a structural gap
   in how `alerts` connects to the rest of the lineage model — see Open
   Questions and `IDEA-016`.

6. **Two distinct dead-man mechanisms exist at two different layers, not
   one.** An **external**, out-of-process ping-monitor is built in M1: "an
   external dead-man heartbeat (free ping-monitor service — the monitor
   must live outside the monitored box)" (§16 M1 gate), matching decision
   26's "the dead-man monitor lives outside the monitored host" — this
   detects total outage of the whole application, including the case where
   the app is too dead to report on itself. Separately, an **internal**,
   in-app `heartbeat` Celery Beat task (§11) posts a content-bearing daily
   status to the Discord webhook (feeds green/stale, quarantine count, last
   brief generated) and fires "an explicit failure alert" if the daily
   brief hasn't generated by 09:00 ET or any Phase 1 feed is stale past its
   own per-feed threshold. This second mechanism is what the M8 gate's
   "brief dead-man alert" (§16) refers to — not a second external monitor;
   it is a content-aware internal check layered on top of the
   content-blind external liveness check.

7. **Retry semantics differ by scope, and the spec does not connect them
   explicitly.** §11's "Task rules: max_retries=3 exponential backoff" (§11)
   governs the scheduled Celery Beat compute/ingest tasks generally — the
   ~28 jobs in that section's table. The M8 gate's "retries never duplicate
   in-app alerts" (§16) is a distinct guarantee about the *delivery* layer.
   Recorded as a decision only to the extent that these are stated as
   separate concerns; which of decision 4's three dedup layers actually
   backs the M8 guarantee is deferred to Open Questions.

## Open questions for M3/M8 (not resolved by this ADR)

- **`alerts` (§8) has no FK back to the `events` row it presumably fired
  from.** `alerts(id PK, rule_id FK, fingerprint TEXT UNIQUE, entity TEXT,
  payload JSONB, fired_at, delivered JSONB)` names no `event_id`,
  `calculation_run_id`, or `snapshot_id` — nothing that ties an alert to the
  specific event that triggered it, even though §4 states directly that
  "Alerts, outcome tracking, websockets, and scorecards all consume this
  one stream [`events`]," and decision 26's lineage discipline stamps
  snapshot/calculation/code lineage on every event so any displayed number
  traces back to its source. The M4 gate requires "one event appears in
  API, alert history, and outcome queue" (§16) — querying "the alert(s) for
  event X" or "the event behind alert Y" needs some link, and none is
  declared. This is a structural gap between the events-v2 lineage model
  (§8's `events`/`event_subjects`, ADR-0004/ADR-0005) and the older
  `alert_rules`/`alerts` tables, which read as though they predate that
  redesign and were never updated to match it. Registered as **IDEA-016**
  in `docs/IDEAS.md`.

- **The M3 gate's "dispatcher restart delivers pending exactly once" (§16)
  is not achievable as literally stated for every channel
  `notification_deliveries` covers, and this is a spec defect, not just an
  unspecified mechanism.** For webhook-based external channels — Discord is
  the only P1 example (§7.16: "Delivery: in-app + Discord webhook (P1, both
  free)") — exactly-once delivery is not achievable by any dispatcher
  design: Discord's webhook API exposes no idempotency key, so a send that
  times out after the server actually received it is indistinguishable,
  from the dispatcher's side, from one that never arrived. Retrying either
  duplicates the post or drops it — there is no third option. At-least-once
  (accepting occasional duplicate posts on retry) is the real ceiling for
  that channel. In-app delivery is different in kind: it's a Postgres write
  the app fully controls, so exactly-once *materialization* (e.g., an
  insert gated by a `UNIQUE(outbox_id, channel)`-style constraint — not
  currently declared in §8) is genuinely achievable there. This is
  presumably why the M8 gate narrows its own claim to "retries never
  duplicate **in-app** alerts" rather than repeating M3's unqualified
  "exactly once" — M8's wording may already reflect this boundary even
  though M3's gate never states it. Registered as **IDEA-017** in
  `docs/IDEAS.md`.

- **`alerts.delivered JSONB` (§8) and the full `notification_deliveries`
  table appear to represent overlapping information with no stated
  relationship.** Is `alerts.delivered` a denormalized snapshot of that
  alert's `notification_deliveries` rows (and if so, refreshed when, by
  what), or an independent record that can drift from the per-channel
  table? Same shape as the `primary_subject`/`event_subjects` question
  ADR-0005 left open for `events` — not resolved here either.

- **No outbox-dispatcher or notification-sender task appears in §11's
  Celery Beat table.** The M3 gate requires a "dispatcher" that can
  "restart" (§16), which reads as a distinct, presumably long-running
  process — but §11 lists roughly 28 scheduled ingest/compute jobs and
  nothing resembling a delivery dispatcher. Whether it is a Beat-scheduled
  periodic task omitted from the table, or a different kind of always-on
  worker outside the Beat schedule entirely, is not stated.

- **M8 promises "a retry view" (§16) with no named endpoint or page.** §9's
  API table and §10's frontend spec both describe `/alerts` as "history +
  typed rule-builder forms (no raw JSON in UI)" (§10) with
  `GET /alerts?since=`, `POST /alerts/rules`, `GET/DELETE /alerts/rules/{id}`
  (§9) — none of which obviously covers a per-channel delivery-retry view.
  Whether the retry view is a sub-view of `/alerts`, a new page, or a new
  endpoint is not stated.

## Consequences

- The `IDEA-016` gap is the one most likely to bite first: without an
  `event_id` (or similar) on `alerts`, the M4 gate's "alert history" join
  and the M8 gate's per-alert delivery-retry view either need an
  undeclared link added to the schema before those milestones are built, or
  need to be implemented by matching on `alerts.fingerprint`'s embedded
  `entity`/`date` against `events.primary_subject_id`/`occurred_at` — a
  fragile, implicit join the spec never states as intentional. This should
  get a `# DECISION:` comment (most likely: add `event_id FK` to `alerts`)
  before M3's outbox work or M8's alert-rules work begins, not be
  discovered mid-implementation.
- The `IDEA-017` gap should be resolved before M3's acceptance tests are
  written, not after: a test suite that literally asserts "exactly once"
  against a mocked Discord webhook is testing something no real
  implementation can satisfy, and would either be quietly weakened later or
  would block the M3 gate on an impossible bar. The in-app/external split
  this ADR proposes should be decided (or a different split chosen) before
  those tests are drafted.
- Because three separate mechanisms handle dedup at three different layers
  (decision 4), test coverage for "no duplicates" needs to exercise all
  three independently — a test that only re-runs a calculation (natural_key)
  says nothing about whether a retried Discord send duplicates
  (notification_deliveries), and vice versa.
- The two dead-man mechanisms (decision 6) should stay described as
  layered, not redundant, in any implementation notes: removing the
  internal `heartbeat` task because "the external monitor already covers
  it" would silently lose the brief-lateness and per-feed-staleness checks,
  which the external ping-monitor cannot see.

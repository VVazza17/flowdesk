# FlowDesk — Spec Defects Registry

Flagged contradictions, stale cross-references, and other defects found in the frozen
`docs/architecture.md` spec (or in this project's CLAUDE.md, where it cites the spec as its
authority). These are **not** parked features — see `docs/IDEAS.md` for that registry — they
are open questions the spec itself needs a correction release (or a `# DECISION:` comment) to
resolve, each blocking a specific milestone until it's addressed.

Numbering is shared with `docs/IDEAS.md` (both draw from the same IDEA-NNN sequence) so IDs
never collide between the two files. This file starts at IDEA-013 because IDEA-001–012 are
parked features that stayed in `docs/IDEAS.md`.

---

## IDEA-013: Natural-key / episode contradiction in `events` (§8 vs §7.16)

**Status:** Open — spec correction needed, blocks M3
**Raised:** 2026-08-16 (ADR-0005)

### The proposal
Not a feature proposal — a contradiction between two parts of the frozen spec. `events.natural_key`
(§8) is defined as `kind + specific subjects + trigger state + config_version`, with no
date/timestamp component. §7.16's own worked example — "a premium at z>2 for five straight days
is one episode, and a scorecard that counts it as five is lying with its own n" — requires five
distinct rows to exist, or there is nothing for a scorecard to miscount as five. These two cannot
both hold unless "trigger state" is day-specific; if it is, the natural key is deduplicating
same-day reprocessing only, not a persistent condition — narrower than "dedup" implies elsewhere
in the spec (decision 26, §16 M3 gate).

### Why it is appealing
N/A — this is a flagged spec defect, not a proposed feature. No appeal case applies.

### Why it is parked
Not parked by choice — genuinely unresolved. M3 cannot be built without deciding which side is
wrong: either §8's natural-key formula needs an explicit time component, or §7.16's example (and
the raw-count-vs-episode-count scorecard distinction it implies) needs correcting. ADR-0005
documents the tension but does not resolve it, per this project's rule that ADRs cite spec
sections rather than invent detail.

### The version that would be legitimate
Two candidate resolutions, neither chosen here: (a) add a time/date component to the natural-key
formula so day-over-day persistence produces distinct rows, restoring the §7.16 example; or (b)
correct §7.16's example and the raw-count-vs-episode-count scorecard distinction to match a
coarser, non-time-scoped natural key. Either is a one-section correction release, same class as
v4.9.9/v4.9.10.

### Unfreeze triggers
Resolve with a `# DECISION:` comment or a spec correction release before the M3 dedup framework
is implemented — not a gate that opens on its own, a decision someone has to make. See ADR-0005,
"Open questions for M3," item 1.

## IDEA-014: Stale `§7.15` cross-reference on `events.episode_id` (should read §7.16)

**Status:** Open — one-line spec correction, cosmetic (does not block M3 on its own)
**Raised:** 2026-08-16 (ADR-0005)

### The proposal
Not a feature proposal — a stale citation. `events.episode_id`'s inline comment (§8) reads `--
§7.15 sample independence`. §7.15 is Risk Lens (Scenario Replay & Signal Crowding), unrelated to
episodes. The actual definition of episodes and sample independence is in §7.16 ("Episodes
(sample independence)"). The changelog records that §7.15 Risk Lens was inserted in v4.7 and
"subsequent module sections renumbered"; this comment most likely predates that renumbering and
was never updated.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice — a one-line comment fix that simply hasn't been made yet. Noted here so it
isn't rediscovered from scratch; see also the matching pattern under IDEA-006 (the "enters §7.16
when the simulator is built" line, which should likely read §7.17 for the same renumbering
reason), and IDEA-015 (a third, unrelated instance of the same class of drift).

### The version that would be legitimate
Change the §8 comment from `-- §7.15 sample independence` to `-- §7.16 sample independence`.

### Unfreeze triggers
None needed — this is a same-class correction to the v4.9.9/v4.9.10 changelog corrections already
in the document, whenever the next correction pass happens. See ADR-0005, decision 4.

## IDEA-015: CLAUDE.md's "never integrate a broker API" hard rule contradicts revised decision 15

**Status:** Open — project-instruction correction needed; also documents a systemic pattern (see note)
**Raised:** 2026-08-16 (found while backfilling this registry for ADR-0005)

### The proposal
Not a feature proposal — a contradiction between this project's CLAUDE.md hard rules and the spec
CLAUDE.md cites as its own authority. §17.1 states, verbatim: "Never integrate a broker API, order
routing, or any trade-execution code, in any phase, regardless of user-story framing (§2.15)."
Decision 15 — the section §17.1 cites — says the opposite: "(Revised at owner's direction, v4.1)
FlowDesk is two planes: the Intelligence Plane never executes; the Execution Plane may — own
capital only, behind hard gates." §17.1's hard rule is pre-v4.1 text that was never updated when
decision 15 was revised to add the gated Execution Plane, and it still cites the now-contradictory
section as its own authority. CLAUDE.md transcribed §17.1 faithfully — the drift originates in the
spec, not in CLAUDE.md's transcription of it.

### Why it is appealing
N/A — flagged spec/instruction defect, not a proposed feature.

### Why it is parked
Not parked by choice — this is a live disagreement between CLAUDE.md (which states it overrides
default behavior) and docs/architecture.md §2 decision 15, and an implementing agent will follow
whichever one it read most recently unless the owner states which governs. Not something an agent
should resolve unilaterally, since it changes what is and is not permitted to be built.

### The version that would be legitimate
Two options, both defensible, neither chosen here:
(a) **CLAUDE.md is a deliberate, stricter override** — the owner does not want execution code in
this repository regardless of what the spec's two-plane design permits. In that case CLAUDE.md's
line should stand, but should say explicitly that it overrides §2.15 rather than (incorrectly)
citing it as agreeing.
(b) **CLAUDE.md should match decision 15's gated two-plane design**, e.g.: "The Intelligence
Plane never executes. Execution-Plane code is out of scope until M0–M8 ship and decision 15's
gates are met — never build it opportunistically." This preserves the practical protection
(nothing gets built prematurely) without contradicting §2.

### Unfreeze triggers
Requires an explicit owner decision between (a) and (b), then a matching edit to CLAUDE.md's
§17.1 hard-rule line. Not resolved by this entry.

**Broader pattern (updated as later ADRs surfaced more instances — see IDEA-016, IDEA-018):**
this is now five confirmed instances of the same drift class, found across ADR-0004 through
ADR-0006 — a revision or renumbering happened somewhere in the document and a dependent reference
elsewhere was never updated to match: (1) `events.episode_id`'s `§7.15` comment, stale since the
v4.7 section renumbering (IDEA-014); (2) IDEA-006's "enters §7.16 when the simulator is built"
line, same v4.7 renumbering, should read §7.17; (3) this entry — pre-v4.1 text surviving the
decision-15 revision; (4) `IDEA-003`'s "confidence tier: low, per ADR-0007" citation — confidence
tiers are §7.16/v4.9 content, and none of the seven planned ADRs (ADR-0007 is private-vs-public
deployment mode) covers them (IDEA-018); (5) `alerts` (§8) having no `event_id` FK back to
`events`, because `alert_rules`/`alerts` predate the events-v2 lineage redesign and were never
updated to match it (IDEA-016) — this last one matters most for the pattern, since it shows the
drift isn't confined to prose citations, it reached the schema itself. A systematic pass — grep
every `§` cross-reference in the spec and CLAUDE.md, and check the target section actually still
says what the citing text claims, plus a schema-level check for tables that predate a later
redesign — is worth doing as its own piece of work, separate from the remaining ADRs.

---

## IDEA-016: `alerts` has no FK back to the `events` row it fired from

**Status:** Open — spec/schema defect, likely blocks M4's "alert history" join and M8's
per-alert retry view
**Raised:** 2026-08-16 (ADR-0006)

### The proposal
Not a feature proposal — a structural gap between two subsystems in the same schema.
`alerts(id PK, rule_id FK, fingerprint TEXT UNIQUE, entity TEXT, payload JSONB, fired_at,
delivered JSONB)` (§8) names no `event_id`, `calculation_run_id`, or `snapshot_id` — nothing
ties an alert row to the specific `events` row that triggered it. §4 states directly that
"Alerts, outcome tracking, websockets, and scorecards all consume this one stream [`events`],"
and decision 26's lineage discipline stamps snapshot/calculation/code lineage on every event so
any displayed number traces back to its source; `alerts` has none of that. The M4 gate requires
"one event appears in API, alert history, and outcome queue" (§16) — querying "the alert(s) for
event X," or displaying the event lineage behind a given alert, needs a link that isn't declared.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice — genuinely unresolved. `alert_rules`/`alerts` read as though they predate
the events-v2 lineage redesign (the v4.0 changelog: schema-versioned payloads, event_subjects,
rich natural keys, episodes, supersession, transactional outbox) and were never updated to match
it — the same class of drift as IDEA-014 and IDEA-015 (a revision happened and a neighboring
piece of the document/schema wasn't updated to match), this time in the schema itself rather than
in a cross-reference or a hard rule.

### The version that would be legitimate
Most likely fix, not applied here: add `event_id FK` (and arguably `calculation_run_id`,
`snapshot_id` for full parity with `events`) to `alerts`, so an alert's lineage is a direct join
rather than an implicit match on `fingerprint`'s embedded `entity`/date against
`events.primary_subject_id`/`occurred_at`.

### Unfreeze triggers
A `# DECISION:` comment or spec correction release, before M3's outbox work or M8's alert-rules
work begins — not something to discover mid-implementation. See ADR-0006, Open Questions and
Consequences.

## IDEA-017: M3's "exactly once" dispatch guarantee is unachievable for webhook channels

**Status:** Open — spec defect (overclaim), not just an unspecified mechanism
**Raised:** 2026-08-16 (ADR-0006)

### The proposal
Not a feature — an overclaim in the M3 gate. "dispatcher restart delivers pending exactly once"
(§16 M3 gate) is stated as a flat, universal guarantee across whatever
`notification_deliveries.channel` covers (`discord|websocket|inapp|email`, §8). Exactly-once
delivery is not achievable for Discord (the only P1 external channel, §7.16) by any dispatcher
design: Discord's webhook API exposes no idempotency key, so a send that times out after the
server actually received it is indistinguishable, from the dispatcher's side, from one that never
arrived. Retrying either duplicates the post or drops it — there is no third option. At-least-once
(accepting occasional duplicate posts on retry) is the real ceiling for that channel. In-app
delivery is different in kind: it's a Postgres write the app fully controls, so exactly-once
*materialization* (e.g., an insert gated by a `UNIQUE(outbox_id, channel)`-style constraint — not
currently declared in §8) is genuinely achievable there. This is presumably why the M8 gate
narrows its own claim to "retries never duplicate **in-app** alerts" rather than repeating M3's
unqualified "exactly once."

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice. M3's literal gate text, taken as written, is unsatisfiable for the Discord
channel — no implementation can pass a test asserting exactly-once webhook delivery, because the
failure mode (an ambiguous timeout) is a property of the transport, not the code.

### The version that would be legitimate
Split the M3 gate's single "exactly once" line into two explicit guarantees: (a) in-app delivery
and outbox/event bookkeeping are exactly-once (Postgres-enforced); (b) external webhook channels
(Discord, and any future paid email channel) are at-least-once, with
`notification_deliveries.attempts`/`status` used to bound and surface retries rather than to
promise a guarantee the transport can't back. M8's "retries never duplicate in-app alerts" already
reads consistently with this split; M3's gate is what needs the correction.

### Unfreeze triggers
A spec correction release (same class as v4.9.9/v4.9.10) to reword the M3 gate, ideally before
M3's acceptance tests are drafted — a test suite that literally asserts "exactly once" against a
mocked Discord webhook would either be quietly weakened later or block the gate on an impossible
bar. See ADR-0006, Open Questions and Consequences.

## IDEA-018: `IDEA-003`'s "per ADR-0007" citation is dangling

**Status:** Open — stale cross-reference in the registry itself
**Raised:** 2026-08-16 (found while backfilling this registry for ADR-0005; registered separately
per ADR-0006 review)

### The proposal
Not a feature proposal — a stale citation inside `IDEA-003` (Options-flow detail), unrelated to
that entry's own subject. Its Unfreeze triggers section reads: "Options-chain ingestion has proven
stable enough that OI and volume fields are trustworthy (delayed chains are the weakest data
source in the stack — confidence tier: low, per ADR-0007)." Confidence tiers (high/medium/low,
weakest-input chips, hedged brief wording) are §7.16/v4.9 content ("Input confidence tiers and
score bands," introduced in the v4.9 changelog) — nothing to do with ADR-0007, which this
project's ADR plan (CLAUDE.md; ADR-0004/0005/0006's own numbering) assigns to private-vs-public
deployment mode. None of the seven planned ADRs covers confidence tiers at all.

### Why it is appealing
N/A — flagged citation defect, not a proposed feature.

### Why it is parked
Not parked by choice — a dangling reference that predates the current ADR numbering (IDEA-003 was
raised 2026-08-01, before ADR-0001 through ADR-0006 existed) and was never reconciled against it.
Left in place rather than corrected here per the instruction not to modify IDEA-001 through
IDEA-005 (IDEA-003 is one of those).

### The version that would be legitimate
Either drop the "(...per ADR-0007)" parenthetical from IDEA-003 (confidence tiers are already
fully specified in §7.16 with no ADR needed), or point it at wherever confidence-tier provenance
actually ends up documented, if that ever becomes its own ADR-adjacent note.

### Unfreeze triggers
A one-line edit to IDEA-003 itself, whenever someone other than this drift-preserving pass touches
that entry. Not resolved here.

## IDEA-019: M1 gate's "public profile exposes read-only routes only" conflicts with §14's admin-route design

**Status:** Open — ambiguous acceptance gate, needs a `# DECISION:` before M1's tests are written
**Raised:** 2026-08-16 (ADR-0007)

### The proposal
Not a feature — a tension between an exit gate and the section it's testing. §14 gives public
deployment mode a two-tier design: anonymous routes are read-only, but there is a "separate
authenticated admin routes" surface (session cookie + CSRF, audit log) that can mutate — alert-rule
mutation is disabled only "for anonymous users," implying the authenticated tier can still mutate.
The M1 gate (§16) instead states flatly: "public profile exposes read-only routes only." Read as a
literal route-level test (no non-`GET` endpoint reachable under the public compose profile,
authenticated or not), this excludes the very admin routes §14 requires. Read loosely (anonymous
access is read-only), it's consistent with §14. The gate text doesn't say which.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice — an acceptance test for M1 has to pick a reading before it can be written,
and the two readings produce materially different implementations (no public admin surface at all,
vs. an authenticated admin surface that mutates).

### The version that would be legitimate
Reword the M1 gate to name the scope explicitly, e.g.: "anonymous access under the public profile
is read-only; the authenticated admin surface may mutate per §14." Leaves the two-tier design
intact and makes the gate testable.

### Unfreeze triggers
A spec correction release or a `# DECISION:` comment before M1's acceptance tests are drafted. See
ADR-0007, Open Questions and Consequences.

## IDEA-020: LLM budget hard-stop depends on a non-durable Redis counter

**Status:** Open — fail-open risk in a governance guarantee, small in dollar terms but real
**Raised:** 2026-08-16 (ADR-0007)

### The proposal
Not a feature — a reliability gap in a cost-control mechanism. §14's cent-level LLM spend cap is
enforced entirely by "a Redis daily token-budget counter that hard-stops LLM calls when exceeded."
Decision 26 (§2) and the §4 architectural rule both state Redis is never the source of truth and
that killing it should lose nothing durable. But this counter *is* the whole enforcement mechanism
for a stated governance guarantee — the one exception to "$0/month in default configuration" (§14),
capped. A Redis restart or eviction mid-day resets the counter to zero, and the system then honors
another day's worth of spend before hitting the cap again: a real, if small ("roughly cents/day"
per §14), violation of the stated guarantee, caused by exactly the failure mode decision 26 was
written to make safe to ignore everywhere else.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice. Given the dollar amounts involved this is unlikely to be a real financial
problem, but as specified the cap is best-effort, not the "hard-stop" the spec's own wording
claims.

### The version that would be legitimate
Either state the cap as best-effort explicitly (drop "hard-stops"), or give the counter a cheap
durable backstop — e.g., a Postgres row written on the same cadence, costing nothing under
Zero-Cost Mode — before `ENABLE_AI=true` is treated as safe to leave on unattended.

### Unfreeze triggers
A spec correction release, or a `# DECISION:` comment adding the durable backstop, before
`ENABLE_AI=true` is documented as safe for unattended use. See ADR-0007, Open Questions and
Consequences.

---

## IDEA-021: Health route names disagree across §5, §9 and §16 (and within CLAUDE.md)

**Status:** Open — resolved for the build by judgment (below); the spec text is still stale
**Raised:** 2026-10-28 (M1 slice 2)

### The proposal
Not a feature — a naming contradiction. §9 line 968 lists `GET /healthz · GET /health/providers`
and §5 line 255 says "/healthz per service". §16's M1 scope (line 1107) names
`/health/live·ready·data·providers`. §15 names no health route at all, so no acceptance test
settles it. CLAUDE.md contradicted itself the same way: its slice list used `/health/live`
while its tech-stack line said `/healthz`.

### How it was resolved for the build
Slice 2 serves `/health/live`; slice 6 adds `ready`, `data` and `providers` under `/health/`.
No `/healthz` alias exists. CLAUDE.md's tech-stack line now reads "`/health/*` endpoints per
§16 M1 scope".

**This is a judgment, not a mechanical application of a stated rule.** CLAUDE.md's precedence
rule is literally scoped to §3 versus §16 ("Where §3 and §16 disagree, §16 governs"). §9 and §5
are not §3. Extending the rule to them is an inference from §16 being "the single normative
sequence" for the build, and from its M1 scope being the only place the full four-route family
is named. A reader who disagrees with that inference should treat §9 line 968 as the
authority and reopen this entry.

### Why it is parked
Not parked by choice. The frozen spec cannot be edited in place, so §9 and §5 stay stale until
a correction release.

### The version that would be legitimate
A spec correction release changing §9 line 968 and §5 line 255 to the `/health/*` family, plus
a one-line note in §16 that §16 governs the API surface it names.

### Unfreeze triggers
A spec correction release. Until then, nothing blocks a milestone: the build follows §16.

---

## IDEA-022: §8's ops tables leave types, nullability and constraints unspecified

**Status:** Open — resolved for the build by stopgap (below); the spec text is still silent
**Raised:** 2026-10-28 (M1 slice 4)

### The proposal
Not a feature — an under-specification. §8 (lines 778–802) lists columns for `providers_health`,
`ingestion_runs`, `task_runs` and `backfill_runs`, but types only `INT`, `TEXT` and the primary
keys. Left open: the `id` type; every timestamp type; nullability and defaults; the `status`
vocabularies (other §8 tables such as `data_quarantine.state` and
`notification_deliveries.status` list theirs; these four do not); and the type of
`task_runs.input_watermark` / `output_watermark`. One question is a real design gap, not a type
choice: `backfill_runs` has `feed` but no `provider`, and no uniqueness rule, so it is
undefined whether a feed may have several runs or exactly one active run.

### How it was resolved for the build
Migration `0002` uses `BIGINT` identity ids, `TIMESTAMPTZ` for every timestamp and both
watermarks, `TEXT` for `status` with no CHECK, NOT NULL only on `provider`, `feed`,
`task_name`, `status` and `started_at`, and no indexes beyond the primary keys. No columns were
added. `backfill_runs` has no uniqueness constraint. Each choice carries a `# DECISION:`
comment in `backend/app/models/ops.py`.

**Typing the watermarks as TIMESTAMPTZ assumes every feed watermarks temporally.** If a feed
ever needs a sequence number or a cursor instead, that is where it breaks.

### Why it is parked
The frozen spec cannot be edited in place. Nothing in M1 reads these columns' semantics.

### The version that would be legitimate
A spec correction release stating the column types, nullability, `status` vocabularies, the
watermark type, and whether `backfill_runs` is one-active-run-per-feed (and whether it needs a
`provider`).

### Unfreeze triggers
A spec correction release, or before M2 first writes `ingestion_runs` / `backfill_runs` rows,
whichever comes first — status vocabularies and the backfill uniqueness rule are
hard to change once rows exist.

---

## IDEA-023: `providers_health` has no quarantine columns, but §6 says quarantine counts feed it

**Status:** Open — spec/schema defect, blocks M2's quarantine work
**Raised:** 2026-10-28 (M1 slice 4)

### The proposal
Not a feature — a sentence with nothing to write to. Line 374 (§6, bar validation) says
quarantined rows "feed providers_health and the ops heartbeat". §8 defines `providers_health`
as `(provider, feed, last_success_at, last_error_at, last_error TEXT)` — no count, no
quarantine timestamp, nothing a quarantine count could land in.

### How it was resolved for the build
It was not. Slice 4 implements the five §8 columns exactly and adds none, because inventing
quarantine columns is the unrequested schema change the slice was told not to make.

### Why it is parked
Not parked by choice. Either §8 is missing columns or line 374 means a derived value (e.g. a
count over `data_quarantine`) rather than stored state; the spec does not say which.

### The version that would be legitimate
Either add the intended columns to `providers_health` in §8, or reword line 374 to say the
counts are computed from `data_quarantine` and surfaced through `/health/providers`.

### Unfreeze triggers
A spec correction release, or a `# DECISION:` comment, before M2's quarantine work begins.

---

## IDEA-024: §10 line 1003 and §16 line 1109 disagree on where first-run progress comes from

**Status:** Open — resolved for the build by judgment (below); the spec text is still stale
**Raised:** 2026-10-28 (M1 slice 4)

### The proposal
Not a feature — a contradiction. §10 (line 1003) says the first-run screen shows "backfill
progress per feed, from providers_health". §16 M2 (line 1109) says "first-run progress API +
screen from backfill_runs". `providers_health` carries no progress columns;
`backfill_runs` has `total_units`, `completed_units` and `current_unit`.

### How it was resolved for the build
The build follows §16: progress comes from `backfill_runs`. **This is a judgment, not a
mechanical application of a stated rule.** CLAUDE.md's precedence rule is scoped to §3 versus
§16; extending it to §10 is an inference from §16 being the normative build sequence, and from
`backfill_runs` being the only table with progress columns. A reader who disagrees should treat
line 1003 as the authority and reopen this entry.

### Why it is parked
The frozen spec cannot be edited in place.

### The version that would be legitimate
A spec correction release changing line 1003 to name `backfill_runs`.

### Unfreeze triggers
A spec correction release. Nothing blocks a milestone before M2's first-run screen.

### Drift class
This is the **sixth** instance of the same drift class — a section revised without its
dependents — alongside IDEA-013, 014, 016, 018 and 021.

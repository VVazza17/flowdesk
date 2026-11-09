# FlowDesk — Spec Defects Registry

Flagged contradictions, stale cross-references, and other defects found in the frozen
`docs/architecture.md` spec (or in this project's CLAUDE.md, where it cites the spec as its
authority). These are **not** parked features — see `docs/IDEAS.md` for that registry — they
are open questions the spec itself needs a correction release (or a `# DECISION:` comment) to
resolve, each blocking a specific milestone until it's addressed.

Numbering is shared with `docs/IDEAS.md` (both draw from the same IDEA-NNN sequence) so IDs
never collide between the two files. This file starts at IDEA-013 because IDEA-001–012 are
parked features that stayed in `docs/IDEAS.md`.

## Drift class: the canonical list

**Definition** (from IDEA-015's broader-pattern note): a revision or renumbering happened somewhere in the spec,
and a dependent reference or definition elsewhere was never updated to match.

**This list is the count.** Do not recount from the per-entry "Nth instance" sentences: they were written at
different times, each from a partial list, and two of them are wrong (see the last paragraph). A new entry appends
a row here and takes the next number. The number is the row's position, which is the order the instances were
written up, not the order of the IDEA ids.

| # | Entry | Revision | Stale dependent |
|---|---|---|---|
| 1 | IDEA-013 | v4.9 episodes (§7.16) | §8 `natural_key` has no time component. (The entry names no revision; it is counted because every earlier label counted it.) |
| 2 | IDEA-014 | v4.7 renumbering | §8 `events.episode_id` comment cites §7.15 |
| 3 | IDEA-015 | v4.1 decision 15 revision | §17.1 "never integrate a broker API" rule |
| 4 | IDEA-016 | events-v2 lineage redesign | `alerts` has no `event_id` FK |
| 5 | IDEA-018 | ADR numbering | IDEA-003's "per ADR-0007" citation |
| 6 | IDEA-021 | §16 M1 route list | §9 line 968 and §5 line 255 health route names |
| 7 | IDEA-024 | §16 M2 scope | §10 line 1003 first-run progress source |
| 8 | IDEA-019 | §14 two-tier design | §16 gate line lost the "anonymous" qualifier |
| 9 | IDEA-032 | later milestones' components | §16 M1 gate names a worker and a provider |
| 10 | IDEA-034 | v4.9.10 provenance line (§16, line 1111) | §8 `bars` has no `provider`/`feed`/`venue_scope` and no revision columns |
| 11 | IDEA-035 | v4.9 versioned canonical rewrite (§6.12, §16 M2 gate) | §8 `bars` key `(symbol_id, interval, ts)` has no place for a data version |
| 12 | IDEA-036 | M0 spike 4 findings (§6.5) | §8 `insider_trades` columns need the Form 4 XML that §6.5 assigns to M10 |

Not a row: the stale "enters §7.16 when the simulator is built" line (architecture.md line 1418, quoted in
IDEA-006; the simulator is §7.17). IDEA-015's note counts it as its own instance, but it is the same v4.7
renumbering as row 2, it has no entry of its own, and IDEA-014 already points at it. Counting it separately would
make IDEA-032 the tenth. Overrule this and the count shifts by one from row 3 onward.

**IDEA-015 is in the class.** It is the defining case: decision 15 was revised in v4.1 and §17.1, which cites it,
kept the pre-revision text. Its own note counts itself (item 3) and IDEA-014 calls it "a third instance". No entry
gives a reason for leaving it out, so the omissions in IDEA-019's list ("seventh") and IDEA-024's list ("sixth")
were slips, not a ruling. Those two labels are therefore wrong; the ordinals in them are superseded by the table
above. They were not edited.

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

**Status:** Resolved for the build by owner decision (B-minimal, below); the spec text is still stale
**Raised:** 2026-08-16 (ADR-0007)
**Resolved:** 2026-11-02

### The proposal
Not a feature — a tension between an exit gate and the section it's testing. §14 gives public
deployment mode a two-tier design: anonymous routes are read-only, but there is a "separate
authenticated admin routes" surface (session cookie + CSRF, audit log) that can mutate — alert-rule
mutation is disabled only "for anonymous users," implying the authenticated tier can still mutate.
The M1 gate (§16) instead states flatly: "public profile exposes read-only routes only." Read as a
literal route-level test (no non-`GET` endpoint reachable under the public compose profile,
authenticated or not), this excludes the very admin routes §14 requires. Read loosely (anonymous
access is read-only), it's consistent with §14. The gate text doesn't say which.

### How it was resolved for the build
**Decision (Kyle, 2026-11-02): B-minimal — read-only is judged by HTTP method.** Under the public
profile, every route either uses only read-only methods or is admin-guarded. GET, HEAD and OPTIONS
all count as read-only: HEAD is a bodyless GET, and OPTIONS is needed for CORS preflight and changes
nothing.

**The invariant fails closed.** It fails on any route it cannot classify, WebSockets included. A
check that ignores what it doesn't understand provides false assurance. When the §9 `WS /ws` route
arrives in Phase 2a, the test should break and force a decision.

**Reasoning.** §14 (line 1063) already says "anonymous routes read-only" in its own words. §16's
gate line (line 1107) is a lossy restatement of that sentence that dropped "anonymous". This is
spec drift, not a design disagreement, and §14 governs on security semantics. CLAUDE.md's
precedence rule covers only §3 versus §16, so §14 governing here rests on this decision, not on
that rule.

**Slice 7 scope.**
- Slice 7 builds no auth stack. It builds only the invariant above. No admin route exists yet, so
  for now every public-profile route must use only read-only methods.
- Cookie flags, CSRF and the audit log are deferred, not dropped: they arrive with the first route
  that needs them.

**What the method check does not cover: a GET handler that writes.** The method check cannot see
this, and code review isn't a mechanism. The intended enforcement is to run GET requests in a
read-only database transaction, so a write inside a GET handler fails at Postgres. HEAD gets the
same treatment, since it is a bodyless GET. **This is not built, and until it is, nothing enforces
it.**

It belongs to the slice that introduces the request-scoped database session. That slice has no
number yet: M1's remaining slices (8–10) add no data routes, and M2 has not been cut into slices.
The first route §16 names that needs it is M2's first-run progress API. The `/health/*` routes
reach Postgres through the probe engine in `backend/app/deps.py`, not a session, so that slice must
also decide whether they move onto it. The mechanism only covers Postgres writes made through that
transaction; it does not cover Redis.

**Riders under the public profile.**
- `/health/providers` redacts `last_error`.
- `/docs` and `/openapi.json` are disabled.

**Addendum (Kyle, 2026-11-03, recorded with slice 7).**
- **`/health/data` also redacts `ingestion[].error`.** It is the same kind of raw provider text as
  `last_error`. As built, both keys are removed rather than nulled, because null would read as "no
  error"; `last_error_at`, `last_attempt_failed` and the run's `status` still show that a failure
  happened. `tasks[].error_class` stays: it is a class name, the detail every `/health/*` body
  already reports.
- **`api-public` binds to loopback, so the public profile is not internet-ready after slice 7.**
  Slice 7 delivers the route invariant and the redaction riders, not a deployable public site.
- **Strict CORS, request-size limits and rate limits have no owning slice, and all three are
  required before any internet exposure** (§14 line 1063 lists them). M1's remaining slices (8–10)
  do not add them and M2 has not been cut into slices. ADR-0007's open question about which layer
  enforces them (Caddy or FastAPI) is also still open.

### Why it is appealing
N/A — flagged spec defect, not a proposed feature.

### Why it is parked
It no longer blocks the build. It stays open only because the frozen spec cannot be edited in place.
Before this decision, an acceptance test for M1 had to pick a reading before it could be written,
and the two readings produce materially different implementations (no public admin surface at all,
vs. an authenticated admin surface that mutates).

### The version that would be legitimate
**Proposed fix for the next spec release:** correct §16's gate line (line 1107) to restore the word
§14 uses, replacing "public profile exposes read-only routes only" with "public profile: anonymous
routes read-only per §14 — every route uses only GET, HEAD or OPTIONS, or is admin-guarded." This
leaves the two-tier design intact and makes the gate testable.

### Unfreeze triggers
A spec correction release applying the fix above. Nothing blocks a milestone until then: slice 7
follows this decision. See ADR-0007, Open Questions and Consequences.

### Drift class
This is the **seventh** instance of the same drift class, alongside IDEA-013, 014, 016, 018, 021
and 024. Here the dependent is §16's gate line, which lost a qualifier when restating §14.

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

**Addendum, 2026-11-08 (M2 slice 1).** The worker canary is the first thing to write `task_runs` rows, so the
vocabulary question has arrived for that table. The stopgap, in `backend/app/tasks/runs.py`: `status` is
`running` / `succeeded` / `failed` (TEXT, no CHECK); `completed_at` is set when a run finishes either way, and
only `succeeded` counts as a completion for health; `attempt` starts at 1. `ingestion_runs` and `backfill_runs`
are still unwritten, so their triggers stand for slices 4 and 10.

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

---

## IDEA-025: Health endpoints have no specified response shape; §9's envelope scope is ambiguous

**Status:** Open — resolved for the build by judgment (below); the spec text is still silent
**Raised:** 2026-10-28 (M1 slice 6)

### The proposal
Not a feature — an under-specification. §9 line 968 lists `GET /health/providers` as "Liveness +
feed freshness" with no body, and §16 line 1107 names `/health/ready` and `/health/data` without
defining them anywhere. §9 also says "Envelope for all responses: `{data, as_of, stale}`" under
the `/api/v1` prefix; the health routes are served at `/health/*`, outside that prefix. No section
states the readiness status codes or what "degraded" means, though the M1 gate requires a
"documented health state".

### How it was resolved for the build
Bare JSON, no envelope, same as `/health/live` since slice 2. `/health/ready`: 200 `ok`,
200 `degraded` (Redis down), 503 `not_ready` (Postgres down or migrations behind). `/health/data`
reports pipeline state from the ops tables, `/health/providers` reports `providers_health`
freshness. Full state table: `docs/HEALTH.md`.

**This is a judgment, not a mechanical application of a stated rule.** Reading "all responses" in
§9 as including `/health/*` is also defensible; a reader who prefers that should treat the
envelope as authoritative and reopen this entry.

### Why it is parked
The frozen spec cannot be edited in place.

### The version that would be legitimate
A spec correction release defining the three response shapes and the readiness status codes, and
stating whether `/health/*` sits inside the envelope.

### Unfreeze triggers
A spec correction release. Public exposure of `/health/providers` is settled by IDEA-019 (resolved
2026-11-02): `last_error` is redacted under the public profile. Slice 7 implements it.

---

## IDEA-026: `providers_health` cannot say when a feed was first seen, so "never succeeded" has no age

**Status:** Open — implementation finding, blocks nothing now; bites in M2
**Raised:** 2026-10-28 (M1 slice 6)

### The proposal
Not a spec contradiction and not drift: §8 (line 778) is internally consistent. This is a gap found
by building against it. `providers_health(provider, feed, last_success_at, last_error_at,
last_error)` records the last success and the last error, but not when the feed was first
registered. `/health/providers` therefore reports a feed with `last_success_at IS NULL` as
`never_succeeded` (top-level `pending`), and that state cannot escalate: a feed that has been
pending for 10 minutes and one pending for 3 days are indistinguishable. Only `last_error_at`
hints at activity, and only if the feed has failed.

This matters because the three feed situations need different responses (`docs/HEALTH.md`): no
feeds and never-succeeded are not failures, stale is. Without an age, a feed that never works
stays "not broken" forever. §6 line 369 and the ops heartbeat (§11 line 1043: "silence must be
impossible to mistake for health") both want broken feeds to be loud.

### How it is handled for the build
`never_succeeded` is reported as non-broken with `last_attempt_failed` as the only signal.
`classify_feed` carries a `# DECISION:` comment saying so. Nothing is invented: no age is guessed
from other tables.

### Why it is parked
The frozen spec cannot be edited in place, and nothing reads this before M2: the table is empty
until the first feed is registered.

### The version that would be legitimate
Add a first-seen column to `providers_health` in §8 (for example `first_seen_at TIMESTAMPTZ NOT
NULL`, set on registration), plus a per-feed "grace period before first success" threshold
alongside the staleness thresholds, so `never_succeeded` can escalate once it is overdue. This is
a schema decision as much as a threshold one, so it wants the same pass as IDEA-022.

### Unfreeze triggers
Resolve before M2 registers the first real feed, so the migration adding the column lands before
any row exists. Related but distinct: IDEA-023 is a contradiction (line 374 sends quarantine
counts to a table with no columns for them); this entry is a missing field that no section
contradicts.

---

## IDEA-027: Decision 24 doesn't cover PSF-2.0 or MPL-2.0, and the gate doesn't separate shipped from dev-only

**Status:** Resolved for the build by owner decision, 2026-11-04 (M1 slice 8). A spec correction release should record it.
**Raised:** 2026-11-04 (M1 slice 8)

### The proposal
Decision 24 (§2) names MIT/BSD/Apache as permissive, puts LGPL in the companion repo, bans AGPL, and
says CI fails the build on "AGPL or unknown licenses". It says nothing about other known licences.
Real dependencies fall outside its list:

- `typing-extensions` is PSF-2.0, in the runtime lock. `greenlet` is `MIT AND PSF-2.0`, also runtime.
- `pathspec` is MPL-2.0. mypy depends on it, so it is dev-only.
- `certifi` is MPL-2.0. It came in through `httpx`. It left the audited set when slice 8 replaced
  `httpx` with `httpx2`, and it remains in the audit tools' own lock, which is not audited.
  If an HTTP client brings it back (likely M2), the gate fails and it stops for review.

Read literally, "fails on AGPL or unknown" would pass all of these and also anything else that merely
has a name. `backend/tools/license_audit.py` is stricter: it is an allow-list, so anything not
reviewed fails.

### How it is handled for the build
- PSF-2.0 is allowed for any package.
- MPL-2.0 is allowed per package, currently only `pathspec`, on the basis that it is unmodified and
  never vendored. Any other MPL dependency fails until someone reviews it.

The `pathspec` entry exists because the gate caught it during slice 8, after the original decision
named only `certifi`. The `certifi` entry was then removed by the owner, because it matched nothing.

### A known property of the gate
The gate treats shipped and dev-only dependencies identically: it audits the dev lock, which includes
the runtime lock, as one set. Licence obligations attach only to what is distributed, and `pathspec`
never ships in the image. So the gate is stricter than the obligations it exists to protect.

### The version that would be legitimate
If per-package MPL review gets tedious, the fix is to split the allow-list by lock file, with one list
for `requirements.lock` (shipped) and a wider one for `requirements-dev.lock` (not distributed), rather
than loosening the list globally.

### Unfreeze triggers
Revisit when per-package MPL review becomes tedious, or before any distribution or commercial use,
when the shipped set should get a proper legal read (decision 24 says as much). The repo's own LICENSE
file is a separate question, held by the owner.

---

## IDEA-028: Decision 14 bans bare "arbitrage"; §7.5, §15 and §17 ban only "arbitrage opportunity"

**Status:** Open. Blocks nothing now; matters when the first premium template is written.
**Raised:** 2026-11-04 (M1 slice 8)

### The proposal
Decision 14 (§2) says the Premium Monitor "never calls them 'arbitrage,' 'free money,' 'guaranteed,' or
'riskless.'" §7.5, §15 and §17 list the phrase as "arbitrage opportunity". The two differ: a template
saying "this is an arbitrage" breaks decision 14 and passes the other three.

### How it is handled for the build
`backend/tools/banned_phrases.py` follows §17's list, because §17 is the hard-rules section and §15
states the test. A `# DECISION:` comment there points here. Not resolved.

### The version that would be legitimate
Pick one wording in a correction release. Moving to the stricter word is a one-line change to
`PHRASES`. It would also catch legitimate explanatory text, so it is not automatically the better
choice.

### Unfreeze triggers
Resolve before the first premium-module template is written (§7.5).

---

## IDEA-029: Nightly backups have no owner, no retention rule and no off-box copy

**Status:** Open. Design settled 2026-11-08 and built in M2 slice 2 (the `backup` service, `docs/BACKUP.md`), but **not closed**: it closes when a real unattended dump lands on the real desktop and restores from there. Must be closed before M2 slice 6 persists its first scheduled bar (corrected 2026-11-08: this line said "before M2 writes its first persistent observation"; see the section below).
**Raised:** 2026-11-04 (M1 slice 9)

### The proposal
§13 says "Nightly `pg_dump` to a local disk path or free-tier object storage". No slice or milestone owns
making that happen, and the M1 gate requires only that a backup "restores clean". Slice 9 built the scripts,
so M1 can pass with no backup ever being taken automatically. §13 also leaves three things open:

- **What runs it.** Celery Beat doesn't exist yet. Host cron works for a VM, but not on a Windows machine that
  may be asleep, and a container job needs Docker socket access. A missed run also needs to be visible, which
  is close to what slice 10's dead-man heartbeat does.
- **How many dumps to keep.** Nothing prunes `dumps/`, so a nightly job eventually fills the disk it shares
  with the database.
- **Where they go (the off-box question).** A dump on the same disk protects against a bad `DROP`, not against
  losing the machine. §13 allows a local disk path, which is enough for the first and not for the second.

### Why the trigger is the first irrecoverable observation rather than a date
Some of FlowDesk's data is irreplaceable, but less than this entry first said. Prices can be re-fetched. So can
FRED: M0 spike 3 showed that `output_type=4` with a widened real-time window returns every observation's
first-published value and its publication date for any past date (ADR-0003, §6.4). What cannot be recovered is
what only a live run captures: a bar captured live is as-traded on its own date (§6.12), and `c_total_return` as
it stood on a given day cannot be re-derived, because `Adj Close` is rewritten by every later dividend
(ADR-0002). That record starts at the first scheduled yfinance run, which is M2 slice 6, not at the first FRED
run in slice 5. From then on, every day without a working off-box backup is a day of record that a lost machine
would erase permanently.

Correction, 2026-11-08: this section previously named the point-in-time macro record as the irreplaceable part,
and said it "cannot be reconstructed for any provider without a vintage API". FRED has one. The ordering of the
build is unchanged, because slice 2 (the backups) precedes both slice 5 and slice 6.

### How it is handled for the build
`docs/BACKUP.md` documents the scripts and says plainly that nothing runs them automatically. No scheduler has
been added yet. Not resolved yet.

Design settled by Kyle at M2 planning (2026-11-08), built in M2 slice 2:
1. **Local first, then off-box as a separate step.** The dump is taken and kept locally. The off-box copy is
   attempted afterwards, so a sleeping desktop reads as a failed copy and not as a failed backup. The copy is
   rsync over SSH, with catch-up on wake.
2. **Encrypted, to Kyle's desktop.** The key lives in Kyle's password manager and never only on the laptop. The
   rule: the key and the backups never share a single point of failure. Development runs count as data worth
   protecting.
3. **A stale backup does not degrade `/health/ready`.** It would page for something that can wait until
   morning, and the warning in `health.py` says conditions that shouldn't page don't belong there. It shows on
   `/health/data`, and the missed-run signal notifies.
4. **Retention: 14 dailies plus 8 weeklies**, to be revisited when the data volume is real.

Addendum, 2026-11-09. The desktop turned out to be Windows, so the transport is SFTP over Windows OpenSSH Server
and the write-only restriction is NTFS permissions on a dedicated account (`docs/BACKUP.md`). Two parts of the
original ask are **not yet true**, and are recorded here so they are not assumed: (a) **retention is enforced on
the laptop's local copy only.** The account cannot delete on the desktop, by design, so nothing prunes the
destination and the 14+8 rule is aspirational there until a job on the desktop runs `infra/backup/prune.sh`
(nothing does, and the Task Scheduler entry in `docs/BACKUP.md` is untested); (b) **"restores clean from the
off-box copy" has been shown only against stand-in servers**, not the real desktop.

### The version that would be legitimate
A scheduled `backup.sh`, a retention rule, a copy that leaves the machine at zero recurring cost, and a visible
signal when a run is missed. Dumps are unencrypted, so an off-box copy also needs a decision on encryption.

### Unfreeze triggers
M2 slice 2 closed, including a restore drill from the off-box copy, before M2 slice 6 persists its first
scheduled bar.

---

## IDEA-030: Migration 0001's `CREATE EXTENSION` is never exercised by the smokes or tests

**Status:** Open. Blocks nothing on the current image.
**Raised:** 2026-11-04 (M1 slice 9)

### The proposal
Not a spec defect: slices 3 and 4 test less than they claim. The Timescale image pre-installs the extension in
`template1` and in the init-created `POSTGRES_DB`, confirmed on a fresh container of `2.31.0-pg16`. So every
plain `CREATE DATABASE` inherits it, and migration 0001's `CREATE EXTENSION IF NOT EXISTS timescaledb` has been
a no-op in every run of `smoke-slice3.sh`, `smoke-slice4.sh`, the migration tests and `docker compose up -d`.

`smoke-slice3.sh` says "applies cleanly to an EMPTY database" and checks that the extension exists afterwards.
That check would pass even if 0001 did nothing.

### What has been measured
Run by hand once, on 2026-11-04: on a database created from `template0`, with no extension, `alembic upgrade
head` exited 0 and left the extension at 2.31.0 and the revision at 0002. So on this image, with this
superuser, **the code works and the gap is coverage**. Nothing repeats that check, so a later change could break
it unseen.

### Where it would hurt
On any environment whose base image might differ, a no-op that was never exercised can become a failed
migration. On this image and role it can't, but that doesn't carry over. Not tested, and each could differ:

- an image that doesn't ship the extension or doesn't preload it, such as plain Postgres
- a role that isn't a superuser, such as a managed Postgres, which §13 documents as an upgrade path
- the arm64 variant of the pinned tag, on which migrations have never run

### How it is handled for the build
`infra/restore.sh` creates its databases from `template0` so it doesn't depend on `template1`, and
`smoke-slice9.sh` restores into one. That covers the restore path, not migration 0001. Not resolved.

### The version that would be legitimate
Have `smoke-slice3.sh` and `smoke-slice4.sh` create their scratch databases with `TEMPLATE template0`, and assert
the extension is absent before migrating, so 0001 really runs. Then run the same check on the target environment.

### Unfreeze triggers
Resolve before deploying to any environment whose base image might differ.

---

## IDEA-031: §16 names an external heartbeat but no section says what it attests, what runs it, or how it's configured

**Status:** Handled for the build by ADR-0008. A spec correction release should record it. Blocks nothing.
**Raised:** 2026-11-04 (M1 slice 10)

### The proposal
§16 M1 requires an "external dead-man heartbeat (free ping-monitor service...)" and decision 26 says the monitor
lives outside the monitored host. That leaves gaps:

- **What a ping attests.** ADR-0006 decision 6 calls the check content-blind and says it detects total outage. No
  section says whether that means the process answering, or the app being able to serve data.
- **The alert window.** No section gives a period or grace time.
- **What sends the pings.** §13's compose service list (`frontend, api, worker, beat, postgres, redis, caddy`) has
  no sender, and §11's Celery `heartbeat` is the separate internal mechanism. M1 has no Celery anyway.
- **The configuration.** §12's list of settings has no ping URL.

### How it is handled for the build
ADR-0008 decides all four: a `heartbeat` compose service, a ping only when `/health/ready` is exactly `ok` (Kyle's
decision), a 5-minute period with 40-minute grace, and `HEARTBEAT_URL`, blank except on a deployed instance.

Under this gate, every `degraded` condition added to `/health/ready` also pages, which ADR-0006's "total outage"
wording alone wouldn't suggest. ADR-0008 section 4 records it.

### The version that would be legitimate
Add the service to §13, the setting to §12, and a sentence in §16 saying what a ping attests, in the next
correction release.

### Unfreeze triggers
The next spec correction release. Revisit the gate if `/health/ready` gains a `degraded` condition that shouldn't
page.

---

## IDEA-032: §16's M1 gate requires killing a worker and a provider, but neither exists until M2

**Status:** Recorded at the M1 gate review. Handled for the build as below. Needs a spec correction release. Blocks nothing, but M2's gate must carry the moved clause.
**Raised:** 2026-11-08 (M1 gate review)

### The proposal
§16's M1 paragraph (line 1107) says the gate includes "killing Redis/worker/provider produces the documented
health state". M1's own scope builds no Celery worker and no provider client; both first exist in M2. Two of the
three components the clause names cannot be killed, so the clause cannot pass literally at M1, whatever is built.
CLAUDE.md already said so before the review ("it can't pass literally"), and `docs/HEALTH.md` records it under
what the endpoints cannot yet show.

The gate line was written against the finished system's component list, not against what M1 builds.

### How it is handled for the build
Kyle's decision at the M1 gate review: the M1 gate is recorded as **passed**, with the Redis part of the clause
verified (HTTP 200, `degraded`, `checks.redis` = `{"status":"down","error":"ConnectionError"}`, recovery to `ok`)
and the worker and provider parts **moved to M2's gate**, where those components first exist. This is a
relocation, not a waiver: M2 does not pass until killing the worker and killing a provider each produce a
documented health state. The documentation to write is in `docs/HEALTH.md`, and the states will have to be added
there when M2 builds the components.

### The version that would be legitimate
A correction release that edits §16: M1's gate keeps "killing Redis" and drops "worker/provider"; M2's gate gains
"killing the worker and a provider produces the documented health state". `docs/architecture.md` is frozen and
was not edited.

### Drift class
This is the **ninth** instance of the drift class, row 9 of the canonical list at the top of this file. Here the
dependent is §16's gate line, which names components from a later milestone's scope. IDEA-015 is in the class
(settled 2026-11-08, reasoning in the list), so the "seventh" in IDEA-019 and the "sixth" in IDEA-024, which leave
it out, are each one low. They were not edited; the list supersedes them.

### Unfreeze triggers
The next spec correction release.

---

## IDEA-033: §16 M2 lists five reference tables; §8 designs one of them

**Status:** Open. Resolved for the build by judgment at M2 slice 3 (each choice will carry a `# DECISION:` comment); the spec text is silent.
**Raised:** 2026-11-08 (M2 planning)

### The proposal
§16's M2 scope begins "Reference tables (symbols, exchanges, currencies, provider mappings, trading calendars)".
§8 gives DDL for `symbols` only: `symbols(id PK, ticker UNIQUE, name, asset_class, sector, role, active BOOL, meta JSONB)`.
Exchanges, currencies, provider mappings and trading calendars have no columns, keys or types anywhere.
`universe_membership` appears only as a signature in prose (line 1111), also without types.

`symbols` itself conflicts with other sections. §6.12 says a delisting is recorded with a "delist date", and there
is no such column. §6.5 says CIK is the durable identity and ticker "a mutable label", but `ticker` is `UNIQUE`,
so a reassigned ticker cannot have two rows. There is no column for exchange or currency. §6.12 says to use an
exchange-calendar library, so it is not stated whether "trading calendars" is a table at all.

### Why it is appealing
N/A. Flagged spec gap, not a proposed feature.

### Why it is parked
The frozen spec cannot be edited in place. Not drift: no revision left a stale dependent. The spec never designed
these tables. Same family as IDEA-022.

### The version that would be legitimate
A correction release adding §8 DDL for the four tables and `universe_membership`, and settling where the CIK and
the delist date live (columns on `symbols`, or the provider-mappings table).

### Unfreeze triggers
A spec correction release. Nothing blocks M2 before slice 3.

---

## IDEA-034: `bars` has no provider/feed/venue_scope and no provenance or revision columns

**Status:** Open. Blocks M2 slice 6 (`bars` migration) until a `# DECISION:` or a correction release.
**Raised:** 2026-11-08 (M2 planning)

### The proposal
§16 line 1111 (added v4.9.10) says: "every market observation also records `provider / feed / venue_scope`
(e.g. consolidated vs single-venue), so a strategy evaluated on one market view can never be silently compared
against another." It is "same milestone", meaning M2. §8's `bars` has
`(symbol_id, interval, ts, o_raw..c_raw, v_raw, c_split_adj, c_total_return, v_split_adj, adjustment_factor,
dollar_volume)` and no such columns.

§8 also gives `bars` none of the provenance that M2's scope lists (`effective_at, available_at, ingested_at,
revision, source, ingestion_run_id`). The scope applies that list to "revision-prone feeds (macro, disclosures)",
which on its face excludes prices. ADR-0002 consequence 3 says `c_total_return` is revision-prone and "needs its
own revision-tracking treatment ... the same machinery already required for revision-prone macro series". So one
column of `bars` is in the revision-prone class while §8 and the M2 scope sentence treat prices as outside it.

### Why it is appealing
N/A. Flagged spec defect, not a proposed feature.

### Why it is parked
The frozen spec cannot be edited in place.

### The version that would be legitimate
A correction release adding the three provenance columns to §8 `bars`, and stating how `c_total_return` revisions
are kept (revision columns on `bars`, or a separate revision table keyed like `macro_metrics`).

### Unfreeze triggers
A spec correction release, or a `# DECISION:` in the slice 6 migration. Kyle's call (2026-11-08), recorded as a
split so that it is written down and not remembered. The provenance columns and ADR-0003's vintage rules are one
design problem, and `macro_metrics` (slice 5) is built before `bars` (slice 6), so the two must not be allowed to
disagree.
- **Settled at slice 5 planning, and binding on every table that stores an observation:** the shared provenance
  vocabulary. That means the names and meanings of `effective_at`, `available_at`, `ingested_at`, `revision` and
  `source`. IDEA-038 (the `effective_ts` / `effective_at` contradiction) is the first input. `macro_metrics` is the
  first table built to it. `bars` must conform to it at slice 6 and does not re-decide it. If planning extends the
  list (for instance `ingestion_run_id` or `provider/feed/venue_scope`), this entry is updated to say so.
- **Waits for slice 6 planning:** the `bars`-specific decisions. These are where `c_total_return` revisions live
  (revision columns on `bars`, or a separate revision table), how `provider/feed/venue_scope` is stored, and
  IDEA-035's version home. These are decided together, since the version home and the revision storage both
  decide how a second value for the same bar is kept.

### Drift class
Row 10 of the canonical list. The revision is v4.9.10's line 1111. The stale dependent is §8 `bars`.

---

## IDEA-035: The "new data version" in the M2 gate has nowhere to live in §8

**Status:** Open. **Blocks M2 slice 8** (corporate actions workflow).
**Raised:** 2026-11-08 (M2 planning)

### The proposal
§16's M2 gate says "confirmed split creates a new data version without deleting raw". §6.12 says canonical
history is rewritten only on a confirmed `corporate_actions` entry, "raw history is immutable — corrections create
new versions". §8's `bars` is keyed `(symbol_id, interval, ts)` with no version column, so a second version of the
same bar would collide with the first. `corporate_actions` has `confirmed BOOL` and `ingested_at` but no version
or supersession field either.

There are at least two ways to satisfy the gate, and they have different consequences. (a) Add a `data_version`
to `bars` and its key, keeping every version as rows. (b) Keep `bars` raw-only and immutable, and derive
`c_split_adj` and `v_split_adj` on read from confirmed `corporate_actions`, so "a new version" is a new set of
confirmed actions. (b) interacts with ADR-0002, which defines `c_raw` as reconstructed from `c_split_adj`.

### Why it is appealing
N/A. Flagged spec defect, not a proposed feature.

### Why it is parked
Not parked by choice. This is a design decision the owner has to make, and it changes the schema of the largest
table. Not resolved here.

### The version that would be legitimate
Two candidate resolutions, (a) and (b) above, neither chosen. Either is a one-section correction release.

### Unfreeze triggers
Kyle's call (2026-11-08): the version-home decision waits until M2 slice 6 planning, where it is made together
with IDEA-034. It must still precede slice 8.

### Drift class
Row 11 of the canonical list. The revision is the versioned-rewrite language in §6.12 and the M2 gate. The stale
dependent is §8 `bars`.

---

## IDEA-036: §6.5 puts Form 4 XML parsing in M10; §8's `insider_trades` and §16 M2 need what it produces

**Status:** Open. Handled for the build by Kyle's decision below.
**Raised:** 2026-11-08 (M2 planning)

### The proposal
§6.5 (M0 spike 4) says Form 4 transaction dates, security, size and open-market-vs-plan flag "live inside the
referenced XML", and says to "scope this into M10 explicitly". §8's `insider_trades` has `transaction_date`,
`side`, `shares`, `value` and `is_open_market`, and `lag_days` is a generated column on `transaction_date`. None of
that can be filled from the EDGAR index alone, which gives filing dates only. Meanwhile §16 M2's scope names
disclosures among the revision-prone feeds that get canonical observations with provenance.

### How it is handled for the build
Kyle's decision at M2 planning (2026-11-08): disclosures in M2, if built at all, are **index-level**. That means
filing date and `acceptanceDateTime` (as `available_at`), and CIK identity. No Form 4 XML parsing before M10.
What follows for the `insider_trades` columns that need the XML (written as NULL, or the row not written until M10)
is not decided here and will need a `# DECISION:` when a disclosures slice exists. M2 builds no disclosures
(IDEA-037), so the decision falls to M10.

### Why it is appealing
N/A. Flagged spec defect, not a proposed feature.

### Why it is parked
The frozen spec cannot be edited in place.

### The version that would be legitimate
A correction release that either moves the XML step into the milestone that first needs it, or states which
`insider_trades` columns are nullable until M10.

### Unfreeze triggers
The next spec correction release.

### Drift class
Row 12 of the canonical list. The revision is the M0 spike 4 finding recorded in §6.5. The stale dependents are
§8 `insider_trades` and §16 M2's scope sentence.

---

## IDEA-037: §16 M2's scope names FX and disclosure work that nothing before M7 and M10 reads

**Status:** Recorded at M2 planning. Handled for the build as below. Needs a spec correction release. Blocks nothing, but M7's and M10's gates must carry the moved work.
**Raised:** 2026-11-08 (M2 planning)

### The proposal
§16's M2 scope names "canonical observations with provenance ... on revision-prone feeds (macro, disclosures)", and
§6 puts Frankfurter (FX), EDGAR and the two Stock Watcher dumps in the Phase 1 provider set. Nothing in M3, M4
or M5 reads any of them. M3 is lineage and event infrastructure, M4 reads ETF and SPY bars, and M5's outcome
tracking works from event prices. The first readers are M7 (the premium monitor's typed FX conversion) and M10
(Congress and Form 4, with public-availability timestamps).

### How it is handled for the build
Kyle's decision at M2 planning (2026-11-08): **M2 builds yfinance, FRED and a fake provider, and nothing else.**
This is a relocation, not a waiver:
- **Frankfurter FX is cut from M2 and inherited by M7.** That is the `fx_rates` ingestion, the typed
  `convert()` and the per-pair magnitude assertion under ADR-0001. The `currencies` reference table stays in M2
  slice 3.
- **The EDGAR index and both Stock Watcher dumps are cut from M2 and inherited by M10.** The EDGAR constraints
  recorded in §6.5 (mandatory contact User-Agent, CIK identity, paging the `files` array, `acceptanceDateTime`
  as `available_at`) and the index-level decision in IDEA-036 travel with them.
- M7 and M10 do not pass their gates until the moved work is built and carries the provenance rules.
- The number of M2 slice 9 is left vacant rather than renumbered, so the slice references in IDEA-033,
  IDEA-034, IDEA-035 and IDEA-036 stay valid.

**FRED stays in M2, and not because anything reads it.** Nothing before M6 does. It stays because it is the only
provider that exercises a revision, and IDEA-034's provenance columns and ADR-0003's vintage rules are one design
problem. Built six months apart, `macro_metrics` and `bars` would disagree. A consequence for M6: its scope
sentence "FRED with first-seen + revision vintages (revisions never overwrite earlier vintages)" duplicates what
M2 slice 5 builds, and that clause is satisfied early by slice 5. M6 keeps the Economy dashboard and the rest of
its scope.

### The version that would be legitimate
A correction release that drops "disclosures" from M2's scope sentence, adds the FX and disclosure ingestion to
M7's and M10's scope, and drops the vintage clause from M6's.

### Drift class
Not a row on the canonical list. This is an owner scope decision, not a stale reference left behind by a
revision. IDEA-032, which is row 9, was different: its gate clause named components that did not exist yet.

### Unfreeze triggers
The next spec correction release.

---

## IDEA-038: The observation timestamp is `effective_ts` in §8 and `effective_at` everywhere else

**Status:** Open. **Blocks M2 slice 5** (`macro_metrics`). Resolved by the shared provenance vocabulary decision at slice 5 planning (see IDEA-034).
**Raised:** 2026-11-08 (M2 planning)

### The proposal
§8 defines `macro_metrics(metric TEXT, effective_ts TIMESTAMPTZ, value NUMERIC, available_at, revision INT, source,
ingestion_run_id, PRIMARY KEY(metric, effective_ts, revision))` and its comment says "never `effective_ts` alone".
Every other statement of the same concept says `effective_at`: §6.4 (spike 3), ADR-0003 decisions 1 and 2,
ADR-0005, and §16 M2's list "(effective_at, available_at, ingested_at, revision, source, ingestion_run_id)". One
column cannot be both. The schema is what a migration is written from, and the ADRs are what the tests are
written from, so the two will disagree in the first slice that touches either.

The same concept has other spellings in the spec as well, which the vocabulary decision will have to cover:
- §6.4 line 330 still says `macro_metrics(metric, ts, value)`, the form from before vintages existed.
- `bars` uses `ts`, and `fx_rates` uses `observed_at`.
- `cot_positions` uses `report_date` (what the data describes) and `published_at` (called `available_at` in the
  comment on that table).
- `corporate_actions` has `available_at` and `ingested_at`, but no effective timestamp beyond `ex_date`.

### Why it is appealing
N/A. Flagged spec contradiction, not a proposed feature.

### Why it is parked
The frozen spec cannot be edited in place.

### The version that would be legitimate
A correction release that picks one name for the observation timestamp (`effective_at` has the ADRs and §16 behind
it), fixes §8 `macro_metrics` and §6.4 line 330 to match, and states for each §8 time-series table which column
plays which role.

### Unfreeze triggers
The slice 5 planning decision on the shared provenance vocabulary, then a spec correction release.

### Drift class
Not a row on the canonical list. I found no revision event behind it: the changelog records no rename of either
spelling, and the two forms appear to have been written together in v4.0's point-in-time provenance change. It is
a plain contradiction between two spellings of one concept, like IDEA-033. (The `macro_metrics(metric, ts, value)`
form on line 330 is different: it is a stale statement left behind by the vintage redesign. I have not counted it
separately, and if it should be a drift row, that is a one-line addition to the list.)

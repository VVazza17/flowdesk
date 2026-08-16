# ADR-0007: Private-vs-Public Deployment Mode

**Status:** Accepted
**Date:** 2026-08-16
**Source:** No M0 spike. Transcribed from §14 (Security, Compliance, Cost),
§13 (Deployment), §16 (M1 gate), §19.5 (Regulatory line), and §2 decision 15
(the Intelligence/Execution plane split, since public mode's exclusion of
the Execution Plane is the same boundary this ADR documents from the other
side). Where the spec is silent or ambiguous, this ADR says so explicitly
rather than inventing detail, and registers genuine spec defects as new
`docs/IDEAS.md` entries per the pattern set in ADR-0005 and ADR-0006.

## Context

§14 states directly: "Two explicit deployment modes (choose one; set by
compose profile)." M1 is where both get built (§16: "private/public
deployment profiles"), and its gate is the acceptance bar for this ADR's
subject. Decision 15 layers a second, related split on top — Intelligence
Plane vs Execution Plane — and §19.5 ties the two together explicitly: "any
hosted, public, or paid deployment ships the Intelligence Plane ONLY — the
Execution Plane is profile-excluded from public builds." This ADR was
explicitly commissioned to check that framing against CLAUDE.md's corrected
§17.1 broker-code rule (fixed 2026-08-16 per `IDEA-015`), since both concern
the same public/private, Intelligence/Execution boundary from different
angles.

There is no spike behind this ADR because there is nothing to measure — §14
and §13 already specify the two modes' properties verbatim; M1 has not been
built yet. This is a transcription of the schema and the gate, not a design
document.

## Decision

1. **Deployment mode is a build/deploy-time compose-profile choice, not a
   runtime toggle.** §14: "choose one; set by compose profile." §13 lists
   the services that get composed — `frontend, api, worker, beat, postgres
   (timescale/timescaledb image), redis, caddy (TLS)` — and the M1 gate
   states the deliverable plainly: "private/public deployment profiles"
   (§16). Mode is therefore selected at `docker compose` invocation time,
   the same mechanism family decision 15 uses to keep the Execution Plane
   out of public builds ("excluded at the compose-profile level from
   public mode," §2 decision 15) — one profile system, at least two
   dimensions of exclusion riding on it (see decision 5).

2. **Private mode (default): server-side auth, nothing durable exposed,
   mutations allowed.** §14, verbatim properties: API bound to the private
   network / behind Caddy basic auth; the static `APP_TOKEN` never reaches
   browser JavaScript; Postgres and Redis never exposed; mutations (alert
   rules, admin) allowed. The M1 gate's "Postgres/Redis never externally
   exposed" (§16) isn't scoped to one mode in the gate text — it reads as a
   floor that holds under both modes, not a private-mode-only property.

3. **Public mode is two-tier, not flat read-only.** §14: anonymous routes
   are read-only, but there is a **separate authenticated admin surface**
   (HTTP-only secure session cookie + CSRF protection, strict CORS,
   WebSocket origin validation, request-size limits, per-route rate
   limits, admin audit log) — and alert-rule mutation is disabled
   specifically "for anonymous users," implying the authenticated admin
   tier can still mutate. §14 also states the reason public mode can't
   reuse private mode's mechanism verbatim: "A static bearer token in a
   public browser app is a published credential — never ship it" — the
   admin session must be a different credential type in kind, not just the
   same `APP_TOKEN` behind a login screen. This two-tier shape is in
   tension with the M1 gate's own summary of it; see Open Questions and
   `IDEA-019`.

4. **The $0/month cost guarantee lives in the same section as the two
   deployment modes, and is stated as: nothing spends by default, one
   feature is the sole exception, and that exception is hard-capped.** §14:
   "Budget: $0/month in default configuration. The only possible spend is
   the optional LLM (`ENABLE_AI=true`), controlled by a small model class,
   one brief/day, cached Q&A, and a Redis daily token-budget counter that
   hard-stops LLM calls when exceeded (default cap set to roughly
   cents/day)." Recorded as a decision to the extent stated; the
   enforcement mechanism's reliability is deferred to Open Questions and
   `IDEA-020`.

5. **This ADR's two modes are both Intelligence-Plane deployment postures;
   the Execution Plane is not a third mode alongside them.** Decision 15:
   the Intelligence Plane "is the ONLY plane that may ever exist in a
   hosted, public, shared, or commercial deployment (§19.5)." §19.5
   restates it directly: "any hosted, public, or paid deployment ships the
   Intelligence Plane ONLY — the Execution Plane is profile-excluded from
   public builds." Public mode therefore structurally cannot carry the
   Execution Plane at all; private mode is where it would live *if* it
   ever exists, gated separately by `ENABLE_EXECUTION=false` (default) and
   decision 15's arming gates — private mode does not imply the Execution
   Plane is present, only that it isn't structurally forbidden the way
   public mode forbids it.

6. **Consistency check against CLAUDE.md's corrected broker-code rule
   (requested directly for this ADR): no contradiction found.** CLAUDE.md's
   §17.1 line, corrected per `IDEA-015`, reads: "The Intelligence Plane
   never executes. Execution-Plane code... is out of scope until M0–M8
   ship and decision 15's gates are met — never build it opportunistically."
   That is a **temporal/build gate** — don't write Execution-Plane code
   yet. §14/§19.5's "profile-excluded from public builds" is a
   **deployment-profile gate** — if it's ever built, never ship it in a
   public deployment. The two constraints operate on different axes (when
   code may be written, vs. where it may run) and both hold simultaneously
   without tension. Nothing in this ADR's sources requires revising
   CLAUDE.md again.

7. **The regulatory rationale is load-bearing for the whole plane/mode
   split, not incidental context.** §19.5 ties decisions 14–22 (no
   recommendations, no sizing, no execution, no "buy X") directly to
   keeping a hosted product "on the information-publisher side of
   investment-adviser regulation rather than the personalized-advice
   side," and states "formal legal review is required before charging
   anyone" — before *any* paid or commercial deployment, regardless of
   which of §14's two modes is used underneath it.

## Open questions for M1 (not resolved by this ADR)

- **The M1 gate's "public profile exposes read-only routes only" (§16)
  is in tension with §14's own two-tier design for that same profile.**
  §14 explicitly gives public mode a "separate authenticated admin routes"
  surface that can mutate (alert-rule mutation is disabled only "for
  anonymous users," and an "admin audit log" only makes sense if admin
  actions occur). Read literally — as a route-level test, e.g. "no
  non-`GET` endpoint is reachable under the public compose profile,
  authenticated or not" — the gate directly excludes the admin routes §14
  requires for public-mode administration to exist at all. Read loosely —
  as "anonymous/unauthenticated access is read-only" — it's consistent
  with §14. The gate text doesn't say which reading governs, and an
  acceptance test for M1 has to pick one before it can be written.
  Registered as **IDEA-019** in `docs/IDEAS.md`.

- **The LLM budget hard-stop's guarantee rests on a Redis counter, which
  decision 26 elsewhere insists must never be treated as durable.** §14's
  cent-level cap is enforced by "a Redis daily token-budget counter that
  hard-stops LLM calls when exceeded." Decision 26 (§2) and the §4
  architectural rule both state Redis is never the source of truth and
  that killing it should lose nothing durable — but this counter *is* the
  entire enforcement mechanism for a governance guarantee (the $0-by-default
  budget's one stated exception, capped). A Redis restart or eviction
  mid-day would reset the counter to zero, and the system would then honor
  another day's worth of spend before hitting the cap again — a real,
  if small (per §14, "roughly cents/day"), violation of the stated
  guarantee, caused by exactly the failure mode decision 26 was written to
  make safe to ignore everywhere else. Registered as **IDEA-020** in
  `docs/IDEAS.md`.

- **The Execution Plane's compose-profile mechanics are asserted but never
  spelled out.** Decision 15 says it is "excluded at the compose-profile
  level from public mode," but §13's actual `docker-compose` service list
  — `frontend, api, worker, beat, postgres, redis, caddy` — names no
  execution-plane service at all. Not blocking for M1 (Execution-Plane code
  is out of scope until M0–M8 ship per CLAUDE.md's corrected rule), but
  worth flagging now so it isn't assumed already specified when that
  milestone actually arrives.

- **Which layer enforces public mode's CORS/rate-limit/CSRF/WS-origin-
  validation rules is not stated.** §13 names Caddy as the TLS/reverse-proxy
  layer and ties private mode's basic auth to it explicitly ("behind Caddy
  basic auth," §14). Public mode's stricter rule set (strict CORS, WS
  origin validation, request-size and per-route rate limits, CSRF) could
  live in Caddy configuration or in FastAPI middleware (§4's `ws.py` owns
  the WebSocket manager) — the spec doesn't assign it to either.

- **Public mode's admin session mechanism implies a login flow that is
  never described.** An "HTTP-only secure session cookie" (§14) has to be
  issued by something, against some credential — the spec doesn't say what
  (a single owner-only pre-shared secret exchanged for a session, a
  password, a magic link). This most plausibly reads as a single-owner
  admin credential rather than the "user accounts" CLAUDE.md's hard rules
  forbid pre-Phase-3 (that rule reads as aimed at multi-user account
  systems, not the app owner's own admin login) — but the spec never states
  this distinction explicitly, so it's recorded as a reasonable reading,
  not a confirmed one.

## Consequences

- `IDEA-019` should be resolved with a `# DECISION:` comment before M1's
  acceptance tests are written, the same way `IDEA-017` needed resolving
  before M3's — an ambiguous gate produces either an accidentally-too-strict
  test (no admin routes ship at all, breaking §14's own design) or an
  accidentally-too-loose one (any authenticated mutation route silently
  passes "read-only routes only" because the test only ever checked
  anonymous access).
- `IDEA-020` means the LLM budget cap, as currently specified, is a
  best-effort control, not a hard guarantee, whenever Redis restarts
  mid-day. Given the amounts involved (cents/day) this is unlikely to be a
  real financial problem, but it should be stated as best-effort rather
  than advertised as a hard stop, or the counter should be given a cheap
  durable backstop (e.g., a Postgres row written on the same cadence,
  costing nothing under Zero-Cost Mode) before `ENABLE_AI=true` is treated
  as safe to leave on unattended.
- Decision 6 is the direct answer to why this ADR was commissioned: the
  CLAUDE.md correction from `IDEA-015` and this ADR's sources describe two
  different, compatible constraints (temporal vs deployment-profile), so no
  further CLAUDE.md edit is needed as a result of this ADR.

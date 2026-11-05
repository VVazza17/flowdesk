# FlowDesk — Project Instructions

Cross-market capital flow & economic intelligence platform. Solo build, zero-cost mode.
Owner: Kyle. Design phase Jul–Aug 2026. Implementation starts Oct 2026.

## The spec is the law

The architecture spec is `docs/architecture.md` (v4.9.10, FROZEN, ~1500 lines, 231 KB).

**Never read the whole file.** It is ~58k tokens — a quarter of the context window, every
session. `grep` for the section header you need, then read that section only.

| Need | Section |
|---|---|
| Binding design decisions | §2 |
| Tech stack | §5 |
| Data sources, provider quirks, verified spike findings | §6 |
| Module specs (formulas, thresholds) | §7.x |
| DB schema | §8 |
| API spec | §9 |
| Frontend spec | §10 |
| Background jobs | §11 |
| Deployment | §13 |
| Security, deployment modes, cost | §14 |
| Testing & acceptance | §15 |
| **Build order and exit gates** | **§16** |
| Hard rules + normative pseudocode + glossary | §17 |

Where §3 and §16 disagree, **§16 governs**.

The eight ADRs in `docs/adr/` are binding where they interpret the spec. ADR-0001 … 0007 record what
the M0 spikes actually measured — several findings falsify what the spec originally assumed. ADR-0008
records M1 slice 10's heartbeat decisions.

## Current state

- **M0 CLOSED** (2026-08-16). Seven ADRs written; premium oracle fixtures recorded.
- **M1 in progress.** Slices 1–10 built; slice 10's alert drill is still open: compose (Postgres/TimescaleDB + Redis), FastAPI +
  `/health/live`, Alembic baseline, §8 ops tables, structlog JSON + correlation-ID middleware,
  `/health/ready` · `/health/data` · `/health/providers` (states documented in `docs/HEALTH.md`),
  private/public compose profiles with the IDEA-019 route invariant, GitHub Actions CI with
  hash-pinned locks, backup/restore scripts proven on a throwaway hypertable (`docs/BACKUP.md`),
  external heartbeat sender (ADR-0008, `docs/HEARTBEAT.md`).
- **The external heartbeat is not proven end to end.** The sender passes its smoke against a fake monitor.
  The real provider has never been called and no alert has ever fired: Kyle's drill in `docs/HEARTBEAT.md`
  closes this. `HEARTBEAT_URL` stays blank on development machines; a sleeping laptop would page constantly.
- **Any new `degraded` condition on `/health/ready` also pages the owner** (ADR-0008 section 4). The sender
  pings only on exactly `ok`.
- **Nothing takes backups automatically yet** (IDEA-029). The scripts exist; no scheduler, retention or
  off-box copy does. A plain `pg_restore` silently damages hypertables: use `infra/restore.sh`.
- **The public profile is not internet-ready.** `api-public` binds to loopback; strict CORS,
  request-size limits and rate limits have no owning slice and are required before any internet
  exposure (IDEA-019 addendum in `docs/SPEC-DEFECTS.md`).
- **arm64 is only proven under QEMU.** The image builds, loads its compiled wheels and answers
  `/health/live` under emulation. Migrations and TimescaleDB have never run on arm64.
- **Next: Kyle's heartbeat drill, then the M1 gate review.** The gate says "killing Redis/worker/provider", and
  no worker or provider exists until M2, so it can't pass literally. That's Kyle's call; do not decide it.

M1 is built as ten reviewable slices, in order. Each ends with something I can verify myself:

1. Repo skeleton + compose (Postgres/TimescaleDB, Redis) → `docker compose up -d`, psql connects
2. FastAPI app + Dockerfile + `/health/live` → curl returns 200
3. Alembic baseline + migration smoke test → `alembic upgrade head` on an empty DB
4. Ops tables: task_runs, ingestion_runs, backfill_runs, providers_health → migration applies
5. structlog JSON + correlation-ID middleware → one request, one JSON line carrying the id
6. `/health/ready` · `/health/data` · `/health/providers` → killing Redis gives a documented
   degraded state, not a 500
7. private/public compose profiles → every public-profile route uses only GET, HEAD or OPTIONS or
   is admin-guarded, and any route the check can't classify fails it (IDEA-019)
8. CI: ruff, mypy, pytest, migration smoke, vulnerability scan, license audit
9. Backup/restore scripts → restore into a fresh DB reproduces the data
10. External dead-man heartbeat (the monitor must live outside the monitored box)

**M1 exit gate (§16):** fresh clone starts with one command; migrations apply clean; backup
restores clean; killing Redis/worker/provider produces the documented health state; Postgres and
Redis never externally exposed; public profile exposes read-only routes only. (That last clause is
a lossy restatement of §14; IDEA-019 records the reading the build follows.)

Build `docker buildx` for amd64 + arm64 from slice 1 — §13 targets an Oracle Cloud ARM free
tier, and finding that mismatch in month four is a bad afternoon.

## Open decisions that block work

Tracked in `docs/SPEC-DEFECTS.md` (IDEA-013 … IDEA-031). Four block a milestone:

- **IDEA-013 → blocks M3.** `natural_key` has no time component, but §7.16's episode example
  requires five daily rows to exist. One of the two sections is wrong.
- **IDEA-017 → blocks M3 tests.** The M3 gate promises exactly-once delivery to Discord, which
  no implementation can achieve — webhooks expose no idempotency key.
- **IDEA-016 → blocks M4/M8.** `alerts` has no FK back to `events`; the M4 gate requires that join.
- **IDEA-029 → blocks M2's first persistent observation.** Backups have no scheduler, retention or off-box
  copy, and the point-in-time record can't be re-fetched once it starts accumulating.

Do not resolve these unilaterally. Flag and wait.

## Hard rules (§17.1 — follow literally)

- Do not reorder the build order. Do not start a milestone before the previous gate passes.
- Do not add: Kafka, Kubernetes, microservices, GraphQL, user accounts (pre-Phase 3),
  social scraping, or any agent framework.
- `signals/` contains **pure functions only** — no DB client, no HTTP library, no clock. That
  code belongs in `ingest/` or `services/`. This is what makes live and replay comparable.
- Every number shown in the UI or a brief exists in Postgres first. Never compute ad hoc in a
  handler.
- The Intelligence Plane never executes. Execution-Plane code (§2 decision 15's gated,
  own-capital-only path) is out of scope until M0–M8 ship and decision 15's gates are met —
  never build it opportunistically, however the request is framed.
- Never emit "arbitrage opportunity", "free money", "guaranteed", or "riskless" in any UI text,
  template, or prompt. `backend/tools/banned_phrases.py` enforces this in CI lint, over `frontend/`,
  `backend/app/ai/`, and `backend/app/` paths with `template` or `prompt` in the name. User-facing text
  must live in those paths, or it is not checked. None exist yet, so today it scans 0 files.
- Never add a dependency or service with a mandatory recurring cost while `ZERO_COST_MODE=true`.
  Stub it behind a flag and document the paid path.
- Provider drifted? Keep the interface and schema fixed, adapt the client internals. Provider
  gone? Stub the ingest, mark the feed stale, continue — never block the build.
- Never invent data. A failed provider call surfaces as a failure, not a sample value.
- When ambiguous, choose the simpler option and leave a `# DECISION:` comment.

## Feature freeze

No new modules, signals, or integrations until M0–M2 are built and six months of outcome data
exist. An idea goes in `docs/IDEAS.md` with an unfreeze trigger — not in the code, not in the
spec. Twelve are parked there (IDEA-001 … IDEA-012).

The doc's own rationale: *every remaining risk in this project is an execution risk, and design
elaboration is the most comfortable way to avoid execution.*

## Tech stack

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, Uvicorn
- DB: PostgreSQL 16 + TimescaleDB (hypertables for all time series)
- Cache/bus: Redis 7 — **never the source of truth**, Postgres owns all durable state
- Jobs: Celery + Beat, timezone America/New_York
- Frontend: Next.js 14+ App Router, TypeScript, Tailwind, shadcn/ui, TradingView Lightweight
  Charts, Recharts
- AI: optional, `ENABLE_AI` default false. Deterministic template brief is the default.
- Deploy: Docker Compose, local machine or free-tier ARM VM. Multi-arch images required.
- Observability: structlog JSON, Sentry, `/health/*` endpoints per §16 M1 scope

## How to work on this repo

1. **Plan before you code.** Anything larger than one file: propose the plan and the file list,
   then wait. Use plan mode.
2. **One slice at a time.** Do not run ahead because the next step seems obvious. Finish, test,
   stop, report.
3. **Tests are part of done** — not when the page renders (§16 operating principles).
4. **Explain as you go.** When you introduce a pattern (Alembic migration, Celery task, async
   session), say in one or two sentences what it does and why it's there.
5. **Flag a wrong or ambiguous spec; never silently reinterpret it.** Corrections go to
   `docs/SPEC-DEFECTS.md` or a `# DECISION:` comment.
6. **Prefer targeted edits to whole-file rewrites.** On a long file, rewriting the whole thing
   has dropped content near the end three times in this project. Append or replace a named
   section; say so explicitly.
7. **Never shorten to hit a length target.** If something doesn't fit, say so — don't cut
   content to fit a line budget.
8. **Commit directly to main.** Solo build, no review workflow, no feature branches.

## Commands

```
cp .env.example .env              # first time only; set a real POSTGRES_PASSWORD
docker compose up -d              # private profile (COMPOSE_PROFILES in .env): stack + migrate + api
docker compose --profile public up -d   # public instead (replaces, not adds); `docker compose rm -sf api` first
docker compose exec postgres psql -U flowdesk -d flowdesk
docker compose exec redis redis-cli ping
bash infra/smoke-slice1.sh        # slice 1 acceptance check
bash infra/smoke-slice2.sh        # slice 2: amd64+arm64 build (arm64 skips without emulation), /health/live 200, loopback bind
bash infra/smoke-slice3.sh        # slice 3: alembic upgrade head on an empty DB; up -d runs migrate
bash infra/smoke-slice4.sh        # slice 4: migrate reaches 0002; the four ops tables exist
bash infra/smoke-slice5.sh        # slice 5: one request -> one JSON log line with the X-Request-ID
bash infra/smoke-slice6.sh        # slice 6: kill Redis -> degraded 200; kill Postgres -> 503; never 500
bash infra/smoke-slice7.sh        # slice 7: public = read-only, redacted, no docs; private = neither
bash infra/smoke-slice8.sh        # slice 8: every gate green, then each goes red on a broken temp copy
bash infra/smoke-slice9.sh        # slice 9: hypertable + aggregate + policies survive a restore; 15 breaks go red
bash infra/smoke-slice10.sh       # slice 10: pings only on ready=ok, each outage silences them; uses a fake monitor
bash infra/backup.sh              # dump + manifest into dumps/. Restoring and the version trap: docs/BACKUP.md
bash infra/restore.sh dumps/<name>.dump --db <new-or-empty-db>   # refuses a non-empty target
bash infra/ci-checks.sh lint      # also: test | audit | license | arm64. Same code ci.yml runs
bash infra/lock-deps.sh           # after editing backend/requirements*.txt; regenerates the locks
docker compose down               # add -v to wipe the pgdata volume; inactive profiles are skipped,
                                  # so a public stack needs `docker compose --profile public down`
# NOTE: smoke scripts use infra/lib.sh wait_ready, not `up --wait`. Compose v5.6.1 fails --wait on
# the migrate one-shot even when it exits 0: seen on the other machine (2026-10-28, per Kyle) and on
# this one (2026-11-03). Other versions untested, including the v2.29.2-desktop.2 Kyle read here
# before Docker Desktop's 2026-11-03 upgrade. `docker compose up -d` is still the one command.
# NOTE: a native Windows postgresql-x64-17 service (automatic start) holds 5432 on this machine and
# stops the stack from starting. Stop it, or set POSTGRES_PORT in .env.
# NOTE: this machine has no arm64 emulation, by Kyle's choice. smoke-slice2.sh skips arm64 with a notice;
# `ci-checks.sh arm64` fails with `exec format error`. CI's arm64 job installs its own QEMU and is the gate.
# Migration tests (skipped without DATABASE_URL), from backend/:
#   DATABASE_URL=postgresql+asyncpg://flowdesk:<pw>@127.0.0.1:5432/flowdesk pytest
```

## Repo layout

```
docs/
  architecture.md      # v4.9.10 FROZEN spec — grep, never read whole
  adr/                 # ADR-0001 … 0008, binding
  IDEAS.md             # parked features, IDEA-001…012, each with an unfreeze trigger
  SPEC-DEFECTS.md      # flagged spec defects, IDEA-013…031
  BUILD-LOG.md         # narrative history: what shipped, what was decided, what broke
  HEALTH.md            # /health/* response shapes and the documented degraded states
  BACKUP.md            # restore runbook, the version trap, why plain pg_restore is forbidden
  HEARTBEAT.md         # external monitor setup, the alert drill, where it is armed, swapping provider
spikes/                # M0 proof scripts + evidence JSON (historical — do not modify)
tests/
  fixtures/            # oracle/golden test data (premium_oracle.json)
```

`backend/`, `frontend/` and `infra/` get created during M1.
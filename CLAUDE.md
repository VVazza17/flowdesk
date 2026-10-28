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

The seven ADRs in `docs/adr/` are binding where they interpret the spec. They record what the
M0 spikes actually measured — several findings falsify what the spec originally assumed.

## Current state

- **M0 CLOSED** (2026-08-16). Seven ADRs written; premium oracle fixtures recorded.
- **M1 in progress.** Slices 1–4 done: compose (Postgres/TimescaleDB + Redis), FastAPI +
  `/health/live`, Alembic baseline, §8 ops tables.
- **Next: slice 5** — structlog JSON + correlation-ID middleware.

M1 is built as ten reviewable slices, in order. Each ends with something I can verify myself:

1. Repo skeleton + compose (Postgres/TimescaleDB, Redis) → `docker compose up -d`, psql connects
2. FastAPI app + Dockerfile + `/health/live` → curl returns 200
3. Alembic baseline + migration smoke test → `alembic upgrade head` on an empty DB
4. Ops tables: task_runs, ingestion_runs, backfill_runs, providers_health → migration applies
5. structlog JSON + correlation-ID middleware → one request, one JSON line carrying the id
6. `/health/ready` · `/health/data` · `/health/providers` → killing Redis gives a documented
   degraded state, not a 500
7. private/public compose profiles → **needs the IDEA-019 decision first**
8. CI: ruff, mypy, pytest, migration smoke, vulnerability scan, license audit
9. Backup/restore scripts → restore into a fresh DB reproduces the data
10. External dead-man heartbeat (the monitor must live outside the monitored box)

**M1 exit gate (§16):** fresh clone starts with one command; migrations apply clean; backup
restores clean; killing Redis/worker/provider produces the documented health state; Postgres and
Redis never externally exposed; public profile exposes read-only routes only.

Build `docker buildx` for amd64 + arm64 from slice 1 — §13 targets an Oracle Cloud ARM free
tier, and finding that mismatch in month four is a bad afternoon.

## Open decisions that block work

Tracked in `docs/SPEC-DEFECTS.md` (IDEA-013 … IDEA-024). Three block a milestone:

- **IDEA-019 → blocks M1 slice 7.** §14 gives public mode an authenticated admin surface that
  may mutate; the M1 gate says "read-only routes only". An acceptance test must pick a reading.
- **IDEA-013 → blocks M3.** `natural_key` has no time component, but §7.16's episode example
  requires five daily rows to exist. One of the two sections is wrong.
- **IDEA-017 → blocks M3 tests.** The M3 gate promises exactly-once delivery to Discord, which
  no implementation can achieve — webhooks expose no idempotency key.
- **IDEA-016 → blocks M4/M8.** `alerts` has no FK back to `events`; the M4 gate requires that join.

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
  template, or prompt. A grep test enforces this.
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

## Commands

```
cp .env.example .env              # first time only; set a real POSTGRES_PASSWORD
docker compose up -d --wait       # Postgres/TimescaleDB + Redis
docker compose exec postgres psql -U flowdesk -d flowdesk
docker compose exec redis redis-cli ping
bash infra/smoke-slice1.sh        # slice 1 acceptance check
bash infra/smoke-slice2.sh        # slice 2: amd64+arm64 build, /health/live 200, loopback bind
bash infra/smoke-slice3.sh        # slice 3: alembic upgrade head on an empty DB; up -d runs migrate
bash infra/smoke-slice4.sh        # slice 4: migrate reaches 0002; the four ops tables exist
docker compose down               # add -v to wipe the pgdata volume
# NOTE: smoke scripts use infra/lib.sh wait_ready, not `up --wait` (Compose 5.6.1 fails --wait
# on the migrate one-shot even when it exits 0). `docker compose up -d` is still the one command.
# Migration tests (skipped without DATABASE_URL), from backend/:
#   DATABASE_URL=postgresql+asyncpg://flowdesk:<pw>@127.0.0.1:5432/flowdesk pytest
```

## Repo layout

```
docs/
  architecture.md      # v4.9.10 FROZEN spec — grep, never read whole
  adr/                 # ADR-0001 … 0007, binding
  IDEAS.md             # parked features, IDEA-001…012, each with an unfreeze trigger
  SPEC-DEFECTS.md      # flagged spec defects, IDEA-013…024
  BUILD-LOG.md         # narrative history: what shipped, what was decided, what broke
spikes/                # M0 proof scripts + evidence JSON (historical — do not modify)
tests/
  fixtures/            # oracle/golden test data (premium_oracle.json)
```

`backend/`, `frontend/` and `infra/` get created during M1.
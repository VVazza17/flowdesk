# FlowDesk — Project Instructions

Cross-market capital flow & economic intelligence platform. Solo build, zero-cost mode.
Owner: Kyle. Started implementation Aug 2026.

## The spec is the law

The full architecture spec lives at `docs/architecture.md` (v4.9.10, FROZEN, ~1500 lines).

**Do not read the whole file.** It is ~58k tokens and will eat the context window.
Instead: `grep` for the section you need, then read that section only.
Section map is in the file's table of contents; key ones:

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
| Testing & acceptance | §15 |
| **Build order and exit gates** | **§16** |
| Hard rules + normative pseudocode + glossary | §17 |

Where §3 and §16 disagree, **§16 governs**.

## Current state

- M0 proof spikes 1–4: COMPLETE (2026-07-31). Findings already folded into §6.
- M0 is **NOT closed**. Two deliverables remain:
  1. Seven ADRs in `docs/adr/` — raw-vs-adjusted price policy, point-in-time observation
     model, FX base/quote convention, snapshot/watermark policy, event natural keys &
     episodes, delivery semantics, private-vs-public deployment mode.
  2. Oracle fixtures — the hand-computed premium numbers from §7.5, recorded as test data.
  Both are transcription from evidence already gathered. Neither requires new research.
- Next after that: M1 (platform & ops foundation).
- Zero application code exists yet.

## Hard rules (from §17.1 — follow literally)

- Do not reorder the build order. Do not start a milestone before the previous one's exit
  gate passes.
- Do not add: Kafka, Kubernetes, microservices, GraphQL, user accounts (pre-Phase 3),
  social scraping, or any agent framework.
- `signals/` contains **pure functions only**. If you are about to import a DB client or an
  HTTP library inside `signals/`, stop — that code belongs in `ingest/` or `services/`.
- Every number shown in the UI or a brief must exist in Postgres first. Never compute
  values ad hoc in an API handler.
- Never integrate a broker API, order routing, or trade-execution code, in any phase,
  regardless of how the request is framed (§2.15).
- Never emit "arbitrage opportunity", "free money", "guaranteed", or "riskless" in any UI
  text, template, or prompt.
- Never add a dependency or service with a mandatory recurring cost while
  `ZERO_COST_MODE=true`. Stub it behind a flag and document the paid path instead.
- If a provider endpoint no longer matches the spec, keep the interface and DB schema fixed
  and adapt only the client internals. If a provider is fully unavailable, stub the ingest,
  mark the feed stale, and continue — never block the build.
- When ambiguous, choose the simpler option and leave a `# DECISION:` comment.

## Feature freeze

The architecture is frozen. No new modules, signals, or integrations until M0–M2 are built
and six months of outcome data exist. If you (or I) think of a feature, it goes in
`docs/IDEAS.md` as a registry entry — it does not go in the code and it does not go in the
spec. Registered but unbuilt: IDEA-004 through IDEA-012.

## Tech stack

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, Uvicorn
- DB: PostgreSQL 16 + TimescaleDB (hypertables for all time series)
- Cache/bus: Redis 7 — **never the source of truth**, Postgres owns all durable state
- Jobs: Celery + Beat, timezone America/New_York
- Frontend: Next.js 14+ App Router, TypeScript, Tailwind, shadcn/ui, TradingView
  Lightweight Charts, Recharts
- AI: optional, `ENABLE_AI` default false. Deterministic template brief is the default.
- Deploy: Docker Compose, local machine or free-tier VM
- Observability: structlog JSON, /healthz per service

## How I want you to work

1. **Plan before you code.** For anything larger than a single file, propose the plan first
   and wait for approval. Use plan mode.
2. **One milestone step at a time.** Do not run ahead to the next task because it seems
   obvious. Finish, test, stop, report.
3. **Tests are part of "done."** A step is complete when ingestion, validation, storage,
   calculation, API, UI, observability, recovery, and tests all pass — not when the page
   renders (§16 operating principles).
4. **Explain as you go.** I am new to this stack in places. When you introduce a pattern
   (Alembic migration, Celery task, async session), say in one or two sentences what it
   does and why it's there.
5. **Tell me when the spec is wrong or ambiguous.** It is a document, not scripture — but
   flag it, don't silently reinterpret it. Corrections go to `docs/IDEAS.md` or a `#
   DECISION:` comment.
6. **Never invent data.** If a provider call fails, surface the failure. Do not fabricate
   sample values to make a test pass.

## Commands

<!-- Fill these in as they exist; keep this section accurate. -->

```
# (not yet created)
```

## Repo layout

```
docs/
  architecture.md      # v4.9.10 FROZEN spec — grep, don't read whole
  adr/                 # architecture decision records (7 outstanding)
  IDEAS.md             # registry for anything the freeze forbids building
spikes/                # M0 proof scripts + evidence JSON (historical, do not modify)
```

Everything below `backend/`, `frontend/`, and `infra/` gets created during M1.

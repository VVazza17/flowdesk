# FlowDesk

FlowDesk is a cross-market capital flow and economic intelligence platform, built solo in zero-cost mode.
It will ingest market and macro data, compute signals from it and report what it finds; it observes and never executes.
Only the M1 platform foundation exists so far (database, API health endpoints, migrations, CI, backups, heartbeat), with no ingestion or UI yet.

## Start it

Requires Docker with Compose.

```
cp .env.example .env     # first time only; set a real POSTGRES_PASSWORD in it
docker compose up -d     # Postgres/TimescaleDB, Redis, migrations, the API and the heartbeat sender
curl http://127.0.0.1:8000/health/ready
```

`docker compose up` fails with "set in .env" until the `cp` step is done. The API and Postgres publish on
127.0.0.1 only, and Redis publishes no port. If something else already holds port 5432 (a native Windows PostgreSQL service, for
one), set `POSTGRES_PORT` in `.env`. The public profile and the rest of the command list are in `CLAUDE.md`.

## Where things are

| Need | Read |
|---|---|
| The architecture spec (frozen, ~1500 lines; search it for a section, don't read it whole) | `docs/architecture.md` |
| Binding decisions that interpret the spec | `docs/adr/` |
| What `/health/*` returns and what each degraded state means | `docs/HEALTH.md` |
| Backing up and restoring (never use a plain `pg_restore`) | `docs/BACKUP.md` |
| The external dead-man heartbeat: setup, alert drill, swapping provider | `docs/HEARTBEAT.md` |
| What was built and decided, in order | `docs/BUILD-LOG.md` |
| Known spec defects and open decisions | `docs/SPEC-DEFECTS.md` |
| Parked ideas, each with an unfreeze trigger | `docs/IDEAS.md` |
| How to work on this repo, hard rules, current state | `CLAUDE.md` |

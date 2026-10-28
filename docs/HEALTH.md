# FlowDesk — Health endpoints and documented degraded states

Implemented in M1 slice 6. The M1 gate: killing Redis (or Postgres) produces the state below,
never a 500. Verified by `infra/smoke-slice6.sh` and `backend/tests/test_health_*.py`.

The spec (§9 line 968) gives no response shape for these routes (IDEA-021, IDEA-025). The shapes
here are implementation decisions, not spec text. Bodies are bare JSON, not the §9 envelope.

Failure detail is the exception **class name only**. Messages can contain connection strings.

## `GET /health/live` — is the process answering

Always `200 {"status":"ok"}`. Touches no database, Redis or clock. Docker's healthcheck uses it.

## `GET /health/ready` — can this API serve real data

| Situation | HTTP | `status` | Notes |
|---|---|---|---|
| Everything up, schema at head | 200 | `ok` | |
| **Redis down** | **200** | **`degraded`** | Redis is a cache/bus, never the source of truth. `checks.redis` = `{"status":"down","error":"<Class>"}` |
| Postgres down / unreachable / timed out | 503 | `not_ready` | `checks.migrations.status` = `unknown` |
| Schema behind code (or never migrated) | 503 | `not_ready` | `checks.migrations` = `{"status":"behind","current":<rev or null>,"head":<rev>}` |

Each check has a 2 s timeout; a hung dependency reports `down` / `TimeoutError`.

## `GET /health/providers` — feed freshness (`providers_health`)

Postgres only: a Redis outage does not affect it. Postgres unreachable, or tables missing →
`503 {"status":"unavailable","error":"<Class>"}`.

Top-level `status`, in precedence order. **Only `stale` means something broke.**

| `status` | Meaning | Broken? |
|---|---|---|
| `no_feeds` | No `providers_health` rows at all (fresh install, before M2) | no |
| `stale` | At least one feed succeeded before and is now past its threshold | **yes** |
| `pending` | No stale feed, but at least one feed has **never succeeded** | no |
| `unjudged` | No stale feed, but some feed has no threshold configured | no |
| `ok` | Every feed is fresh | no |

Per-feed `status`: `fresh` · `stale` · `never_succeeded` (`stale: false`) · `no_threshold`
(`stale: null`). `last_attempt_failed` is true when the last error is newer than the last success.
Staleness is HTTP 200: it describes a feed, it is not an API outage.

Thresholds: `app/settings.py` `FEED_STALE_AFTER`, keyed `(provider, feed)`, empty until M2.
There is no global default (§6.11: thresholds follow each series' own cadence).

`never_succeeded` carries no age because `providers_health` has no first-seen column, so it can
never escalate to `stale` by itself. Logged as IDEA-026.

## `GET /health/data` — pipeline state as recorded

Postgres only; same `503 unavailable` behaviour. Returns the latest `ingestion_runs` row per
(provider, feed), latest `backfill_runs` row per feed, and latest `task_runs` row per task name.
`status` is `no_runs` (all empty) or `reported`. **It reports; it does not judge**, because §8 gives
these tables no status vocabulary (IDEA-022).

**Assumption pending IDEA-024:** backfill progress is read from `backfill_runs` (§16 line 1109).
§10 line 1003 says it comes from `providers_health`. If IDEA-024 resolves that way, the
`backfills` key and its query move.

## Not covered yet

- Killing the worker or a provider (also in the M1 exit gate): neither exists until M2.
- Public-profile exposure of these routes, notably `providers[].last_error` (raw provider text):
  slice 7, blocked on IDEA-019.

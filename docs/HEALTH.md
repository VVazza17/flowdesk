# FlowDesk — Health endpoints and documented degraded states

Implemented in M1 slice 6; the worker check was added in M2 slice 1. The M1 gate: killing Redis (or
Postgres) produces the state below, never a 500. Verified by `infra/smoke-slice6.sh` and
`backend/tests/test_health_*.py`. The worker half of the clause (IDEA-032, moved to M2's gate) is verified by
`infra/smoke-m2-slice1.sh` and `backend/tests/test_worker_health.py`; the provider half is slice 4's.

The spec (§9 line 968) gives no response shape for these routes (IDEA-021, IDEA-025). The shapes
here are implementation decisions, not spec text. Bodies are bare JSON, not the §9 envelope.

Failure detail is the exception **class name only**. Messages can contain connection strings.

## `GET /health/live` — is the process answering

Always `200 {"status":"ok"}`. Touches no database, Redis or clock. Docker's healthcheck uses it.

## `GET /health/ready` — can this API serve real data

| Situation | HTTP | `status` | Notes |
|---|---|---|---|
| Everything up, schema at head | 200 | `ok` | |
| **Redis down** | **200** | **`degraded`** | Redis is a cache/bus, never the source of truth. `checks.redis` = `{"status":"down","error":"<Class>"}`. The worker check goes `stale` too after a few intervals; see below. |
| **Worker or Beat stopped, wedged, or not completing** | **200** | **`degraded`** | `checks.worker.status` = `stale`, or `never_completed` past its grace. See "The worker check". |
| Fresh stack, no canary completed yet | 200 | `ok` | `checks.worker` = `never_completed` with `in_grace: true`. |
| Postgres down / unreachable / timed out | 503 | `not_ready` | `checks.migrations.status` = `unknown` |
| Schema behind code (or never migrated) | 503 | `not_ready` | `checks.migrations` = `{"status":"behind","current":<rev or null>,"head":<rev>}` |

Each check has a 2 s timeout; a hung dependency reports `down` / `TimeoutError`.

**This route drives the external heartbeat.** Only `ok` sends a ping (ADR-0008, `docs/HEARTBEAT.md`), so on a
monitored instance **`degraded` is not a quiet state: it pages the owner after 45 minutes**, even though it is a
200. Adding a new `degraded` condition here means adding a new reason to page. If it shouldn't page, it doesn't
belong in `degraded`, or the gate in `app/heartbeat.py` changes in the same commit.

### The worker check (`checks.worker` in `/health/ready`)

**What it measures.** A canary task, `ops.canary`, runs every `CANARY_INTERVAL_SECONDS` (default 60). Beat
schedules it, Redis carries it, a worker runs it, and the run is recorded in `task_runs`. The API reads the age
of the newest `succeeded` run. So the check is keyed on *recent completion*, not on whether a process is alive:
a worker that is up but frozen, or stuck, goes stale exactly like one that is dead. Verified by pausing the
container (`docker compose pause worker`: process alive, not working).

| `checks.worker.status` | Meaning | `/health/ready` |
|---|---|---|
| `ok` | A canary succeeded within `stale_after_seconds` (3 x the interval, 180 s by default) | `ok` |
| `stale` | The last success is older than that | **`degraded`** |
| `never_completed`, `in_grace: true` | No canary has ever succeeded, and this API process started less than `stale_after_seconds` ago. A fresh stack has not had time yet. | `ok` |
| `never_completed`, `in_grace: false` | Still none, past the grace | **`degraded`** |
| `unknown` | Postgres is down (`reason: postgres_down`) or the read failed (`error: <Class>`). Not being able to ask is not evidence that the worker is dead, and the cause shows in its own check. | unchanged by this check |

`ok` and `stale` carry `last_completed_at` and `age_seconds`. Every state carries `task` and
`stale_after_seconds`.

**One state for several causes, on purpose.** Beat dying, the worker dying, the worker freezing, and Redis dying
all stop completions, so all read `stale`. Telling them apart is a job for the logs, `docker compose ps`, and
(for Redis) its own check, which sits beside this one. Redis down therefore shows two things at once: `redis`
`down` immediately, and `worker` `stale` a few intervals later.

**This degrades, and so it pages.** A stale worker stops the external heartbeat's pings, and the monitor alerts
after 45 minutes (ADR-0008 section 4). That is intended (Kyle, M2 planning): a dead worker means no data is
being ingested. A stale **backup** is the opposite case and must not appear here; it will show on `/health/data`.

**Where the grace comes from.** `task_runs` is empty on a fresh install and has no first-seen time to measure
from (the same gap as IDEA-026), so the grace is measured from the API process's start. An API that has been up
a long time over an empty `task_runs` is past its grace, which is right: something should have run by then.

**What it does not catch yet.**
- A task stuck in `running` after its process died. The canary is instant, so nothing to sweep exists yet; the
  sweep arrives with the first long task (backfill, slice 10), using `heartbeat_at`.
- A long task starving the canary. When one shares the worker, a canary queued behind it could read as a dead
  worker. Revisit when the first long task lands (a separate queue or a dedicated worker).

Settings: `CANARY_INTERVAL_SECONDS` (`app/settings.py`), read by the API and by Beat from one variable so they
cannot disagree. The smoke test sets it to 3 so the stale threshold is 9 s.

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
these tables no status vocabulary (IDEA-022). The one exception is `task_runs`, where the code writes
`running` / `succeeded` / `failed` (a stopgap, also under IDEA-022), and the worker check reads `succeeded`.
`tasks[]` therefore shows the canary's latest run, and `error_class` only (never `error_message`).

### `backups` (M2 slice 2)

`GET /health/data` also carries a `backups` object with the two steps of the scheduled backup, side by side
(`docs/BACKUP.md`). Unlike the run lists, these have **states**, because "the last backup is three days old" is
a fact worth stating:

| `state` | Meaning |
|---|---|
| `ok` | A run succeeded within `stale_after_seconds` (36 h, `BACKUP_STALE_AFTER_HOURS`) |
| `stale` | It did, but longer ago than that |
| `never_succeeded` | It has run and failed, and has never succeeded (e.g. `last_error_class` = `NoRecipient`) |
| `not_configured` | The newest run was `skipped`: no off-box destination is set. Never a success. |
| `no_runs` | No row at all: the container has not tried yet |

`backups.backup` is the dump, encrypt and keep-locally step. `backups.copy` is the off-box copy. They are
separate so that a sleeping desktop reads as `copy.last_attempt_failed: true` with `last_error_class:
Unreachable` while `backup` stays `ok`. A copy whose last attempt failed is still `ok` while an earlier copy is
recent enough. Error classes: `NoRecipient`, `DumpFailed`, `DumpUnreadable`, `EncryptFailed`, `ReadFailed`
(backup); `Unreachable`, `SftpFailed`, `SizeMismatch`, `ConfigInvalid`, `NothingToCopy` (copy). An interrupted upload is not an error
class: it is stepped over (`docs/BACKUP.md`).

**The expected state while the laptop is away from the destination.** The destination is on the home LAN, so
on a weekday daytime every copy attempt fails and retries every 30 minutes (`BACKUP_RETRY_CAP_MINUTES`): about
twenty failed `ops.backup_copy` rows a day. `/health/data` shows `copy.state: ok` (the last success is recent),
`last_attempt_failed: true` and `last_error_class: Unreachable`. That combination, on a weekday, is normal.
`tasks[]` carries only the newest row per task, so the many failed rows do not bury or crowd anything in this
response. `copy.state` going `stale` (36 h) is the real signal here, and the page behind it is the monitor.

**Known limit: `Unreachable` is too wide.** `run-copy.sh` maps every ssh exit 255 to `Unreachable`, and 255 is
also what ssh returns for a refused key and for a changed host key. So a copy that has broken for good at home
reads exactly like a normal Tuesday until it goes `stale`. Splitting the classes (so those two read differently
from "host away") is proposed in `docs/BUILD-LOG.md` (2026-11-10) and is not built. `error_message` holds the
detail but this endpoint never returns it.

**This never touches `/health/ready` or the HTTP status.** A stale backup can wait until morning, and anything
that degrades `ready` pages (ADR-0008 section 4). The page comes from the external monitor noticing the copy's
pings stop. Both steps appear under `tasks[]` too, as the latest run per task.

**Assumption pending IDEA-024:** backfill progress is read from `backfill_runs` (§16 line 1109).
§10 line 1003 says it comes from `providers_health`. If IDEA-024 resolves that way, the
`backfills` key and its query move.

## Public profile (`DEPLOYMENT_MODE=public`, slice 7)

Status codes and every state above are the same on both profiles. The bodies differ as follows,
per IDEA-019 and its addendum:

| Route | Public body |
|---|---|
| `/health/providers` | `feeds[].last_error` key removed |
| `/health/data` | `ingestion[].error` key removed |
| `/health/live`, `/health/ready` | unchanged (they carry exception class names at most) |

The keys are removed, not nulled: `null` would read as "no error". `last_error_at`,
`last_attempt_failed` and the run's `status` stay, so a failure is still visible. `/docs`, `/redoc`
and `/openapi.json` return 404. Verified by `infra/smoke-slice7.sh` and
`backend/tests/test_public_profile.py`.

## Not covered yet

- Killing a provider (also in the M1 exit gate, moved to M2's gate by IDEA-032): no provider exists until slice
  4 builds the framework. The worker half is done (slice 1, above).
- Whether the backup has ever been restored on the real desktop: only the closing drill in `docs/BACKUP.md`
  tells you; `backups` says whether runs succeed, not whether the files can be read.

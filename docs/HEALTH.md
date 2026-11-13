# FlowDesk — Health endpoints and documented degraded states

Implemented in M1 slice 6; the worker check was added in M2 slice 1. The M1 gate: killing Redis (or
Postgres) produces the state below, never a 500. Verified by `infra/smoke-slice6.sh` and
`backend/tests/test_health_*.py`. The worker half of the clause (IDEA-032, moved to M2's gate) is verified by
`infra/smoke-m2-slice1.sh` and `backend/tests/test_worker_health.py`. The provider half (slice 4) is
verified by `infra/smoke-m2-slice4.sh` and `backend/tests/test_ingest_runner.py`, against the fake provider
only; see "Killing a provider" below.

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
| **Ingest worker stopped, wedged, or not completing** | **200** | **`degraded`** | `checks.ingest_worker.status` = `stale`, or `never_completed` past its grace. See "The ingest worker check". |
| Fresh stack, no canary completed yet | 200 | `ok` | `checks.worker` = `never_completed` with `in_grace: true`. |
| Postgres down / unreachable / timed out | 503 | `not_ready` | `checks.migrations.status` = `unknown` |
| Schema behind code (or never migrated) | 503 | `not_ready` | `checks.migrations` = `{"status":"behind","current":<rev or null>,"head":<rev>}` |

Each check has a 2 s timeout; a hung dependency reports `down` / `TimeoutError`.

### The readiness rule

**`/health/ready` degrades when the machinery has stopped, not when a source is unavailable.** This is a
rule, not a case to be decided each time a new check is added.

- *Machinery* is what FlowDesk runs and can restart: Postgres, Redis, the worker and Beat, the schema. If one
  of these is not working, nothing is being ingested or served, and only the owner can fix it.
- A *source* is something outside FlowDesk whose state we only observe: a data provider, a feed, the off-box
  backup destination. It can be down, slow, rate-limiting or late for reasons FlowDesk cannot act on.

The test for a new condition: *if the external thing recovered by itself, would FlowDesk be working again
with no action from the owner?* If yes it is a source, and it does not degrade `ready`. If it needs the
owner to restart or repair something, it is machinery, and it does.

| Condition | Class | Degrades `ready`? | Where it shows |
|---|---|---|---|
| Redis down | machinery | **yes** | `checks.redis` |
| Worker or Beat dead, wedged, or not completing | machinery | **yes** | `checks.worker` |
| Ingest worker dead, wedged, or not completing | machinery | **yes** | `checks.ingest_worker` |
| Postgres down, schema behind | machinery | **503** | `checks.migrations` |
| One feed stale, or a provider down (killed) | source | no | `/health/providers` |
| Every feed stale at once | source | no | `/health/providers` |
| Backup stale, off-box copy failing | source | no | `/health/data` `backups` |

Why it matters: this route drives the external heartbeat, and `degraded` pages after 45 minutes (below).
Providers include an unofficial one (yfinance) that will fail on its own schedule. Paging for that would
page about things the owner cannot fix, and a pager that cries wolf gets ignored when the worker really dies.
A stale feed is loud where it belongs: `/health/providers` goes `stale`. It does not page through the ADR-0008
monitor; the ops heartbeat of §11 (a later milestone) is the place for that.

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
- ~~A task stuck in `running` after its process died.~~ Fixed in M2 slice 5, step 3, part C: every task beats
  `heartbeat_at` and the sweep fails the rows nothing is alive behind. See "The sweep" below.
- ~~A long task starving the canary.~~ Fixed in M2 slice 5, step 0: the canary has its own queue and its own
  worker process, and ingest work has another. See "The ingest worker check" for what that cost.

Settings: `CANARY_INTERVAL_SECONDS` (`app/settings.py`), read by the API and by Beat from one variable so they
cannot disagree. The smoke test sets it to 3 so the stale threshold is 9 s.

`checks.worker` is the **ops worker**: the `worker-ops` compose service, one slot, consuming the `ops` queue
and nothing else, running `ops.canary` and the sweep (`ops.sweep`) and nothing else. No amount of ingest work can make it wait, so a
`stale` here is never a long task. (`docker compose pause worker-ops` is the wedged case.)

### The ingest worker check (`checks.ingest_worker` in `/health/ready`)

**Why it exists.** Splitting the canary onto its own worker made `checks.worker` immune to ingest load, and in
the same stroke blind to the ingest worker. Without a second check, a wedged ingest worker would surface only
as every feed going stale on `/health/providers`, which by the readiness rule never pages: a machinery failure
wearing a source failure's costume. It would break the rule in the one case the rule was written for. So the
ingest worker has its own canary, `ops.canary_ingest`, on the `ingest` queue (the `worker` compose service,
two slots), and its own check. (Decided by Kyle, 2026-11-11, option (b).)

It has the same states as the worker check (`ok`, `stale`, `never_completed` with `in_grace`, `unknown`) and
the same fields, with `task` = `ops.canary_ingest`. It differs in one thing, the threshold:

**`stale_after_seconds` = 3 x `CANARY_INTERVAL_SECONDS`**, the same as the ops worker's. (Until M2 slice 5, step 3
part C it was `INGEST_TASK_MAX_SECONDS` plus three intervals, half an hour by default, because a long task could
starve the canary that long and a tighter number would have paged on a healthy backfill. Part C removed the
reason, below.) `INGEST_TASK_MAX_SECONDS` is now only Celery's hard `task_time_limit`.

**`busy`: alive, not degraded.** When the ingest canary has not completed within the threshold, the check asks
one more question: is a task **of the ingest worker** running with a fresh `heartbeat_at`? If so the state is
`busy` (`running_task` names it) and it does **not** degrade `/health/ready`: a worker that is doing its job
behind a long task is alive. If not, the canary's verdict stands: `stale`, or `never_completed` past its grace.
The question is asked only when the canary says otherwise, and a failure to ask changes nothing (not being able
to ask is not evidence). Only the ingest worker can be `busy`: the ops worker runs the canary and the sweep and
nothing long, so nothing excuses a stuck ops canary.

- **Which tasks count** (`app.tasks.names.INGEST_WORKER_TASKS`): Celery tasks that run in the ingest worker. A
  manual backfill (`python -m app.backfill`, a separate process) deliberately does **not**: its heartbeat says
  nothing about the ingest worker, and counting it would let a wedged ingest worker look busy for the length of a
  backfill.
- **What a beat proves.** The worker PROCESS is alive and can reach Postgres (a thread writes it, so a body stuck
  in a blocking call keeps beating). It does not prove the body is making progress; that is bounded by the
  per-request timeouts and, last, the hard task time limit, which kills the process and so stops the beats.
- **What it costs, stated plainly.** A wedged ingest worker with no task running shows `stale` within three
  canary intervals (minutes, not half an hour). A worker **killed mid-task** reads `busy` until its last beat goes
  stale (up to 120 s), then `stale` and degraded, and the sweep fails the orphaned rows within 120 to 240 s of the last
  beat (`infra/smoke-m2-slice5-step3-sweep.sh` kills one with SIGKILL and watches exactly this). A wedged worker
  *with a task that is itself stuck but still beating* reads `busy` until that task hits the hard limit. That is the
  one case the heartbeat cannot see.
- The old "KNOWN GAP" (two slots running long tasks back to back could keep the canary waiting past the
  half-hour threshold and page a healthy worker) is gone with the threshold: `busy` does not depend on queue
  position.
- A task killed by the time limit leaves its `task_runs` and `ingestion_runs` rows `running`. The sweep that
  `runs.py` defers to "the first long task" is due with the same work: step 3's FRED backfill is the first
  long task, so that trigger fired early. Built together with the heartbeat check, at step 3.

Settings: `INGEST_TASK_MAX_SECONDS`, read by the workers (to enforce it) and the API (to judge by it).
Verified by `infra/smoke-m2-slice5-step0.sh` (queues separate; ingest saturation leaves `ready` `ok`; each
worker paused degrades it) and `backend/tests/test_worker_health.py`.

### The sweep (M2 slice 5, step 3, part C): `running` rows nothing is alive behind

**One definition of alive** (`app/tasks/liveness.py`; the writer, the probe and the sweep import it, none restates
a number): a task beats `heartbeat_at` every `HEARTBEAT_INTERVAL_SECONDS` = 30 from a background thread started
by `run_recorded` (the one wrapper every task goes through, **including a manual backfill**), and a beat is fresh
for `HEARTBEAT_FRESH_SECONDS` = 120 (four intervals: three beats can be lost to a slow commit or a pause before a
live task looks dead). A NULL heartbeat is never fresh. A test pins `FRESH >= 3 x INTERVAL`.

**The rule, one for both tables** (`app/tasks/sweep.py`, task `ops.sweep` on the `ops` queue, every 120 s, so a
saturated ingest queue cannot starve it):
- a `task_runs` row that is `running` and not fresh becomes `failed`, `error_class = 'Orphaned'`;
- an `ingestion_runs` row that is `running` and whose `task_run_id` does not point at a `running` task with a
  fresh heartbeat becomes `failed`, `error = 'Orphaned: ...'`. A NULL `task_run_id` fails that test by itself.
- Both in one transaction, `task_runs` first, so the two tables never disagree about the same run.
- `ingestion_runs.task_run_id` (migration 0008, nullable, `ON DELETE SET NULL`) is written by `run_feed`, which
  **refuses to run outside a recorded task** (`NotRecorded`, before writing anything). Rows from before the
  migration have none and cannot be backfilled.

**Detection time**: a dead task's rows are failed between 120 s and 240 s after its last beat.

**A swept row whose task finishes later keeps the sweep's verdict**; the late result is a note appended to the row
(`completed after sweep at <ts>, rows_written=<n>` or `failed after sweep at <ts>: <Class>`), never a status, and
`task_completed_after_sweep` / `ingest_completed_after_sweep` is logged. Every completion write is
`... WHERE id = :id AND status = 'running'`, so a sweep and a completion that race are serialized by the row
lock. `providers_health` still records a provider call that succeeded (it is about the source, not the
machinery). A task's heartbeat thread exits by itself when its row stops being `running`, and is stopped on every
exit path of `run_recorded` (return, exception, `SystemExit`, a failing final write).

**What the sweep deliberately does not touch**, each a reasoned exclusion and not a gap:
- **`backfill_runs`.** A resumable backfill spans several task runs by design (slice 10: a worker restart mid-run,
  after which a new task picks the same row up). A row that is `running` with no live task between a crash and the
  resume is correct, not orphaned; "has anything picked this up recently" depends on the resume design (slice 10).
  Pinned by a test. (Kyle, 2026-11-12.)
- **`ops.backup` and `ops.backup_copy`** (`UNSWEPT_TASKS`). The backup container writes their rows with psql, not
  through `run_recorded`, so nothing beats their heartbeat: a healthy dump or SFTP copy longer than 120 s would be
  failed as orphaned, and the script's unconditional final `UPDATE` would then overwrite the verdict. Their
  staleness is read from `/health/data` (`backups`) and the external monitor's missed-run signal. Found while
  building part C; revisit if the backup scripts ever heartbeat. Pinned by a test. **Two writers of `task_runs`,
  two protocols:** the backup scripts' final write (`task_finish`, `infra/backup/lib.sh`) is unconditional, while
  `run_recorded`'s is `AND status = 'running'`. If the backups ever join the sweep, that write becomes
  conditional in the same change.

**Not yet watched, by decision (Kyle, 2026-11-13):** no check reads `ops.sweep`. The sweep shares the `ops` queue and
worker with the canary, so the ops worker dying already degrades readiness (`checks.worker`). The uncovered case is
narrow: the sweep task failing while the canary keeps succeeding, which leaves orphaned rows unswept with no state
change. It leaves an `ops.sweep` row each run, so it is visible. Revisit if it is ever seen or a second task joins the
`ops` queue.

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
Each feed also carries `first_seen_at`, and `never_succeeded: true` when it is `stale` without ever having
succeeded.
Staleness is HTTP 200: it describes a feed, it is not an API outage.

Thresholds: `app/settings.py` `FEED_STALE_AFTER`, keyed `(provider, feed)`, empty until M2.
There is no global default (§6.11: thresholds follow each series' own cadence).

**A feed that has never succeeded escalates.** `providers_health.first_seen_at` (migration 0004; a column §8
does not list, IDEA-026 addendum) is set when the feed is first registered. The seed registers every feed
declared in `FEED_STALE_AFTER`, so a feed that is scheduled but never runs is visible, not absent. Once
`now - first_seen_at` is past that feed's own stale threshold, `never_succeeded` becomes `stale` (with
`never_succeeded: true`). Inside the window it is still `never_succeeded`, which is not broken. A feed with no
threshold cannot be judged and stays `never_succeeded`.

## `GET /health/data` — pipeline state as recorded

Postgres only; same `503 unavailable` behaviour. Returns the latest `ingestion_runs` row per
(provider, feed), latest `backfill_runs` row per feed, and latest `task_runs` row per task name.
`status` is `no_runs` (all empty) or `reported`. **It reports; it does not judge**, because §8 gives
these tables no status vocabulary (IDEA-022). The one exception is `task_runs`, where the code writes
`running` / `succeeded` / `failed` (a stopgap, also under IDEA-022), and the worker check reads `succeeded`.
`tasks[]` therefore shows the canary's latest run, and `error_class` only (never `error_message`).

`ingestion[]` (one entry per feed: its latest run) is written by `run_feed` with `status` `running` /
`succeeded` / `failed` (a stopgap under IDEA-022; TEXT, no CHECK). There is one row per run, however many
attempts the run took. `error` is `<kind>:<Class>: <text>` with credentials removed; the public profile
removes the key.

**`rows_written` is a verification signal, not a statistic.** It counts the rows a run created or *changed*
in canonical state, never the rows it attempted to write. `rows_read` is what the provider returned. A re-ingest
of an identical payload therefore reads N and writes 0, and that 0 is the observable evidence for the M2 gate's
clause "re-ingesting identical payloads yields identical canonical rows". Do not change it to count attempts:
that would make a correct idempotent re-run look as if it had written everything again.

### Known bias: `available_at` for a FRED first print is up to a day late (M2 slice 5)

Anything on this endpoint (or later, in the per-series data age) that is computed from `macro_metrics.available_at`
inherits a documented bias. A first print's `available_at` is the end of its publication day in New York
(ADR-0003, addendum of 2026-11-11), so it is **late by 0 to 24 hours, never early**, and a series' data age
read as `now - max(available_at)` is correspondingly up to a day **younger** than the truth. That is the safe
direction for a staleness alarm only in the sense that it cannot invent a look-ahead; it can delay a stale
verdict by up to a day, which is small beside the per-series thresholds (generous multiples of observed lag,
days to months). Do not tighten a threshold to chase it.

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
(backup); `Unreachable`, `AuthRefused`, `HostKeyChanged`, `SftpFailed`, `SizeMismatch`, `ConfigInvalid`,
`NothingToCopy` (copy). An interrupted upload is not an error class: it is stepped over (`docs/BACKUP.md`).

**The expected state while the laptop is away from the destination.** The destination is on the home LAN, so
on a weekday daytime every copy attempt fails and retries every 30 minutes (`BACKUP_RETRY_CAP_MINUTES`): about
twenty failed `ops.backup_copy` rows a day. `/health/data` shows `copy.state: ok` (the last success is recent),
`last_attempt_failed: true` and `last_error_class: Unreachable`. That combination, on a weekday, is normal.
`tasks[]` carries only the newest row per task, so the many failed rows do not bury or crowd anything in this
response. `copy.state` going `stale` (36 h) is the real signal here, and the page behind it is the monitor.

**Reading the copy's `last_error_class`.** `run-copy.sh` reads ssh's own error text, so the failures that all
used to be `Unreachable` are told apart:

| `last_error_class` | What ssh said | What it means |
|---|---|---|
| `Unreachable` | name does not resolve, timed out, no route, connection refused or closed, **or any text not recognised** | The destination is away or asleep. Expected on a weekday. Also the fallback: OpenSSH's wording is not a stable interface, so an unrecognised message never becomes a confident class. |
| `AuthRefused` | `Permission denied (...)` | The server answered and refused the key. Never a normal Tuesday. |
| `HostKeyChanged` | `REMOTE HOST IDENTIFICATION HAS CHANGED`, `Host key for <host> has changed`, or `No <type> host key is known for ...` when `known_hosts` DOES hold a key for the host (the server offered a different key type) | A pinned host key did not match. Treat it as a security signal until you know why: the desktop was reinstalled, or something else answered. |
| `ConfigInvalid` (from ssh) | `No <type> host key is known for ...` and nothing is pinned for the host | Setup state: the key was never pinned (`docs/BACKUP.md`), so nothing can be impersonating it. Does not page. |

**`AuthRefused` is visible here and in the container's logs; nothing pages on it sooner than the monitor does**
(48 hours). A refused key is misconfiguration and fires legitimately while the desktop is being set up.

**`HostKeyChanged` pages at once, through the monitor and not through `/health/ready`.** The copy step POSTs to
`BACKUP_PING_URL` + `/fail` (the monitor's failure endpoint) when the scheduler sees one, so the alert does not
wait out the 48 hours. **At most once per occurrence:** the scheduler remembers it has told the monitor and does
not depend on the provider to deduplicate; the next successful copy ends the occurrence, and a container restart
forgets it. A signal the monitor did not accept is retried on the next attempt. Readiness is about serving
requests, and degrading it pages (ADR-0008 section 4), so `/health/ready` is untouched. The log lines are
`copy_fail_signalled` and `copy_fail_signal_failed`. An unpinned host is not this class (table above), so setting
up the desktop does not page.

**That makes `BACKUP_SSH_HOST` a name, never a bare IP, and the code enforces it.** On another network the same
private IP can belong to someone else's machine, which would read as a changed host key and page for going to
work. `run-copy.sh` and `verify-destination.sh` refuse a bare IP (`ConfigInvalid`, exit 78). With a name that
resolves only at home, being away is `Unreachable` and the signal stays trustworthy. A hosts-file or `extra_hosts`
entry that maps the name to the IP brings the problem back.

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

- Killing a **real** provider: slice 4 proves the states against the fake provider only. FRED's client
  (`app/ingest/fred.py`, slice 5 step 2) maps its failures into the five kinds, proven against a mock
  transport (`tests/test_fred_client.py`), **not against FRED**: no real call has been made and none of
  its error behaviour is measured. See "FRED's failures" below. yfinance (slice 6) is still to do. The
  worker half is done (slice 1, above).
- Whether the backup has ever been restored on the real desktop: only the closing drill in `docs/BACKUP.md`
  tells you; `backups` says whether runs succeed, not whether the files can be read.

## Killing a provider (M2 slice 4; the provider half of IDEA-032)

An external API cannot be killed, so the gate's clause needs a definition. **A provider is killed when every
call to it, after retries, ends in failure.** That is what FlowDesk can observe, and it covers four ways a
source goes away, all mapped by the client into one of five kinds (`app/ingest/errors.py`):

| Way it goes away | Kind | Retried within the run? |
|---|---|---|
| Unreachable: refused, timeout, DNS | `transient` | yes |
| Answers "slow down" persistently (429) | `rate_limited` | yes |
| Refuses the request: bad key, bad series, FRED's "No vintage dates exist" 400 | `permanent` | no |
| Silent empty result (yfinance, ADR-0002) | `empty_result` | no |
| Payload is not the known shape | `schema_mismatch` | no |

### FRED's failures (M2 slice 5, step 2): documented, not measured

What the client does with each response, from FRED's errors page (the rate-limit sentence read verbatim by
Kyle, 2026-11-12, see the BUILD-LOG). The page lists the complete set of statuses FRED may return: 400, 404,
423, 429, 500. Error bodies are XML or JSON with a message attribute carrying the description. Only the
three 400s in the table were observed; the rest is documentation:

| FRED answers | Kind | Retried within the run? | Note |
|---|---|---|---|
| 429 | `rate_limited` | yes | Documented past 120 requests a minute, with "a temporary block" for ignoring it. The status and every header except cookies are logged on the first natural one: whether FRED sends `Retry-After` is unknown. |
| 400, "No vintage dates exist" | **depends on the caller** | no | **Backfill: `permanent`**, fails the run (the window was built from the series' own vintage dates, so none in it is a bug or a moved record). **Poll: not a failure at all**, an empty result and a normal quiet day (nothing was published in the window), so it must never show as a stuck or failed feed. The client raises `NoVintageDates` (a `Permanent`) unless the caller passes `no_vintages_is_empty=True`. ADR-0003 addendum, 2026-11-13. The default window can still not be sent with a vintage output type. |
| 504 on a large `output_type=3` window | `transient` today | yes (**useless**) | **Observed 2026-11-13.** FRED's gateway answers 504 at 60 s for a window of about 600 or more vintage dates (about 0.16 s per vintage date). Retrying the same window can never succeed; the windowed fetch must split it (ADR-0003 addendum). Not built yet. |
| 400, "exceeds the maximum number of vintage dates ... (2000)" | `permanent` | no | **Observed.** An explicit refusal, never a silent truncation; the backfill splits the real-time window (BUILD-LOG, step 2c). |
| 400, "series does not exist" | `permanent` | no | ADR-0003 decision 8. **Observed**: HTTP 400, not 404. |
| 400, anything naming `api_key` | `permanent` | no | Missing, malformed or unregistered key. **Observed**: all three arrive as HTTP 400 with a JSON body (fixtures in `tests/fixtures/fred/`, `tests/test_fred_real_bodies.py`). |
| 401, 403 | `permanent` | no | **Not observed**: FRED sends key failures as 400. Kept as `permanent`, never `transient` (Kyle, 2026-11-11): retrying a refused client only hammers what refused it. |
| 423, 500, 502, 503, 504 | `transient` | yes | 423 and 500 are on FRED's list but unexplained, so treating them as transient is a **judgement call, not a documented meaning**; logged. 502-504 are not on the list at all. |
| 404 | `permanent` | no | On FRED's list, unexplained; falls under "other 4xx". |
| redirect, other 4xx | `permanent` | no | Redirects are never followed: the key would go to whatever host answered. |
| connection refused, reset, timeout | `transient` | yes | A reset cannot be told from a network blip, so it is retried: 4 attempts per run, then Beat's next firing. A feed that never succeeds goes `stale` (`first_seen_at`). |
| not JSON, wrong shape, a value that is not a decimal or `.` | `schema_mismatch` | no | Rejected, not guessed at. `NaN` and `Infinity` are rejected. |

An exception raised by this client carries fixed text and never anything FRED sent back, because the key
rides in the URL and FRED may echo a request. The HTTP library's own request log is held at WARNING for the
same reason (`app/logging.py`).

**FRED's budget is 60 requests a minute** (`RateBudget(1.0, 1)`, half the documented 120, which is
documented and not measured: no 429 has been seen). A provider with no budget is refused
(`budget_unavailable`), which is what kept FRED uncallable until the sentence was read.

(A sixth, `budget_unavailable`, means FlowDesk refused to call because the rate limiter could not grant a slot;
that one is a fault in our own Redis, not a provider.)

**One run is one `ingestion_runs` row.** Within a run, tenacity retries only the retryable kinds: 1 try + 3 retries
(§11's `max_retries=3`), exponential with jitter, and every attempt takes a rate-budget slot. Celery retries are
off for ingest tasks. Beat is the outer retry: the next firing tries again. A Celery retry would also write a second
`ingestion_runs` row for one logical run, which would break the record that idempotency is measured against
(the §6.11 / §11 overlap is IDEA-042).

### The documented states, in order

| When | `ingestion_runs` | `providers_health` | The feed in `/health/providers` | `/health/ready` |
|---|---|---|---|---|
| Healthy | `succeeded` | `last_success_at` advances | `fresh` | `ok` |
| Just killed (inside the threshold) | `failed`, `error` = `<kind>:<Class>: <text>` | `last_error_at`, `last_error` = `<kind>:<Class>`; `last_success_at` unchanged | `fresh`, `last_attempt_failed: true` | `ok` |
| Past the feed's own threshold | `failed` | as above | **`stale`**; top-level `status` `stale`, HTTP 200 | **`ok`** |
| Recovered | `succeeded` | `last_success_at` advances | `fresh`, `last_attempt_failed: false` | `ok` |

`/health/ready` stays `ok` throughout. That is the readiness rule above, not an oversight: a dead provider is a
source going away, and the machinery (worker, Redis, Postgres) is still working.

**`providers_health.last_error` is the kind and the exception class, never the error text.** That column is served
by `/health/providers`; the public profile removes the key but the private profile does not, and a client
exception routinely carries the request URL (FRED authenticates with `api_key=` in the query string). So the
text is not stored there at all. The scrubbed text goes into `ingestion_runs.error`, which the public profile
also removes. `backend/tests/test_ingest_runner.py` reads the stored rows back with SQL and asserts that no
credential is in them.

### The test

- `backend/tests/test_ingest_runner.py`: each failure kind through `run_feed` against a real database; attempt
  counts; one row per run; the stored credential check; `rows_written` 0 on an identical re-ingest.
- `backend/tests/test_provider_health_states.py`: the state walk above, and `first_seen_at` escalation.
- `infra/smoke-m2-slice4.sh`: the fake provider on the real stack (Beat, Redis, worker, Postgres, API). It sets
  `FAKE_PROVIDER_MODE=down` and recreates the worker, then asserts the table above, including that `ready` stays
  `ok`; sets the mode back and asserts recovery; and registers a feed that never runs and asserts it escalates.

The fake provider is off by default (`FAKE_PROVIDER_ENABLED`) and registers nothing in a normal stack.

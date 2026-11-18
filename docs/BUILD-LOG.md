# FlowDesk — Build Log

A narrative, session-by-session record of what got built and decided. Not a changelog (git
history covers that) and not a spec (docs/architecture.md covers that) — this is the "what
happened and why" thread between them, for a future session to read cold.

**Format:** reverse-chronological, newest entry at top. Append new entries; do not rewrite
old ones except to fix a factual error, and note the correction inline rather than silently
editing history.

---

## 2026-11-08 — M1 gate review: passed

Kyle recorded the M1 gate as **passed**. The review was run as if the ten slices had proven nothing: a fresh
`git clone` into a scratch directory, each §16 clause checked directly, then the slice smokes as a second opinion.

**What was checked, and what held**
- *Fresh clone, one command.* A bare `docker compose up -d` fails ("set in .env"); after `cp .env.example .env` it
  came up in 9 s (image layers were cached). §16 line 1080 itself says "fresh clone + `.env` + `docker compose up`",
  so the `.env` step is in the spec; there was no README saying so (fixed below).
- *Migrations.* Alembic at 0002, the four ops tables and TimescaleDB 2.31.0 present, `migrate` exited 0.
- *Backup restores clean.* Own round-trip, not the script's word: 25 ops rows plus a 5-chunk hypertable with a
  composite primary key. After `backup.sh` then `restore.sh` into a new database, row counts and content hashes were
  identical, the hypertable primary key survived, duplicates were rejected in an old chunk and a new one, the identity
  sequence continued, and restore refused a non-empty target.
- *Redis down.* HTTP 200, `degraded`, `checks.redis` = `{"status":"down","error":"ConnectionError"}`, `/health/live`
  unaffected, recovery to `ok`. Exactly what `docs/HEALTH.md` documents. Postgres down gave 503 `not_ready`; no 500s.
- *Never externally exposed.* Postgres and the API publish on 127.0.0.1, Redis publishes nothing. Probed every host
  IPv4 address including the LAN one: closed.
- *Public profile read-only.* POST/PUT/PATCH/DELETE 405, docs paths 404. The route invariant was checked for the
  `_IncludedRouter` vacuous-pass trap: it sees all four real routes and rejects a nested POST, a Mount and a WebSocket.

**What did not hold literally.** "Killing Redis/**worker/provider**": no worker or provider exists until M2. Kyle moved
those two to M2's gate. IDEA-032 records it as a spec defect (the ninth of the drift class); it is a relocation, not a
waiver.

**Caveats on the evidence**
- The dev stack held 5432 and 8000 and could not be stopped, so the clone ran on 55432 and 18000. A default-port start
  was not tested.
- Five smokes (1, 4, 5, 6, 9) failed on first run, and every cause was the reviewer's: a public-profile stack left
  running held the API port, and seeded test rows broke slice 6's fresh-install precondition. All passed after cleanup.
- Slice 2 was not run (it creates a global buildx builder). Slice 8 was not run during the review because it pinned the
  compose project name to `flowdesk` and would have acted on the dev stack.

**Fixed afterwards (one commit)**
- `infra/smoke-slice8.sh` no longer hard-codes `COMPOSE_PROJECT_NAME=flowdesk`. It pins the name compose derives for the
  invoking checkout (`docker compose config`), still honours an explicit override, and prints the project it is scoped to.
  Verified by running it in the scratch clone: all its checks went green and red as designed, the project was
  `gate-review`, and the dev stack's containers were untouched.
- `README.md` added: what FlowDesk is, the `cp .env.example .env` step, `docker compose up -d`, where the runbooks are.

**Observed, not fixed: the readiness check rebuilds alembic's `ScriptDirectory` on every call.**
`SqlRedisProbes.migration_revisions` (`backend/app/services/health_probes.py`) builds a fresh `Config` and
`ScriptDirectory` each time and re-executes every file in `alembic/versions/`. Measured in a throwaway container:
about 1.3 ms and 2 file executions per call at 2 migrations, no object reuse between calls, and it is synchronous
code inside an `async def`, so it runs on the event loop and the 2 s `asyncio.wait_for` cannot interrupt it. Cost
grows with the number of migrations. Not as hot as first suspected: the 10 s Docker healthcheck hits `/health/live`,
which never reaches it; the only built-in caller of `/health/ready` is the heartbeat, every 300 s, and it does not
call at all while `HEARTBEAT_URL` is blank. The "setup plugin alembic.autogenerate…" log lines seen during the
review were one-off (a single request ID, from a previous run), the first import of alembic, not per request.
The head cannot change while the process lives, so it could be computed once. Decision deferred to Kyle.

**Also noticed, unresolved.** CLAUDE.md says this machine has no arm64 emulation by Kyle's choice. On 2026-11-08
`docker run --platform linux/arm64 python:3.12-slim uname -m` printed `aarch64` and slice 8's arm64 check went green,
so emulation is present now. The CLAUDE.md note was not changed.

## 2026-11-05 — M1 slice 10 closed: the heartbeat drill passed

Result of the drill in `docs/HEARTBEAT.md`, run by Kyle against the real provider (Healthchecks, check
`flowdesk-desktop`, simple schedule, period 5 / grace 40, alert by email). Times are as Kyle reported them, EST.
The drill's calendar date wasn't given and isn't recorded here.

- **Arming:** pings showed on the dashboard within 5 minutes. The real provider accepted the URL and the 5/40
  timing, which closes the "confirm the dashboard accepts them" caveat in the runbook.
- **Outage:** `docker compose down` at 11:57. The DOWN alert arrived at 12:41:10, 44 minutes after the stack went
  down and inside the 45-minute design (period 5 + grace 40).
- **Recovery:** the UP notice arrived at 17:40:05. After that the stack pinged on schedule for about two hours with
  no gaps.
- **Disarmed:** `HEARTBEAT_URL` is blank again on this machine. The sender's logs show `armed: false` and
  `heartbeat_disabled`, as the runbook says to expect.
- **Now proven:** the real provider accepts the sender's pings, the 45-minute timing, an alert reaching someone off
  the box, and the recovery notice. This closes the first two items under "What has been proven" in ADR-0008.
- **Still not proven:** the sender on arm64 as a compose service (third item in that ADR section); the drill has
  been run on one outage shape only (full stack down), not on `degraded`; the smoke hasn't run on Linux.
- **Check paused:** per runbook step 6, Kyle paused `flowdesk-desktop` in the dashboard (reported after the first
  version of this entry). It is paused, not deleted, so it won't alert while `HEARTBEAT_URL` is blank. Pings
  resume it, so re-arming this machine later needs no new check.
- **Slice 10 is closed.** That makes M1 slices 1 to 10 all built. The M1 gate review is next, and its open question
  stands: the gate says "killing Redis/worker/provider", and no worker or provider exists until M2. That's Kyle's
  call; it is not decided here.

---

## 2026-11-04 — M1 slice 10: external dead-man heartbeat

- **What shipped:** `backend/app/heartbeat.py` (a stdlib-only sender), a `heartbeat` compose service,
  `infra/smoke-slice10.sh`, `backend/tests/test_heartbeat.py`, ADR-0008, `docs/HEARTBEAT.md`, IDEA-031. No new
  dependency, migration or CI job. pytest covers the sender's logic in CI; the smoke isn't in CI, to save minutes.
- **Status: built and smoke-tested, not yet proven end to end.** The drill in `docs/HEARTBEAT.md` needs a provider
  account, which only Kyle can create. Until it passes, nothing has shown that an alert reaches anyone: the real
  provider has never been called and the 45-minute timing has never run.
  *(Superseded, recorded 2026-11-05: the drill passed. See the entry above.)*
- **Push, not pull.** A pull monitor would have to reach the API from the internet, and the API is loopback-only.
- **Decided (Kyle):**
  - A ping means `/health/ready` is exactly `ok`, so `degraded` stops pings too. From M2 a dead Redis also stops
    Beat and the internal heartbeat, so nothing else would report it. The planning question offered "any 200" and
    `/health/live` as the alternatives.
  - 45 minutes of silence before an alert (period 5, grace 40), deliberately rather than a default. The downtime
    estimates behind it aren't measured.
  - The consequence is recorded in ADR-0008, not left to be found later: any new `degraded` condition also pages.
    The warning is also in `services/health.py` and `docs/HEALTH.md`.
  - `HEARTBEAT_URL` stays blank on development machines and is set only on a deployed instance. Alerts from a
    sleeping laptop would teach you to ignore them. The drill arms it temporarily.
  - The flapping blind spot goes in the ADR: a stack broken most of the time that lands one `ok` ping per 40 minutes
    reads as healthy.
  - The provider's fallback goes in the ADR as well, including the bad case: a provider that vanishes fails silently.
- **Found while building:**
  - **Healthchecks answers a mistyped UUID with 200 `OK (not found)`**, per its docs, so checking the status code
    alone would report a typo as success. The sender reads the body.
  - The image's `HEALTHCHECK` is inherited by anything built from it. It probes port 8000, so the sender would be
    unhealthy forever and hang `wait_ready`. Disabled for this service.
  - Python as PID 1 ignores SIGTERM unless it installs a handler, which would make every `docker compose stop` wait
    the full 10 seconds. The sender installs one.
- **Verified on Windows / Git Bash, Compose v5.6.1:** `ci-checks.sh lint` and `test` (193 passed, 24 of them new);
  `smoke-slice10.sh`; smokes 1, 6 and 7 for regressions. Smokes 2, 3, 4, 5, 8 and 9 were not re-run. None of this has
  run on arm64. The smoke hasn't run on Linux.
- **Known limits, in ADR-0008:** outages under 40 minutes never page; flapping reads as healthy; a provider that dies
  fails silently; a deployed instance that loses its URL is unmonitored with only a log line; the dashboard's values
  can't be enforced from the repo.
- **Open for the M1 gate review, not decided here:** this is the last slice, but the gate says "killing
  Redis/worker/provider produces the documented health state", and no worker or provider exists until M2
  (`docs/HEALTH.md`). The gate can't pass literally. IDEA-029 (no automatic backups) doesn't block M1, but does block
  M2's first observation.

---

## 2026-11-04 — M1 slice 9: backup/restore

- **What shipped:** `infra/backup.sh`, `infra/restore.sh`, `infra/smoke-slice9.sh`, `docs/BACKUP.md`, a
  `backup-restore` CI job. No application code, migrations or dependencies changed.
- **Why the smoke uses a throwaway hypertable (Kyle's call at planning):** the four ops tables are plain, so a
  round trip of them proves nothing about what every later time series will be. The fixture also has a
  continuous aggregate and two policies, because §8 line 941 requires them and Timescale's backup pages don't
  mention them. It lives in a scratch database, inline in the smoke, with no Alembic revision.
- **Spike result, plain `pg_dump -Fc` + `pg_restore` on 2.31.0-pg16.** Source: 480 rows, 10 chunks.
  - Lost 48 rows, all of `_hyper_1_10_chunk`: `could not find hypertable with id 1`.
  - Lost the parent's primary key: `ONLY option not supported on hypertable operations`.
  - **So a duplicate key in a chunk created after the restore was accepted** (2 rows for one key). A duplicate
    in an old chunk was still rejected, because old chunks kept their own constraint.
  - Still showed 10 chunks, both jobs, `restoring=off`, and an identical aggregate, because the aggregate is
    materialized and its rows were restored separately. Exit code 1, `errors ignored on restore: 2`.
  - The plain restore did not fail outright: on a script that ignored pg_restore's exit code, or a check that
    looked at chunks and jobs, it would have passed. Only a row-level comparison caught the data loss.
- **Spike result, Timescale's documented procedure:** identical in every check, zero messages under
  `--exit-on-error`, so no error allowlist was needed. Works both into a database with the extension
  pre-installed and into a `template0` database without it. The aggregate and both policies survive; the
  retention job runs and deletes nothing.
- **The spike changed the plan.** The planned primary-key test passed on the plain restore, because it inserted
  into an old chunk. Added: index and constraint definitions in the structure comparison, a duplicate-key test
  in a chunk created after the restore, and a "primary key lost" red check.
- **Decided (Kyle):**
  - Nightly scheduling is not built. IDEA-029 holds scheduling, retention and off-box copies, and must be
    resolved before M2 writes its first persistent observation. M1 can therefore pass with no automatic backup.
  - Compressed chunks aren't in the fixture. The spec doesn't use them.
  - CI runs this smoke on every code push.
  - `docs/BACKUP.md` states the version trap explicitly: an old dump needs the old image tag.
- **Built to §15 item 7b, the strictest of three bars:** a fresh *container* and the app serves from it. CLAUDE.md
  says fresh DB, the §16 gate says restores clean. `/health/data` must return a feed name that exists only in
  the source, so the answer can't come from the real database.
- **Also verified by hand, not in the smoke:**
  - Version trap on a real older image (2.30.0-pg16), both directions: refused, naming the right image, nothing
    created. The upgrade path works: restore on the old image, swap the image on the same volume, `ALTER
    EXTENSION timescaledb UPDATE`. Rows, aggregate, chunks and job unchanged. One minor version only.
  - The `docs/BACKUP.md` disaster sequence, literally, on a separate compose project: plain `up -d` first is
    refused (`not empty`), and the documented order ends with `/health/ready` ok. That database had no
    hypertables, since the real schema has none yet.
- **Found, not fixed: slices 3 and 4's "empty" databases aren't extension-free.** The image pre-installs
  timescaledb in `template1`, so `CREATE DATABASE` inherits it, and migration 0001's `CREATE EXTENSION` has only
  ever been a no-op in those smokes. Confirmed on a fresh container. Their passing says less than their comments
  claim. `restore.sh` uses `template0` so that it doesn't depend on this. Tracked as IDEA-030, which also records
  a one-off manual run: on a `template0` database, 0001 does create the extension on this image.
- **Found and fixed in the follow-up: `smoke-slice8.sh` leaked 9 scratch databases per run.** `migrated_url` in
  `test_ops_tables.py` ran `command.upgrade` before its `yield`, so slice 8's deliberately raising migration
  skipped the `DROP`. It now uses `try/finally`, and `smoke-slice8.sh` fails if any scratch database survives a
  raising migration. The 18 left on this machine were dropped by Kyle's decision: 0 tables each, background
  workers only. CI was never affected: its database is discarded.
- **Not proven:** arm64 (nothing here has run on it), restore time at real volumes, and a dump taken under live
  writes. The hand-run checks ran on Windows / Git Bash only. The smoke itself also passed on Linux: the
  `backup-restore` CI job was green on its first run, at `de72da8` (corrected from "has not run yet").
- **Git Bash bit again:** it rewrote the `/health/ready` argument into a Windows path on its way to `docker.exe`,
  which read as the API failing against the restored database. `MSYS_NO_PATHCONV=1` on that call.

---

## 2026-11-04 — M1 slice 8: CI

- **Follow-up, same day (supersedes the matching points below):**
  - The `certifi` licence entry is removed by owner decision. `pathspec` is now the only MPL-2.0 entry.
  - `smoke-slice2.sh` now skips the arm64 half with a notice when the probe reports `exec format error`,
    pointing at CI's arm64 job. Any other probe failure still fails. Reinstalling emulation locally was
    declined. CI's `arm64` job is the only arm64 gate.
  - **The 180 ignored Grype matches, now reviewed: 0 Critical**, 60 High, 63 Medium, 11 Low, 46
    Negligible. All 180 are Debian 13.7 base-image packages, none from our Python dependencies. The 60
    High rows are 18 unique CVEs: 59 rows Debian marks `wont-fix` and 1 `not-fixed` (zlib,
    CVE-2026-85091, a heap buffer overflow). None is on the known-exploited list, and EPSS tops out at
    0.6%. Scanned amd64 only, with the 2026-11-04 Grype DB, on a floating base tag, so this is a
    snapshot. Nothing fixed: it is an input to the base-image choice before deploy.

- **What shipped:** `.github/workflows/ci.yml` with six jobs (lint, test, migration-smoke, arm64,
  security, license); `infra/ci-checks.sh`, the same code for CI and a local shell;
  `infra/lock-deps.sh` and three hash-pinned locks; `backend/pyproject.toml`;
  `backend/tools/license_audit.py` and `banned_phrases.py`; `infra/smoke-slice8.sh`. The Dockerfile
  now installs with `--require-hashes`. Actions are pinned to full SHAs, with `contents: read` and no
  secrets. Runs on push to main and manually; only `security` runs nightly. Docs-only pushes skip CI.
- **Repo visibility:** found public during planning, though it was meant to be private. Kyle made it
  private on 2026-11-04. It had been public since 2026-10-22, with 0 forks. `.env` was never committed;
  the rest of the history was not audited for secrets.
- **mypy:** first-ever `--strict` run reported 27 errors, against a stop threshold of 50 set by Kyle
  (25 in tests, 1 in app, 1 in alembic). All fixed with annotations. Pre-existing `# type: ignore` and
  `# noqa` comments were removed too, so none remain. No runtime behaviour changed. `0002_ops_tables.py`
  changed only in an annotation (`sa.Column[int]`).
- **Decided:**
  - Licences: IDEA-027. PSF-2.0 allowed, MPL-2.0 per package. Note the gate caught `pathspec` (MPL-2.0,
    via mypy) after the original decision named only `certifi`, and the `certifi` entry now matches
    nothing because the `httpx2` swap removed it from the audited set.
  - `httpx` -> `httpx2`: clears the Starlette deprecation warning deferred at slice 2.
  - QEMU on amd64 for arm64, because native arm64 runners aren't free on private repos.
  - Grype, not Trivy, for the image scan: Trivy's action tags were hijacked in March 2026
    (CVE-2026-33634).
  - pip-tools is pinned in `lock-deps.sh`, not in the dev requirements as first planned, so that its
    dependencies stay out of the licence audit. `actions/setup-python` and `setup-buildx-action` were
    dropped from the plan: the checks run in containers and don't need them.
  - The LICENSE file is on hold by owner decision, tied to whether the repo stays private and any later
    commercialisation. So this slice does not complete decision 24, which also requires FlowDesk's own
    repo to carry MIT or Apache-2.0.
- **Banned-phrase check, built earlier than the spec places it.** §16 gives a forbidden-vocabulary test
  to M5.5, and §7.5/§15 have one for premium templates. Kyle moved it here as a lint rule. CLAUDE.md
  claimed "a grep test enforces this" when none had ever existed; that line is corrected. The check
  scans 0 files today because no UI, template or prompt paths exist yet, so it guards nothing real until
  they do. Its tests plant files to prove it fires. M5.5's recommendation vocabulary is a different list
  and is still M5.5's job. Decision 14 and §17 word the "arbitrage" ban differently: IDEA-028, open.
- **Known future friction (not a defect):** the rule is literal with no suppression, so "not guaranteed"
  fails it. That's correct per §17 as written, but it collides with standard disclaimer language, and
  M5.5 is where disclaimers get written.
- **Verified:**
  - CI run 41910297474 on `dbe1dd6`: all six jobs passed, confirmed from the logs. 169 tests passed with
    none skipped, 36 packages licence-audited, slice 3 smoke passed, arm64 answered `/health/live` 200
    under emulation.
  - `smoke-slice8.sh` locally: four gates green; ruff, type error, banned phrase, stale lock, failing
    test, skip guard, raising migration, known advisory, unknown licence and empty licence report each
    go red for the expected reason; phrases in docs don't fire.
  - Smoke 1 and 3 to 7 pass locally against the locked image after the type fixes.
- **Not verified / not covered:**
  - **arm64 is proven only under QEMU.** Nothing has run on arm64 hardware, and migrations and
    TimescaleDB have never run on arm64 at all. This stays open until a deploy to the §13 ARM VM.
  - **The Grype gate has only ever passed.** It has not been shown to go red, because the local smoke
    doesn't run it. The first run reported 193 matches: 180 with no fix (ignored by `only-fixed`, and
    their severities were not reviewed), 13 fixable at Medium or below, in the base image's `pip` 25.0.1
    and Python 3.12.16.
  - The licence gate's red path was tested at tool level, not end to end through a real package.
  - **`smoke-slice2.sh` fails on this machine** at the arm64 half with `exec format error`, because
    emulation went missing from Docker Desktop again. The amd64 build and smoke 3 pass. CI covers the
    arm64 build.
  - `python:3.12-slim` in the Dockerfile is a floating tag, so the base image isn't pinned the way the
    Python dependencies are. §13 names lock files and Actions, not base images, so it's left as is.
- **Actions minutes:** the first run took about 3 minutes end to end. Rounding each job up, that's about
  11 billed minutes per push, well under the plan's 25-minute guess, plus about 3 per night for `security`.

## 2026-11-03 — M1 slice 7: private/public compose profiles

- **What shipped:** `api` (`profiles: [private]`) and `api-public` (`profiles: [public]`) from one
  shared compose definition, on the same loopback port; `COMPOSE_PROFILES=private` in `.env.example`;
  `DEPLOYMENT_MODE` in `app/settings.py`; `app/route_policy.py`; `create_app(mode)`;
  `redact_providers` / `redact_data`; `tests/test_public_profile.py`; `infra/smoke-slice7.sh`.
- **Gate (IDEA-019):** every public-profile route uses only GET, HEAD or OPTIONS, and anything the
  check cannot classify fails it. There is no admin-guarded branch, because no admin route exists; the
  first one defines what "guarded" means. Not internet-ready: `api-public` is on loopback, and strict
  CORS, request-size limits and rate limits have no owning slice (IDEA-019 addendum).
- **Decided:**
  - The invariant is also enforced at startup: a public app with a violating route refuses to boot,
    so it holds before slice 8 CI exists.
  - Redaction removes the key rather than nulling it, since null reads as "no error".
    `last_error_at`, `last_attempt_failed` and `status` stay.
  - `/redoc` is off too. The rider names `/docs` and `/openapi.json`; `/redoc` serves the same schema.
  - An unrecognised `DEPLOYMENT_MODE` refuses to start, with no case-folding. Unset means private (§14).
  - The shared host port is the "choose one" enforcement: enabling both profiles fails on the bind.
- **Measured on Compose v5.6.1:** `--profile public` replaces `COMPOSE_PROFILES=private` from `.env`
  rather than adding to it; `COMPOSE_PROFILES=private,public` enables both. Switching needs the other
  API removed first (`docker compose rm -sf api`), or the port is still held.
- **Found: `iter_route_contexts` silently drops unknown route types nested in an included router**
  (FastAPI 0.162.0, `_IncludedRouter._build_effective_context` returns None), which looked like a hole
  in the fail-closed check. It is not one today: dispatch drops them too, so a live request gets 404.
  Top-level unknown types, Mounts, Hosts and WebSockets are all walked and fail. A test pins the
  walk-matches-dispatch behaviour against a future upgrade. `app.routes` being opaque is pinned too.
- **Corrected:** the plan said the local `.env` lacked `COMPOSE_PROFILES`. It already had it.
  `infra/lib.sh` still defaults it, for any older `.env`.
- **Verified:** full suite in the built image (slice 4/5 pattern, `DATABASE_URL` set against compose
  Postgres): 114 passed, 0 skipped. The first run had 4 failures from a real bug in a violation-message
  f-string, fixed in the code. Smoke 1 through 7 pass (2 and 5 after the fixes below). Smoke 7 can fail: a POST route added to the
  health router stops the public app from starting (`/health/mutate: not read-only (POST)`), and
  disabling redaction is reported as a leak. Fresh-clone check on a wiped volume: `up -d` starts `api`
  in private mode, `/health/ready` ok, schema at 0002. All of this ran from a scratch copy of the
  working tree on port 55432, because of the Postgres service below.
- **Environment problems found during verification:**
  - **arm64 emulation was missing, now installed by Kyle.** The arm64 build first failed with `exec
    format error` at `pip install`, and untouched `HEAD` failed identically, so it was not this slice.
    With emulation installed, `smoke-slice2.sh` passes from the repo with this slice's code: all 8
    arm64 build steps complete, and a throwaway arm64 `python:3.12-slim` container runs. The arm64
    image of FlowDesk itself has still only been built, not started.
  - **Smoke 5 needed a host `python3`**, which this machine's Git Bash lacks, and reported that as
    "non-JSON line in api logs". Fixed in its own commit: validation now runs inside the api
    container, and the failure message is the validator's own. Passes with no host `python3`; a
    non-JSON line and a validator that cannot run each fail with an accurate message.
  - **The native `postgresql-x64-17` service holds 5432** (automatic start, as in slice 1), so
    `docker compose up -d` in the repo fails here until it is stopped or `POSTGRES_PORT` is changed.
  - A GET handler that writes is still unenforced (IDEA-019), and the invariant reads routes, so
    it does not see anything answered from ASGI middleware.

## 2026-10-28 — M1 slice 6: /health/ready · /health/data · /health/providers

- **What shipped:** `backend/app/api/health.py` (three routes appended; `live` untouched),
  `app/services/health.py` (pure logic: checks, feed classification, response assembly),
  `app/services/health_probes.py` (the real Postgres/Redis/Alembic probes), `app/deps.py`,
  `app/settings.py`; `tests/test_health_ready.py`, `tests/test_health_db.py`;
  `infra/smoke-slice6.sh`; `docs/HEALTH.md` (the documented states); IDEA-025. `redis==8.1.0`
  added. Compose `api` now gets `DATABASE_URL` and `REDIS_URL`, still with no `depends_on`.
- **Gate:** Redis killed -> `/health/ready` 200 `degraded`; Postgres killed -> 503 `not_ready`;
  `/health/live` 200 throughout; `/health/data` and `/health/providers` unaffected by Redis and
  `503 unavailable` (not 500) without Postgres; both recover on restart. The smoke script asserts
  all of it against the real stack, and treats any 500 or any answer slower than 10 s as failure.
- **Decided:**
  - Redis down is `degraded` (200), Postgres down or schema behind is `not_ready` (503).
    Redis is never the source of truth. Only the exception class is reported, never its message
    (messages carry DSNs).
  - **Three feed situations, three states.** No rows -> `no_feeds`. A known feed with
    `last_success_at IS NULL` -> `never_succeeded` (`stale: false`, top-level `pending`): not
    stale, just not yet run. A feed that succeeded but is past its threshold -> `stale`. Only
    `stale` means something broke. `providers_health` has no first-seen column, so
    `never_succeeded` has no age and cannot escalate by itself; that needs a §8 column (logged as IDEA-026, an implementation finding, separate from IDEA-023).
    `last_attempt_failed` shows whether a never-succeeded feed has been failing since the start.
    None of this is reachable until M2 (the table is empty); it is tested with synthetic rows.
  - A feed with no configured threshold reports `stale: null` / `no_threshold`. There is no
    global default, because §6.11 ties thresholds to each series' own cadence.
  - Bare JSON, not the §9 envelope: IDEA-025.
  - `/health/data` reports the latest ingestion/backfill/task rows and does not judge them: §8
    has no status vocabulary (IDEA-022), so a verdict would be invented.
- **Depends on IDEA-024 (unresolved):** `/health/data` reads backfill progress from
  `backfill_runs`, which is §16's reading (line 1109). §10 line 1003 says it comes from
  `providers_health`. If IDEA-024 is resolved the other way, the `backfills` key in
  `/health/data` and its query in `health_probes.py` move. Marked with an `ASSUMPTION` comment in
  `assemble_data` and in `docs/HEALTH.md`.
- **Open item with a home now:** the `DATABASE_URL` password-encoding problem from slice 3 (a
  password with URL-reserved characters breaks the URL compose assembles) is still unfixed and
  was deliberately left out of this slice. `app/settings.py` is where the fix belongs: build the
  URL there from `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` with percent-encoding, and let
  compose pass the parts instead of a pre-assembled string. Until then the caveat comment in
  `docker-compose.yml` stands for both `migrate` and `api`.
- **Found:** `TestClient` used without a `with` block gives every request a fresh event loop, so
  a pooled asyncpg connection from one request breaks on the next (`RuntimeError` from
  `/health/data` after `/health/providers`). A test artifact, not a product bug (uvicorn has one
  loop); `test_health_db.py` holds the client open. The imports are also lazy by design:
  `app.deps` loads sqlalchemy/redis inside `get_probes`, so the existing
  `test_live_does_not_pull_in_db_or_redis_clients` still passes unchanged.
- **Verified:** full suite with `DATABASE_URL` against the compose Postgres in a Python 3.12
  container: 56 passed, 0 skipped. `smoke-slice6.sh` passes; `smoke-slice1` through `5` still
  pass after the compose and image change (slice 2 includes the amd64+arm64 build with the new
  dependency).
- **Not verified / not covered:** killing the worker or a provider (also in the M1 exit gate) is
  not testable until M2 creates them. The arm64 image compiles but has not been run. Whether the
  public profile may expose `providers[].last_error` is a slice 7 question, tied to IDEA-019.
  *(Update 2026-11-02: IDEA-019 resolved; the public profile redacts `last_error`.)*

## 2026-10-28 — M1 slice 5: structlog JSON + correlation-ID middleware

- **What shipped:** `backend/app/logging.py` (`configure_logging`: structlog + stdlib bridge, one
  JSON object per line on stdout, `LOG_LEVEL` env, default INFO); `backend/app/middleware.py`
  (`CorrelationIdMiddleware`, pure ASGI); `backend/tests/test_logging.py`;
  `infra/smoke-slice5.sh`. `structlog==26.1.0` added. Uvicorn runs with `--no-access-log`;
  compose passes `LOG_LEVEL` through to `api`.
- **Gate:** one request, one JSON line carrying `request_id`; the response echoes it in
  `X-Request-ID`. The smoke script also asserts every line the api writes is valid JSON.
- **Decided:** an inbound `X-Request-ID` is honoured only if it matches
  `^[A-Za-z0-9._-]{1,64}$`, else a `uuid4().hex` is generated. The regex is not injection
  defence (JSONRenderer already escapes newlines); it bounds length and keeps ids greppable.
  Successful `/health/*` requests (status < 400) log at DEBUG so the 10s Docker healthcheck does
  not flood `docker compose logs api`; any status >= 400 logs at normal level, so a failing probe
  is never hidden. JSON only, no console renderer. `uvicorn.access` is forced off in code as well
  as by the flag, because the first smoke run showed it came back when `configure_logging`
  re-enabled propagation (two lines per request).
- **Known gap (deferred, `# DECISION:` in `middleware.py`):** an unhandled-exception 500 has no
  `X-Request-ID` response header. Starlette's outer `ServerErrorMiddleware` builds that response
  outside our middleware. The log line still carries the id. Fix needs an exception handler.
- **Extension point, not built:** the contextvar binding (`structlog.contextvars`) is where
  propagating the id into Celery tasks goes later, so a scheduled job's logs can carry the id of
  whatever triggered it.
- **Smoke-script note:** because health probes are DEBUG, `smoke-slice5.sh` recreates `api` with
  `LOG_LEVEL=DEBUG`. An EXIT trap restores the stack as found on every exit, failures included:
  the original `LOG_LEVEL` (or none), and api running or stopped as it was. Checked on the pass
  path, a forced failure, and an api that started out stopped.
- **Verified:** full suite with `DATABASE_URL` set against the compose Postgres, run in the built
  `flowdesk-api` image (interpreter and installed deps from the image; `backend/` mounted for
  `tests/`, which `.dockerignore` keeps out of the image; pytest/httpx installed into the
  throwaway container only): 28 passed, 0 skipped, including the migration round trip and
  `alembic check` drift. No scratch databases left behind.

## 2026-10-28 — M1 slice 4: ops tables

- **What shipped:** migration `0002` creating `providers_health`, `ingestion_runs`, `task_runs`
  and `backfill_runs` exactly as §8 lists them (no columns added); SQLAlchemy models in
  `backend/app/models/ops.py` registered on `Base.metadata`; `backend/tests/test_ops_tables.py`;
  `infra/smoke-slice4.sh`. The tests check column names, column order and primary keys against
  §8. The column *types* they assert are the slice-4 decisions below, not spec requirements:
  §8 does not state them (IDEA-022). Do not read those assertions as spec conformance.
- **Decided:** `BIGINT` identity ids, `TIMESTAMPTZ` throughout (watermarks included), `TEXT`
  status with no CHECK, no indexes beyond PKs. Each has a `# DECISION:` comment. §8 types almost
  nothing for these tables, so these are stopgaps, logged as IDEA-022.
- **Spec defects logged:** IDEA-022 (types/nullability/constraints, incl. `backfill_runs`
  uniqueness; notes that TIMESTAMPTZ watermarks assume temporal feeds), IDEA-023
  (`providers_health` has no quarantine columns though line 374 says counts feed it), IDEA-024
  (§10 line 1003 vs §16 line 1109 on first-run progress source; sixth instance of the
  revised-without-dependents drift class).
- **Changed earlier work:** `infra/smoke-slice3.sh` pinned `alembic_version = 0001`, which would
  fail as soon as `0002` exists. It now asserts only that a version is recorded; each slice's own
  script pins its revision.

## 2026-10-28 — M1 slice 3: Alembic baseline + migration smoke test

- **Baseline `0001`** creates the timescaledb extension and nothing else; downgrade is a
  deliberate no-op (dropping the extension would cascade into every later hypertable). The
  migration owns the extension so a restore into a fresh DB (slice 9) does not depend on the
  image's init scripts. `Base.metadata` carries a constraint naming convention from day one.
- **Async env.py** (asyncpg only, no second driver). Alembic's machinery is synchronous;
  `connection.run_sync(fn)` runs it inside the async connection's greenlet so each DB call
  awaits asyncpg underneath. `asyncio.run` is the single place the event loop is created.
- **Pins:** alembic 1.20.1, sqlalchemy 2.0.55 (spec says 2.0; 2.1.x exists and was not
  adopted), asyncpg 0.32.0.
- **`migrate` is a one-shot compose service**, not a dependency of `api`, so `/health/live`
  stays up when Postgres is down. Slice 6's `/health/ready` will report "migrations not
  applied" rather than the API crashing.
- **Found: `docker compose up -d --wait` fails when a one-shot exits, even with exit 0.**
  Verified on **Docker Compose 5.6.1**, with `restart: "no"` set: `--wait` printed
  `container flowdesk-migrate-1 exited (0)` and returned 1. Version-specific behaviour; recheck
  after Compose upgrades. Decision: `--wait` is test plumbing, not the exit gate, so
  `docker compose up -d` stays the one command and only the smoke scripts changed. They now
  share `infra/lib.sh` `wait_ready` (long-running services healthy; one-shots exited 0), and
  slices 1 and 2 were moved onto it. Checked that it can fail: a wrong password makes migrate
  exit 1 and `wait_ready` reports it.
  *(Correction 2026-11-03: this entry does not say which machine. Per Kyle, this observation was on
  the other machine. This machine, Wazza, reports Compose v5.6.1 / Docker Desktop 4.98.0 as of
  2026-11-03. It was upgraded from Docker Desktop 4.34.2 at about 14:12 local that day: the backend
  log has the last 4.34.2 client at 20:11:51Z and the first 4.98.0 client at 20:12:59Z, and the
  install log is stamped 14:12. v2.29.2-desktop.2 is Kyle's reading of this machine; it matches the
  time before the upgrade and is no longer installed here. `--wait` was tested here on v5.6.1 on
  2026-11-03 and reproduces: `container fdverify-migrate-1 exited (0)`, exit 1, every other service
  healthy. Whether it reproduces on v2.29.2-desktop.2 is untested.)*
- **Bug caught on first run:** the `alembic` entry point does not put the project root on
  `sys.path`, so `env.py` could not import `app` (`ModuleNotFoundError`). Fixed with
  `prepend_sys_path` in `alembic.ini`.
- **Slice 2 test fixed:** `test_live_does_not_pull_in_db_or_redis_clients` asserted against the
  shared `sys.modules`, which is order-dependent once any other test imports sqlalchemy. It now
  checks in a fresh subprocess. (The removed no-op `starlette` import line was dead code.)
- **Tests:** `backend/tests/test_migrations.py` (scratch DB; upgrade on empty, downgrade/upgrade
  round trip, single head, `alembic check` drift) is skipped without `DATABASE_URL`; slice 8 CI
  will supply it. Smoke scripts 1, 2 and 3 pass, slice 3 also from a cold `down -v`.

## 2026-10-28 — M1 slice 2: FastAPI app, Dockerfile, /health/live

**What shipped**

- `backend/`: `app/main.py` (app factory), `app/api/health.py` (`GET /health/live` returns
  `{"status":"ok"}`, touches nothing else), `tests/test_health_live.py`, `Dockerfile`
  (`python:3.12-slim`, non-root uid 10001, stdlib healthcheck), pinned `requirements.txt` and
  `requirements-dev.txt` (fastapi 0.162.0, uvicorn 0.54.0, pytest 9.2.0, httpx 0.28.1).
- `api` service in `docker-compose.yml`, published on `127.0.0.1:${API_PORT:-8000}`, no
  `depends_on`. `API_PORT` added to `.env.example`. `infra/smoke-slice2.sh`.
- IDEA-021 logged in `docs/SPEC-DEFECTS.md`; CLAUDE.md's `/healthz` line corrected.

**Verified:** pytest passes (3 tests) inside `python:3.12-slim`. `infra/smoke-slice2.sh`
passes: multi-platform build of linux/amd64 and linux/arm64 succeeds, the compose container
reports healthy, `/health/live` returns 200 with the expected body, and the port is bound to
127.0.0.1. Container runs as uid 10001. `smoke-slice1.sh` still passes.

**Decisions**

- Route is `/health/live` (§16 M1 scope), not `/healthz` (§9, §5). The precedence rule in
  CLAUDE.md is literally about §3 vs §16; applying it here is an inference. See IDEA-021.
- Multi-arch smoke: both platforms are built without `--load` (a manifest list cannot be
  exported by the docker exporter); the host-platform image is what compose runs. This machine
  uses the containerd image store, so no extra builder is needed; the script creates a
  `docker-container` builder `flowdesk-multiarch` only when that store is absent. That fallback
  branch was not exercised here.
- Plain pinned requirements files, not uv/poetry; hash-pinned locks wait for slice 8 CI.
- Tests run in a Python 3.12 container because the host has 3.13.

**Not verified / notes**

- The arm64 half of the build proves it compiles, not that it runs; no arm64 container was
  started. Only emulated-build success is evidence.
- pytest warns that Starlette's `TestClient` with `httpx` is deprecated in favour of `httpx2`.
  Left as is for now; revisit when pinning in slice 8.
- FastAPI's `/docs` and `/openapi.json` are still on; slice 7 should decide for the public profile.
  *(Update 2026-11-02: decided in IDEA-019; both are disabled under the public profile.)*

---

## 2026-10-28 — M1 slice 1: compose with Postgres/TimescaleDB and Redis

**What shipped**

- `docker-compose.yml` at the repo root: `postgres` and `redis`, nothing else. `.env.example`,
  `.gitattributes` (LF for `*.sh`), `infra/smoke-slice1.sh`, and a filled-in `Commands`
  block in CLAUDE.md.
- Pinned `timescale/timescaledb:2.31.0-pg16` and `redis:7.4-alpine`. Both verified with
  `docker buildx imagetools inspect` to publish linux/amd64 and linux/arm64. The first
  candidate, 2.17.2, was rejected as stale (2024 line); 2.31.0 was the newest 2.x pg16 tag
  on 2026-10-28. `timescale/timescaledb` is still the maintained repo.
- Postgres published on `127.0.0.1` only; Redis publishes no host port.

**Verified:** `infra/smoke-slice1.sh` passes on a cold start (`down -v`, then up) and on a
warm one: `select 1`, timescaledb extension present, Redis `PONG`, Redis unpublished,
Postgres bound to loopback.

**Decisions**

- Healthcheck uses `pg_isready -h 127.0.0.1` plus `start_period: 30s`. The `-h` forces TCP
  because the entrypoint's temporary init server is believed to listen on the unix socket
  only. Not tested in isolation: the cold-start pass shows the check does not break init,
  not that a socket-only check would have failed.
- Redis has no persistence: it is never the source of truth.

**What broke**

- A native Windows `postgresql-x64-17` service held port 5432, so compose could not bind.
  Stopped by the user; `POSTGRES_PORT` in `.env` is the escape hatch if it recurs.
- The smoke test's first Redis check was wrong: `docker compose port` prints `:0` with exit 0
  for an unpublished port, so any non-empty output tripped it. Fixed in the script.

**Not done:** no image of ours is built yet, so `docker buildx build` for amd64+arm64 starts
mattering in slice 2 (first Dockerfile). Nothing committed.

---

## 2026-08-16 — M0 closed: seven ADRs, oracle fixtures, IDEAS registry consolidated to twenty

**What shipped**

- All seven ADRs in `docs/adr/` (0001–0007), closing M0's outstanding-deliverables list:
  0001 FX base/quote convention, 0002 raw-vs-adjusted price policy, 0003 point-in-time
  observation model, 0004 snapshot/watermark policy, 0005 event natural keys & episodes,
  0006 delivery semantics, 0007 private-vs-public deployment mode.
- `tests/fixtures/premium_oracle.json` — the three oracle cases required by §15/ADR-0001:
  the §7.5 hand-computed worked example, a real spike-evidence case (verbatim from
  `spikes/spike1_evidence.json`), and a synthetic non-0.1-ratio case.
- `docs/IDEAS.md` consolidated to twenty entries (IDEA-001 through IDEA-020). IDEA-001
  through 012 are the pre-existing parked-feature backlog; IDEA-013 through 020 are new —
  spec defects surfaced while writing the ADRs, not features.

**Decision:** M0 is CLOSED as of this date.

**The eight spec defects found while writing ADR-0005/0006/0007 (IDEA-013–020)**

Each is a real contradiction or gap in the frozen spec, not a proposal — logged so a future
session doesn't have to rediscover them by re-reading §8 line by line.

1. **IDEA-013** — `events.natural_key` (§8, no date component) contradicts §7.16's own
   five-day-episode worked example (which needs five distinct rows to exist). Blocks M3;
   needs a decision on which side is wrong before the dedup framework is built.
2. **IDEA-014** — `events.episode_id`'s inline comment cites `§7.15` (Risk Lens); should
   cite `§7.16` (Episodes). Cosmetic, predates a v4.7 renumbering. Doesn't block M3 alone.
3. **IDEA-015** — CLAUDE.md's §17.1 hard rule ("never integrate a broker API... in any
   phase") cites decision 15 as its authority while decision 15 (as revised in v4.1) actually
   defines a gated Execution Plane that may execute. Pre-v4.1 text that never got updated.
   Resolved same day — see below.
4. **IDEA-016** — `alerts` (§8) has no FK back to the `events` row that fired it — no
   `event_id`, `calculation_run_id`, or `snapshot_id`. `alert_rules`/`alerts` predate the
   events-v2 lineage redesign. Likely blocks M4's alert-history join and M8's per-alert
   retry view.
5. **IDEA-017** — M3's exit gate promises "exactly once" dispatch across all channels
   including Discord, which is structurally impossible (no idempotency key on Discord's
   webhook API — a timeout is indistinguishable from a lost send). Overclaim, not just an
   unspecified mechanism; needs the gate split into in-app (exactly-once, achievable) vs.
   webhook (at-least-once, the real ceiling).
6. **IDEA-018** — IDEA-003's own unfreeze triggers cite "confidence tier: low, per
   ADR-0007," but confidence tiers are §7.16 content and ADR-0007 is private-vs-public
   deployment mode — dangling reference, predates the current ADR numbering.
7. **IDEA-019** — M1's exit gate ("public profile exposes read-only routes only") conflicts
   with §14's own two-tier design, which allows an authenticated admin surface that mutates.
   Needs a `# DECISION:` before M1's acceptance tests are written, since the two readings
   produce different implementations. *(Update 2026-11-02: resolved as B-minimal; see the
   IDEA-019 entry in `docs/SPEC-DEFECTS.md`.)*
8. **IDEA-020** — the LLM spend cap's "hard-stop" is enforced by a Redis daily counter, but
   Redis is explicitly never the source of truth (decision 26) and a restart/eviction resets
   it mid-day — a real, if small, gap between what §14 claims and what's actually guaranteed.

**IDEA-015 was also resolved today**, not just logged: CLAUDE.md's broker rule was rewritten
to match decision 15 as revised (Execution-Plane code is out of scope until M0–M8 ship and
decision 15's gates are met, rather than banned outright), with the drift explained inline.
The other seven (013, 014, 016–020) remain open — each needs either a `# DECISION:` comment
or a spec correction release before the milestone it blocks starts.

**Pattern noticed:** IDEA-015's writeup ended up cataloguing five separate instances of the
same drift class across the ADRs — a revision or renumbering happened somewhere in the spec
and a dependent reference elsewhere was never updated to match (three stale `§` citations,
one stale hard-rule authority, one schema table that predates a redesign). That's what
motivated the full cross-reference audit now queued as a pre-M1 cleanup item (see CLAUDE.md
"Current state").

**Lesson learned — whole-file rewrites drop content near the end**

While drafting IDEAS.md and the ADRs, a whole-file rewrite of a long document silently
dropped content near the end of the file three separate times during this session — not a
tool failure, a self-inflicted editing habit (regenerating the entire file from a truncated
mental model of it instead of extending what was there). Each time it required restoring the
missing entries from git history.

**Mitigation going forward:** default to append-only edits on long documents (add new
sections at the end or via targeted inserts, don't regenerate the whole file), and run
`git diff` before committing any change to a long document to confirm nothing upstream of
the edit point silently disappeared.

## 2026-11-08 — post-gate follow-ups

- **Drift class settled.** IDEA-015 is in the class (it is the defining case). The canonical list now lives at the
  top of `docs/SPEC-DEFECTS.md`; IDEA-032 stays the ninth. The "seventh" in IDEA-019 and "sixth" in IDEA-024 are one
  low and were not edited. The IDEA-006 line is counted with IDEA-014, not separately (stated there; overrule moves
  the count by one).
- **arm64.** CLAUDE.md said this machine had no arm64 emulation; it does. Migrations 0001-0002 and the smoke-9
  hypertable, continuous aggregate and policy DDL ran clean on `timescale/timescaledb:2.31.0-pg16` and the API
  image under `--platform linux/arm64` (QEMU). Nothing needed fixing. Not covered: native arm64, backup/restore on
  arm64, the full compose stack on arm64.
- **`migration_revisions()`** reads the alembic head once in `SqlRedisProbes.__init__`, not per call: a blocking
  directory walk inside an `async def` is not cancelled by `wait_for`. Two tests added. CI lint and tests green
  (195 passed); smoke 6 re-run and passed against the rebuilt image.

- **Image scan red (CI `security` job, push and 2026-11-08 schedule run).** Two HIGH findings in `zlib1g`
  (CVE-2026-76844, CVE-2026-85091), fixed in `1:1.3.dfsg+really1.3.1-1+deb13u1`. The current `python:3.12-slim`
  (digest `sha256:5e2bdb1b...`) still carries `+b1`, so no re-pin was possible. `backend/Dockerfile` now upgrades
  that one package and asserts the fixed version landed. Verified on amd64 and arm64 (QEMU); Grype with CI's flags:
  old image exit 2, new image exit 0. No threshold change, no ignore entry. Procedure: `docs/RUNBOOK-IMAGE-CVE.md`.
  Remove the Dockerfile block once the base image ships the fix (runbook step 5).

## 2026-11-08 — M2 planning decisions, and slice 0

**Decisions (Kyle).**
- D1: the base image is pinned by digest, and the CI tooling container in the same change. Postgres and Redis stay
  on exact version tags: `2.31.0-pg16` is already a version pin, and the drift was in `python:3.12-slim`.
- D2: encrypted dump to Kyle's desktop as the off-box copy. Dev runs count. The six points: local dump first and
  the off-box copy as a separate step (rsync over SSH, catch-up on wake); the key in the password manager,
  never only on the laptop; a stale backup does not degrade `/health/ready` (it shows on `/health/data` and the
  missed-run signal notifies); 14 dailies plus 8 weeklies; `never_completed` with a startup grace for the worker
  check; Beat death and worker death documented as one state.
- D3: worker health is keyed on recent `task_runs` completion, not process liveness, so a wedged worker degrades
  too. Degrading `/health/ready` pages (ADR-0008 section 4).
- D4: M2 builds yfinance, FRED and a fake provider only. Frankfurter goes to M7, EDGAR and both Stock Watchers
  to M10 (IDEA-037, a relocation and not a waiver). Slice 9's number is left vacant.
- D5: disclosures are index-level; the §6.5 vs §8 conflict is IDEA-036.
- IDEA-035's version-home decision waits until slice 6 planning, with IDEA-034.
- IDEA-029's wording is corrected: FRED vintages are re-fetchable (`output_type=4`); what is irrecoverable is
  as-traded live-captured bars and `c_total_return` as it stood on a day. That puts the IDEA-029 line at slice 6,
  not slice 5. The ordering is unchanged, because slice 2 precedes both.

**Slices.** 0 pin the bases (done) · 1 worker and Beat, health keyed on task_runs · 2 scheduled encrypted
backups (IDEA-029) · 3 reference tables, universes, calendars (IDEA-033) · 4 provider framework and fake provider
· 5 FRED and macro vintages (ADR-0003) · 6 prices (ADR-0002, IDEA-034, IDEA-035 decided here) · 7 validation and
quarantine (IDEA-023) · 8 corporate actions workflow · 9 vacant (IDEA-037) · 10 backfill and progress API · 11
frontend scaffold and first-run page.

**Slice 0 result.** `backend/Dockerfile`'s `FROM` is now `python:3.12-slim@sha256:5e2bdb1b...` (the multi-arch
index, resolved 2026-11-08). `infra/base-image.sh` reads it for `ci-checks.sh`, `lock-deps.sh` and smokes 2, 8
and 10, so there is one definition. Lint fails on a second one, and smoke 8 proves the guard and the unpinned-FROM
check both go red. Verified: `lock-deps.sh` regenerates the locks byte-identical under the pinned image;
smokes 2, 8 and 10 pass; `ci-checks.sh arm64` passes under emulation; Grype on the pinned image (zlib block
retained) exits 0. `docs/RUNBOOK-IMAGE-CVE.md` step 3a is now "bump the pin". The cost is stated in the runbook:
the base no longer takes fixes by itself, and the scan is the tripwire.

- **IDEA-034 split recorded; IDEA-038 logged.** The shared provenance vocabulary (`effective_at`, `available_at`,
  `ingested_at`, `revision`, `source`) is settled at slice 5 planning and binds every observation table; the
  `bars`-specific decisions and IDEA-035 wait for slice 6 planning. IDEA-038 is the `effective_ts` (§8) vs
  `effective_at` (ADRs, §16) contradiction, classed as a plain contradiction and not a drift row: no revision
  event is behind it.

## 2026-11-08 — M2 slice 1: Celery worker and Beat, health keyed on completions

**Built.** `worker` and `beat` compose services (same image, no profile, no ports, image healthcheck disabled);
`app/celery_app.py` (factory, no import side effects), `app/worker.py` (entry), `app/tasks/{names,runs,canary}.py`.
The canary is a Beat task every `CANARY_INTERVAL_SECONDS` (60) that records itself in `task_runs`
(`running`/`succeeded`/`failed`, a stopgap under IDEA-022). `/health/ready` gains `checks.worker`, judged from
the newest `succeeded` canary: `ok`, `stale` (older than 3 intervals, **degraded**), `never_completed` (ok inside
a startup grace measured from API start, degraded after), `unknown` (Postgres down or unreadable, no verdict).
Beat death, worker death, a frozen worker and a dead Redis all read `stale`, as one state. States are documented
in `docs/HEALTH.md`.

**Verified.** `infra/smoke-m2-slice1.sh` (interval 3 s): worker stopped, worker *paused* (process alive, not
working), Beat stopped, fresh stack, and Redis stopped each give the documented state; never a 500; `/health/live`
always 200; all recover. Smokes 6, 7 and 10 pass (smoke 6's `/health/data` assertions changed from `no_runs` to
`reported`, since the canary now writes `task_runs`). Lint, mypy, 217 tests, audit, license and the arm64 check
pass. Celery 5.7.0 with the `redis` extra resolves against the pinned `redis==8.1.0`; `celery-types` is a
dev-only dependency so mypy stays strict without an ignore.

**Found along the way.**
- A paused container reports `Status=paused` but `Running=true`; the smoke checks `Running` and `Paused`.
- `docker compose pause` is what a wedged worker looks like to the canary: the case a liveness check misses.
- Without `expires` on the schedule, unpausing replays every missed canary at once; the entry sets
  `expires = 2 x interval`.

**Not done, deliberately.** Sweeping `running` rows whose process died (nothing long-running exists yet; slice
10), and protecting the canary from being starved by a long task (revisit with the first one). Both are written
up in `docs/HEALTH.md` under "What it does not catch yet". The smoke is not in CI, like smokes 6, 7 and 10.

## 2026-11-09 — M2 slice 2: scheduled encrypted backups (built, NOT closed)

**Built.** A `backup` compose service (`infra/backup/`, built FROM the same Postgres image so `pg_dump` is the
server's version). A shell scheduler, not Celery Beat, writing the same `task_runs` table. Two independent
steps: `ops.backup` (dump, `pg_restore --list` check, manifest, tar, `age` to the committed public key, kept in
the `backups` volume, 14 daily + 8 weekly retention) and `ops.backup_copy` (rsync over SSH, `--ignore-existing`,
strict host-key checking against a pinned `known_hosts`, then a ping to `BACKUP_PING_URL`). A sleeping destination
is a failed copy, never a failed backup, and the copy catches up by itself. Catch-up after sleep is one rule: due
when the newest backup is older than the latest slot. Fails closed with no real key in `recipients.txt` (the
committed placeholder), which is the state of the dev stack today. `/health/data` gains `backups.backup` and
`backups.copy` with states; neither touches `/health/ready` (Kyle's call). `infra/restore-backup.sh` decrypts with
the private key (host `age` or the one in the image) and hands the pair to `restore.sh` unchanged.

**Verified** (`infra/smoke-m2-slice2.sh`, against a throwaway sshd whose `authorized_keys` line is the documented
one): encrypted file is an age file with no plaintext header and no plaintext left behind; the copy lands; a second
copy sends nothing; the landed file restores with the private key only and not with an SSH key; the restricted key
is refused a shell, a pull and `..` **by rrsync's own messages** (so a probe failing for another reason cannot pass),
and an absolute path is anchored inside the directory; no recipient writes nothing and records `NoRecipient`; a
stopped destination fails the copy but not the backup; the scheduler backs up once, copies after the destination
returns and pings; unconfigured is `not_configured`. Lint, mypy, 230 tests, smokes 6, 7, 8, 9, 10 and M2 slice 1 pass.

**Not verified, and why the slice is open.** Nothing here has touched the real desktop. IDEA-029 closes only when a
real unattended dump lands there and restores from there (`docs/BACKUP.md`, "Closing the slice"). The desktop's OS
and whether its SSH server runs are unknown; rrsync needs a Unix-style receiver (Windows would need WSL or an sftp
variant, neither built).

**Found along the way.**
- **Smoke 9 had been broken since slice 1** and I did not rerun it then. It fingerprints the real database before
  and after, and the canary writes `task_runs` every minute, so the comparison failed (and was racy). Fixed by
  pausing the worker, Beat and backup service for its duration; the exit trap restarts them.
- `docker.exe` needs Windows paths for `docker cp` under `MSYS_NO_PATHCONV=1`; busybox has no `httpd` applet
  (`busybox-extras`); a paused container's `Status` is `paused` but `Running` is true (slice 1).
- The backup image is not covered by the CI vulnerability scan, which builds `backend/` only. Its apk packages float.

**Transport decision, 2026-11-09 (Kyle): this commit is the rsync/rrsync build as verified; the copy step is
replaced in the next change.** The desktop turned out to be Windows. rsync and rrsync need a Unix-style receiver,
which Windows does not have, so the receiving side moves to **sftp over Windows OpenSSH Server**, and the
write-only restriction moves from rrsync to **NTFS permissions on a dedicated local account**.

Why not WSL2 (the other way to keep rsync): sshd inside WSL2 does not start by itself after a reboot; it needs a
Task Scheduler entry to bring it up. A backup destination that silently stops accepting backups after a reboot is
the failure you only find out about when you need the backup. OpenSSH Server on Windows is a native service with
start type Automatic, so it comes back on its own.

What survives unchanged: the container and its scheduler, local-first encryption, retention, fail-closed with no
recipient, catch-up after sleep, `task_runs` and `/health/data`, `restore-backup.sh`, the missed-run ping. What
moves is `run-copy.sh` and the receiving-side setup in `docs/BACKUP.md`.

## 2026-11-09 — M2 slice 2, transport rebuilt: SFTP over Windows OpenSSH, restriction in NTFS (slice still open)

**Changed.** `run-copy.sh` now uses `sftp` (rsync is gone from the image). It lists the destination, uploads
what is missing or a different size, then lists again and compares sizes. It never relies on rename (denied) or
on overwrite (denied under the recommended permissions). `BACKUP_SSH_PATH` has no default and the copy refuses
without it: a default of `.` would copy into the account's start directory, not the folder the permissions were
set on. New `infra/backup/verify-destination.sh` probes the real destination for the one property everything
rests on (the key cannot delete or rename) and says which of two policies is in force. The smoke's stand-in is
now three SFTP-only sshd containers (no-delete, write-once, open). Unchanged: scheduling, catch-up, encryption,
retention, fail-closed, `task_runs`, `/health/data`, `restore-backup.sh`, the missed-run ping.

**What the NTFS experiments found** (the exact `icacls` commands, run on a throwaway folder on Windows with a
stand-in account; `docs/BACKUP.md` has the table):
- **`D` is not Delete in a deny entry.** icacls's `D` shorthand also sets Synchronize, which blocks almost every
  open, so my first results (everything denied, including reading) were an artifact. `DE` is the right flag.
- **"Create and write, deny Delete and Delete-child" does not stop overwriting.** An open asking only for the
  data-write right on an existing file wrote in place and truncated it (24 bytes to 1). Opens that also ask for
  append were denied. Whether `sftp-server.exe` asks for exactly that is unknown, so this variant may leave a
  stolen key able to destroy every backup without deleting one.
- **Write-once works on NTFS.** Folder: list and add files, this folder only. Files inherit read-only plus an
  explicit deny of write, append and delete. A new file can be created and filled in one open (the creating
  handle is not subject to the new file's inherited deny), and nothing can reopen it for writing. Every
  overwrite, truncate, append, delete, rename and mkdir was denied, whatever access was requested. **This is
  the default (Kyle, 2026-11-09)**, chosen on the measurement and not on preference: the original spec, deny
  Delete and Delete-child, was never append-only (a 24-byte backup was cut to 1 byte in place). The experiment
  is `infra/backup/acl-experiment.ps1`, so it can be re-run on the desktop. The cost is that an interrupted upload cannot be repaired by the account: the copy fails
  loudly (`PartialOnDestination`) and an administrator deletes that one file.

**Chroot and "home is the backup folder" were not used.** Microsoft supports `ChrootDirectory` for SFTP only,
and there are reports of Windows `Match User` + `ChrootDirectory` configurations misbehaving, including the
service not starting; a restriction that can stop sshd is the wrong place for one. Nothing found in Microsoft's
documentation changes a local account's SFTP start directory, so the folder is named explicitly. Consequence: the
session is not chrooted, so a leaked key could read what the `Users` group can read on the desktop (the backups
are encrypted, and it cannot write outside the folder).

**Found along the way.**
- OpenSSH's `sftp ls -l` prints the **full path** in the last column, not the bare name. My first parser matched
  names exactly and silently found nothing, so the post-upload size check reported "missing". It now takes what
  follows the last `/` or `\`, and reads size and name from the right of the line, because Windows owner and
  group columns can contain spaces.
- Alpine's default `sshd_config` carries the `Subsystem sftp` line; my minimal stand-in config dropped it and the
  connection opened and closed at once. Windows' default config has it too, so the setup says not to remove it.
- `>` in Windows PowerShell 5.1 writes UTF-16, which ssh cannot read as `known_hosts`, and the empty-passphrase
  argument to `ssh-keygen` is quoted differently across PowerShell versions: both moved to Git Bash in the docs.
- Smoke assertions of absence ("no shell", "no delete") now also assert the refusal's own words
  (`This service allows sftp connections only`, `remote delete ... Permission denied`).
- `prune.sh` runs correctly under Git Bash on Windows (GNU awk): 45 files in, 18 left, non-backup files untouched.

**Three things that are open, logged so they are not assumed closed.**
1. **The backup image is not covered by CI's vulnerability scan.** The `security` job builds `backend/` only. The
   image's Alpine packages float at every build, on top of a Timescale base nobody scans. Slice 0 closed the
   floating tag on the backend image; this is the same problem on a second image, and it is open.
   (`docs/RUNBOOK-IMAGE-CVE.md`, "What this scan does not cover".)
2. **arm64 is untested for the backup image** (`ci-checks.sh arm64` builds `backend/` only).
3. **Retention on the destination is aspirational.** `prune.sh` is a manual step on the desktop. The 14+8 rule
   runs on the laptop's local volume only; the account cannot delete on the desktop, by design, so nothing prunes
   the copies that matter most. What would run it: a weekly Task Scheduler job on the desktop, as an administrator
   or SYSTEM, running `prune.sh` under Git Bash (needs Git for Windows and a clone of the repo). The `schtasks`
   command in `docs/BACKUP.md` is untested; a PowerShell port with a parity check against `retention_delete` is
   the alternative if Git for Windows is not there. Until one runs, the destination grows without bound.

**Not verified, and why the slice stays open.** Windows OpenSSH Server accepting the `sshd_config`, the
`ProgramData` key file location, the `/C:/...` path form, and how `sftp-server.exe` behaves under these
permissions have never run. They are written from Microsoft's documentation. The first real attempt is the test,
and `verify-destination.sh` is the instrument. Sources: Microsoft's OpenSSH Server configuration page (chroot is
SFTP-only; unsupported keywords; `AuthorizedKeysFile` and administrator key rules), WinSCP's FAQ (drive paths as
`/X:/...`), the Win32-OpenSSH issue tracker and an SSH.NET discussion (chroot problems).

## 2026-11-09 — M2 slice 2, follow-up: retries are new objects; the unchrooted account reaches more than the backups

**Retry shape (Kyle's question: can an interrupted upload be stepped over instead of needing an administrator?).
Yes, and it is built and tested.** Write-once forbids reopening a file, not creating one. A backup now counts as
copied when the destination holds an object of that backup at the same size as the local file. If none does, the
copy creates a NEW object, `<db>_<stamp>.r<N>.tar.age` (N one above the highest already there), and leaves the short
one alone. So the chain never stalls and no administrator step is needed; `PartialOnDestination` is gone as an
error class. The copy never overwrites, under any permission variant: one code path. Retention
(`retention_delete`) was taught the names: it keeps the highest-numbered object of each backup and deletes the lower
ones, which is correct because a retry is only made when every lower object is short; ordering is numeric (r10 beats
r9). Tested by `infra/smoke-m2-slice2.sh` on a no-delete destination (50-byte object left, complete `r1` beside it,
nothing more on the next run) and on a write-once one (plain 50 bytes, `r1` 80 bytes, complete `r2`; a direct
overwrite attempt confirmed refused), a retry object restores, a 50-byte object is refused by `age` before it can
reach a database and creates no database. Self-test: 30 assertions.
Costs: litter on a destination nothing prunes yet (documented), and one known edge in retention (the newest backup of
a day having only short objects at the moment prune runs would let an older same-day backup go). Bug on the way: my
loop reused the variable that held the ssh destination for the object name, so sftp tried to connect to a host named
after the file ("Connection closed", exit 255); found by tracing the script in isolation.

**The Users-group question (Kyle: can flowdesk-backup be removed from Users, closing most of the unchrooted gap?).
Partly answerable, and the exposure is bigger than I said.**
- *Does Windows OpenSSH need `Users` membership to authenticate?* **Not established.** Microsoft's documentation does
  not say. The one relevant Win32-OpenSSH discussion only has a commenter who believes sshd does a network logon
  (which the default "Access this computer from the network" right grants to Everyone) and a reply that sshd's
  configuration governs access; neither was verified. It is a two-minute test on the desktop, with a one-line
  fallback, and the docs now say to try it (step 2, step 7).
- *Would removing it close most of the gap?* **No.** Measured on this laptop's default Windows 10 layout
  (`infra/backup/acl-survey.ps1`): removal drops the account's reads of `C:\Windows`, Program Files and ProgramData,
  but `Authenticated Users`, which every account holds, has **Modify on anything created directly under a drive
  root** (`Authenticated Users:(OI)(CI)(IO)(M)`, inherited by new folders). 17 of this laptop's 20 top-level folders
  carry it. So an SFTP-only account without a chroot can read, change and delete ordinary top-level folders whatever
  groups it is in.
- **Correction to my own earlier text:** I wrote that the account "could not write outside the backup folder". For
  top-level folders on a default install that was wrong; `docs/BACKUP.md` ("What the account can reach", "Limits")
  and `CLAUDE.md` now say so. What would close it: a chroot (the real fix; reported flaky on Windows, so a guarded
  trial with a config backup and `sshd -t`), or removing `Authenticated Users` from the top-level folders that
  matter, or accepting it with the port off the internet. Undecided; the desktop's real layout is the input.

## 2026-11-10 — M2 slice 2, follow-up: the missed-copy alarm on a laptop that leaves home; Tailscale priced (nothing installed)

**Context (Kyle):** the destination is LAN-only, so a leaked key is useless off the home network, which makes
"accept the gap" (the unchrooted account) defensible and the ACL work optional. The cost: copies succeed only when
the laptop is home, on weekdays it is at work or school.

**Finding: the 12 h grace is borderline for a reason that can be fixed, and it is not "the monitor is wrong".**
The ping is sent after a successful copy. Copy retries back off 15, 30, 60, 120, 240, then 360 minutes
(`backup-loop.sh`), and the cap is 360 minutes. A laptop that spends 08:15-18:00 away has burned through the backoff
by the time it returns, so the first success lands as late as ~22:00, not at 18:00. A weekend ping at ~09:00
followed by a Monday ~22:00 ping is 37 h, past period 1 day + grace 12 h (36 h). So it would page on some Mondays
and sit within hours of the line on other days. That is the "mute it" pattern. (The cap is also not wired through
`docker-compose.yml`; only `BACKUP_RETRY_MINUTES` is, so it cannot be changed from `.env` today.)

**Options, as laid out to Kyle (undecided):**
1. *Alarm matches the away pattern.* Wire `BACKUP_RETRY_CAP_MINUTES` through compose and set it to ~30 so a
   success lands within half an hour of getting home, and size the check to the longest *expected* absence:
   period 1 day, grace 24 h (pages at 48 h). Trade: slower news of a real failure, and a laptop lost on a trip is
   noticed in two days. Costs one failed 20 s connect every 30 min while away.
2. *Destination reachable from anywhere (Tailscale).* Copies then succeed wherever the laptop has internet, so
   the 12 h grace is realistic again, and the failure modes left are the real ones (desktop asleep, key expired).
   What it changes:
   - *Code and compose:* nothing. `BACKUP_SSH_HOST` is only a string; the 20 s `ConnectTimeout` covers a relayed
     first connection.
   - *`.env`:* `BACKUP_SSH_HOST=<desktop>.<tailnet>.ts.net` (or the desktop's 100.x address, which is stable per
     device). `known_hosts` is regenerated with `ssh-keyscan` using that exact string; the sshd host key itself does
     not change. If a container cannot resolve the name, use the 100.x address or give the service
     `dns: [100.100.100.100]`; neither is tested.
   - *Runbook:* "same address" (step 3, the DHCP reservation) is replaced by the tailnet name. Add: Tailscale on both
     machines; Run unattended on the desktop; disable key expiry on the desktop (and decide for the laptop), or the
     copy dies silently at 180 days; the desktop must not sleep while plugged in, because it is now the dominant
     failure; the sshd firewall rule is scoped to `100.64.0.0/10` on the profile the Tailscale adapter gets, and the
     LAN rule is removed, so port 22 is closed to the LAN as well as the internet; a tailnet ACL allowing only
     laptop -> desktop:22; verify from inside the container, not just the host.
   - *Risk model (reverses the "useless off my network" premise for one case):* a stolen laptop carries both the key
     and tailnet membership, so from anywhere it reaches the desktop's `Authenticated Users`-writable folders until
     the device is removed in the admin console. A new runbook step ("laptop lost: remove it from the tailnet
     first"). With Tailscale the ACL/survey work is no longer optional.
   - *Monitor:* 12 h grace becomes realistic again. `/health/data` staleness (36 h) is unchanged.
   - *Cost and flags:* $0 within the Personal plan, so `ZERO_COST_MODE` is not touched. It is also free of a code
     flag: if Tailscale goes away, point `BACKUP_SSH_HOST` back at the LAN name. A deploy on the Oracle ARM VM would
     join the same tailnet and use the same config.

**Tailscale, facts checked 2026-11-10:** Personal plan is $0, non-commercial, up to 6 users, unlimited user devices,
ACLs and MagicDNS included (tailscale.com/pricing). Node keys expire after 180 days by default and an expired device
drops off the tailnet; expiry can be disabled per machine on every plan (tailscale.com/kb/1028/key-expiry).
Windows needs "Run unattended" for the node to be up before anyone logs in (tailscale.com/kb/1088/run-unattended);
forum reports of it misbehaving exist and may predate current releases. **Not verified:** whether a Docker Desktop
container resolves MagicDNS names or routes to 100.x addresses through the host's Tailscale; Tailscale's own docs
route containers through a sidecar and say Docker uses its own DNS unless told otherwise.

**Prune retention edge (Kyle's direction, recorded in `docs/BACKUP.md` "Retention on the destination"):** a backup
whose only destination objects are short should not count as that day's backup. The copy would drop a tiny sidecar
after verifying the size; prune counts only backups that have one. Not built.

## 2026-11-10 — M2 slice 2, follow-up: Option 1 chosen; the retry cap is wired; Tailscale parked as IDEA-039

**Decision (Kyle):** the alarm matches the LAN-only destination. `BACKUP_RETRY_CAP_MINUTES` is wired through
`docker-compose.yml` (default **30**, `.env.example` documents it), and the missed-copy check is **period 1 day,
grace 24 h** (`docs/BACKUP.md`). Reason: Tailscale would reverse the premise the ACL decision rests on (a stolen
laptop carries tailnet membership as well as the key), pulling the `Authenticated Users` work forward to solve a
problem Kyle does not have; he needs copies from his desk at night. Tailscale is **parked as IDEA-039** in
`docs/IDEAS.md`, trigger: the first deployed instance (the Oracle ARM VM, §13). The Docker Desktop MagicDNS
question is recorded there as the first thing to test.

**Side effect to know about:** the cap is shared. `backup-loop.sh` uses `RETRY_CAP` for the dump retries as well as
the copy retries, so a persistently failing dump (today: the committed placeholder `recipients.txt` gives
`NoRecipient` until a real key is added) now retries every 30 minutes, about 48 failed `ops.backup` rows a day
instead of about 6, and a `DumpFailed` repeats `pg_dump` that often. Splitting the cap (a separate
`BACKUP_COPY_RETRY_CAP_MINUTES`) is a one-variable change; not done, since the instruction was the one variable.

**Does a workday away bury a real failure? Checked in the code; the answer is "mostly no, with one real hole".**
- *What the twenty rows do.* About 20 failed `ops.backup_copy` rows per workday (15 min, then 30 min steps across
  ~9.75 h). `/health/data` reports `backups.copy` from the age of the newest *success* (`ok` within 36 h) and the
  newest run's status, and `tasks[]` carries only the newest row per task. So a normal weekday reads
  `state: ok`, `last_attempt_failed: true`, `last_error_class: Unreachable`; the row count does not appear in the
  response and does not change the state. Documented in `docs/HEALTH.md` and `docs/BACKUP.md` as the expected state.
- *The hole.* `run-copy.sh` `classify_and_fail` maps every ssh exit 255 (and timeout 124) to `Unreachable`. 255 is
  also what ssh returns for a key the server refuses and for a changed host key. So a copy that has broken for good
  while the laptop is at home produces the same `last_attempt_failed: true` + `Unreachable` as a normal Tuesday,
  and `error_message` (which has the real stderr) is never returned by `/health/data`. The signal that remains is
  `copy.state` turning `stale` at 36 h and the monitor at 48 h. That is the backstop working, and slower than the
  failure deserves. **Documented as a known limit; not fixed.**
- *Proposed fix (needs a go-ahead; touches `run-copy.sh`, `selftest.sh`, the smoke, `HEALTH.md`, `BACKUP.md`).*
  Classify from ssh's stderr instead of the exit code alone: `Permission denied` -> `AuthRefused`; `REMOTE HOST
  IDENTIFICATION HAS CHANGED` / `Host key verification failed` -> `HostKeyChanged`; `Could not resolve hostname`,
  `timed out`, `No route to host`, `Connection refused` and the 124 timeout -> `Unreachable` (unchanged meaning:
  "host away"). The first two are never a normal Tuesday and can be paged on sooner than 48 h. The smoke already has
  stand-in sshd containers to provoke each case. Order of work: add the classes, then the smoke cases, then the docs.

## 2026-11-10 — M2 slice 2, follow-up: ssh failures are classified; the retry cap is split by whether a retry can help

**Kyle asked for:** `Permission denied` -> `AuthRefused`; a changed or unverifiable host key -> `HostKeyChanged`;
resolution failures, timeouts, no route and connection refused stay `Unreachable`; anything unrecognised falls back to
`Unreachable`. And the cap split by whether a retry can help: copy transients 30 minutes, `DumpFailed` the long cap,
`NoRecipient` and `ConfigInvalid` not retried (or the long cap, if one-per-slot needed new state).

**Finding that changed the plan: the production `-q` made the classifier impossible.** `sftp -q` silences ssh's error
text. Run from the image against a stand-in sshd, **every** failure (wrong key, changed host key, unpinned host,
no such host, refused port, timeout) printed only `Connection closed`. So the earlier worry, that the stored detail
was just the tail of the stderr, understated it: the detail was empty of information, and no pattern match could
have worked. `run-copy.sh` now uses `-o LogLevel=ERROR`. With that, OpenSSH 10.2 prints (captured, and pinned in
`selftest.sh`): `fd@sink: Permission denied (publickey,keyboard-interactive).`; `WARNING: REMOTE HOST IDENTIFICATION
HAS CHANGED!` ... `Host key verification failed.`; `No ED25519 host key is known for sink and you have requested strict
checking.` ... `Host key verification failed.`; `ssh: Could not resolve hostname ...`; `ssh: connect to host ... port
2222: Connection refused`; `ssh: connect to host ... Operation timed out`. (My first test run also used a key with
the wrong mode from a Windows bind mount and got a misleading result; production copies the key to mode 600 in
`entrypoint.sh`, and the second run did the same.)

**Built.**
- `classify_ssh_failure RC FILE` (lib.sh): only exit 255 is read for text; 124 is `Unreachable`; other exits are
  `SftpFailed`. Patterns are anchored at the start of a line, so a phrase quoted mid-message does not count. Host-key
  patterns are checked before `Permission denied`. No match is `Unreachable`.
- Exit codes (sysexits): 75 `EX_TEMPFAIL` for `Unreachable` and for "task_runs not writable" (nothing was recorded, so
  nothing is noise); 78 `EX_CONFIG` for `NoRecipient`, `ConfigInvalid`, `BadDatabaseName`; 1 for the rest.
- `retry_plan` (lib.sh) and the scheduler: 75 backs off to `BACKUP_RETRY_CAP_MINUTES` (30), 1 to the new
  `BACKUP_RETRY_LONG_CAP_MINUTES` (360), 78 waits for the next slot. A delay already above the cap in force is brought
  down to it.
- **One-per-slot did not need persistent state.** It is a variable in the scheduler's memory (`next_try = next slot`).
  A restart clears it, which is also exactly when a changed key or setting is re-read, so the cost is: after editing
  `recipients.txt` (read live) the scheduler does not notice until the next slot or `docker compose restart backup`.
  The log line says so. Rows from the placeholder-key dev stack: about 48 a day under the shared 30-minute cap
  (previous commit), now 1 a day.
- `AuthRefused` and `HostKeyChanged` take the **long** cap, not 78 and not 30. My call, not specified: a retry cannot
  help from this machine, but the fix may be on the desktop (authorised_keys, restoring its host key), which this
  machine cannot see; so it keeps trying, slowly. A restart retries at once.

**Tested.** `selftest.sh`: 65 assertions, including the captured OpenSSH text, the traps (mid-line phrase,
no text, unseen wording, an SFTP-level `Permission denied` at exit 1) and the backoff sequences. The smoke
(`infra/smoke-m2-slice2.sh`) provokes each class against the stand-in sshd: bad key, changed key, unpinned host,
no such host, closed port, healthy; then runs three scheduler containers for 16 s at base 2 s / transient cap 2 s /
long cap 600 s: a missing key was tried 1 time, an unreachable host 9, a refused key 4 (2, 4, 8 s backoff).

**Not done, said plainly.**
- `HostKeyChanged` shows on `/health/data` (`last_error_class`) and in the logs. **Nothing pages on it sooner than
  the 48-hour monitor.** Paging sooner means degrading `/health/ready` (ADR-0008 section 4); that is a decision for
  Kyle, not made here.
- A false `HostKeyChanged` is possible on a foreign network if `BACKUP_SSH_HOST` is a raw private IP that another
  network assigns to someone else's machine. Documented (`docs/HEALTH.md`, `docs/BACKUP.md`): use a hostname that
  only resolves at home.
- Not tested against Windows OpenSSH Server's wording (the stand-in is OpenSSH 10.2 on Linux, and the message text
  comes from the *client*, which is what is classified, but the server's `Permission denied (...)` method list will
  differ). `verify-destination.sh` on the real desktop is still the check.

### M2 slice 2: a changed host key pages at once; a bare IP is refused (2026-11-10)

This supersedes the first two "Not done" bullets above.

- **`HostKeyChanged` now tells the monitor immediately**, by POSTing to `BACKUP_PING_URL` + `/fail`, and not through
  `/health/ready` (readiness is about serving requests, and degrading it pages: ADR-0008 section 4). It is sent **at
  most once per occurrence**, held in the scheduler's memory (`backup-loop.sh`, `hostkey_signalled`) like the
  one-per-slot flag, because the provider's deduplication was never verified. An occurrence ends with the next
  successful copy; a container restart forgets it; a signal the monitor did not accept is retried. Each attempt is a
  new process, so `run-copy.sh` exits 76 for this class and the scheduler does the telling.
- **"No <type> host key is known for ..." is `ConfigInvalid`** (exit 78), not `HostKeyChanged`: nothing was pinned, so
  nothing can be impersonating it, and the drill would have paged. Exception, decided here: OpenSSH prints the same
  line when the host has pinned keys but the server offers a different key type, which is a mismatch. So
  `classify_ssh_failure` asks `ssh-keygen -F` whether `known_hosts` holds the host (as `[host]:port` off port 22);
  if so it is `HostKeyChanged`. `AuthRefused` does not signal: it fires legitimately during desktop setup.
- **A bare IP in `BACKUP_SSH_HOST` is refused** by `run-copy.sh` (`ConfigInvalid`) and `verify-destination.sh`, so the
  false `HostKeyChanged` on a foreign network cannot be configured in. A hosts-file or `extra_hosts` entry that maps
  the name to the IP would defeat it; the code cannot see that, and `docs/HEALTH.md` says so.
- **Smoke, run 2026-11-10: passed.** 11 changed-key failures produced exactly 1 signal; 5 refused-key and 1
  unpinned-host failures produced none. The first run failed usefully: the stand-in monitor's static files answer a POST
  with 501, so the signal was (correctly) retried every attempt; the sink now has a CGI endpoint that answers 200.
- **Still unproven:** that the real provider accepts `POST .../fail` on the check's ping URL (healthchecks.io
  documents it), that the container resolves the desktop's name at home, and everything against Windows OpenSSH
  Server. All three are the desktop drill's.

**Addendum, same day: the `-q` sweep.** `verify-destination.sh` still ran sftp with `-q` (silencing ssh's error text,
so every connect failure read "Connection closed") and its `size_of` discarded stderr. It now uses
`LogLevel=ERROR`, classifies a failed connect or upload with `classify_ssh_failure` (with `known_hosts`, host and port,
so a pinned-key mismatch is `HostKeyChanged` and an unpinned host is `ConfigInvalid`), and prints the class and its
`docs/HEALTH.md` meaning beside the failure, plus what ssh said. The smoke's own `remote_size` and `SFTP` helpers had
the same `-q` and are fixed. Swept `infra/` and the docs for every ssh/sftp/scp/ssh-keyscan call: `run-copy.sh` was
already right; `ssh-keygen -q` (key generation, no network) is left alone. The smoke now runs the probe against a
changed key, an unpinned host, a refused key and an unresolvable name and asserts each class. Smoke passed.

## 2026-11-10 — M2 slice 3 planning: reference tables, universes, calendars

**Decided (Kyle).** Design only; nothing built yet. The schema choices are recorded in IDEA-033.
- **Vocabulary.** `universe_membership` is a validity range, not an observation. It uses only `available_at` and
  `source`, with ADR-0003's meaning. The tripwire is written into IDEA-034.
- **Closure gap.** Logged as IDEA-040, not fixed. The seeder is insert-only: a ticker dropped from config is
  reported as drift and no interval is closed. A nullable `closed_available_at` looks like the cheap path and is
  recorded, not chosen. Nothing in M2 can trigger it.
- **Seeded `valid_from` is the seed date.** An empty result is honest and a wrong one is not (a 2012 query should
  not claim XLRE, which did not exist). **Consequence: no historical query against these universes returns
  anything before the seed date.** Revisit at slice 6. Membership in a self-defined universe is a stipulation, not
  an observation, so once first-bar dates exist a membership start can legitimately be declared from them.
- **`BREADTH_UNIVERSE` is created empty.** §6.2 says "S&P 500 constituents" and gives no list, and M2 has no
  constituent provider. Seeding it would mean inventing about 500 tickers.
- **Calendars.** `trading_sessions` is a table. Sessions and holiday/early-close fixtures are slice 3; session math
  is a follow-on. The `exchange_calendars` dependency exists either way.
- **`config.py` vs `settings.py`:** §6.2 says `config.py`, the repo has `settings.py`. That is a `# DECISION:`
  comment, not a defect.
- **Logged:** IDEA-040 (closure gap), IDEA-041 (12 vs 14 vs "11 sector ETFs", a plain contradiction, not a drift row). IDEA-033 and IDEA-034
  carry the decisions above.
- **`provider_mappings` uniqueness (approved):** Index A on `(symbol_id, provider, id_kind)`, Index B on
  `(provider, id_kind, external_id)` excluding `cik` (GOOG and GOOGL share one CIK), both `WHERE retired_at IS
  NULL`, plus CHECKs on `id_kind` and the CIK form. The CHECK is a deliberate exception, recorded in IDEA-022.

### Slice 3 result (2026-11-10)

**Built.** Migration `0003_reference_tables.py` and `app/models/reference.py` (seven tables, schemas as in
IDEA-033); `app/services/reference_seed.py` and `app/services/sessions.py`; `python -m app.seed`, run by the
`migrate` service after `alembic upgrade head`; the §6.2 lists in `app/settings.py`; `exchange-calendars==4.13.2`
in `requirements.txt` and the three locks; `infra/smoke-m2-slice3.sh`; `tests/test_reference_tables.py` and
`tests/test_sessions.py`. `smoke-slice4.sh` now asserts "0002 or later" instead of exactly 0002, since the head
moved. `ci-checks.sh arm64` also imports numpy, pandas and exchange_calendars now.

**Verified.** Lint, mypy and 269 tests pass with none skipped (the real-database tests ran). The calendar fixtures
pass against the library as it is: NYSE 2025-07-04 and the 2025-01-09 national day of mourning have no session,
2025-07-03 and 2025-11-28 close at 13:00 New York (17:00 and 18:00 UTC), the 09:30 open moves with the clock
change, and KRX 2025-01-29, 2025-10-03 and 2025-10-09 have no session. The `provider_mappings` rule holds in the
real database: GOOG and GOOGL share one CIK, a symbol cannot hold two active CIKs, one live ticker string cannot
name two symbols, `id_kind` is a closed set, a CIK must be ten digits. `pip-audit` finds nothing in the three
locks. Smokes 3, 4, 9 and the new M2 slice 3 smoke pass; the arm64 build loads numpy, pandas and
exchange_calendars under QEMU. The smoke 8 and M2 slice 1 smokes were not re-run (smoke 8 includes the licence
gate, which is red).

**What the seed does on a live stack.** 24 symbols, five universes (rotation 14, benchmark 1, style extras 2,
commodity proxies 7, breadth 0), 24 memberships starting on the seed date, two currencies, two exchanges, and
sessions for XNYS and XKRX from 2015-01-01 to a year past today. A second run inserts nothing.

**Open: the licence gate is red.** `exchange-calendars` pulls numpy 2.5.4, whose licence field is
`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` (bundled components). `0BSD`, `Zlib` and `CC0-1.0` are not on
the allow-list in `backend/tools/license_audit.py`, and every other package in the set passes. The tool's rule is
that new entries are recorded in IDEA-027 first and are the owner's call. The narrow option, in the tool's own
pattern for `pathspec`, is a per-package exception for numpy covering just those three parts. Nothing has been
changed. Until it is decided, CI's licence job and smoke 8 fail.

**Notes.** pandas 3.1 emits a `Pandas4Warning` about `Series.values` while `exchange_calendars` builds a calendar;
it appears to come from the library and is not ours. Nothing prunes `trading_sessions`; the table is small.

### Slice 3: licence decision (2026-11-10)

The owner allowed numpy as a per-package exception keyed to the exact expression
`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` (`EXPRESSION_EXCEPTIONS` in `backend/tools/license_audit.py`,
recorded first in the IDEA-027 addendum). numpy's own licence is BSD-3-Clause; the other parts come from vendored
components, so 0BSD, Zlib and CC0-1.0 are not approved in general. A different string from a future numpy fails the
gate. The "Open: the licence gate is red" paragraph above is superseded by this entry.

Re-run after the exception: lint, mypy, 270 tests, `ci-checks.sh license` (56 packages, 0 not allowed), `smoke-slice8.sh` and `smoke-m2-slice1.sh` all pass. Slice 3 is closed apart from computing sessions on native arm64, which is untested.

## 2026-11-11 - M2 slice 4: provider framework and fake provider

**Decisions (Kyle, on the plan).** (1) Stale feeds stay off `/health/ready`, and the rule is written down as a rule
in `docs/HEALTH.md` ("The readiness rule": it degrades when the machinery has stopped, not when a source is
unavailable). (2) `providers_health.first_seen_at`, with a never-succeeded feed escalating at its own threshold
(IDEA-026 addendum). (3) Tenacity only, Celery retries 0 for ingest tasks: Beat is the outer retry, because a Celery
retry would write a second `ingestion_runs` row for one logical run; the §6.11 / §11 overlap is IDEA-042. (4)
`rows_written` counts created-or-changed rows only, documented as a verification signal so nobody later changes it
to count attempts. Plus: a test that no credential reaches `providers_health.last_error`, asserted on the stored value.

**Built.** `app/ingest/{errors,budget,runner,fake}.py`; `app/tasks/fake_ingest.py` (scheduled only when
`FAKE_PROVIDER_ENABLED`); migration 0004; `classify_feed` escalation; the seed registers declared feeds; compose
passes the fake's variables; `tenacity==9.1.2` (Apache-2.0) in the locks; tests `test_ingest_errors`,
`test_rate_budget`, `test_ingest_runner`, `test_provider_health_states`, `test_fake_provider`;
`infra/smoke-m2-slice4.sh`; HEALTH.md gains the readiness rule and "Killing a provider".

**Left out on purpose** (slice 5 or 6 adds it with a real provider in hand): pagination, windowed quotas (FRED's
limit is in no spec or ADR), partial-batch success, per-capability protocols, env-based provider selection,
`Retry-After`, tuned backoff numbers. The retry numbers (4 attempts, 1 s initial, 60 s cap) are placeholders.

**Found by the credential test.** The scrubber's first version failed on `Authorization: Bearer <key>`: the generic
`name=value` pattern took the word "Bearer" as the value and left the key behind. Fixed by scrubbing Bearer first.
That is the exact-pattern layer; only the exact-value layer (a client passing its own key as `secrets`) is a
guarantee, and `providers_health.last_error` does not depend on either since it never holds the text.

**A correction to the framing of the credential concern.** The public profile already removes
`feeds[].last_error` (IDEA-019, `redact_providers`), so a key there would not be on the public endpoint today. The
private profile serves it unredacted, and one redaction layer is not a reason to store a secret, so the test and
the kind-and-class-only design stand either way.

**Verified.** Lint, mypy, 329 tests; `ci-checks.sh` licence and audit with `tenacity` in the locks (via smoke 8, which passes); smokes 3, 4, 6, 7, 8, M2 slice 1, M2 slice 3 and the new M2 slice 4 pass. Not covered: any real provider, and arm64 for the new code beyond smoke 8's import check.

**Found while verifying.** (1) Smoke 6 failed on a stray `fake` feed: a fake task message still queued in Redis ran after the flag was turned off. The task is now a no-op unless `FAKE_PROVIDER_ENABLED`, with a test. (2) The slice 4 smoke failed once, straight after smoke 6, because `/health/ready` was degraded by a canary smoke 6 had left stale; it now waits for `ready` to be `ok` before it starts, and passed three runs in a row before and after. (3) **Unverified risk, not observed:** the canary shares the worker and its queue with ingest tasks, and a retrying task sleeps while holding a worker slot. A provider that is down for many feeds could in principle starve the canary and degrade `ready`, which would contradict the readiness rule in practice. Nothing here triggers it (one fake feed). Revisit with slice 5 or 6, when real feeds share the worker: a separate queue for the canary is the likely fix, and it is a decision for Kyle.

## 2026-11-11 — M2 slice 5 plan: FRED and macro vintages (approved, not started)

Plan only; no code yet. ADR-0003 binds the slice: availability filtering on row existence, vintages never
overwritten, the widened-window 400 as a bug, `"."` kept as a gap, per-series staleness.

**Decisions (Kyle, 2026-11-11).**
- **Canary starvation is Step 0, before any scheduled feed.** One worker at `--concurrency=2` on one queue means a
  long backfill can starve the canary, and `/health/ready` would degrade and page as a dead worker. Fix: an `ops`
  queue and a `worker-ops` service (`-Q ops -c 1`) for the canary alone; the existing worker takes `-Q ingest`;
  `task_default_queue="ingest"` so a new task defaults to the queue where starvation is survivable; ingest prefetch
  1. A test first shows the starvation going red, then the fix turns it green.
- **Option (b): a second canary on the ingest queue**, with a threshold sized to the longest allowed task. Reason
  to record: without it a wedged ingest worker would surface only as stale feeds, which the readiness rule says
  never pages. That is machinery failure disguised as source failure, and it breaks the rule in the case it was
  written for. Adds a degraded condition, so it pages (ADR-0008 section 4). Smokes M2-1 and M2-4 name which worker
  they stop; HEALTH.md and the comment at `settings.py` ("Revisit when long tasks share the worker") change.
- **Vocabulary** (also in SPEC-DEFECTS IDEA-034 and IDEA-038): `effective_at`, `available_at`, `ingested_at`,
  `revision`, `source`, plus `ingestion_run_id`. `macro_metrics.effective_at` is a **DATE**. §7 has no SQL join of
  `macro_metrics` to `bars`; macro and bars are aligned by date in ThemeScore, Scenario Replay and the Market Graph,
  which must use the session date and never `CAST(ts AS date)`. `value` is nullable: `"."` is a stored gap.
- **Data age on `/health/data`**, from `max(available_at)` per series; `providers_health` stays poll health.
  Thresholds come from each series' own backfilled `available_at - effective_at` history, sized as **generous
  multiples of observed lag**, not tight fits: a release-schedule change is not predicted by history.

**Steps.** 0 canary isolation · 1 migration 0005 (`macro_metrics`) · 2 FRED client and error mapping (the three
400s by message, 429 as `rate_limited`, 423/500 as `transient` and logged, key redaction in httpx's URL logging,
offset paging) · 3 backfill (`output_type=4`, widened window) and daily poll at 08:00 ET, revision only on change ·
4 point-in-time read in `services/`, tested against the GDPC1 oracle (23,526.085 on 2025-05-01, one 2025 quarter
then) · 5 per-series data age · 6 smoke, HEALTH.md, `.env.example` (`FRED_API_KEY`).

**To verify before the backfill is designed.** FRED caps vintage dates per request (series_observations page: 2000
for JSON, lower for daily series); the M0 spike only covered GDPC1 and DGS10, so a widened window on the other
daily series may need chunking. Also which request shapes trigger the "No vintage dates" 400.

**FRED rate limit: documented, not measured.** Source: https://fred.stlouisfed.org/docs/api/fred/errors.html,
**confirmed verbatim on the page by Kyle, 2026-11-12** (the earlier fetch-tool quotation matched it):

> 429 Too Many Requests (Up to 120 requests per minute are allowed before being served a 429 error code. N
> ot complying with the throttling can result in a temporary block.)

The page lists the complete set of status codes it may return: **400, 404, 423, 429, 500**. 423 and 500 are
listed with no explanation, and the page does not mention `Retry-After`. Error bodies are XML or JSON with a
message attribute carrying the description, which matches the three real 400s captured in step 2b. A 6,000/day
figure appears in third-party guides and in no official source found. The `fred` entry in `RATE_BUDGETS` is
`RateBudget(1.0, 1)` (1 request per second, 60 a minute, half the cap), added 2026-11-12. **No 429 has been
observed**, so the 120 is a documented number and the budget is a margin against it, not a measurement; the
client logs status and headers on the first natural 429.
The slice 4 retry placeholders (`RetryPolicy`, `runner.py`) are replaced by a per-provider override for FRED;
its numbers carry the same label.

**Decisions added after approval (Kyle, 2026-11-11).**
- **The ingest canary's false-alarm gap is closed with `heartbeat_at`, not queue priority.** Priority cannot
  help a canary waiting behind a task that already started, and it ties the guarantee to Redis transport
  behaviour that cannot be verified from here. The ingest worker counts as alive if a task completed recently
  **or** one is running with a fresh `heartbeat_at`; queue position stops mattering. This is the same mechanism
  as the sweep for rows left `running` when the time limit kills a task (`tasks/runs.py`, deferred to "the
  first long task, slice 10"). Step 3's FRED backfill is the first long task, so that trigger fired early:
  both are built together at step 3. Step 0 ships without them, so until then the threshold can false-alarm
  (HEALTH.md, "The ingest worker check").
- **FRED client User-Agent (Kyle, 2026-11-11; belongs to step 2).** The client sets an explicit User-Agent from
  `FRED_USER_AGENT`, defaulting to a generic `FlowDesk` string with no personal contact; httpx's default is not
  relied on. Pattern: §6.5's EDGAR rule (mandatory contact UA, fail loudly at startup), except FRED documents no UA
  requirement, so a missing value falls back to the default and does not stop startup. The theory that FRED filters
  on UA is unconfirmed: curl with a Chrome-style UA was reset too (2026-11-11), so the reset may be this
  machine's network. Either way a 401/403 maps to a non-retryable kind and is never `transient`; a connection reset
  cannot be told from a network blip, so it stays `transient`, bounded by the 4 attempts and Beat's next firing,
  and a feed that never succeeds goes stale (`first_seen_at`).

### M2 slice 5, step 0 (2026-11-11): the canary no longer queues behind ingest work — built and verified

**Built.** Two queues, two worker processes. `worker-ops` (`-Q ops -c 1`) runs `ops.canary` and nothing else;
`worker` (`-Q ingest -c 2`) runs everything else, including the new `ops.canary_ingest`. `task_default_queue` is
`ingest`, so a task nobody routed lands where starvation is survivable. `worker_prefetch_multiplier=1`.
`task_time_limit` is `INGEST_TASK_MAX_SECONDS` (default 1800, a guess). `/health/ready` gains
`checks.ingest_worker`, which degrades like `checks.worker`; its threshold is `INGEST_TASK_MAX_SECONDS` plus three
canary intervals, because a legal task can starve its canary for that long. The fake provider gained a `slow`
mode (`FAKE_PROVIDER_SLOW_SECONDS`) so something can hold the slots. HEALTH.md has "The ingest worker check".
Smokes M2-1 and 9 now name `worker-ops` / both workers; M2-4's diagnostics read the `ingest` queue.

**Verified, both ways (Kyle's gate: a fix for a bug nobody demonstrated isn't verified).**
- *Red on the pre-fix stack.* The fix files (`docker-compose.yml`, `celery_app.py`, `services/health.py`,
  `api/health.py`) were stashed, leaving the `slow` fake mode in place (without it nothing saturates the slots) and
  adding the one-line `FAKE_PROVIDER_SLOW_SECONDS` passthrough to the old compose. `smoke-m2-slice5-step0.sh
  --baseline` (saturation only; one worker, one queue) FAILED on the assertion it exists for: `/health/ready` was
  `degraded` with `checks.worker` `stale` at 9 s while both slots were busy. The old stack has no `worker-ops`, so
  the red is the canary starving and not a missing service.
- *Green on the fixed stack* (stash popped, the temporary compose edit discarded): the full smoke passed. Queues
  are separate (the ingest worker never received `ops.canary`, nor the ops worker `ops.canary_ingest`); with both
  ingest slots busy the ingest canary starved up to 16 s, past the ops threshold of 9 s, and `/health/ready` stayed
  `ok` with `checks.worker` `ok`; pausing the ingest worker gave `ingest_worker` `stale` and `degraded` with the ops
  worker `ok`; pausing the ops worker gave the mirror.
- *The project's own 3.12 image,* `ci-checks.sh lint` (ruff, format, mypy, locks, banned phrases) and `ci-checks.sh
  test`: **346 passed, 0 skipped** (the 67 that skip without `DATABASE_URL` ran). The earlier 279 passed / 67
  skipped was a Python 3.13 scratch venv, which is a weaker claim.
- *Edited smokes re-run and passing:* M2-1 (now stopping and pausing `worker-ops`), M2-4, 6 and 9.
- Tests were written first and failed first (ImportError on the new names; the slow-mode tests on assertion).

**Not covered.** arm64 for the new code beyond what `ci-checks.sh arm64` already does (not run), `smoke-slice8.sh`
(not run), and Beat's handling of two entries at the same interval beyond what the smoke exercised.

**KNOWN GAP, shipped on purpose: the ingest canary's threshold can false-alarm until step 3.** It is sound for
one long task per slot. Two slots running long tasks back to back can keep the canary waiting past it, and that
would page a healthy worker. (I said earlier it could only false-alarm "when a task overruns"; that was too
strong.) The decided fix, and why queue priority was rejected, are in the plan entry above under "Decisions
added after approval". Nothing runs long before step 3, so no false alarm is possible until then.

**arm64, known local behaviour.** `ci-checks.sh arm64` failed once ("image did not run as aarch64") on the first
run after a cold Docker Desktop start, and passed on the immediate re-run (2026-11-11). Read as binfmt/QEMU
registration not being ready after a cold start (Kyle's reading; not diagnosed here). **No retry is added:** CI
installs its own QEMU, and a self-retrying gate hides real failures. A local failure right after Docker starts:
run it again before suspecting the code. A failure in CI is real.

### M2 slice 5, step 1 (2026-11-11): macro_metrics and migration 0005

**Built.** `backend/alembic/versions/0005_macro_metrics.py`, `backend/app/models/macro.py`, and
`backend/tests/test_macro_metrics.py`. Columns, in order: `metric` TEXT, `effective_at` DATE, `revision` INT
(default 0), `value` NUMERIC (nullable), `available_at` TIMESTAMPTZ, `ingested_at` TIMESTAMPTZ (default now()),
`source` TEXT, `ingestion_run_id` BIGINT (nullable). Primary key `(metric, effective_at, revision)`: a later value
for a period is a new row. The first table built to the shared provenance vocabulary (IDEA-034); IDEA-038 is
marked resolved.

**Decisions made in the migration, each a `# DECISION:` there.**
- *Plain table, not a hypertable.* §8 does not mark it with the hypertable symbol, and about 17 series of at most
  tens of thousands of rows each is small; 0003 set the precedent.
- *No index beyond the primary key,* and *no CHECK on `revision`* (the ops tables' convention).
- *`ingestion_run_id` is a foreign key to `ingestion_runs`, ON DELETE SET NULL.* §8 gives only a BIGINT. A purged
  ledger row must neither take the observation with it nor be blocked by it.
- *`value` is nullable:* a FRED "." is a stored row with `NULL`, never 0.0 and never forward-filled.

**Tests (written first; 13 of 14 failed before the migration existed, the 14th, "not a hypertable", passed
vacuously because no table existed).** They pin the shape: names and types; the primary key; defaults; a DATE
read back as 2025-01-01 under `SET TIME ZONE 'America/Toronto'` (the failure DATE exists to rule out); a revision
and its first print coexisting (GDPC1 Q1-2025, 23,526.085 then 23,548.210); the same vintage rejected twice with
the stored value untouched; a gap stored as NULL; exact NUMERIC round trip; each identifying column required; the
foreign key and its SET NULL behaviour; downgrade and re-upgrade. They do NOT test point-in-time reads (step 4)
or ingestion (step 3).

**Verified.** In the project's 3.12 image: lint, format and mypy clean; **371 passed, 0 skipped** (346 before, 25 new: 14 for the table, 11 for the triggers), including `test_models_match_migrations`, which diffs the model against the migration. Smokes 3, 4, M2-3
and 9 pass (9 proves a backup round-trips with the new table in the real database). The real database is at 0005 with the triggers (the table was empty; dropped by hand, stamped 0004 and upgraded after the triggers were added). **Restore proven with rows:** on two scratch databases, `pg_dump -Fc | pg_restore --no-owner --exit-on-error` carried two GDPC1 vintages across, and an UPDATE on the restored copy was refused with the intended message and left the value intact.

**Decided (Kyle, 2026-11-11): `available_at` for a date-granular publication is the end of that day in
America/New_York.** Never early, up to 24 hours late, for first prints only: a documented bias with its direction
and size, recorded in an addendum to ADR-0003, in HEALTH.md and in IDEA-034 (it binds every observation table).
M5's outcome tracking must carry it or it will attribute up to a day of lag to the strategy. One assumption stays
open until the backfill meets real data: FRED's dates are presumably US Central, whose day ends an hour after the
New York midnight used here; compare `realtime_start` with known release times for a few series.

**Added: the database refuses to change a stored vintage (Kyle, 2026-11-11).** BEFORE UPDATE, DELETE and TRUNCATE
triggers on `macro_metrics` (same migration 0005). Reason: an overwritten vintage is the only failure here that
is both catastrophic and undetectable, and the primary key does not prevent it. One carve-out, which is mine and
should be read as a refinement of what was asked: an UPDATE may clear `ingestion_run_id` to NULL, because the foreign
key's `ON DELETE SET NULL` is itself an UPDATE and a blanket refusal would have made any referenced
`ingestion_runs` row undeletable. **Checked before blocking DELETE:** nothing deletes from `macro_metrics` (the
table is new; the only DELETEs near it are on `ingestion_runs`, in smokes 4, 9, M2-4 and step 0, and reach it only
as that SET NULL). `pg_restore` loads with COPY into an empty target and `pg_dump` emits triggers after the data, so
restore is unaffected. `DROP TABLE`, the downgrade, is not blocked. So DELETE and TRUNCATE are blocked too.
`docs/RUNBOOK-VINTAGE-REPAIR.md` is the sanctioned way around: BUILD-LOG entry first, a backup, then
`DISABLE TRIGGER USER` / the repair / `ENABLE TRIGGER USER` in one transaction with `SET LOCAL log_statement = 'all'`
so the server log records it. Nothing monitors that the triggers are enabled (not built; the one-transaction rule
is the guard, and the runbook says where a check would go).

**Tests added for it (written first; 11 failed, then passed):** every stored column refuses an UPDATE (seven
cases) and the refused statement changes nothing; DELETE and TRUNCATE refuse; the run pointer may be cleared but
not repointed; the triggers exist and are enabled (`tgenabled = 'O'`); the runbook's procedure works, an
abandoned repair is rolled back with the triggers still enabled, and the guard is back afterwards.

### M2 slice 5, step 2 (2026-11-12): the FRED client

**Built.** `backend/app/ingest/fred.py` (`FredClient`, `FredObservation`), settings `fred_api_key()` and
`fred_user_agent()`, `backend/tests/test_fred_client.py` (57 tests), `.env.example` entries, and HEALTH.md's
"FRED's failures". It fetches and parses; it writes nothing (storing vintages is step 3, reading them back is
step 4). `first_prints(series)` sends output_type=4 with the widened window (1776-07-04 to 9999-12-31, ADR-0003);
`latest(series)` sends no real-time window. Paged with `limit`/`offset`, a page cap of 100, and a refusal of an empty
page before the count is reached.

**Decisions, each in the code.**
- *`httpx2` is now a runtime dependency* (BSD-3-Clause; Pydantic's continuation of httpx, see "Provenance" below). It
  was already a *direct dev pin* from `cc4ec01` (M1 slice 8, replacing `httpx 0.28.1`), not something FastAPI or
  Starlette pulled in: `pip show httpx2` reports `Required-by:` empty. Starlette and FastAPI name it only in optional
  extras (`starlette[full]`, `fastapi[standard]`), and Starlette's `TestClient` imports it. (An earlier version of
  this entry said it was "already the dev dependency behind FastAPI's test client"; that was an assumption, not a
  check, and was wrong.) The runtime lock gains `httpx2`, `httpcore2` and `truststore`;
  licence audit 0 not allowed, `pip-audit` clean, locks regenerated with `infra/lock-deps.sh`. This is a dependency
  I added; the alternative was stdlib `urllib` in a thread, with every timeout, header and test seam hand-built.
- *The client makes the "No vintage dates exist" 400 unreachable:* `observations` raises `ValueError` for
  output types 2, 3 and 4 without an explicit window, before any request. It is still classified (a `permanent`
  configuration bug) in case FRED returns it anyway.
- *Every exception carries fixed text only,* never anything FRED sent back, and `from None` on network errors. The key
  rides in the URL and FRED may echo a request.
- *`httpx2` logs `HTTP Request: GET <url>` at INFO, key included.* This is not hypothetical: with the guard removed
  the test failed. `configure_logging` now holds `httpx2` and `httpcore2` at WARNING whatever `LOG_LEVEL` is.
- *401 and 403 are `permanent`* (Kyle): never retried. 423 and 500 are `transient` and logged with their headers; a
  429 logs its status and all headers but cookies, which is how `Retry-After` gets measured on the first natural one.
- *`FRED_API_KEY` is validated as 32 lowercase alphanumerics* (FRED's own description of a valid key), refused
  without echoing the value. *`FRED_USER_AGENT`* defaults to `FlowDesk/0.1 (macro-data ingest)`: no email.
- *Redirects are not followed* (the key would go to whatever host answered).
- *Paging and the budget:* the runner takes one rate-limit slot per attempt; the client takes one for every further
  page through a `before_next_page` callback the caller supplies.
- *A well-formed empty result is returned empty,* not raised: for a poll it can be legitimate, and whether it is a
  failure is step 3's call.

**Verified.** In the project's 3.12 image: lint, format and mypy clean; **428 passed, 0 skipped** (371 before, 57
new). The two guards that matter most were mutation-checked: removing the logger quieting fails
`test_the_http_library_cannot_log_the_url_even_at_debug`, and putting the exception text into the `Transient`
message fails `test_no_error_text_can_carry_the_key_even_if_fred_echoes_it`. The production image imports the client
(`httpx2` 2.16.0). `ci-checks.sh license` and `audit` pass.

**Provenance of `httpx2`: a gap, recorded and accepted (Kyle, 2026-11-12).** Checked against PyPI and GitHub, not
just the package's own metadata:
- *Repository and publisher:* PyPI's project URLs point to `github.com/pydantic/httpx2`; the PyPI maintainer is
  "Pydantic Services Inc." (`engineering@pydantic.dev`) and the author Tom Christie. The README says it is a
  continuation of HTTPX with Pydantic "picking up stewardship". The history is carried over (`lovelydinosaur` 912
  commits, `Kludex` 165, `florimondmanca`, `sethmlarson`). The Pydantic GitHub org is verified (2022).
- *The gaps:* it is **not** Encode's `httpx` under a 2.x name but a different org's continuation of it; the package
  is **about six months old** (repo and first upload both 2026-05-11, 22 releases since); and **PyPI publishes no
  attestation for 2.16.0** (the provenance endpoint returns 404), so the upload cannot be cryptographically tied to
  the repo. Mitigations: the pin is hash-pinned in both locks, `pip-audit` is clean, and the licence is BSD-3.
- *Why it is accepted:* Starlette itself prefers it, the work to avoid it (a stdlib client plus a hand-written
  redirect-refusing handler on a path carrying an API key, replacing tested code with untested code) was judged to
  be a worse risk than the dependency.
- **Review trigger:** when the pin is next bumped, recheck whether PyPI publishes an attestation for the new
  release. The same note sits above the pin in `backend/requirements.txt`, where a bump will see it.

**NOT verified.**
- **No real FRED call has been made,** so every status mapping is from FRED's documentation, not observed. The
  timeouts (30 s, 10 s connect) are placeholders. The first real call is the first evidence of the response shapes
  (field names, the `count` and `limit` fields, `realtime_*` on output_type=4); the fixtures are written from the
  documented shape and ADR-0003's evidence.
- **The FRED rate budget was withheld until 2026-11-12**, when Kyle read the sentence on FRED's errors page; it is
  quoted verbatim in the slice 5 plan entry above, and `"fred": RateBudget(1.0, 1)` is now in `RATE_BUDGETS`. The
  tripwire test became `test_fred_budget_is_half_the_documented_limit`. Still documented, not measured.
- The slice 4 retry placeholders (4 attempts, 1 s, 60 s) are unchanged: nothing has been observed that would tune
  them. They stay labelled placeholders.

**Not done, deliberately.** `FRED_API_KEY` is not yet passed to any container. `x-api-env` is shared by `api`,
`api-public`, `worker` and `beat`, and the API has no use for the key (and `api-public` faces the outside); step 3
passes it to `worker` only.

### M2 slice 5, step 2b (2026-11-12): the first real FRED calls — PARTIAL, blocked on a key

Asked for before step 3's backfill design: (1) the vintage-date cap on a widened-window `output_type=4` request for
every daily series in 6.4, (2) the real response shape against the documentation-derived fixtures, (3) one real
error body. **There is no FRED API key on this machine** (not in `.env`, not in the environment), so (1) and (2) are
**not measured**. (3) is, and a little more.

**Measured, keyless (3 requests, 1.5 s apart).**
- The API host is reachable from here (`api.stlouisfed.org`; `fred.stlouisfed.org`, the website, was reset earlier).
- **A missing, a malformed and a well-formed-but-unregistered `api_key` are all HTTP 400** with
  `Content-Type: application/json; charset=UTF-8` and a body of the form
  `{"error_code":400,"error_message":"Bad Request.  The value for variable api_key is not registered.  Read https:\/\/fred.stlouisfed.org\/docs\/api\/api_key.html for more information."}`
  (the slashes are JSON-escaped as `\/`; two spaces after "Bad Request."). Not 401, not 403.
- No rate-limit or `Retry-After` header on those responses (a 400, so not informative about a 429).
- The three bodies are in `tests/fixtures/fred/` unmodified, and `backend/tests/test_fred_real_bodies.py` feeds them
  through the client: each is `permanent`, not retried, names the api key, and copies nothing FRED said into the
  exception. **This grounds the 400 / `api_key` branch of the taxonomy.** The 401/403, 423, 429, 5xx, vintage-400 and
  unknown-series branches are still documentation only.

**Not measured, and why it matters.** (1) decides whether the backfill is chunked, so the backfill's windowing is
**pending** (see the step 3 plan). (2) matters because every FRED fixture except the three above is built from the
documented shape.

**The probe is ready.** `probe_fred.py` (kept out of the repo: `spikes/` is frozen, and it is a measurement, not a
test) was run end to end against a local stub FRED (41 requests, no bug left in the loop) and against the real API
with a throwaway key (it stops after one request at the key check). With a key in `.env` it needs one command, takes
about a minute, and paces itself at 1.25 s a request (about 48 a minute against the 120 a minute FRED documents). For
GDPC1 and each of the 17 series in 6.4 it records:
- `series/vintagedates` count over the widened window, and the widened-window `output_type=4` request: HTTP status,
  seconds, bytes, `count` vs rows returned (silent truncation), top-level keys, observation keys, value formats,
  `"."` counts, and anything that is neither a decimal nor `"."`;
- **whether old observations carry a real first-publication date or one bulk-load date** (distinct `realtime_start`
  values, the most common one and its share, and any `realtime_start` earlier than its own `date`). This is a risk
  the plan below depends on: if, say, every 1960s observation of a daily series carries the same `realtime_start`,
  then "first print" for history is a bulk-load date, not a publication date;
- the `latest` shape (`output_type=1`) for DGS10, and real bodies for three provokable errors:
  `output_type=4` with the default window ("No vintage dates exist"), an unknown series, and `limit=100001`;
- it stops at the first 429 and prints that response's headers (cookies removed).
Full responses go to a scratch directory; trimmed ones go to `tests/fixtures/fred/` afterwards.

**To run it (Kyle):** get a free key at https://fredaccount.stlouisfed.org/apikeys, add `FRED_API_KEY=<key>` to the
gitignored `.env` (never in chat), and say so. The key is read from the environment and never printed or written.

### M2 slice 5, step 2c (2026-11-12): the real FRED measurements

With a key in `.env` (never printed; 32 lowercase alphanumerics, checked by shape only) the probe ran: 41 requests
for probe 1 (`vintagedates`, widened `output_type=4`, `latest`, three provoked errors, 18 series) and 68 for the two
follow-ups (latest values per series for a completeness comparison; chunked windows on the six capped series), all
at 1.25 s spacing, **no 429 and no other non-200 except the 400s below**. About 110 requests in total. The scripts
live in the session scratchpad, not the repo (`spikes/` is frozen); fixtures are in `tests/fixtures/fred/`.

**1. The vintage-date cap: 2000, and it is an explicit 400, not silent truncation.** The body, from `DGS10`:
`{"error_code":400,"error_message":"Bad Request.  There are 5147 vintage dates in the specified real-time period:
1776-07-04 to 9999-12-31.  This exceeds the maximum number of vintage dates allowed for this file type (2000)."}`
The message carries the count and the limit, so the detection is mechanical. Per series (vintage dates from
`series/vintagedates`; the error's count is one lower than that endpoint's, cause unexplained): **over the cap, 400:**
DGS10 5,148, DGS2 5,149, DFII10 5,130, VIXCLS 3,971, T10YIE 3,164, T10Y2Y 3,160. **Under, 200:** SOFR 1,902,
IORB 1,319, MORTGAGE30US 856, UNRATE 801, HOUST 791, BAMLH0A0HYM2 784, BAMLC0A0CM 782, CPIAUCSL 671, GDPC1 420,
DTWEXBGS 406, CSUSHPINSA 144, DRCCLACBS 62. (All 17 series of 6.4 plus GDPC1.) **SOFR is 98 vintage dates from the
cap**; that is why the design chunks every series.
`output_type=4` rows are one first print per observation date, not one row per vintage (GDPC1: 420 vintage dates,
140 rows, 140 distinct dates), and several observations can share a vintage (DTWEXBGS: 2,025 rows, 405 vintages).
Chunked windows were then measured on the six capped series (3, 3, 2, 3, 3, 2 windows of at most 1,900 vintage
dates): all 200, `count` equals the rows returned, no date in two windows, no gaps in range except VIXCLS below.

**2. Silent shortfalls the cap does not cover (the correctness finding).** Comparing the union against the latest
values for every series: **every date the union lacks is either earlier than the series' first vintage or a `"."`
that was never published as a number**, never a date inside the covered range with a value. Full table, dates in
latest and absent from `output_type=4` (all before its first date): GDPC1 179, CPIAUCSL 306, UNRATE 146, HOUST 18,
CSUSHPINSA 477, DRCCLACBS 81, MORTGAGE30US 2,047, DTWEXBGS 3,415, SOFR 258, BAMLH0A0HYM2 1, BAMLC0A0CM 1, IORB 1,
DGS10 11,344, DGS2 7,584, T10Y2Y 9,824, T10YIE 2,887, DFII10 722; VIXCLS 5,481 before its first vintage plus **32
holiday `"."` dates inside the range**. In the other direction: dates in `output_type=4` and not in latest: 0 for all
17. This is the "complete costume" the backfill must not wear; the design (step 3 plan, part A) compares against
the latest values and refuses on any unaccounted date.

**3. Real response shape against the documentation-derived fixtures: they agree, with differences worth knowing.**
The real success bodies (`ok_first_prints_DRCCLACBS.json`, `ok_latest_DGS10.json`, unmodified) parse through the
client unchanged. Top-level keys: `realtime_start`, `realtime_end`, `observation_start` (`1600-01-01`),
`observation_end`, `units`, `output_type`, `file_type`, `order_by`, `sort_order`, `count`, `offset`, `limit`,
`observations`. Observation keys: `realtime_start`, `realtime_end`, `date`, `value` (value always a string; `"."`
a gap; no other non-numeric value in any series). Differences: (a) on a first print `realtime_end` is **where the
value's interval ended** (GDPC1: first print 1992-01-29, `realtime_end` 1992-02-27), not `9999-12-31`; what it
means precisely for a never-revised value was not checked;
the client ignores it, as it should; (b) `latest` rows carry `realtime_start = realtime_end =` today; (c) **IORB**
first prints have `realtime_start` **1 to 6 days before their own `date`** on every row (a rate announced ahead of
its effective date), which is legitimate and means `available_at < effective_at` is a valid state; migration 0005
has no CHECK against it, and none may be added; (d) no rate-limit header and no `Retry-After` on any 200, and
`Cache-Control: no-cache`; (e) latency 0.24 to 1 s per request, one 4.2 s outlier (DTWEXBGS).

**4. Real error bodies (HTTP status is 400 for all of them, none 404).** Saved in `tests/fixtures/fred/`:
vintage cap exceeded (above); `output_type=4` on the default window: `No vintage dates exist for the specified
real-time period: 2026-11-12 to 2026-11-12.`; an unknown series: `The series does not exist.` (**HTTP 400, not 404**);
`limit=100001`: `Variable limit is not between 1 and 100000.` The client maps each to `permanent`; the new cap
message gets its own text ("split the window") so a log line says what to do. 401, 403, 404, 423, 429 and 5xx are
still unobserved.

**Method.** Measured 2026-11-12 against `api.stlouisfed.org/fred`; a single session, one key, one network. Counts
will drift as vintages accrue (every number above is today's). The 2000 limit is the message's own figure, stated for
this file type; whether it differs for other output types was not measured.

### M2 slice 5, step 3 plan (2026-11-12) — plan only, no code; windowing settled by step 2c

**Scope:** make macro_metrics fill and stay current, and make a long ingest task something the health model can
tell from a wedged one. Five parts; only A's windowing waits on the measurement.

**A. Backfill (first prints).** `ingest.fred_backfill`, started by hand (`celery call`), never by Beat. Per series,
`FredClient.first_prints` then idempotent insert of `revision = 0` rows with `ON CONFLICT DO NOTHING`;
`available_at` = 00:00 America/New_York on the day after `realtime_start` (ADR-0003 addendum), computed by one
pure, tested function. `rows_written` counts rows created, so a re-run writes 0. Progress in `backfill_runs`
(`total_units` = series, `current_unit` = the series in flight), so a killed run resumes at the first series not
complete. Runs on the `ingest` queue under the heartbeat (part C).
- **WINDOWING (settled by step 2c's measurement, 2026-11-12): chunk every series, uniformly.** FRED refuses a
  window holding more than 2000 vintage dates with an explicit 400, so no series is requested in one piece even
  when today it would fit (SOFR has 1,902 vintage dates and crosses the cap within months; a "chunk only if big"
  rule would break on a date nobody is watching). Per series: `series/vintagedates` over the widened window, split
  the sorted dates into runs of **at most 1,900** (a margin under 2000, which the error text counts one differently
  from `vintagedates`, see 2c), and request `output_type=4` with `realtime_start` = the run's first date and
  `realtime_end` = its last. Measured on all six capped series: every window answered 200, `count` equalled the rows
  returned, **no observation date appeared in two windows**, and **nothing inside the covered range was missing**
  except the VIXCLS holiday gaps below. Windows are disjoint and cover the vintage history, so the union is the
  answer. `FredClient.first_prints` becomes `first_prints_window(series_id, start, end)` plus the chunker in
  `app/ingest/macro.py` (pure, tested on the six real vintage-date counts).
- **The completeness check the backfill must run (the "missing data wearing a complete costume" case).** The cap
  is *not* silent, but two other things are, and both are found only by comparing against the latest values
  (`output_type=1`, one request per series): after assembling the union, every date in the latest series must be
  accounted for. A date is *accounted for* if (a) it is in the union; (b) it is earlier than the earliest date in the
  union (**pre-vintage history**, below); or (c) its latest value is `"."` (**a gap never published as a number**,
  below). Anything else is a **refusal**: the backfill writes nothing for that series and fails with a
  `schema_mismatch`-kind error naming the count, never a partial series. A date in the union and not in latest also
  refuses (never observed: 0 across all 17).
- **Pre-vintage history is not available through `output_type=4`, and FRED does not say so. DECISION FOR KYLE, not
  taken here.** `output_type=4` returns observations only from the series' first stored vintage onward: GDPC1 from
  1991-10 (179 of 319 observations absent, 1947 to 1991), CPIAUCSL 306 of 958, DGS10 11,344 of 16,920, MORTGAGE30US
  2,047 of 2,902. No error, no count, no flag. Those observations exist in the latest values and have **no
  publication date**, so they cannot be stored as first prints with an honest `available_at` (any date would be
  invented, and the point-in-time filter would then show a value as known when nobody can say it was). Options:
  (A) leave them out of `macro_metrics` and document that point-in-time reads start at each series' first vintage
  (recommended: it keeps the table's one promise); (B) store them with `available_at` = the series' first vintage
  date and a marker, which needs a column the table lacks and makes the filter lie in a documented way; (C) keep
  them in a separate non-point-in-time table for features that need long history (z-scores over decades) and never
  read it for replay. Check §7 for which modules need history before the first vintage before choosing.
- **`"."` that was never a number is absent from `output_type=4`.** VIXCLS has 32 market-holiday dates in range
  (e.g. 2010-11-25) that are `"."` in the latest values and appear in no vintage; the BAML and DTWEXBGS gaps that
  *do* appear in `output_type=4` are different (why FRED reports those and not these was not determined). Consequence for the
  poll (part B): on its first run it will see those dates in the latest values with no stored row and, by the
  rule "a period never seen is a new revision 0 at the poll instant", insert them as NULL with `available_at` =
  that day. That is honest (nobody knew it earlier) but writes a burst of gap rows; I propose the poll skips a
  never-seen period whose value is `"."` and counts it in a log field, to be confirmed.
- **Bulk-load `realtime_start`: not present.** The feared case (every old observation sharing one bulk-load date) did
  not occur in any series: distinct `realtime_start` values are about one per vintage (the most common value covers
  at most 1.6 percent of rows), and no first print is dated before its own period except IORB (below). The
  first-print dates in the covered range are real publication dates.

**B. Daily poll.** `ingest.fred_poll`, Beat crontab 08:00 America/New_York (6.4/11), one `run_feed` per series
(`provider='fred'`, `feed=<series id>`), sequential, so each series has its own `ingestion_runs` row, its own
`providers_health` row and its own staleness (ADR-0003 decision 5). Each fetches `latest` (the whole current series,
one request: revisions land on old periods, PAYEMS months back and CPI seasonal factors years back, so a "recent
window" would miss them) and diffs against the newest stored revision of every period:
- *unchanged* writes nothing; *changed* (including value to NULL and NULL to value) appends `revision + 1` with
  `available_at` = the poll instant (injected clock) and `ingested_at` the same instant;
- a period never seen before is a new `revision = 0` whose `available_at` is the poll instant too (it can only be
  new now); a stored period absent from the poll is logged with a count and left alone, never deleted
  (triggers forbid it);
- **the poll refuses a series with no stored baseline** (a `permanent`-kind error naming the backfill). Without this
  guard a poll on an empty series would write the whole history with `available_at` = today, which silently destroys
  point-in-time history. This guard is the one thing here with no precedent in the spec.
- Two writers on one series (a poll during a backfill): a Postgres advisory transaction lock keyed on the series.

**C. The `heartbeat_at` liveness check and the running-row sweep** (decided 2026-11-11; replaces queue priority;
reading confirmed by Kyle 2026-11-12: canary at 3 intervals for condition 1, any running row with a fresh
`heartbeat_at` for condition 2).
- **One definition, in one place.** `app/tasks/liveness.py` holds two constants and one function, and the health
  probe and the sweep both import them; neither restates a number.
  `HEARTBEAT_INTERVAL_SECONDS = 30` (how often a running task writes) and `HEARTBEAT_FRESH_SECONDS = 120` (four
  intervals: three consecutive beats may be lost to a slow commit or a GC pause before a live task looks dead).
  `is_fresh(heartbeat_at, now) -> bool` is `now - heartbeat_at <= HEARTBEAT_FRESH_SECONDS`, and a NULL heartbeat is
  never fresh. A test pins `HEARTBEAT_FRESH_SECONDS >= 3 * HEARTBEAT_INTERVAL_SECONDS` so the two cannot drift
  into a state where a healthy task is swept.
- **What writes it, and when.** `run_recorded` (the one wrapper every task goes through) writes `heartbeat_at` at
  start (as it already does), at completion (already), **and every 30 s in between from a background thread with
  its own short DB connection**, so no task body has to remember to. This matters for the backfill: it is the first
  task long enough to need it, and a beat only at start and end would look dead for its whole run. The thread stops
  when the body returns or raises. **What a beat proves:** the worker process is alive and can reach Postgres. It
  does *not* prove the body is making progress (a body stuck in a call keeps beating). That case is bounded by
  `httpx2`'s 30 s timeout per request and, as the last resort, `task_time_limit` (`INGEST_TASK_MAX_SECONDS`), which
  kills the process and so stops the beats. Backfill progress is a separate signal, `backfill_runs.current_unit`.
- `checks.ingest_worker` is `ok` if the ingest canary succeeded within 3 canary intervals **or** a `task_runs` row
  is `running` with a fresh heartbeat (new status `busy`, which does not degrade); `stale` only if neither.
  Consequence: the ingest canary's threshold no longer needs to be sized to the longest task
  (`INGEST_TASK_MAX_SECONDS` + 3 intervals), so it reverts to 3 intervals and a wedged ingest worker shows in
  minutes, not half an hour. `INGEST_TASK_MAX_SECONDS` stays as the hard `task_time_limit`. This removes step 0's
  documented false-alarm gap and its slow detection together.
- **Sweep, one rule for both tables (Kyle, 2026-11-12).** `ops.sweep` on the **ops** queue (a saturated ingest queue
  cannot starve it), every 2 minutes. The only test of liveness is `is_fresh` on a `task_runs.heartbeat_at`:
  - a `task_runs` row that is `running` and not fresh becomes `failed`, `error_class = 'Orphaned'`;
  - an `ingestion_runs` row that is `running` and whose `task_run_id` does **not** point at a `task_runs` row that is
    both `running` and fresh becomes `failed`, `error = 'Orphaned'` (same class name). A NULL `task_run_id` fails that
    test too, so it needs no special case: the rule is "not linked to a live task".
  - Order within one sweep: `task_runs` first, then `ingestion_runs`, in one transaction, so the two tables cannot
    disagree about the same run for even one sweep.
  - **Migration `0006` adds `ingestion_runs.task_run_id BIGINT NULL REFERENCES task_runs(id) ON DELETE SET NULL`**
    (the macro_metrics precedent for ingestion_run_id). Nullable because rows already written (fake-provider runs,
    smokes) have no task, and **they cannot be backfilled** (there is nothing to link them to); that is the reason to
    do it now, before real FRED rows exist. No row written after the migration may have NULL: `run_recorded` sets a
    context variable holding its row id, and `run_feed` reads it and raises (a `permanent`-kind configuration error)
    if it is unset, so a feed cannot be run outside a recorded task by accident. The earlier "second rule"
    (`INGEST_TASK_MAX_SECONDS` + 1 minute) is deleted. No index on `task_run_id` (the table is small and the sweep
    reads only `running` rows); revisit if it is ever slow.
  - **`backfill_runs` is deliberately excluded from the sweep (Kyle, 2026-11-12). A reasoned exclusion, not a TODO.**
    A resumable backfill spans several task runs by design: M2 slice 10's gate is a worker restart mid-run, after
    which a new task picks the same `backfill_runs` row up. One `task_run_id` FK cannot express "the runs that
    worked on this", and a row that is `running` with no live task between a crash and the resume is **correct, not
    orphaned**: the work is unfinished and waiting to be resumed. So the question the rule must answer there is not
    "is the linked task alive" but "has anything picked this up recently", and that depends on the resume design
    (who resumes, after how long, what a stale-and-unclaimed row means), which is decided at slice 10. Sweeping it
    now with the task rule would mark every crash-then-resume `failed`, which is wrong. `backfill_runs` gets no
    column in `0006`. Step 3's backfill reads and writes its progress row as planned and takes no part in the sweep.
    **Where this is recorded when built:** the docstring of `app/tasks/sweep.py` (what the sweep covers: `task_runs`
    and `ingestion_runs`; what it does not: `backfill_runs`, with this reason) and the sweep section of
    `docs/HEALTH.md`, so a reader of either sees an exclusion with its reason rather than a gap. A test pins that the
    sweep leaves a `running` `backfill_runs` row with no live task untouched.
- **A swept row whose task completes later (DECIDED, Kyle asked for a decision, not whatever the code does).** This is
  reachable: the heartbeat thread can stall (a lost DB connection, a long GC pause, the laptop sleeping) while the
  body carries on, and the sweep fails the row; the body then finishes. Today's code would silently overwrite it:
  `run_recorded`'s completion and failure writes are `UPDATE ... WHERE id = :id` with no status condition
  (`app/tasks/runs.py`). **Decision: the sweep's verdict stands, and the late result is recorded as a note, never as
  a status.**
  - Every completion or failure write becomes `UPDATE ... WHERE id = :id AND status = 'running'`, for `task_runs`
    **and** `ingestion_runs` (`run_feed`'s final write). If it matches zero rows the row was swept: the status stays
    `failed`/`Orphaned`, `error_message` is extended with `completed after sweep at <ts>, rows_written=<n>` (or
    `failed after sweep at <ts>: <ExcClass>`), and a `task_completed_after_sweep` warning is logged.
  - *Why not overwrite with a note:* for the stretch between the last beat and the completion nothing was heard from
    the worker, which is itself the incident (a stalled heartbeat, a sleeping machine); turning the row `succeeded`
    erases that, and `/health/ready` and the page for a real stall would depend on whether a task happened to finish
    afterwards. *Why not refuse silently:* the work did happen and its rows exist (the inserts are idempotent and the
    triggers keep them valid), so a row that says only "Orphaned" would mislead the next reader into re-running the
    backfill; the note carries the fact. The cost is one `failed` row per such event, which is what an operator
    wants to see.
  - Atomicity: the sweep's own `UPDATE` carries `AND status = 'running'` and the freshness condition in the same
    statement, so a sweep and a completion that race are serialized by the row lock: whichever commits first wins and
    the other takes the zero-rows path above. The sweep re-checks freshness in the `UPDATE`, so a beat that lands
    between the sweep's read and write saves the row.
- **The heartbeat thread is torn down on every exit path (design; verified by tests when built).** The pattern is one
  `try/finally` that encloses everything after the thread starts, so it cannot be skipped by a path someone adds
  later: `stop.set()`, then `thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS)`, then a warning if it is still alive.
  Covered: body returns; body raises `Exception`; body raises `BaseException` (`CancelledError`, `SystemExit`,
  `KeyboardInterrupt`); the **final status write itself raises** (it runs inside the `try`, so the `finally` still
  stops the thread); and `SoftTimeLimitExceeded` (an `Exception` subclass, so the same path as a failure). The thread
  is a daemon, so a path the `finally` cannot reach (the hard time limit `SIGKILL`, `kill -9`, power loss) ends it
  with the process, which is the intended meaning: no process, no beats. The thread never raises into the body: its
  DB write is wrapped, logs and keeps trying on the next tick, and a failing heartbeat only makes the row go
  not-fresh. It also exits on its own if its row is no longer `running` (checked in the same `UPDATE ... WHERE
  status = 'running'`, zero rows means stop), so a swept task's thread does not keep writing to a failed row.
  Tests: a thread-count assertion after each exit path above (no `heartbeat` thread alive), a body that raises
  `SystemExit`, a final write that raises, and a thread whose row has been swept stops beating.
- Tests (liveness): `is_fresh` at the boundary and for NULL; the constants relation; a task longer than 3 intervals
  keeps `heartbeat_at` advancing (injected clock and a short interval); a killed task's beats stop and the sweep fails
  it after `HEARTBEAT_FRESH_SECONDS`, not before; a live long task is never swept; an `ingestion_runs` row linked to a
  live task is never swept and one linked to a dead (or NULL) task is; a completion after the sweep leaves `failed` and
  adds the note (for both tables); `ingest_worker` returns `busy` with a stale canary and a fresh running row, and
  `stale` with neither.

**D. Wiring.** `FRED_API_KEY` and `FRED_USER_AGENT` to the `worker` service only (never `x-api-env`, which `api` and
`api-public` share); the key passed to `run_feed(secrets=[...])`; `settings.FRED_SERIES` (the 17 of 6.4, config, not
code, per 6.4's "adding a tile = one row") and per-series `FEED_STALE_AFTER` for the poll's health. A
`FRED_BASE_URL` override (default the real API) so the smoke can run the whole path on the real stack against a local
stub FRED.

**E. Verification.** Tests: the available_at function (DST edges included); the store (first run writes N, identical
re-run writes 0, a changed value appends revision 1 and keeps revision 0, gap to value and value to gap, the baseline
guard, the advisory lock, the foreign key, inserts passing the immutability triggers); the heartbeat liveness
statuses; the sweep; the feed bodies against fixtures. `infra/smoke-m2-slice5-step3.sh`: the real stack, a stub FRED,
a backfill then a poll, a revision injected in the stub and seen as `revision = 1`, a killed worker mid-backfill
swept to `failed`, `ready` staying `ok` while a long backfill runs (the step 0 scenario, now without the false-alarm
gap).

**Files.** New: `app/ingest/macro.py` (pure rules), `app/services/macro_store.py`, `app/ingest/fred_feed.py`,
`app/tasks/fred_ingest.py`, `app/tasks/sweep.py`, `tests/test_macro_store.py`, `tests/test_fred_feed.py`,
`tests/test_heartbeat_liveness.py`, `tests/test_sweep.py`, `infra/smoke-m2-slice5-step3.sh`. New also: `app/tasks/liveness.py`, `alembic/versions/0006_ingestion_runs_task_run_id.py`. Changed:
`app/tasks/runs.py`, `app/ingest/runner.py`, `app/models/ops.py`, `app/tasks/names.py`, `app/celery_app.py`, `app/settings.py`, `app/services/health.py`,
`app/services/health_probes.py`, `docker-compose.yml`, `.env.example`, `docs/HEALTH.md`. **One migration, `0006`:** `ingestion_runs.task_run_id` (part C). `macro_metrics`
and `backfill_runs` already exist.

**What step 3 needs from Kyle, in order.** (1) A FRED key in `.env` so the probe can run; windowing waits on it.
(2) The rate-limit sentence read on FRED's page and the `fred` budget entry added: unit tests do not need it, but
neither the smoke (the runner refuses a provider with no budget) nor any real run can proceed without it.
(3) A yes or a correction on the heartbeat reading in C.

### M2 slice 5: FRED API Version 2 checked against ADR-0003 (2026-11-12)

Asked: does v2 expose vintages, and is v1 on a deprecation path? Read on 2026-11-12: the v1 index
(`fred.stlouisfed.org/docs/api/fred/`), the v2 index (`.../fred/v2/index.html`), the one v2 endpoint page
(`.../v2/release_observations.html`) and FRED's announcement "FRED Launches New Version of API" (4 November 2025,
`fred.stlouisfed.org/news/2025/11/fred-launches-new-version-of-api`). **Caveat on method:** the fetch tool summarized
each page rather than returning it verbatim, so the quotes below are the tool's, and a statement that a page "does
not mention" something is the tool's reading of the page, not a text search I ran.

- **v2 has no vintage support.** It has exactly one endpoint, `fred/v2/release/observations`, "the observations for
  all series on a release". Its request parameters are `release_id`, `format`, `limit` (1 to 500000) and
  `next_cursor`. There is **no `realtime_start`, `realtime_end`, `output_type`, `vintage_dates` or as-of parameter**,
  and observations carry only `date` and `value`. It is a current-values bulk download paged by cursor. The
  ADR-0003 mechanism (`output_type=4` over a widened real-time window) exists only in v1.
- **Nothing says v1 is deprecated.** The v1 index still describes v1 as retrieving data from "FRED and ALFRED", lists
  the vintage-dates endpoint, and says nothing about retirement; neither the v2 pages nor the announcement mention
  v1, deprecation or a timeline. v2 is presented as an addition ("ideal for ... observations for all series on a
  release in bulk"). **Absence of a notice is not a promise**: nothing here dates v1's life.
- **Plan impact: none to the design.** Series-level vintage retrieval is v1 and stays v1. v2 is a possible later
  optimisation for the daily poll (one request per release instead of one per series) but cannot replace v1 for the
  backfill, and the poll needs the *latest* values only, so it would be a legitimate use; not adopted, because 17
  series at one request each is already cheap and v2's own caveat (a response can mix updated and non-updated series
  during a release update; check `last_updated` and reprocess) adds a failure mode v1 does not have.
- **The risk worth carrying to M6 is on the record, not solved:** if FRED ever retires v1, vintage retrieval via
  the API goes with it. The mitigation already in place is that the vintages are *stored* (`macro_metrics`, immutable,
  backed up), so a retirement would stop new history from being fetched but would not erase what is held.
  **Review trigger:** re-read the FRED announcements page (`fred.stlouisfed.org/news/`) for any v1 notice at each
  FRED-client change and at M6 planning; the probe's run with a real key is a second check, since it exercises v1 as it
  is today.

### M2 slice 5, step 3, part A built: the FRED backfill (2026-11-12)

Built to the plan's part A and the decisions Kyle took on step 2c's findings. Parts B (daily poll), C (heartbeat and
sweep), D's remaining wiring and E's smoke are **not** built; this is one reviewable piece.

- **New:** `app/ingest/macro.py` (pure: `available_at_for`, `chunk_vintage_dates`, `account_for`, `FredSeries`),
  `app/services/macro_store.py` (the only writer of `macro_metrics`), `app/ingest/fred_feed.py` (the per-series
  body), `app/tasks/fred_ingest.py` (`ingest.fred_backfill`, started by hand). **Changed:** `app/ingest/fred.py`
  (`vintage_dates`, `first_prints_window`, `first_prints_chunked`; the rate hook is now per request, not per page),
  `app/settings.py` (`FRED_SERIES`, `fred_base_url`), the `worker` service env (`FRED_API_KEY`, `FRED_USER_AGENT`,
  `FRED_BASE_URL`; never `x-api-env`), `.env.example`, migration 0005's comment (no schema change), ADR-0003.
- **What it does per series:** `series/vintagedates`, windows of at most 1,900 vintage dates, `output_type=4` per
  window, the latest values, then `account_for`: a real value with no first print, or a first print the latest
  values lack, **refuses** and writes nothing. Otherwise first prints go in as `revision = 0` in one transaction
  under a per-series advisory lock, skipping rows already stored; a stored row that disagrees with FRED's first
  print refuses (nothing is overwritten). Pre-vintage periods and unpublished `"."` dates are skipped and logged
  (`fred_backfill_series`: `pre_vintage_excluded`, `gaps_without_trace_skipped`).
- **Decisions made while building, each reversible:** (1) **a mismatch between a series' configured and observed
  `first_covered_period` warns and does not refuse** (`fred_vintage_start_changed`): the data is still fully accounted
  for, and refusing would block on a benign change. (2) **Resuming is re-running**: every write is idempotent
  (`rows_written` is 0 on a repeat), so a killed backfill is finished by calling the task again; `backfill_runs`
  records progress (`total_units`, `completed_units`, `current_unit`, status) for the operator. Skipping completed
  series waits for slice 10's resume design. (3) **A failed series does not stop the others**; the task raises at the
  end naming the failed series, and `backfill_runs.status` is `failed`.
- **Not done, on purpose:** no heartbeat yet (part C), so a backfill of this length still relies on the ingest
  canary's long threshold; no stub-FRED smoke yet (part E).


**Verified on the real stack against the real API (2026-11-12, dev database).** `celery call ingest.fred_backfill` on the
`worker` service: all 17 series succeeded in 120 s (about 2 requests a second in total, under the 1 a second budget per
slot; no 429, no retry). **38,294 rows in `macro_metrics` across 17 metrics**, 17 `ingestion_runs` rows `succeeded`
with `rows_written` summing to 38,294, one `backfill_runs` row `succeeded` 17 of 17. Every `pre_vintage_excluded` count
equals the probe's (DGS10 11,344, GDPC1-style gaps as measured), VIXCLS skipped its 32 unpublished holiday dates, and
**no `fred_vintage_start_changed` warning**, so the configured `first_covered_period` of all 17 series matches what FRED
returned. 1,240 stored rows are NULL (gaps FRED did publish in a vintage); 636 rows have `available_at < effective_at`
(IORB, as predicted). **A second run created 0 rows in all 17 series** (`rows_read` 38,294, `rows_written` 0) in 81 s.
The vintage store is therefore populated in the dev database; it is not in any backup yet.
Tests: 484 pass, lint and mypy clean in the 3.12 image; two mutations (an accounting that never reports an unaccounted
value, and a chunk size with no margin) each turned 5 tests red and were reverted.

### M2 slice 5, step 3: why ADR-0003 dates revisions by the poll, and whether FRED can give the true date (2026-11-12)

Asked before the poll is built: was the poll date for revisions chosen for cost? **The ADR does not say so.** Decision 3
never mentions request cost. Its reasoning is: `output_type=4` is first-published only, so it "is not the mechanism
for capturing later revisions"; therefore ongoing ingestion polls, diffs against the last stored value, and dates a
change by the poll on which it was first seen, "accurate to within one polling interval", defended as "conservative
in the safe direction". What it **assumes without testing** is that no cheap way exists to get a revision's true
publication date. ADR-0003's authors tested `output_type=4` only (M0 spikes 3 and 3b).

**That assumption does not hold (measured 2026-11-12, 6 requests).** `output_type=3` ("new and revised observations
only") over the widened window returns a series' whole revision history with each vintage's true date, in one request
per series, in a **wide** format: each row is `{"date": ..., "<SERIES>_<YYYYMMDD>": value, ...}` with one key per
vintage in which that period changed, not the long `realtime_start`/`value` rows of `output_type=4`. GDPC1: 319 rows,
for 1947-01-01 fourteen vintages from 1992-12-22 to 2023-09-28. The same 2000-vintage-date cap applies. A short window
works for a poll: `realtime_start=2026-10-01` on GDPC1 returned the single new row (`2026-07-01`, vintage `20261029`);
DGS10 over `2026-11-02..2026-11-12` returned 7 rows. **A window containing no vintage date answers HTTP 400 "No vintage
dates exist"** (ADR decision 4's error), which for a poll is a normal quiet day and not a configuration bug, so a poll
using this must not treat it as one.

What this changes if adopted: a revision's `available_at` could be `available_at_for(true vintage date)`, the same
convention as a first print, so a rebuild from FRED reproduces the table exactly and the "late by up to one polling
interval" bias disappears; and the backfill could capture the **whole revision history**, not only first prints (the
ADR's "initial history only" rests on the same assumption). What it does not change: periods before a series' first
vintage still have no first-print date. Not yet checked: that `output_type=3` values agree with `output_type=4` first
prints for every series, and what the first vintage column holds for a period whose first print predates it.
**No code changed; the poll is not built; waiting on Kyle's decision.**

### M2 slice 5, step 3: Option 3 chosen; the two unchecked points measured; ADR-0003's rationale falsified (2026-11-13)

**Kyle's decision: Option 3.** The backfill stores the whole revision history and the poll uses the true vintage dates.
Reason on record: with the poll fixed and the backfill unchanged, a replay between a first print and today reads the
original value and misses every revision published in between, so the system would answer "what was first said" when
the question is "what was then believed."

**The two unchecked points, measured** (real API, `output_type=3` against `output_type=4`, windows of about 100
vintage dates; the probe is a scratch script and is not in the repo, like `probe_fred.py`):
- *Do the two outputs agree on first prints?* **Yes: 0 differences in date or value in all 17 series.** Eleven were
  compared in full; the six long dailies (DGS10, DGS2, T10Y2Y, VIXCLS, DFII10, T10YIE) over three windows each
  (first, middle, last), not in full. For the eleven, the last column of every chain equals the latest value (0
  differences) and no chain has two equal consecutive values.
- *What does the first column hold for a period whose first print predates the vintage record?* A revision's date,
  never the first print and never the series' first vintage. Such periods appear in `output_type=3` only if revised
  after the record began (CPIAUCSL 306, UNRATE 119, HOUST 18, CSUSHPINSA 475, DRCCLACBS 81, MORTGAGE30US 266, DTWEXBGS
  3,415); all are earlier than `first_covered_period`, none inside it. Their chains start at an unobserved base.
  `"."` appears inside chains (DTWEXBGS has 211 periods whose only column is `"."`; CPIAUCSL has 247 mid-chain
  `"."` columns followed by a number), which decision 7 already covers as a stored NULL.
- **Not asked for, found, and it changes the plan: cost.** `output_type=3` takes about 0.16 s per vintage date in the
  window. 100 dates: 17 s; 300: 48 s; **600 or more, and the 1,900 the backfill uses now, get a 504 from FRED's gateway
  at exactly 60 s.** 34,244 vintage dates across the 17 series is about 90 minutes of server time. The client maps 504
  to `transient`, so today a too-large window would be retried until the budget ran out. A first attempt at the probe
  died on this (a 60 s read timeout), which is how it was found.

**Built (this commit):** the "No vintage dates exist" 400 is context-dependent. `NoVintageDates` (a `Permanent`
subclass; same kind, never retried) is raised by default, so the backfill is unchanged and fatal;
`FredClient.observations(..., no_vintages_is_empty=True)` turns it into an empty result for a poll, for vintage output
types only, and no other 400 is affected. Five tests (fatal by default, windowed request still fatal without the flag,
quiet day with the flag and exactly one request, a bad key or unknown series is still a failure with the flag, the
flag is refused for `output_type=1`). Ruff, format and mypy clean; 110 pass, 8 DB-gated tests skipped. **Run on the
host with a throwaway venv, not in the pinned container: Docker Desktop was not running. Re-run `ci-checks.sh lint`
and `test` when it is.** HEALTH.md says what each caller sees.

**Recorded:** ADR-0003, Addendum 2026-11-13. It sets out how an ADR is falsified (assumption quoted, what the evidence
covered, the measurement, what survives, inline markers, the Status line), applies it as F1, and replaces the backfill
refusal check with six per-period rules (first-print agreement; a chain starts at a first print or is pre-vintage; a
chain ends at the latest value; every latest value explained; chain structure; stored history is a prefix of fetched).
It also states what no rule can see: a revision missing from the middle of a chain.

**Decisions made while writing it, both reversible, for Kyle to confirm or reverse:**
1. **Pre-vintage periods stay out even when revised.** *Approved by Kyle 2026-11-13, with a correction to how it is
   recorded:* it is **two rules, not one**. A, "no vintage exists" (no publication date; not recoverable; counts
   `no_vintage_excluded` and `gaps_without_trace_skipped`), and B, "revised but pre-vintage" (every revision has a
   real date; the chain has no beginning; **recoverable data we choose not to model**; count
   `revised_pre_vintage_excluded`). B must not inherit A's reason. ADR-0003 addendum, "Two exclusions, two rules, two
   counts".
2. **`output_type=4` stays in the backfill** as the independent witness that a chain begins at a real first print.

**Not built, needs a plan (CLAUDE.md: more than one file):** the wide-format parser; the chain-aware store (revision
numbers, prefix check); the windowed `output_type=3` fetch with 504 splitting (about 100 dates a window); the redesigned
check; the poll; a resume design for a 90-minute run (part C's heartbeat now matters). The 38,294 `revision = 0` rows
in the dev database stay valid. CLAUDE.md's M2 slice 5 notes need the same update when that lands.

### M2 slice 5, step 3: plan for the revision history, the skip, and the poll (2026-11-13). PLAN, NOT BUILT; waiting on Kyle

Decisions this plan rests on: Option 3; ADR-0003 addendum 2026-11-13 (decision 3 falsified, six-rule check, two
exclusions A and B kept apart); `output_type=4` stays as the witness. Nothing below is built.

**What the skip is, and is not.** The backfill **skips a series that is already fully captured**, so a re-run costs
only the incomplete series. **This is not resume.** A series that was interrupted is fetched again from its first
request; nothing resumes inside a series, and slice 10's resume design (what "picked up recently" means, orphaned
`backfill_runs`) stays untouched. It cannot be finer than a series anyway: rules 3 and 4 need the whole series, so a
series is committed all at once or not at all. "Fully captured" is a durable fact, not an inference: a row in the new
`series_history_state` whose `rule_version` equals the code's current one. A change to the six rules bumps the
version and every series is re-captured, on purpose. A `force` argument re-runs a named series.

**Files, in three reviewable commits.** Each ends with something checkable, and I stop after each.

*Commit A, no database.* The rules and the client, proven against stub transports and recorded real bodies.
- `backend/app/ingest/fred.py` (changed): wide-format parser (`FredVintageRow`: period plus `{vintage date: value}`);
  `revision_history(series_id)`: vintage dates, windows of `FRED_O3_WINDOW` (100) dates, **504 splitting** (halve the
  window and retry each half, down to one date; a single date that still 504s is raised as it is today, transient);
  **once a window has 504'd, the rest of that series uses the smaller size** so one slow day does not cost a 60 s
  failure per window. New `GatewayTimeout(Transient)` so the split can tell a 504 from other 5xx; nothing else
  changes for 5xx. **The read timeout for these requests is raised from 30 s to 70 s**: `TIMEOUT` is 30 s today and
  a 300-date window takes 48 s, so at 30 s the client would give up on requests FRED would have answered, and see a
  client timeout where it must see FRED's own 504 at 60 s. 70 s is above the gateway's 60.
- `backend/app/ingest/errors.py`: `GatewayTimeout`.
- `backend/app/ingest/macro.py` (changed, still pure): `build_chains` (union of windows, ordered, equal-neighbour
  skip counted), `check_history` implementing the **six rules** and returning a verdict with **separate** counts
  (`no_vintage_excluded`, `gaps_without_trace_skipped`, `revised_pre_vintage_excluded`, `duplicates_skipped`) and the
  refusal reasons; `FRED_RULE_VERSION`. `account_for` is retired, not kept beside it (two accountings would drift).
- `backend/app/settings.py`: `FRED_O3_WINDOW`, `FRED_READ_TIMEOUT`.
- Tests: `test_fred_history_parser.py`, `test_fred_window_split.py`, `test_macro_history_rules.py`. **One test per rule
  that goes red when that rule is removed** (the mutation habit of the last step), a test that A and B are never
  summed, and fixtures recorded from the real API (a small revised series such as DRCCLACBS, a pre-vintage-revised
  period, a `"."` column, a 400 and a 504 body) under `tests/fixtures/fred/`.

*Commit B, the database and the real run.*
- `backend/alembic/versions/0006_series_history_state.py` + model in `app/models/macro.py`: `series_history_state`
  (`series_id` PK, `rule_version`, `captured_through` date, `completed_at`, `ingestion_run_id` FK SET NULL, counts:
  `first_prints`, `revisions`, and the four exclusion counts as **separate columns**). **A table §8 does not have**,
  by the same reasoning as `providers_health.first_seen_at`: it is the durable "fully captured" fact and the poll's
  window start. `# DECISION` comment on it, not a silent addition. **Number: part C's planned `0006`
  (`ingestion_runs.task_run_id`) is unbuilt, so whichever lands first takes 0006.**
- `backend/app/services/macro_store.py` (changed): `insert_history` (revision numbers from chain position, one
  transaction, the per-series advisory lock kept, **rule 6**: stored rows must be a prefix of the fetched chain, else
  refuse and write nothing), `append_revisions` for the poll. `insert_first_prints` is replaced by `insert_history`
  (the 17 stored `revision = 0` rows are its first elements, so nothing is migrated).
- `backend/app/ingest/fred_feed.py` (changed): `history_body` replaces `backfill_body`: vintage dates, `output_type=4`
  chunked (as now), `output_type=3` windowed, latest last, the six rules, write, then upsert `series_history_state`
  **in the same transaction**, so "captured" can never be true without the rows.
- `backend/app/tasks/fred_ingest.py` (changed): **the skip** (a series whose state row has the current
  `rule_version` is not fetched; logged `fred_history_skipped`; `ingestion_runs` gets no row for a skip), the order
  (smallest vintage count first), and the task layout under the 30-minute question below.
- Tests: DB-backed (skipped without `DATABASE_URL`, run in the container): idempotent re-run writes 0; the skip
  fetches nothing for a captured series and everything for an incomplete one; a rule-version bump re-captures; a
  failing rule writes nothing and leaves no state row; prefix refusal; revision numbers; `available_at` of a revision
  is the vintage date's end of day in New York.
- **Real run on the dev database, timed per series and recorded here**, before Commit C. That run is the measurement
  the 30-minute question needs.

*Commit C, the poll.*
- `backend/app/ingest/fred_feed.py`: `poll_body`: per series, window `[captured_through, today]`, `output_type=3`
  with `no_vintages_is_empty=True` (a quiet day is a success with 0 rows), the same chain builder and the same rules
  applied to the periods that changed (`output_type=4` over the same window witnesses a new period's first print);
  appends revisions; advances `captured_through`. A series with no state row is **refused** (poll before backfill is
  an error, not a silent first print).
- `backend/app/tasks/fred_ingest.py` + `backend/app/celery_app.py`: `ingest.fred_poll` and its Beat entry (08:00
  America/New_York, §6.4); one task, short windows, minutes. A gap after a sleeping laptop is handled by the same
  windowed fetch.
- `docs/HEALTH.md`, `CLAUDE.md`, this log, `.env.example` (window and timeout settings). **Not in C:** part C's
  heartbeat/sweep and the stub-FRED smoke (E) stay separate steps, as before. Poll freshness thresholds per series
  (decision 5) belong with them and are named here so that they are not forgotten.

**How the 90 minutes meets the 30-minute task limit. UNRESOLVED; I need a decision, or a measurement first.**
`task_time_limit` is `INGEST_TASK_MAX_SECONDS`, default 1800, a **hard kill** (`celery_app.py`). Its own comment says
the number is a guess that "the FRED backfill ... could approach". It is now approached:
- *One task for all 17 series cannot work.* About 90 minutes of FRED time in total at 0.16 s per vintage date, plus
  one rate-budget slot (1 s) per window. Killed at 30 minutes, it leaves `task_runs`, `ingestion_runs` and
  `backfill_runs` rows `running` (the sweep is part C, unbuilt; `backfill_runs` is outside it by decision), and
  nothing says so.
- *Per series, most fit, with less margin than they look.* Estimated from 34,244 vintage dates: DGS10, DGS2 and DFII10
  (about 5,150 each) take about 15 minutes; T10YIE, T10Y2Y and VIXCLS (3,160 to 3,970) 9 to 11; SOFR about 5; the
  other ten under 4 each. **The largest is half the limit, on a rate measured at two points on one series at one
  time of day.** If FRED is twice as slow, DGS10 is killed at 30 minutes, *every time*, and because a series is
  all-or-nothing it can never complete. The skip does not help that case: it only skips the finished.
- *The limit is not free to raise.* The ingest canary's staleness threshold is built from it
  (`docs/HEALTH.md`: `INGEST_TASK_MAX_SECONDS` + 3 canary intervals), so a per-task limit above the global one
  invalidates the threshold's assumption, and raising the global one makes a wedged ingest worker take longer to show.
  Part C's heartbeat removes that dependence (the threshold reverts to 3 intervals) and is not built.

Options:
1. **(Proposed default) One Celery task per series, queued smallest first, the global limit unchanged.** The
   backfill task only enqueues; each series is its own task with its own `task_runs` row, so a kill costs one series
   and says which. With two ingest slots the wall time is about 45 minutes. A `soft_time_limit` of the limit minus
   60 s on these tasks lets the run record `failed` with a reason instead of being killed silently. The skip makes
   re-enqueueing the whole set safe. **This does not remove the 2x risk**, it makes it visible and per-series.
2. **A per-task limit for these tasks (for example 2 hours), after part C's heartbeat lands.** Fixes the 2x risk;
   needs part C first, and a decision that a wedged ingest worker may take that long to show until then.
3. **Do the real run in a scratch container with no limit** (it is a one-off, by hand, `celery call` not Beat).
   Zero code, and it is a one-time exception to a guard that exists for a reason.

My recommendation is 1, with the real run's per-series timings deciding whether 2 is needed before the poll goes to
Beat. If any series takes more than about 20 minutes, I stop and bring it to you with the numbers.

**Not covered by this plan:** mid-series resume; the sweep; the bar tables (slice 6); `IDEA-034`/`035` vocabulary.

### M2 slice 5, step 3: the 30-minute question decided: Option 3, as a first-class entry point (Kyle, 2026-11-13)

**Decision.** The history backfill is not a Celery task. It is `python -m app.backfill`, an ordinary entry point of the
backend image, run by hand in the `worker` service's container (`docker compose run --rm` or `exec`; the precedent is
`python -m app.seed` in the `migrate` service). It calls **the same `run_feed` per series**, so `ingestion_runs`,
`providers_health`, the rate budget (the same Redis spacing limiter), the retries, the credential scrubbing and the
provenance chain (`macro_metrics.ingestion_run_id`) behave exactly as under Celery. It is **not a scratch script**:
it is tested, documented in the runbook and part of the repo.

**Reasoning, on record.** The backfill is one-time plus occasional deliberate re-capture (a `rule_version` bump, a
`force` of one series). **The poll is the only thing that runs forever**, and its windows are short, so it fits the
task limit comfortably. Option 3 therefore **removes** the 90-minute question rather than managing it: the guard
(`task_time_limit` = `INGEST_TASK_MAX_SECONDS`, the hard kill the ingest canary's threshold is built from) stays
exactly as it is, and nothing long-running is ever put under it. The 2x-slower-FRED risk changes character: from "a
series is killed at 30 minutes every time and can never complete" to "the run takes three hours instead of ninety
minutes."

**Consequences for the plan** (the plan's Commit B changes; Commit A is unaffected):
- The Celery task `ingest.fred_backfill` and its `max_retries=0` task are **removed**, not kept beside the entry
  point: a task that cannot fit its limit should not exist to be called by mistake. The one-task-per-series fan-out,
  the soft limit and options 1 and 2 are dropped.
- `app/backfill.py` (new, with `if __name__ == "__main__"`), arguments: none (all series not yet captured at the
  current `rule_version`), `--series ID[,ID]` and `--force`. It writes `backfill_runs` as the task did. It does **not**
  go through `run_recorded` (that wrapper is the Celery task's `task_runs` row and heartbeat thread, and a process
  with no task limit does not need either); `ingestion_runs` rows therefore have a NULL `task_run_id`, which is
  already legal (part C's `task_run_id` column is nullable and the sweep treats NULL as "not linked to a live task").
  **That last point matters and is flagged for Kyle:** once part C's sweep exists, an `ingestion_runs` row with a NULL
  `task_run_id` that is still `running` is failed as `Orphaned` after the sweep interval, *including a healthy
  90-minute backfill's current series*. The entry point must either write its own heartbeat-fresh `task_runs` row or
  the sweep must know about it. Decided before part C, not after; recorded here so it cannot be missed.
- A killed or interrupted `python -m app.backfill` is finished by running it again: the skip bounds the cost to the
  incomplete series.

**Stop condition, unchanged.** The first real run is timed per series and recorded in this log. **Any series over
twenty minutes: stop and tell Kyle with the numbers**, before the poll goes near Beat.

### M2 slice 5, step 3, commit A built: the wide-format parser, the windowed fetch with 504 splitting, the six rules (2026-11-13)

No database, no migration, no Celery change. The current backfill still runs as it did; nothing here is wired into it.

- **`app/ingest/errors.py`:** `GatewayTimeout(Transient)`: a 504, same kind and still retryable.
- **`app/ingest/fred.py`:** `_parse_wide_page` and `FredVintageRow`; `FredClient.revision_history(series_id, window=100)`
  returning a `RevisionHistory` (vintage dates, rows, `splits`, `final_window`); `HISTORY_TIMEOUT` (read 70 s, above the
  gateway's 60 s; the default stays 30 s for every other request). A window that 504s is **halved from the same start,
  and the smaller size kept for the rest of the series**; a single date that still 504s is raised. Every attempt,
  timed-out ones included, takes a rate-budget slot. A "No vintage dates exist" 400 inside a window built from the
  series' own dates is `NoVintageDates` and fatal.
- **`app/ingest/macro.py` (pure):** `HISTORY_WINDOW = 100` (measured), `FRED_RULE_VERSION = 1`, `Revision`,
  `FirstPrintRef`, `Refusal`, `HistoryVerdict`, `check_history` with the six rules. **The counts are separate fields
  and no field adds them:** `no_vintage_excluded` and `gaps_without_trace_skipped` (A), `revised_pre_vintage_excluded`
  (B), `duplicates_skipped`.
- **Fixtures, live and unmodified (`tests/fixtures/fred/`, 2026-11-13):** `ok_revisions_DRCCLACBS` (142 periods, 62
  vintages), `ok_revisions_CSUSHPINSA_one_vintage_with_gaps` (515 rows, 144 of them "."), `error_gateway_timeout_o3_window`
  (a real 504, 60 s). No credential in any (checked).

**Real data corrected the parser (found by running the fixture, not by thinking).** `output_type=3` returns **bare
rows**, `{"date": ...}` with no vintage column, for a period that exists in the window but did not change in it (32 of
the 515 rows of the CSUSHPINSA fixture). The first parser rejected them as malformed. They are normal, carry no
information, and are now parsed as empty rows and ignored by the check. The ADR addendum's sentence "a period that was
never revised is in neither output" was corrected to say so.

**Deviations from the plan, each small:** (1) **`account_for` is not retired yet.** The current `backfill_body` still
calls it, so removing it here would break the working backfill; commit B replaces the body and deletes it in the same
change, so the two accountings never coexist beyond this commit. (2) **No `FRED_O3_WINDOW` / `FRED_READ_TIMEOUT`
settings:** the window is a property of FRED's gateway (a constant with its measurement beside it), and an operator
knob would invite setting it to the cap. (3) Rule 6 is in `check_history` as a pure `stored` argument; reading the
stored chain back from `macro_metrics` is commit B.

**Checks.** Ruff, format and mypy clean. **404 pass, 100 skipped** (DB-gated) across the suite; the 139 FRED/macro tests
all pass. Nine `test_banned_phrases.py` errors are a Windows `PermissionError` on pytest's temp directory on this host
and occur identically with these changes stashed; they were not caused by this commit. **Ran on the host in a throwaway
venv, not the pinned container (Docker Desktop was not running): re-run `ci-checks.sh lint` and `test` when it is.**
**Mutation check, ten mutations, each turned the suite red and was reverted:** each of rules 1, 2, 3, 4 and 6 removed;
rule 5's conflict check and its repeated-value skip removed separately; exclusion B not counted; A and B merged into
one count; bare rows not skipped.

### M2 slice 5, step 3, commit B built: the history store, the skip, and `python -m app.backfill` (2026-11-13)

**Decision recorded (Kyle, 2026-11-13): a manual backfill is a task execution and belongs in `task_runs` like any
other.** The entry point calls `run_recorded` (plain Python, not Celery-bound), so a backfill has a real ledger row
and the sweep's rule works on it unchanged: no exception, no special case, and the NULL-fails-naturally property
(`ingestion_runs.task_run_id` NULL is "not linked to a live task") survives. This supersedes the flag I raised after
commit A (that the entry point would need its own `task_runs` row or a sweep exemption): neither is needed.
**Two things this does not yet give, both part C and both already planned:** `run_recorded` writes its row at start and
at end only, and the 30-second heartbeat thread belongs to part C's change to `run_recorded`, which the entry point
inherits without changing; and `ingestion_runs.task_run_id` (the link from each series' run to this row) is part C's
migration. Until part C lands there is no sweep to apply the rule, so nothing is wrongly failed in the meantime.

- **New:** `app/backfill.py` (`python -m app.backfill [--series A,B] [--force]`; `backfill_all`, `execute`,
  `parse_args`); `alembic/versions/0006_series_history_state.py` and `SeriesHistoryState` in `app/models/macro.py`;
  `vintage_for` in `app/ingest/macro.py` (the inverse of `available_at_for`, tested across both clock-change days).
- **Rewritten:** `app/services/macro_store.py` (`insert_first_prints` is replaced by `commit_history`: it takes the
  per-series advisory lock, reads the stored chains, runs the judge, and only if every rule holds inserts each chain's
  missing tail and upserts the state row, all in one transaction), `app/ingest/fred_feed.py` (`history_body`:
  `output_type=3`, then `output_type=4` as the witness, then the latest values last, then the six rules).
- **Removed:** the Celery task `ingest.fred_backfill` (`app/tasks/fred_ingest.py`, its `celery_app` include),
  `account_for` and `Accounting` and their tests. The name `ingest.fred_backfill` survives only as the `task_runs`
  ledger name.
- **Migration 0006 is `series_history_state`**; part C's `task_run_id` migration is now `0007`. The table is a
  departure from section 8 with a `DECISION` comment in the migration. Its counts are separate columns
  (`no_vintage_excluded`, `gaps_without_trace_skipped`, `revised_pre_vintage_excluded`, `duplicates_skipped`): A is not B.
- **The skip is not resume, and the code says so.** A series is skipped only if its state row has the current
  `FRED_RULE_VERSION`; one that was interrupted is fetched again from its first request (a test pins that the first
  call is `vintagedates`). `--force` needs `--series`: re-capturing everything must be asked for by name.
- **One dropped item from the plan:** "smallest series first" existed to fit the 30-minute limit, which no longer
  applies; series run in configured order.
- **The slow-series warning:** `fred_history_series_over_twenty_minutes` is logged for any series over 20 minutes
  (the stop condition). It does not stop the process; the person watching the run does.

**Tests.** Run on the real compose Postgres (not skipped). The **full suite was 511 passed** (26 `test_banned_phrases`
tests deselected for the host-only Windows `PermissionError` on pytest's temp directory noted at commit A); ruff,
format and mypy clean. One test (the revision-number hole) was added after that run. It passes: the
"stored revisions re-inserted" mutation run below executed `test_fred_backfill.py` and `test_backfill_cli.py` in full,
unmutated in every other respect, and went green. **The full suite was not re-run after that addition**: the throwaway venv
under the scratch directory lost its `Scripts` folder and the host was reported low on memory, so rebuilding it was
left alone. Re-run `bash infra/ci-checks.sh lint` and `test` in the pinned container. New and rewritten: whole
history stored with revision numbers, `available_at` from FRED's vintage date and gaps stored as NULL; a re-run
writes 0 and a new revision is appended alone; the two exclusions stored apart; a failed rule writes nothing, not
even the state row; stored history that disagrees refuses (rule 6) and changes nothing; **the 38,294 old `revision = 0`
rows stay valid** (only revisions are added, `ingested_at` of the old rows untouched); a window that 504s is split and
the stored history is identical to the unsplit one; a stored chain with a hole in its revision numbers is refused;
skip, rule-version bump, `--force`, interrupted-restarts-from-the-first-request, continue-past-a-failed-series, and
the `task_runs` row (succeeded, and failed with `BackfillIncomplete`).
**Mutation check, ten mutations on the store, the skip and the ledger: nine turn the suite red** (refusal not raised;
state row not written; hole check removed; B written into A's column; the skip never skipping; the skip ignoring the
rule version; `--force` ignored; a skipped series not counted done; the `run_recorded` wrapper bypassed). **One stays
green and is an equivalent mutant:** re-inserting the already-stored revisions changes nothing observable, because
`ON CONFLICT DO NOTHING` and the primary key absorb them; the real guards there are the key, the immutability
triggers and rule 6. Not a test gap, and not hidden.

**The first real run on the dev database (started 2026-11-13 07:45 UTC; NOT finished, see below).**
- *Stop condition (any series over 20 minutes):* **not triggered.** Per-series wall time, `python -m app.backfill` against
  the real API with the real Redis budget: **DGS10 590 s (9.8 min), DGS2 435 s, T10Y2Y 269 s**, and the other eight finished
  series 3 to 13 s each (MORTGAGE30US 11.9, BAMLH0A0HYM2 12.3, BAMLC0A0CM 11.1, CPIAUCSL 10.5, UNRATE 13.0, HOUST 11.7,
  CSUSHPINSA 5.3, DRCCLACBS 3.3). **Eleven series, 22 minutes in all, no window split, no `fred_vintage_start_changed`
  warning, no rule refusal.** The largest is under half the 20-minute line.
- *The 90-minute estimate was wrong, and why.* It assumed 0.16 s per vintage date, measured on DGS10. The small series cost
  about 0.015 s per vintage date: the cost follows the **rows a window returns**, not the number of vintage dates, and a
  daily series returns far more rows per window. 34,244 vintage dates in total did not mean 90 minutes. The six series
  still to run (VIXCLS, DTWEXBGS, DFII10, T10YIE, SOFR, IORB) include two daily series of DGS10's size.
- *Cross-check of the two exclusions against the first backfill's single `pre_vintage_excluded` count.* A plus B equals it
  where it was recorded: DGS10 11,320 + 24 = 11,344; CPIAUCSL 0 + 306 = 306; UNRATE 27 + 119 = 146; HOUST 0 + 18 = 18. The
  split is new information the old count hid: UNRATE's 119 and CPIAUCSL's 306 are *revised* pre-vintage periods (B, recoverable),
  DGS10's 11,320 are unrevised (A, not recoverable). `macro_metrics` went from 38,294 to 50,766 rows with the 11 series
  done; the old `revision = 0` rows were extended, not rewritten (DGS10: 5 rows created, 4 of them revisions).
- *What went wrong, and it is operational, not a code bug.* The laptop **slept** during VIXCLS's first request (07:45 UTC
  start; VIXCLS began 08:07; the next event is a `transient` failure at 17:14, 9 hours later): the request hung until
  the machine woke and the connection died, then `run_feed` treated it as an ordinary transient failure and began its
  retry. That is the retry policy working as designed, and it is also a real hazard of a ~45-minute run on a laptop:
  **do not let the machine sleep during a backfill** (and a stale `task_runs.heartbeat_at` will say so once part C's
  heartbeat and sweep exist).
- *The run was then interrupted by the environment.* The harness stopped the `docker compose run` client because the system
  was low on memory. The container (`flowdesk-worker-run-...`) was still up at 17:15 UTC with 0% CPU and no new event after
  the failed attempt, so what it is doing is unknown; **I did not restart it, as instructed for a memory-pressure stop.** State
  in the database at that moment: **11 of 17 series captured** (`series_history_state` rows, rule version 1),
  `backfill_runs` 11 of 17 `running` (current unit VIXCLS), one `ingestion_runs` row (VIXCLS) `running`, one `task_runs`
  row `running`. Those three `running` rows will stay so until the process finishes or is stopped (the sweep is part C).
  Finishing is `python -m app.backfill` again: the skip means the eleven cost nothing. **Waiting on Kyle** to say when
  to start it (memory), and whether to stop the idle container first.
- *My monitor of the run was wrong once:* a `cut` in the pipe buffered the lines, so a watch that should have seen DGS10
  finish said nothing for 30 minutes. Fixed (line-buffered `grep` only); recorded because "no events" is not evidence that
  nothing happened.

#### The first real run, finished (2026-11-13 17:37 UTC). Corrects the paragraph above that said it was interrupted.

The run was **not** dead when the previous paragraph said so: the harness had stopped the `docker compose run` client,
but the container kept working, and it was in its retry backoff (0% CPU) when I looked. It finished on its own. I
misread an idle process as a stopped one; recorded because the earlier paragraph is wrong about it.

- **Outcome: 16 of 17 series captured; `backfill_runs` `failed` 16 of 17; the `task_runs` row `failed`
  (`BackfillIncomplete`); no `ingestion_runs` row left `running`.** `macro_metrics` holds 56,795 rows over 17 metrics. The
  16 captured series hold 34,173 first prints and 18,487 revisions. Exclusions across them, kept apart: A, never revised,
  34,404 (`no_vintage_excluded`) and 0 (`gaps_without_trace_skipped`); B, revised but pre-vintage, 4,708.
- **Stop condition (any series over 20 minutes): never triggered.** Slowest: DGS10 590 s, DFII10 537 s (two fast transient
  failures, then success on a later attempt), T10YIE 480 s, DGS2 435 s, T10Y2Y 269 s, SOFR 183 s; the rest under 20 s
  (DTWEXBGS 9.7, IORB 19.1). The whole run's compute was about 45 minutes; its wall time was 10 hours because the laptop
  slept during VIXCLS.
- **VIXCLS is the one failure, and it is a flaw in my rules, not in FRED or the code path.** Rule 2 refused it and wrote
  nothing (correct behaviour for the rule as written): 32 periods have an `output_type=3` chain and no `output_type=4`
  first print. They are the market-holiday dates the 2026-11-12 addendum called "in no vintage": **FRED added `"."` rows
  for them in one bulk change on the 2014-02-05 vintage** (checked for 2010-11-25 and 2010-12-24 against the whole record),
  so they have a real publication date. `output_type=4` does not report a first print whose value is `"."`. The 2026-11-12
  claim was true only of `output_type=4`. VIXCLS keeps the 4,135 `revision = 0` rows from the first backfill and has no
  `series_history_state` row.
- **Proposed, not made, waiting on Kyle:** accept a chain that starts with `"."` and has no `output_type=4` row as a gap
  first print (revision 0, NULL, dated by its vintage per ADR decision 7), counted apart as
  `gap_first_prints_unwitnessed`; a chain that starts with a number and lacks an `output_type=4` row still refuses. No
  `FRED_RULE_VERSION` bump, because the change only admits more and the 16 captured series already satisfy it. Principle to
  record with it: bump the version when a change makes the rules stricter or changes what is stored, not when it relaxes a
  refusal. A correction to the 2026-11-12 addendum goes in the same change.
- **Logging flaw to fix with it:** `fred_history_series_done` is emitted for a failed series too, so "VIXCLS done 251 s" was
  a refusal. It becomes an event carrying the outcome.
- **Remaining after that:** `python -m app.backfill --series VIXCLS` (the other 16 are skipped by the state rows, which is
  also the test of the skip on real data); the full suite and the container lint/test re-run; commit.

#### The VIXCLS amendment made, the sixteen checked from data, VIXCLS captured, container checks green (2026-11-13)

**Why this exists as a second commit.** It exists *because of* the first. The history backfill (commit 1) was run for real;
its rule 2 refused VIXCLS and wrote nothing, and that refusal is what exposed the unwitnessed gap first prints. **The
refusal was the safety net working, not a defect**: a series with a chain `output_type=4` cannot corroborate was held
back instead of being stored half-understood, and the amendment below was made from what the refusal showed. Commit 1
is the backfill as designed; this commit is what running it taught.

**A caveat about the workaround, not a repo defect.** The throwaway-venv checks used at commits A and B (ruff, format,
mypy, pytest on the host) are **not equivalent to `bash infra/ci-checks.sh`**. The container lints all of `backend/`,
including `alembic/`; the venv checks ran on `app` and `tests` only, which is why five over-long lines in two migration
docstrings survived until the container lint ran. "Clean" in the commit A and B entries above means clean for the venv's
scope. Use the container run as the gate; the venv is only a faster loop.

Kyle approved points 1, 2, 3 and 5 of the amendment; **point 4 (no version bump) was rejected as an argument and
settled from data instead**, as he directed.

- **Logging first.** `fred_history_series_done` fired for failed series too and misled twice. It is gone: a series now
  logs `fred_history_series_captured` or `fred_history_series_failed`, and a test pins that a failed series never emits
  the first (nor the old name).
- **Does the amendment change anything already stored? No, measured.** `gaps_without_trace_skipped` was **0 in all sixteen**
  captured `series_history_state` rows, and a chain that starts with an unwitnessed `"."` would have refused its series, so
  none is among the sixteen. Independently, FRED's latest values for **DGS10, DGS2 and T10Y2Y** (named as having holidays
  "exactly like VIXCLS") against the table: every `"."` inside the record is stored as a NULL period, **229 of 229, 229 of
  229, 138 of 138**; their holidays were reported by `output_type=4`, VIXCLS's were not. So skipping the bump is free
  and `FRED_RULE_VERSION` stays 1. **The principle is corrected rather than the data bent to it:** *bump when a change makes
  the rules stricter or changes what is stored; for a relaxation, first count whether any captured series would store
  differently, and bump if any would.* (`macro.py` comment, 0006's docstring and ADR-0003 F2 all say this now.)
- **Rename.** `gaps_without_trace_skipped` asserted "no trace of a publication exists"; that was false. It is now
  **`gaps_without_o4_first_print_skipped`** (a latest `"."` found in neither output), in the verdict, the logs, the model and
  the table (migration **0007**, a rename that keeps the sixteen rows). Old BUILD-LOG entries keep the old name, as history.
- **Rule 2 amended.** A chain at or after the earliest first print that begins with `"."` and has no `output_type=4` row is a
  **gap first print**: revision 0, NULL, dated by its vintage, held to rule 3, counted apart as
  **`gap_first_prints_without_o4_witness`** (new column, same migration). A chain that begins with a number and lacks an
  `output_type=4` row still refuses; a gap first print before the record is still exclusion B.
- **ADR-0003 corrected, mechanism followed (F2).** The 2026-11-12 claim that VIXCLS's 32 holiday dates "appear in no
  vintage ... no one published them" carries an inline `[Corrected ...]` marker, the Status line names F2, and F2 quotes the
  assumption, says what its evidence covered (`output_type=4` only; the record had noted the inconsistency unexplained),
  gives the measurement, and lists what survives and what is superseded. **Measured:** the 2014-02-05 vintage turned **93**
  periods into `"."` (32 inside VIXCLS's record, 61 before it): `output_type=3` shows all 93, `output_type=4` for the same
  vintage returns one row and none of them. New live fixtures: `ok_revisions_VIXCLS_holiday_gaps_2014-02-05`,
  `ok_first_prints_VIXCLS_2014-02-05`.
- **Sleep warning.** `python -m app.backfill` prints `KEEP THIS MACHINE AWAKE ...` to stderr before anything else and logs
  `fred_history_keep_awake`; a test pins it.
- **VIXCLS captured** (`python -m app.backfill`, nothing else named): the **sixteen were skipped with no request at all**
  (the skip, proven on real data), VIXCLS took **92 s**: 4,169 first prints, 10 revisions, **32 gap first prints**,
  exclusions A 5,257 + 0 and B 192, 44 rows created. The holidays are revision 0, NULL, available at 05:00 UTC on
  2014-02-06 (the end of 2014-02-05 in New York). **`backfill_runs` 17 of 17 `succeeded`; the `task_runs` row `succeeded`.
  `macro_metrics` holds 56,839 rows. The first real backfill is complete, all seventeen series.**
- **Tests.** Mutations on the amendment (gap-first branch removed; number-first also accepted; gap-first accepted before the
  record; unwitnessed count dropped; gap-first exempted from rule 3): the last was GREEN, which exposed a missing test (a gap
  first print whose period is absent from the latest values), added; all five are now red. Local run on the real Postgres:
  522 passed. **In the pinned container, `bash infra/ci-checks.sh lint` passes (ruff, format, mypy, locks, banned phrases)
  and `bash infra/ci-checks.sh test` passes: 549 passed.** (The container lint also covers `alembic/`, which my local check
  had skipped; it found five over-long migration docstring lines, fixed.) The 26 `test_banned_phrases` tests that errored
  only on this Windows host's temp directory pass in the container, so that error was host-only, as said.
- **Still open:** the poll (commit C); part C (heartbeat thread in `run_recorded`, `ingestion_runs.task_run_id` as
  migration **0008**, the sweep); nothing is committed.

### M2 slice 5, step 3, part C built: heartbeat thread, `task_run_id` (0008), and the sweep (2026-11-13)

Built to the design Kyle decided on 2026-11-11/12 (step 3 plan, part C), after the two commits of the history backfill.
The design's open item (a manual backfill needs a ledger row) is closed by Kyle's decision of today: **the entry point
goes through `run_recorded`, so a manual backfill is a task execution with a real `task_runs` row, a live heartbeat and
the sweep's rule applied to it unchanged, with no exception.** Nothing is committed.

- **New:** `app/tasks/liveness.py` (the one definition of alive: interval 30 s, fresh 120 s, `stale_cutoff`, `is_fresh`),
  `app/tasks/sweep.py` (+ task `ops.sweep`, `ops` queue, every 120 s), migration `0008_ingestion_runs_task_run_id`,
  `NotRecorded`, `tests/{__init__,conftest,recorded}.py`, `test_liveness.py`, `test_task_heartbeat.py`, `test_sweep.py`.
- **`run_recorded`** (`app/tasks/runs.py`, rewritten whole, it is small): publishes its row id in a `ContextVar`; runs a
  daemon heartbeat thread (own event loop, own `NullPool` connection from `engine.url`) torn down by one `finally` on every
  exit path; every final write is `WHERE id = :id AND status = 'running'`, and a zero-row match (swept) appends a note
  instead of changing the verdict. **`run_feed`** reads the context variable, raises `NotRecorded` before writing
  anything if it is unset, writes `task_run_id`, and has the same conditional final writes and note.
- **Health:** `checks.ingest_worker` gains `busy` (alive, not degraded); its threshold reverts from `INGEST_TASK_MAX_SECONDS`
  plus three intervals to **three intervals**, so a wedged ingest worker shows in minutes, not half an hour. The step 0
  "KNOWN GAP" in HEALTH.md is gone. `HEALTH.md` has a new "The sweep" section with both exclusions and their reasons.
- **The fake provider's task now goes through `run_recorded`** (it did not before), so it has a ledger row and counts for `busy`.

**Decisions made while building, for Kyle to confirm or reverse:**
1. **The sweep does not touch `ops.backup` and `ops.backup_copy` (`UNSWEPT_TASKS`). Found while building; not in the design.**
   The backup container writes those rows with psql, not through `run_recorded`, so nothing beats their heartbeat. A generic
   "running and stale" rule would fail a healthy dump or SFTP copy longer than 120 s as `Orphaned`, and the script's own
   unconditional final `UPDATE` would then overwrite the verdict. A reasoned exclusion like `backfill_runs`, in the sweep's
   docstring, in HEALTH.md, pinned by a test. The alternative (make the shell scripts heartbeat) is a larger change to code that
   is working. **Two writers of `task_runs`, on two protocols.** The backup scripts' final write (`task_finish` in `infra/backup/lib.sh`) is an unconditional `UPDATE ... WHERE id = :id`; `run_recorded`'s is `WHERE id = :id AND status = 'running'`, so that a swept row keeps the sweep's verdict. If the backups ever join the sweep, that write becomes conditional **in the same change**, or the sweep's verdict is overwritten by the next `task_finish`.
2. **`busy` counts only tasks that run in the ingest worker (`INGEST_WORKER_TASKS`), not the manual backfill.** The backfill is
   a separate process; its beats say nothing about the ingest worker, and counting them would let a wedged ingest worker read
   `busy` for the length of a backfill. A test pins that the backfill is in neither set.
3. **A feed that finishes after the sweep still updates `providers_health`** (the source answered); the run row keeps the
   sweep's `failed` with the note. The design said the sweep's verdict stands for the run row and was silent on this table.
4. **Test infrastructure:** `run_feed` refusing to run unrecorded broke every test that called it directly, so there is a
   `tests/recorded.py` stand-in and a shared `scratch_url` in `tests/conftest.py`; `tests/__init__.py` makes `tests` a package
   (mypy rejected the cross-test import otherwise, and plain `pytest` would not find it without `python -m`).
5. **`ingestion_runs` has a column section 8 lacks** (`task_run_id`), annotated in `test_ops_tables.py` like 0004's.

**Verification.** In the pinned container: `ci-checks.sh lint` passes and `ci-checks.sh test` passes, **586 tests**. 17 mutations
across the sweep (boundary, the backup exclusion, ingestion rows never swept, "live" meaning any row, `backfill_runs` swept), `run_recorded`
(unconditional success write, thread never stopped, a beat that ignores a swept row, context variable not published, late
note dropped), `run_feed` (guard removed, link not written, unconditional write), health (busy asked when ok, ops worker
gets busy, probe's freshness filter removed) and `is_fresh` (`>=` to `>`): **all red**; one (`backfill_runs` swept) was first red
for the wrong reason (a `NameError`) and was redone and is caught by its own test. **Smokes on the real stack, all pass:**
`smoke-m2-slice1.sh` (two edits: it ran `truncate task_runs`, which a foreign key now refuses, so it is a `delete`), `smoke-m2-slice4.sh`
(the fake provider, now through `run_recorded`), and `smoke-m2-slice5-step0.sh`, whose saturation scenario now asserts the part C
mechanism: the ingest canary starved to 16 s (past the 9 s threshold), `checks.ingest_worker` read **`busy`**, readiness stayed `ok`.
**The real Beat-scheduled sweep, observed:** a dead task and its orphaned ingestion run planted in the dev database were both
failed (`Orphaned`) in about 110 s, the sweep recorded its own `task_runs` row, and `/health/ready` was `ok` at migration 0008.

**Not done, on purpose:** a smoke that kills the ingest worker mid-task and waits for the sweep (the freshness window is a code
constant, so it would take 4+ minutes; the unit tests and the planted-row run cover the rule, and the step 0 smoke covers the
wedged-worker states); a check that watches the sweep itself (it is visible as `ops.sweep` rows, nothing reads them); the poll
(commit C of the history work, still unbuilt); `backfill_runs` resume (slice 10).

**One consequence worth knowing.** A laptop that sleeps through a backfill, as on 2026-11-13, now behaves as designed: when it wakes,
the sweep may fail the backfill's task and its current series' ingestion run as `Orphaned` before the next beat lands; the backfill
then carries on, its late result is appended as a note, and the series' rows are written all the same. The row says what happened.

#### Part C follow-up (2026-11-13): decisions approved, the kill-and-sweep smoke, and the monitoring gap logged

- **All three decisions above approved** by Kyle (the backup exclusion, `busy` counting only the ingest worker's own tasks,
  `providers_health` on a late completion). Decision 1 now also records the two-writers fact.
- **`infra/smoke-m2-slice5-step3-sweep.sh`: a real death leaves a sweepable row.** The unit tests and the planted-row run test
  the SQL; only a hard kill tests that a process which dies mid-task leaves what the sweep looks for. It `SIGKILL`s the ingest
  worker mid-task (a graceful `SIGTERM` would not do: Celery's warm shutdown lets the task finish, and `run_recorded`'s
  `finally` then writes a status, so there would be nothing to sweep). It takes about five minutes by construction (a beat is fresh
  for 120 s and Beat sweeps every 120 s); making the window an environment variable to shorten it would make it tunable in
  production for the sake of a test. **Passed on the real stack (2026-11-13):** the task's heartbeat was seen to advance (29 s past its start, so the thread is live in
  the real process); after the `SIGKILL` both the `task_runs` row and its linked `ingestion_runs` row stayed `running`; readiness
  read `busy` while the dead worker's last beat was fresh and `stale` and degraded **119 s after the kill**; Beat's sweep failed both
  rows as `Orphaned` **199 s after the kill, when the last beat was 199 s old** (the freshness window is 120 s: a live task is never
  swept, and a dead one is found within 120 to 240 s); the rows stayed swept when the worker came back, the sweep had its own
  succeeded `ops.sweep` row, and readiness recovered to `ok`. The documented cost showed too: a worker killed mid-task reads `busy`,
  not `stale`, until its last beat goes stale.
- **Sweep monitoring: a gap, logged and deliberately not built (Kyle, 2026-11-13).** Nothing watches `ops.sweep` itself. The
  sweep shares the `ops` queue and the ops worker with `ops.canary`, so the death of the ops worker already degrades
  `/health/ready` through `checks.worker`. What stays uncovered is narrow: **the sweep task failing (or being skipped) while the
  canary keeps succeeding**, which leaves orphaned `running` rows unswept without any state changing. Revisit if that case is ever
  seen, or when a second task joins the ops queue; the check would be "the newest `ops.sweep` success is within a few sweep
  intervals", on the same pattern as `checks.worker`.

### M2 slice 5, step 3: the daily FRED poll built (2026-11-13)

After the three commits (the history backfill, the VIXCLS amendment, part C). **Not committed.**

- **What it is.** `ingest.fred_poll` (`app/tasks/fred_ingest.py`), a Celery task on the ingest queue, Beat `crontab(08:00)`
  America/New_York (the app timezone), expires after six hours. Every series in `settings.FRED_SERIES`, one after another, each
  through `run_feed` (its own `ingestion_runs` and `providers_health` row), inside one `run_recorded` task; a failed series does not
  stop the others and the task raises `PollIncomplete` naming them.
- **How a series is polled** (`poll_body`, `app/ingest/fred_feed.py`). It reads `series_history_state.captured_through`, asks FRED
  only for the vintages from that date **inclusive** (`FredClient.revisions_since`: the same windows, 504 splitting and 70 s timeout as
  the backfill), takes the first prints of that window from `output_type=4` as the witness for new periods and the latest values
  from `output_type=1`, **merges the new columns onto the stored chains and runs the same six rules (`check_history`) over the merged
  history**, and `commit_history` appends only the tail that is new. So the poll cannot drift from the backfill's rules, and a quiet
  day writes 0. **Every revision is dated by FRED's vintage, never by the poll** (ADR-0003 addendum, F1); a test polls "in 2031" and
  stores the same `available_at`.
- **It refuses** a series with no captured history or one captured under older rules (`NoBaseline`, before it even asks FRED):
  polling an empty series would write its whole history with `available_at` set to today, which destroys point-in-time history.
  The fix is `python -m app.backfill`, never to poll harder.
- **Rule 3 is the poll's daily consistency check:** after the merge every chain must end at FRED's latest value. A revision published
  between the poll's own reads makes it refuse; the next poll sees both. A known race, kept because the alternative is to store a chain
  that stops short.

**Decisions made while building, for Kyle to confirm or reverse:**
1. **`since` is inclusive, so the 400 "No vintage dates exist" never arises in the poll, and `no_vintages_is_empty` goes unused by it.** The
   windows are built from `series/vintagedates`, and the last captured vintage is itself in the window, so a quiet day is "no vintage
   after `captured_through`", which merges to zero new rows. Inclusive on purpose: a vintage is a DATE, so if FRED changes a value again on
   the date we already captured, the stored column and the fetched one disagree and rules 5 and 6 refuse (a test pins exactly that);
   exclusive would have missed it. The context-dependent 400 stays built and tested (the capability Kyle asked for); the poll's design
   simply never sends a window without vintages, so for it a 400 would mean the record moved and is fatal.
2. **The poll leaves the exclusion counts in `series_history_state` alone** (`commit_history(exclusion_counts=False)`): its verdict sees
   pre-vintage and gap periods only where its window touches them, so overwriting the A, B and duplicates counts would replace a full
   capture's numbers with a partial view. The totals (`first_prints`, `revisions`) are over the merged history and are refreshed.
3. **FRED feed thresholds: 72 h = three missed daily polls (`FRED_POLL_STALE_AFTER`).** Until now the FRED feeds had none and read as
   unjudged. This is poll health, not data freshness (HEALTH.md says so); ADR-0003 decision 5's per-series cadence is about the data and
   belongs to step 4's point-in-time read. A missing `FRED_API_KEY` makes the task raise `NotConfigured` rather than silently do nothing.

**Verification.** 15 poll tests on a real Postgres (quiet day writes 0 and leaves the state alone; a revision is dated by FRED; a new period is a
witnessed first print; a gap first print the `output_type=4` omits; a revised pre-vintage period is excluded, not an error; no baseline; older
rules; a same-date change is refused as rules 5 and 6; a chain not ending at the latest value is refused as rule 3; catching up through 258 vintages
with 504 splitting; exclusion counts untouched; one series never touches another's state row; `poll_all` continues past a failure; each run is linked
to its task; the poll's own clock cannot move a date) plus Beat's schedule, the route, the feed thresholds, `revisions_since`, and the merge helper.
**Mutation: 13 mutants, all red**; one (`UPDATE` without its `WHERE series_id`) was first green because every test had a single state row, which
would have advanced all seventeen in production, so a two-series test was added. **Run for real against the real API on the dev database:** the
poll succeeded for all 17 series in about 70 s (about 4 s each), wrote **13 rows** (observations FRED published since the captures that day), and a
**second poll wrote 0**. What it wrote is worth reading: DGS10's 2026-11-11 (Veterans Day) is a stored NULL, published as `"."` on 2026-11-13; IORB's
three periods dated 11-14 to 11-16 were published on 11-13 (`available_at < effective_at`, legitimate by the migration's own note); every
`available_at` is the end of the publication day in New York, which for today's rows is hours AFTER we polled (the documented always-late bias).
`/health/providers` now judges the FRED feeds `fresh`.

**Not done.** Beat has not yet fired the schedule: the poll was started by hand (`celery call`), and the first scheduled run is 08:00 America/New_York
tomorrow if the stack is up (the entry and its route are pinned by tests, not observed firing). No smoke script with a stub FRED (the plan's part E);
the real-API run above stands in for it today. Step 4 (the point-in-time read) is next.

#### The poll: follow-ups approved (2026-11-13), the invariant, the column semantics, and a general lesson

Kyle approved all three decisions and asked for four things, done before the poll's commit:
- **The invariant that makes the 400 unreachable is recorded where someone tempted to optimise will read it:** `FredClient.revisions_since`
  and `poll_body` (docstrings), HEALTH.md (beside the 400 row), and the ADR (a "Decision 4 as built" note). It is also **executable**: the
  shared fake FRED now answers a vintage-less window with the real 400 (measured 2026-11-12), so a poll that skipped the vintage-date read gets
  it in a test, and a second test shows the 400 directly. Mutation: replacing the read with a direct request turns the test red.
- **`series_history_state` now says, on its own columns, that it carries two vintages of truth** (migration **0009**, `COMMENT ON TABLE` and on
  every column, visible in `\d+` and any SQL client; the model docstring says the same). `captured_through`, `completed_at`, `ingestion_run_id`,
  `first_prints` and `revisions` are CURRENT (the poll advances them); the five exclusion and duplicate counts are AS OF THE LAST FULL CAPTURE and
  the poll never updates them, so `captured_through` can be a week newer than the counts beside it. Three tests: every column has a comment (so a
  column added later must say which vintage it belongs to), the frozen ones say "NOT CURRENT", and the two sets match the code that decides.
- **The general lesson, written up as CLAUDE.md rule 9 ("test keyed code with at least two keys"):** the poll's `UPDATE series_history_state` had no
  `WHERE series_id`, it survived a green suite because every fixture had a single series, and in production it would have advanced all seventeen
  rows. That is not a FRED trap. **Applying it found the same hole elsewhere:** dropping the `WHERE provider, feed` from either `providers_health`
  update in `run_feed` left every test green (a failure in one feed would have stamped `last_error` on every feed). `test_one_feeds_outcome_never_touches_
  another_feeds_health_row` now covers both paths, and both mutants are red. The other keyed writes are by primary key, which is unique.
- **A bonus from the models-match-migrations check:** it compares column comments, so it failed until the model carried the same words as
  migration 0009. The model's comments are now generated from the migration's literals, and from here on `alembic check` fails if the
  semantics on the database and in the model ever differ. The container gate (`lint` clean, `test` 614 passed) was green only after that.

### M2 slice 5, step 4 plan: the point-in-time read (2026-11-14). PLAN, NOT BUILT; waiting on Kyle

The slice's step list (2026-11-11) says: "4 point-in-time read in `services/`, tested against the GDPC1 oracle (23,526.085 on 2025-05-01, one
2025 quarter then) · 5 per-series data age · 6 smoke, HEALTH.md, `.env.example`." Steps 0 to 3 are done and committed. This is step 4 only.

**What it is.** The one place that answers "what did we know about metric M, about period P, at instant T": a row-**existence** filter on `available_at`
(ADR-0003 decision 2), not a value filter. It is the primitive that live and replay will both read through (ADR-0004), so it takes `as_of` as an
argument and reads no clock. It lives in `services/` because it touches the database; `signals/` stays pure.

**Evidence the data is ready, found while planning (real data, dev database).** CPIAUCSL 2025-01-01 is stored as revision 0 = **319.086**, available
2025-02-13 05:00 UTC, and revision 1 = **318.961**, available 2026-02-14 05:00 UTC. M0 spike 3b recorded first print 319.086 and current 318.961 for that
period, independently, through `output_type=4`. The backfill's `output_type=3` chain reproduces both, and dates the revision to FRED's vintage
(2026-02-13, CPI's annual seasonal-factor revision) where the old design would have dated it by whichever poll saw it first.

**Proposed interface** (`app/services/macro_read.py`; frozen dataclasses; `as_of` must be timezone-aware, a naive one raises):
- `value_as_of(engine, metric, period, as_of)` -> `Observation` or `Missing(reason)`.
- `series_as_of(engine, metric, as_of, *, start=None, end=None)` -> `Snapshot(observations, coverage)`: for each period the **latest revision with
  `available_at <= as_of`** (`DISTINCT ON (effective_at) ... ORDER BY effective_at, revision DESC`), periods with no such row absent.
- `latest_as_of(engine, metric, as_of)` -> the newest period known at `as_of`. ADR-0003 decision 6 (a signal at the open resolves to the prior
  session's value) needs no extra code: `available_at` is already the end of the publication day in New York, so 09:30 on day D cannot see D's print.
- `revisions_of(engine, metric, period, as_of=None)` -> the chain as it stood, for diagnosis and for M5 ("what was believed when").
- **Boundary:** `available_at <= as_of`, inclusive; a test pins the exact instant and one microsecond either side.
- **A stored NULL is a published gap** (decision 7), returned as `Observation(value=None)`, never as `Missing`, and never forward-filled.
- **`Missing` carries a reason, not a bare absence** (value objects, not exceptions): `NOT_PUBLISHED_AS_OF` (no row at or before `as_of`);
  `PREDATES_VINTAGE_RECORD` (the period is earlier than the series' `first_covered_period`: a value exists in FRED's latest values but no publication
  date does, exclusion A/B); `BEFORE_VINTAGE_RECORD` (`as_of` is earlier than the series' `first_vintage`: no replay of that day is possible);
  `UNKNOWN_METRIC`. `Coverage` carries `first_vintage` and `first_covered_period` from `settings.FRED_SERIES`, so a caller sees the boundary and
  "no value exists" is never confused with "a value exists but predates the vintage record" (the ADR-0003 addendum's own requirement of this step).
- **No new index** (migration 0005 said: add `(metric, available_at)` when a measured query needs it). The acceptance check measures the largest series
  (DGS10, about 5,600 rows) and the plan changes only if it is slow.

**Tests.**
1. `tests/test_macro_read.py` (scratch DB, synthetic chains): the exact boundary; **existence, not value** (a period first printed after `as_of` is absent,
   one revised after `as_of` shows its earlier revision); a published gap versus a missing period; each `Missing` reason; ordering and `start`/`end`;
   decision 6 (a daily series read at the next open); a naive `as_of` raises; and **two metrics with different chains, asserting one read never returns
   the other's rows** (CLAUDE.md rule 9, checked by deleting the `WHERE metric`).
2. A **recorded-real-data oracle** (`tests/fixtures/fred/ok_revisions_DRCCLACBS.json`, 142 periods and 62 vintages, live data): seed it through the
   real `check_history` and `commit_history`, then for every vintage date (and one instant before it) compare `series_as_of` with an **independent
   pure-Python resolution computed straight from the fixture's columns**. That tests the read against data it did not produce.
3. `tests/test_macro_oracle_live.py`: the M0 spike 3b numbers for CPIAUCSL, PAYEMS excluded (not stored), against the **real stored data**: as of
   2025-02-14 the 2025-01-01 CPI is 319.086; as of 2026-02-13 still 319.086; from 2026-02-14 05:00 UTC it is 318.961. It reads the main database
   read-only and **skips when the series is not captured** (the container's fresh database), so it runs in a development stack after any rule change.
4. Mutations as before: the comparison `<=` to `<`; revision order; the `metric` filter; `Missing` reasons swapped.

**Files.** New: `app/services/macro_read.py`, `tests/test_macro_read.py`, `tests/test_macro_oracle.py` (the recorded-fixture oracle),
`tests/test_macro_oracle_live.py`. Changed: `app/settings.py` (a small `fred_series(metric)` lookup), `docs/BUILD-LOG.md`, `CLAUDE.md`. **No migration, no
route, no change to the writers.**

**Decisions I need from Kyle.**
1. **The oracle.** GDPC1 is **not** one of the 17 stored series (it was the M0 spike's test series, not in section 6.4's list), so the original
   "GDPC1 oracle" cannot be run against stored data. I propose CPIAUCSL (above: independent M0 values, already reproduced by the real data) plus the
   recorded DRCCLACBS fixture. The alternative is adding GDPC1 as an 18th series (a config row, two measured boundary dates, one backfill of a few
   minutes), which is outside the spec's list and the feature freeze's spirit for the sake of one test. **Recommend CPIAUCSL.**
2. **The live oracle as a skip-unless-captured test against the main database** (read-only; runs only in a development stack), rather than a one-off
   recorded check. It costs a test that depends on a developer's data being present. **Recommend yes**, because it re-validates the stored data after
   any rule change, which the container cannot.
3. **No HTTP route in step 4.** Every number shown must exist in Postgres first, and the read is the primitive for it, but a route would need the
   section 9 envelope (`{data, as_of, stale}`, which the health endpoints deliberately do not use, SPEC-DEFECTS) and the public-profile route invariant
   (IDEA-019). Service-level only until a consumer needs it. **Recommend yes.**
4. **`Missing` as a value, `as_of` inclusive and timezone-aware.** Confirm.

**After step 4, unchanged:** 5 per-series data age on `/health/data` from `max(available_at)`, thresholds from each series' own observed
`available_at - effective_at` lag as generous multiples (the 2026-11-11 decision); 6 smoke, `HEALTH.md`, `.env.example`. Not step 4: ADR-0004's snapshots and
watermarks (M3), signals, bars (slice 6).

### M2 slice 5, step 4 built: the point-in-time read (2026-11-14)

Decisions taken by Kyle on the plan: **(1) both oracles**: CPIAUCSL as the live oracle, and GDPC1 captured once as a recorded fixture so that ADR-0003's
stated assertion runs (GDPC1 is **not** added to the ingest list: a production scope change for a test is not worth it, but an ADR assertion nothing
verifies is the drift this project keeps finding); **(2) the live oracle as a skip-unless-captured test, with the skip made loud**; **(3) no HTTP route**; **(4)
`Missing` as a value, `as_of` inclusive and timezone-aware**, with the absence reasons specified up front. Not committed.

- **New:** `app/services/macro_read.py` (`series_as_of`, `value_as_of`, `latest_as_of`, `revisions_of`; frozen `Observation`, `Missing`, `Coverage`, `Snapshot`;
  `Reason`), `tests/{seedfred,liveskips,test_liveskips,test_macro_read,test_macro_oracle,test_macro_oracle_live}.py`, four recorded GDPC1 fixtures
  (`tests/fixtures/fred/ok_{vintagedates,revisions,first_prints,latest}_GDPC1.json`, about 200 KB, captured 2026-11-14). **Changed:** `tests/conftest.py` (the
  banner hook). No migration, no route, no writer changed.
- **The five kinds of no answer** (the module docstring is the specification; precedence is the order): `UNKNOWN_METRIC` (nothing configured or stored);
  `BEFORE_VINTAGE_RECORD` (T is before the series' first vintage, so no replay of that instant is possible for any period); `PREDATES_VINTAGE_RECORD` (the period is
  earlier than `first_covered_period`: FRED's latest values hold a number but no publication date exists, exclusion A or B); `NOT_PUBLISHED_AS_OF` (a revision of
  the period exists, none at or before T); `UNKNOWN_PERIOD` (no revision of that date exists at any time: not an observation date, or beyond what we hold).
  `first_covered_period` is what keeps the second-to-last two distinct; a test shows the same question collapsing to `UNKNOWN_PERIOD` without the bounds, and
  another shows all three on real GDPC1 data. A stored NULL is a published gap and is an `Observation`, never a `Missing`.
- **ADR-0003's assertion, run.** On the recorded GDPC1: 23,526.085 as of 2025-05-01 (one 2025 quarter), 23,548.210 as of 2026-07-31 (+0.094%, four quarters). **The
  assertion has to be pinned to the spike's date, not to "today":** the fixture shows Q1-2025 now reads **23,650.213**, revised by BEA's 2026-09-30 annual
  update. The six rules also accept GDPC1's whole real history (a series the pipeline had never processed).
- **The read against an independent resolution.** For every vintage date of two real series (DRCCLACBS 62, GDPC1 420; the instant itself and one microsecond before)
  `series_as_of` equals a pure-Python resolution computed straight from the fixture's columns, which uses neither the database nor the read module. No instant
  disagrees.
- **The live oracle found nothing wrong and one thing worth recording.** Against the real stored data it reproduces all twelve of M0's CPIAUCSL points (first
  print at its publication, the value then current at the spike's run time), the stored revision dated 2026-02-13 (CPI's annual seasonal revision, FRED's own date),
  **and the one period M0 recorded as `"."`: October 2025 CPI, never collected during the US government shutdown, stored as a published NULL.** Two independent
  paths (`output_type=4` in M0, `output_type=3` here) agree on every point.
- **The loud skip, and a gate it would have broken.** The live oracle needs captured data, so on a fresh database it skips permanently, which is an
  absent test unless it says so. Its reason carries a marker and `tests/conftest.py` prints a banner from `pytest_terminal_summary` listing every skipped
  live-data check **whatever the verbosity** (CI passes no `-rs`); the banner is a pure function with its own tests. **Then I found what I had not checked:
  `ci-checks.sh test` runs against the development stack's own Postgres, so on this machine the live oracle RAN in the container and passed; and GitHub's runner,
  which runs the same script against a fresh database, would have skipped it, and the script's gate (`grep skipped="0"`) would have turned CI permanently
  red** (or invited someone to weaken the gate). The docstring I had written, "not run by the container", was wrong. The gate is now
  `python -m tools.junit_skips`: **any skip fails the run, except a skip whose reason carries the live-data marker, which is allowed and PRINTED again**, so it is
  loud twice (pytest's banner, then the gate). Tested (a live skip passes and prints; a `DATABASE_URL not set` skip still fails; one bad skip among allowed ones
  fails), and **verified in the pinned image against an empty database (what GitHub has): 3 skipped, the banner printed, the gate printed each one, exit 0**.
- **Performance, measured 2026-11-14 on the real DGS10: 5,583 stored rows (5,577 periods), 7 timed runs after a warm-up, median / max:**
  `series_as_of` over the **whole series, 5,577 rows returned: 54 ms / 71 ms** (mostly transfer); `series_as_of` over **one year, 224 rows: 4.7 ms / 5.2 ms**;
  `value_as_of`, **1 row: 2.7 ms / 3.4 ms**; `latest_as_of`, **1 row: 2.7 ms / 3.0 ms**. Migration 0005's "add `(metric, available_at)` when a measured query needs it" is
  not triggered **by this load**. **That is a conclusion about about 5,600 rows of daily macro per series, the smallest load `macro_read` will ever carry, and not
  about bars.** Slice 6 (prices: thousands of symbols, intraday, years deep) **must re-measure with its own row counts; it must not inherit "no index needed"**. The
  same warning is in `macro_read`'s module docstring, where its author will see it.
- **Mutation, 13 mutants on the read, all red**, after a correction: two stayed green at first (dropping `WHERE metric` from `value_as_of` and `latest_as_of`),
  because my two-metric test gave both metrics chains of the same shape and the right answer arrived by luck of ordering. **The other key's data must be able
  to WIN the ordering** (a higher revision of the same period, a newer period); that refinement is in CLAUDE.md rule 9.
- **Verification:** in the pinned container `ci-checks.sh lint` passes and `ci-checks.sh test` passes, **650 tests**, with the live oracle running (it reads the dev
  stack's database there); 618 locally before the gate tool and its tests were added. The mutations above and the empty-database run are the other evidence.

**Next, unchanged:** step 5, per-series data age on `/health/data` from `max(available_at)`, thresholds from each series' own observed `available_at - effective_at` lag as
generous multiples; step 6, smoke, `HEALTH.md`, `.env.example`.

### M2 slice 5, step 5 built: per-series data age on `/health/data` (2026-11-14)

The home the readiness rule points to: a stale series is visible without degrading `/health/ready`. Not committed.

- **One deviation from the recorded plan, decided by the data.** The 2026-11-11 plan said to size thresholds from each series' **lag** (`available_at - effective_at`). "Has a new
  period stopped arriving?" is a question about the **gap between publications**, and the two differ widely on the real data (measured, five years of first prints):
  DRCCLACBS publishes about every **91** days but its lag is **143**; HOUST about every 30 with a lag of 48; MORTGAGE30US weekly with a lag of 1; IORB's lag is zero or negative.
  Sized from lag, the quarterly series would have taken over a year to read stale. So: **threshold = 3 x p95 of the gaps between consecutive publication days, rounded up
  to whole days (floor 2)**; lag stays on the response as information. **p95, not the maximum**, so that the 2025 shutdown (CPI's longest gap 55 days against a median of 29,
  HOUST's 114 against 30) does not set the threshold for five years. Result: daily series **9 days**, weekly **21 to 24**, monthly **105 to 109**, DRCCLACBS **286**.
- **First prints only.** Age is `now - max(available_at)` over `revision = 0`: a revision proves the source is alive, not that new periods arrive, and DRCCLACBS revises almost
  weekly. A test with a revision published long after the first print pins it.
- **Thresholds are config, measured once** (`settings.FRED_DATA_STALE_AFTER`, dated 2026-11-14), like `FRED_SERIES`' boundaries, not computed per request. `python -m app.measure_cadence`
  (read-only) prints gaps, the rule's suggestion and the configured value; **for all 17 series the suggestion equals the configured value**. A live-data test fails loudly in a
  development stack if a threshold leaves 2 to 6 x its measured p95 (skips loudly on a fresh database, allowed by the gate).
- **Four states, and the two non-fresh non-stale ones are deliberate:** `unjudged` (stored, no threshold: never reported fresh) and `no_data` (configured, nothing stored: not captured
  yet, not stale). The age is **clamped at zero** because `available_at` is tonight's midnight for a value published today.
- **Changed:** `app/services/health.py` (`assemble_data_age`, `assemble_data` gains `data_age`; the `HealthProbes` protocol gains `macro_age`), `health_probes.py`, `api/health.py`,
  `settings.py`. **New:** `app/ingest/cadence.py` (pure), `app/measure_cadence.py`, `tests/{fakeprobes,test_cadence,test_data_age,test_macro_age_db,test_data_age_live,livedb}.py`.
  `tests/livedb.py` is the shared "is there real captured data" gate (the step 4 live oracle now uses it). No migration. The public profile serves `data_age` unchanged (series ids and
  ages are not secrets); a test pins it.
- **A bug caught on the way, in my own measuring query:** `available_at` is 00:00 New York on the day AFTER publication, so a plain `AT TIME ZONE 'America/New_York'` date is one day late.
  The gaps were unaffected (a constant shift) but the dates would have been wrong; the query subtracts a microsecond first (the inverse of `available_at_for`), and a test pins the
  exact publication dates.
- **Mutation: 17 mutants on the probe, the measuring query, the assembly, the route, the cadence maths and the config; all red.** One was green at first: removing the floor, because my
  floor test landed on exactly 2 days through the ceiling (1.2 rounds up to 2). The floor only bites when 3 x p95 is under 2 days, which real data (whole-day gaps, p95 >= 1) never reaches;
  the test now reaches it directly with a synthetic p95 of 0.2. *(Corrected 2026-11-14: this entry first called the floor "a guard real data never reaches", which invites deleting it. It is forward-looking, for slice 6's sub-daily cadences, and its VALUE, 2 days, is a daily-cadence value that must become unit-aware then; the note is beside `FLOOR` in `app/ingest/cadence.py`.)*
- **Real endpoint, dev stack, 2026-11-14:** all 17 series `fresh`; e.g. DGS10 0.61 days against 9, CPIAUCSL 3.61 against 105 (newest period 2026-10-01, lag 40), DRCCLACBS 80.65 against 286
  (lag 146), IORB's newest period 2026-11-16 (lag -3). **Cost, with its row count:** `macro_age` is one grouped scan, **11.3 ms median (11.6 max) over 56,852 `macro_metrics` rows
  (38,355 first prints, 17 series)**. That is the macro load only; it is not a conclusion about any larger table.
- **A hazard that bit me and is now guarded.** A `ruff format .` run from the repo root (a failed `cd`) reformatted the Python code blocks inside the FROZEN `docs/architecture.md`
  and five spike files; git undid it, and a root `ruff.toml` now excludes `docs`, `spikes`, `tests` and `infra` (committed in `7966d94`).

**Next:** step 6, smoke, `HEALTH.md`, `.env.example` (`FRED_API_KEY`), and the slice 5 gate review.

### M2 slice 5, step 5 follow-ups (2026-11-14)

- **`revisions_of(..., *, as_of)` is a required keyword.** The whole-chain read, the module's one hindsight-leak path, must now be written
  `as_of=None` at every call site, visible in every diff; omitting it (or passing it positionally) is a `TypeError`, pinned by a test.
- **Lint coverage, written down** (CLAUDE.md "Commands" has the table; `tests/test_lint_coverage.py` pins it). The gate runs inside `backend/`, config
  `backend/pyproject.toml`, 112 files; `mypy` `files` must equal the directories of Python (`app`, `tests`, `alembic`, `tools`). The repo-root `ruff.toml` excludes
  `docs spikes tests infra` but only governs a run from the root; the nearest config wins, so it never reaches `backend/tests`. Checked empirically: identical file
  list from `backend/` and from the root. Mutation: breaking the root guard or mypy's `files` goes red.
- **The 2-day floor is forward-looking, not dead** (note beside `FLOOR`; wording corrected above). Its VALUE is a daily-cadence value and must become unit-aware in slice 6.
- **Plan numbers: rule 10 added to CLAUDE.md.** Three plan numbers falsified by measurement so far: ADR-0003 F1, F2, and step 5's lag-sized thresholds (gap is the quantity).
- **Beat and `ingest.fred_poll`.** Observed (not exact-time): `task_runs` id 2725, a Beat-fired run on the `ingest` queue, succeeded, fired 2026-11-14 13:34 NY on wake after an
  ~18 h sleep (Beat catches up). The 08:00 firing with an awake machine is **still unobserved** (next expected 2026-11-15 13:00Z). Pinned deterministically instead:
  `tests/test_beat_schedule.py` (entry, ingest queue with no per-entry override, 6 h expiry, 08:00 New York in winter and summer); mutants on the hour and the queue are red.

### M2 slice 5, step 6 and gate review (2026-11-14). Not committed; the gate is Kyle's call

**Step 6.** `docs/HEALTH.md` gains "What has and has not been shown for a killed FRED" and drops a stale "Not built yet" on the 504 splitter. `.env.example` now says what
`FRED_API_KEY` is required for and what a blank does (`NotConfigured`, loud, in both the backfill and the poll; read from the code, not assumed). `FRED_USER_AGENT` and
`FRED_BASE_URL` were already documented.

**The stub-FRED smoke (Kyle: close this one first, 2026-11-14).** A full poll against a stub was not built: it needs a seeded baseline per series, and it would run on the development
database. What was built is narrower and aimed at what the column protects. `bash infra/smoke-m2-slice5-fred-failures.sh` runs ONE FRED call per status (500, 429, 423)
through the real mapping path (`FredClient` -> `run_feed` -> `ingestion_runs` / `providers_health`), inside the worker container (real image, real Redis limiter, real Postgres), against a local stub
that answers that status and **echoes the whole request URL, canary key included, in its body**. Feeds are `SMOKE_<status>` under provider `fred`, removed afterwards; no real series is touched.
- **Why not a closed port** (Kyle's point, confirmed): a refused connection's text is `All connection attempts failed`, no URL. **Checked 2026-11-14 on httpx2 itself:** `Response.raise_for_status()`
  raises `HTTPStatusError` with the message `Server error '500 Internal Server Error' for url '<full URL including api_key=...>'`. So the leak is real the day someone replaces our status mapping with
  httpx2's. Our `_raise_for_status` never calls it: every message is fixed text, and `_get` catches `httpx2.HTTPError` and keeps only the class name.
- **What it asserts:** a positive control (the stub does echo the key, so "absent" can fail); 500 and 423 map to `transient`, 429 to `rate_limited`, each retried (2 requests for 2 attempts); the exception text has
  no key, no `api_key`, no URL; `providers_health.last_error` is exactly `<kind>:<Class>`; `ingestion_runs.error` is `failed <kind>:<Class>...` without the key; `/health/providers` and `/health/data`
  serve the key nowhere (and `/health/providers` does list the SMOKE feeds, so the absence is not an empty answer); `/health/ready` stays 200.
- **Mutation, run against rebuilt worker images, both red:** (1) the 500/423 branch appends `response.url` to its message: FAIL at "the exception text carries the key" (the stored columns would still
  have been clean, because `ingestion_runs.error` is scrubbed by exact value and `last_error` takes the class only, so the text check is the one that catches layer 1 failing);
  (2) `health_error` returns `kind:Class:text`: FAIL at "providers_health.last_error is 'transient:Transient:FRED answered 500', expected 'transient:Transient'". Sources restored, worker rebuilt clean, smoke green.
- **The two leak sinks guard different things; neither covers the other** (noted beside both assertions in the smoke, and at `_raise_for_status` in `app/ingest/fred.py`). The exception MESSAGE (logs, anywhere exception text
  surfaces) is caught only by the exception-text check. The STORED columns (served over HTTP) are caught only by the exact `last_error` check. Mutant 1 above stayed clean in the columns; mutant 2 never touched the message.
  Deleting either check as redundant removes a sink's only guard. **`response.raise_for_status()` is the reflex call and it leaks** (its message carries `api_key=`); a comment says so where error handling gets added.
- **Not covered:** 401/403, 404, 504, 400 and the schema-mismatch kinds through the real worker (unit-tested only); the poll's merge under failure; recovery from failure on the real stack (slice 4's smoke shows
  recovery for the fake). 429's `Retry-After` is not known: the stub sends none, and no real 429 has been seen.

**Beat: accepted** (Kyle). The schedule is pinned deterministically across DST (`tests/test_beat_schedule.py`), the queue is pinned, and the task ran through the real worker (task_runs 2725, ingest queue, succeeded).
- **Lateness, with no near-miss.** Due 13:00Z (08:00 New York), ran 18:34Z: 5 h 34 min late after an ~18 h sleep. That lateness did not use up the 6 h expiry: Celery computes a seconds-valued `expires` from the
  moment the message is SENT (`celery/app/amqp.py`, `add_seconds_to_datetime(now, expires)`), and Beat sent it at 18:34Z. Checked in the Celery source, not assumed. The expiry bites only if the message waits more than 6 h
  in the queue after Beat sends it (worker down or saturated). On a laptop that sleeps, Beat and the worker wake together.
- **Expiry stays 6 h** (Kyle's decision). A morning-long worker outage costs one day of a poll that reads the full revision history on its next run; a longer expiry buys nothing real.
- **A dropped poll is benign, by design, and visible at 72 h.** The poll asks FRED for every vintage since `captured_through` (inclusive) and merges onto the stored chains, revisions dated by FRED's vintage, never by the poll,
  so the next poll catches up and a double run merges zero new rows. One dropped poll is not alerted; the FRED feeds go stale in `/health/providers` after **72 h** (three missed polls: poll health). `/health/data` `data_age`
  is a different quantity (the age of the newest first print, 9 days for the daily series) and is not the signal for a dropped poll.
- **Beat's schedule file is `/tmp/celerybeat-schedule` inside the container**, so last-run memory is probably lost on a container recreate. **ASSUMED, untested.** Parked as **IDEA-043**, not fixed: compose is not
  touched at a gate boundary, and a skipped idempotent poll costs nothing. Its unfreeze trigger is slice 6, whose price polls are not catch-up-safe in the same way.

**No FRED 429, 423 or 500 observed: accepted as an observation gap, permanently.** Two different sentences, now separate:
1. *We have never seen FRED do it.* Fine forever; it is the provider's behaviour. HEALTH.md says so for each status.
2. *We have never tested our handling.* **No longer true** for 500, 423 and 429: the smoke above runs each through the real worker and the credential invariant. Still not exercised through the real worker: the other
   statuses in the table (above).

**Slice 5 against its steps and the M2 gate clauses**
- Steps 0 to 5 built and committed; step 6 above. Container gate: lint passes, 682 tests pass (2026-11-14); the new smoke passes.
- "Re-ingesting identical payloads yields identical canonical rows": shown by `rows_written` 0 on re-runs and a second poll (real data), and by the idempotency tests.
- Backfill resume: not built (slice 10); the skip is not resume.
- **Slice 5 closes with two named gaps, not three:**
  1. *Beat's 08:00 firing on an awake machine is unobserved* (accepted). Check `task_runs` after 2026-11-15 13:00Z with the machine awake; a Beat container recreate dropping a missed firing is ASSUMED, untested.
  2. *No real FRED 429, 423 or 500 has ever been seen* (accepted permanently; the handling is now tested).


## 2026-11-14 — M2 slice 6 plan: prices (ADR-0002, IDEA-034, IDEA-035). PLAN, NOT BUILT; waiting on Kyle

Plan only; no code, no migration. Rule 10 applies: **every number below is tagged MEASURED (date, how) or ASSUMED.** Nothing about `bars` has been
measured in this project yet, and the only price measurements that exist are M0 spike 2's (2026-07-31). Slice 6 is where slice 5's "no index needed" stops
being inheritable, so step 1 is measurement, before any schema is committed to.

### Numbers this plan stands on

| Number | Value | Status |
|---|---|---|
| Symbols seeded | 24, all `asset_class = etf` | **MEASURED 2026-11-14**, `SELECT count(*) FROM symbols` on the dev stack (14 rotation + SPY + QQQ + RSP + 7 commodity proxies) |
| NYSE sessions | 3,235 rows, 2015-01-02 to 2027-11-12, about 251 a year | **MEASURED 2026-11-14**, `trading_sessions` (XNYS); the per-year figure is derived from it. XKRX: 3,160 rows |
| `provider_mappings` rows | 0 | **MEASURED 2026-11-14.** No yfinance mapping is seeded; slice 6 must seed 24 |
| `BREADTH_UNIVERSE` | empty (§6.2 gives no constituent list) | **MEASURED** (slice 3); S&P 500 daily bars are therefore out of this slice |
| 15-minute bars per regular session | 26 | arithmetic (6.5 h x 4); early closes have fewer |
| Daily rows, 24 symbols x 2 years | about 12,000 | derived from the two MEASURED rows above |
| 15-minute rows, 24 symbols | about 157,000 a year (24 x 26 x 251) | derived; the 26 is arithmetic, the 24 and 251 are MEASURED |
| Scanner universe, 15-minute | about 3.3 million a year (500 x 26 x 251) | **ASSUMED**: "~500" is §6.12's wording, there is no list, and the scanner is Phase 2, not M2. Quoted only to show where the index question starts to bite |
| Revision rows for `c_total_return` | at most about 48,000 a year (24 x 4 dividends x about 502 daily rows) | **ASSUMED**: 4 dividends a year is a guess, several of the 24 pay none; every earlier bar is rewritten by each dividend (ADR-0002, **MEASURED 2026-07-31**, KO) |
| yfinance intraday look-back | 15-minute data limited to about 60 days | **ASSUMED, not verified**: my recollection of yfinance's documented limits. M0 spike 2 tested daily bars only. Step 1 measures it. If true, **intraday history is not backfillable and a missed 15-minute bar is gone for good** |
| yfinance batch and spacing | about 100 tickers per request, 1 s or more apart | **ASSUMED**: §6.12's text ("roughly"); no spike measured it. It becomes `RATE_BUDGETS["yfinance"]` only after step 1 |
| What `auto_adjust=False` returns | `Close` is split-adjusted; `Adj Close` is total return; unknown ticker gives an empty frame without raising; SPY has an extra `Capital Gains` column; holidays are absent | **MEASURED 2026-07-31**, M0 spike 2 (NVDA 10:1 split, KO, SPY), `spikes/spike2_evidence.json`. **The yfinance version is not recorded in the spike files**, so step 1 re-runs the key checks on the version we pin |
| Whether Yahoo's US volume is consolidated | unknown | **ASSUMED nothing**; `venue_scope` must not claim it until measured (D1) |
| Timing and plan shape of any `bars` read | none | **NOT MEASURED.** The only reads timed are macro: 2.7 to 54 ms on 5,583 rows of one series, and the data-age scan at 11 ms over 56,852 rows (**MEASURED 2026-11-14**). Neither says anything about `bars` |

### Prerequisites, neither of them code

1. **IDEA-029 must close first, for anything scheduled.** Slice 2's restore drill from the real desktop is still open. What a lost machine would erase for good is what only a live run captures: bars as
   traded at the time, and `c_total_return` as it stood on a day. So steps 1 to 5 below (nothing scheduled, daily backfill only, all re-fetchable) can proceed; **step 6 (Beat) cannot be enabled until the drill passes.** That is yours to do on the desktop.
2. **IDEA-043 is decided before step 6**, because a missed intraday bar, unlike a missed FRED poll, is not caught up by the next run. Recreating `beat` between firings is untested (ASSUMED to lose its last-run memory); run that test first.

### Decisions I need from you (each blocks something, none is taken)

- **D1. Provenance columns on `bars` (IDEA-034).** Recommend adding `source`, `feed`, `venue_scope`, `available_at`, `ingested_at`, `ingestion_run_id` and one more §8 lacks, **`raw_basis`**
  (`live` | `reconstructed` | `unreconstructed`), because ADR-0002 makes `c_raw` three different things (a live capture; `c_split_adj` times the cumulative split factor; or, for backfill spanning a split before
  slice 8, not yet reconstructable), and a reader must be able to tell. No `revision` column on `bars` (D2). `venue_scope` is stored as `provider_default` until consolidation is measured; claiming `consolidated` would be inventing it.
  Names and meanings follow the settled vocabulary; types differ as already decided (an intraday `available_at` is a real instant, no end-of-day rule).
- **D2. Where `c_total_return` revisions live (IDEA-034).** Recommend a separate table keyed like `macro_metrics`: `bar_total_return(symbol_id, interval, ts, revision, value, available_at, ingested_at, ingestion_run_id, source)`,
  written in the same transaction as the bar; `bars.c_total_return` keeps the latest value and is **never read by a replay** (a comment and a test say so). Cost: two homes for one number, which is the drift this project has been bitten by, hence the rule.
  Alternative: derive total return on read from raw prices and confirmed dividends (no revisions, no table), which needs dividends as a first-class feed and departs from ADR-0002 decision 4 as written.
- **D3. The version home for a confirmed split (IDEA-035), decided now, built in slice 8.** Spec options: (a) `data_version` in the key of the largest table, every hot read filters it; (b) derive adjusted columns on read. Recommend a third: **(c) `bars` stays the current canonical
  version with its key unchanged; a confirmed action moves the superseded rows' adjusted columns into `bars_superseded(…, superseded_at, corporate_action_id)` and rewrites in place.** Raw columns never change, so "raw history is immutable" holds, "a new data version without deleting raw" holds, and no read pays for versioning.
  I have not measured anything to prefer it; it is the option that does not touch the hot path.
- **D4. A batch with one dead ticker.** An unknown or delisted ticker returns an empty frame without raising (**MEASURED 2026-07-31**). One feed per universe-and-interval (24 symbols in one request) means one bad symbol would either fail every symbol's rows or hide. Recommend: **write the good symbols' rows, then fail the run
  with `empty_result` naming the symbols**, so the failure is a failure and the data is not held hostage. Per-symbol quarantine states are slice 7 (IDEA-023).
- **D5. Timestamps.** Daily bar `ts` = the session's `opens_at` instant from `trading_sessions`; intraday `ts` = the bar's start instant. Nothing may `CAST(ts AS date)` (settled in the slice 5 vocabulary); the session date is a join to `trading_sessions`. **Only completed bars are stored** (bar end at or before the fetch), so no row is ever the in-progress bar; `available_at` is the fetch instant (ASSUMED reasonable: the first moment we could have known it).
- **D6. A spec gap I would log as IDEA-044 on your word:** §8 gives retention for 1-minute (90 days) and 30-minute (2 years) bars and says nothing about the 15-minute bars that §6.1 and §11 poll. Recommend 2 years, same as 30-minute (ASSUMED, a choice not a measurement).
- **D7. Out of this slice, by name:** S&P 500 constituent bars (no list exists), the Stooq fallback (M2 builds yfinance, FRED and a fake only, IDEA-037), 1-minute bars (paid path), the intraday-to-daily continuous aggregate (it would be a second source for daily bars; the provider's own daily bar is the one), validation and quarantine (slice 7), corporate-action confirmation (slice 8).

### Steps (each ends with something you can verify)

1. **Measure before designing.** (a) A throwaway hypertable at three synthetic sizes, 12,000 / 157,000 / 3,300,000 rows (the three rows above), timing and `EXPLAIN` for: latest bar per symbol; one symbol's window; **a cross-section of all symbols at one timestamp** (rotation and breadth read this way, and it is the shape the primary key `(symbol_id, interval, ts)` does not serve; **the hypothesis, ASSUMED, is that it needs an `(interval, ts)` index and that the macro conclusion does not carry**); chunk interval. (b) A live yfinance probe on the pinned version: one daily batch of 24 and one 15-minute batch, column sets by name, an empty-frame ticker, the real intraday look-back, the batch timing, and whether a 15-minute call returns the in-progress bar. Everything recorded as MEASURED with date and how, and this plan's table updated, including any number it falsifies (rule 10 keeps the tally).
2. **Migration for `bars` (+ `bar_total_return`, `bars_superseded` if D2 and D3 are taken) and the 24 `provider_mappings`.** Hypertable, indexes chosen by step 1, comments on columns as in 0009. Migration smoke and `alembic check`.
3. **yfinance client and error mapping.** `auto_adjust=False` always and explicit; columns mapped by name, unexpected schema rejected (`schema_mismatch`); an empty frame is `empty_result`; `RATE_BUDGETS["yfinance"]` from step 1. New dependency: needs the hash-pinned lock, the license audit (its dependency tree, not just yfinance), the vulnerability scan, and an **arm64 wheel check** (an HTTP dependency with compiled wheels is exactly where arm64 breaks; ASSUMED available, unchecked). No key exists, so the FRED credential concern does not apply, but the same "fixed-text messages, never the library's" rule and the same two-sink smoke are built **at the same time as the client, not after** (that is what slice 5 taught).
4. **Daily backfill, 2 years x 24, as an entry point** (like `app.backfill`, the same `run_feed` and `run_recorded` path). Raw reconstruction from the `Stock Splits` column inside the window, `raw_basis` recorded, idempotent on `c_raw` / `c_split_adj` (**a re-run writes 0 rows**, the M2 gate clause), `c_total_return` going through the revision path. Nothing scheduled.
5. **Point-in-time read `bars_as_of`** in `services/`, with `as_of` a required keyword and a stated hindsight path, if any. Tests follow rule 9 with a second symbol AND a second interval whose data would win the ordering (a newer `ts`, a higher revision), and a mutation run for every keyed read. Re-timed at its own row counts.
6. **Beat: 16:30 daily and every 15 minutes in session**, on the ingest queue. **Blocked on both prerequisites.** Data age for bars on `/health/data` with thresholds from measured gaps (the 2-day floor's value becomes unit-aware here; see the note beside `FLOOR`).
7. **Smoke, HEALTH.md, `.env.example` (nothing new expected), gate review**, with the failure smoke against a URL-echoing stub for yfinance's failure kinds, named gaps stated, not implied.

### Where I expect this plan's numbers to be wrong

The three most likely to be falsified, in order: the 60-day intraday limit (it decides whether a missed bar is unrecoverable), the batch and spacing figure (it decides the budget), and the assumption that the primary key serves the reads that matter. If step 1 falsifies any, this entry gets an addendum saying so and the tally in CLAUDE.md rule 10 goes to four.

### Addendum 2026-11-15 — Kyle's decisions on D1–D7 (recorded this day; they were given in an earlier session and never written down)

Source: Kyle, 2026-11-15, restating the earlier session's answers. **Standing rule from this date: any decision Kyle gives is written to disk in the turn it is given.**
Status of each: **D1, D2, D4, D6, D7 decided. D3 and D5 PENDING.** IDEA-043 is unfrozen.

- **D1: DECIDED, yes.** Add the provenance columns (`source`, `feed`, `venue_scope`, `available_at`, `ingested_at`, `ingestion_run_id`) and `raw_basis`
  (`live` | `reconstructed` | `unreconstructed`). **`venue_scope` is stored as `provider_default` until consolidation is measured**; never `consolidated` before then.
- **D2: DECIDED, yes, with one constraint.** `c_total_return` revisions live in a separate table keyed like `macro_metrics`
  (`bar_total_return(symbol_id, interval, ts, revision, value, available_at, ingested_at, ingestion_run_id, source)`), written in the same transaction as the bar.
  `bars.c_total_return` holds **the latest vintage only**. **The point-in-time read must never select it: explicit column lists, never `SELECT *`** (a test pins the read's
  column list). **The column comment on `bars.c_total_return` must say it carries the latest vintage and must not be read by a replay.**
- **D3: PENDING (held until step 1 measures).** The deciding principle, from Kyle: **pick the option that makes it impossible for a replay to read a post-split rewritten
  value without explicitly asking for it.** If all three options (a: `data_version` in the key; b: derive adjusted columns on read; c: `bars_superseded`) satisfy that, pick
  the one with the fewest moving parts and **say which measured number made the choice moot.** Not decided here, and the step 1 benchmark does not touch it: it
  measured no versioned-read shape. Built in slice 8 either way.
- **D4: DECIDED, yes, plus an escape hatch.** Write the good symbols' rows, fail the run with `empty_result`, and **name the dead symbols**. Then distinguish
  **"failed today" from "gone"**: a delisted symbol needs a way out of the universe that is **not a code edit**, or the run stays red forever.
  *Proposal for the mechanism (mine, NOT yet approved):* an operator command that closes the symbol's `universe_membership` interval (data, with a recorded reason and date; never a
  delete of bars), after which the feed no longer requests it and the run can go green. The failure text says "empty today" for a first empty result and "empty for N
  consecutive runs: suspected gone, retire with <command>" after that; **N is not set and is ASSUMED to need a measured choice**. Retirement is always an explicit act, never automatic.
  Per-symbol quarantine states stay slice 7 (IDEA-023).
- **D5: PENDING, I propose and Kyle approves.** The rules, written out. They pin the three things Kyle named:
  1. **Open or close: a bar's `ts` is its OPEN (start) instant**, for daily and intraday alike. Daily `ts` = that session's `opens_at` from `trading_sessions`. The bar's end is `ts` + the
     interval for intraday, and `closes_at` from `trading_sessions` for daily (not `ts` + 24 h). Only completed bars are stored (end at or before the fetch); `available_at` = the fetch instant.
  2. **Timezone: `TIMESTAMPTZ`, i.e. an absolute UTC instant, stored and compared as instants.** The client converts to UTC at the boundary; a timezone-naive provider index is rejected
     (`schema_mismatch`). No SQL converts `ts` to a local zone or `CAST(ts AS date)`.
  3. **Session boundary is exchange-local, via the calendar:** a bar belongs to the session whose `[opens_at, closes_at)` contains its `ts`, found by a join to `trading_sessions`
     (built from `exchange_calendars` in the exchange's own zone, so DST, early closes and XKRX follow the exchange, not New York or UTC). The session date is `trading_sessions`' exchange-local date.
     Regular-session bars only (`prepost` explicit and false).
  **Not pinned by these rules (named, not hidden):** (i) **whether yfinance labels an intraday bar by its open or its close is ASSUMED open and not verified**; step 1(b)'s live probe decides it, and
  if it is the close the client shifts by the interval; (ii) what instant yfinance's *daily* label means (assumed: the exchange-local date, mapped to the session by date; unverified); (iii) the 15-minute grid on early-close
  days (the last bar may be short; unmeasured); (iv) ties and duplicate timestamps within a batch (the primary key rejects them, the client's behaviour on one is undecided).
- **D6: DECIDED as a handoff, not a recommendation.** Retention for 15-minute bars is logged as **IDEA-044**, a disk-budget question that comes to Kyle with a
  GB-per-year figure (315 bytes/row at 24 and at 500 symbols, plus what compression does to it) and no recommendation from me. The figures follow in `docs/IDEAS.md` once the benchmark's guard is verified. (Note: this plan's
  earlier "D6" text proposed 2 years; that proposal is withdrawn.)
- **D7: DECIDED, yes.** Out of this slice, by name: S&P 500 bars, Stooq, 1-minute bars, the intraday-to-daily aggregate, quarantine (slice 7), corporate actions (slice 8).
- **IDEA-043: UNFROZEN 2026-11-15**, before prices go on Beat: a missed price bar cannot be caught up the way a FRED poll can. The recreate test (`infra/idea043-beat-recreate.sh`) is run
  as its first step; its result decides the volume.

### Addendum 2026-11-15 — step 1(a) measured, IDEA-043 tested, and the disk guard's first version measured the wrong thing

**C: free space, MEASURED 2026-11-15 03:27, PowerShell `Get-PSDrive C`: 1,396.45 GB free of 1,862 GB** (`df -k /c` agrees: 1,952,874,564 KB total). Docker's VM disk is a different filesystem:
**`/dev/sdc`, 1,007 GB total, 937 GB free, MEASURED 2026-11-15 by `df` inside a container.** `docker_data.vhdx` (25.4 GB) lives on C: and grows into it.
Dev stack at the start of the session: `/health/ready` 200; `macro_metrics` **56,852 rows**, the figure slice 5 closed with. `benchpg` and its volume were removed by name; no prune was run.

**The guard.** The first version checked C: only. C: is the right limit for the VHDX growing, but it is **blind to the VM disk filling**, and it showed it: the first benchmark moved C: by 1 GB while the VM
disk took about 2 GB. `infra/bench-bars.sh` now watches **both**, each with its own floor (its own start value minus a 15 GB budget), kills the container if either trips, and reports the lowest value seen on each.
Tested by running it with a zero budget (it trips and cleans up). The benchmark was then re-run under the corrected guard: **lowest seen, host C: 1,395 GB (start 1,395), VM disk 935 GB (start 937): this run used 2,052 MB of the VM disk
and no measurable C:.** The first run's numbers were reproduced, so they stand.

**Step 1(a) results** (`docs/evidence/2026-11-15-bars-benchmark.txt`; MEASURED 2026-11-15, TimescaleDB 2.31.0 / PG 16.15, median of 5 `EXPLAIN ANALYZE`, **synthetic data** (smooth prices to 4 dp, random volume), weekly chunks,
draft columns = section 8 plus the D1 provenance columns):

| Rows | Uncompressed | Compressed | Latest per symbol | One symbol, 30 d | Cross-section at one ts |
|---|---|---|---|---|---|
| 12,024 (24 sym, daily) | 755.6 B/row (101 chunks) | **965.4 B/row, larger** | 1.6 / 5.0 ms | 0.6 / 0.8 ms | 0.04 / 0.22 ms |
| 162,888 (24 sym, 15 min) | 286.7 B/row | 108.6 B/row | 0.9 / 3.0 ms | 0.85 / 1.8 ms | 0.04 / 1.2 ms |
| 3,393,500 (500 sym, 15 min) | 246.0 B/row | 65.2 B/row | 4.2 / 20.7 ms | 3.0 / 1.9 ms | 0.15 / 19.4 ms |

(Timings are uncompressed / compressed.) **What this says:**
- **Rule 10 tally goes to four. This plan's hypothesis that the cross-section "needs an `(interval, ts)` index and that the macro conclusion does not carry" is FALSIFIED.** With only the primary key and the hypertable's default `ts` index, chunk exclusion plus that index
  answered the cross-section in 0.04 to 0.15 ms even at 3.4 million rows, and the extra index changed nothing (it cost 1.6 to 24 MB). **Limit of the claim:** each table held one interval, so a table mixing daily and 15-minute rows at the same `ts` was not measured.
  So, for what was measured, **no index beyond the primary key is needed**, as in slice 5.
- Compression is a real saving for 15-minute bars (0.38x and 0.27x) and a real read cost at 500 symbols (cross-section 0.15 to 19.4 ms). It **hurts the daily table because 12,000 rows over 101 weekly chunks is about 120 rows a chunk**: the daily table needs a much longer chunk interval, a step 2 choice not yet measured.
- **Not measured by this benchmark:** the 60-day intraday limit and the batch/spacing figures (both step 1(b), a live probe, not run), any versioned-read shape (D3), and real-price compression.
- 315 bytes/row (Kyle's figure) was **not reproduced**: the earlier run was not kept. Measured here: 246 to 287 uncompressed, a floor given the synthetic data. IDEA-044 carries both.

**IDEA-043 TESTED** (`infra/idea043-beat-recreate.sh`, `docs/evidence/2026-11-15-idea043-beat-recreate.txt`; MEASURED 2026-11-15, throwaway Redis and Beat on their own network, a `*/2` crontab entry, **one trial per case**, not the real schedule):
- **A, today's setup (schedule file in the container's `/tmp`), container removed and recreated across a missed firing: the recreated Beat did NOT run the missed firing.** The ASSUMED "loses its last-run memory" is now MEASURED for a recreate.
- **B, schedule file on a named volume: the recreated Beat ran the missed firing at once (caught up).**
- **A trap found on the way: a fresh named volume is root-owned and Beat runs as uid 10001, so the first attempt died with `Errno 13` on the schedule file.** The fix therefore needs the mount path created and owned by `flowdesk` in the image (`backend/Dockerfile`, so a new volume inherits it), not just a `volumes:` line in compose.
- **Not tested:** a plain `docker restart` (the container's filesystem survives it), catch-up of *several* missed firings (Celery's behaviour is to run a missed entry once, not once per miss: unverified here, and for 15-minute price bars that means a long outage still loses the bars in between), and the real Beat schedule. **Catch-up by Beat is therefore not a substitute for a gap check on the data**; that belongs to step 6.
- Not built: compose and the Dockerfile are untouched. IDEA-043 is unfrozen, and the build is step 6's first task, pending your go-ahead on the shape above.

### Addendum 2026-11-15 (second) — Kyle's decisions on IDEA-044, D5, D4, D3, IDEA-043, the index scope, and Monday's probe

Written the turn they were given. **Status after this addendum: D1, D2, D4, D5, D6/IDEA-044, D7 DECIDED. D3 DECIDED BY MEASUREMENT (both branches recorded below). IDEA-043 fix APPROVED.**

- **IDEA-044: CLOSED. No retention policy and no compression.** Reasoning (Kyle): C: has 1,396 GB free (MEASURED 2026-11-15); at 500 symbols the data grows about 1 GB a year uncompressed; compression takes the cross-section read from 0.15 ms to 19 ms to save space not needed.
  The unreproduced 315 bytes/row is replaced by the measured 246 to 287 range, with 315 marked unreproduced (`docs/IDEAS.md`). **Reopen only if the universe grows about 10x or free space on C: drops below about 100 GB.**
- **D5: APPROVED**, as proposed, with one addition and four answers.
  - **Rules (unchanged):** `ts` = the bar's open instant, `timestamptz` UTC; session boundaries exchange-local via `trading_sessions`.
  - **Addition: `available_at`.** For intraday bars it is **no earlier than `ts` + interval**; for daily bars it is **the session close** (`trading_sessions.closes_at`). **The point-in-time read filters on `available_at`, never on `ts`.**
    Filtering `ts <= T` returns a bar still forming at T, leaking up to one interval of the future into a replay. It is the same unifying rule as FRED. (This replaces the earlier text that made `available_at` the fetch instant;
    the fetch instant is `ingested_at`.) **Never store yfinance's in-progress bar**: its values are not final.
  - **The four gaps, answered:** (i) open- vs close-labelling is **MEASURED Monday, not assumed** (the earlier "assumed open" is withdrawn as a basis for any code); (ii) a daily bar's label is **the exchange-local session date, with `available_at` at that session's close**;
    (iii) **early-close days: only bars inside that day's `trading_sessions` window; no synthetic bars after an early close**; (iv) **a duplicate `(symbol, interval, ts)` in one batch: identical rows are deduplicated; differing rows fail that symbol for the run under D4. Never last-write-wins.**
- **D4: APPROVED, with an operator command.** The command **closes the symbol's universe membership by setting an end date (`valid_to`) and never deletes**, so a backtest as of an earlier date still includes the symbol (deleting would be survivorship bias).
  **Confirmed 2026-11-15** against migration 0003 and the dev database: `universe_membership` has `valid_to DATE NULL`, half-open `[valid_from, valid_to)`, `valid_to` the first day NOT a member; **the seed already never reopens a closed interval** (it reports drift, `reference_seed.py`).
  "Empty for N runs" raises a **distinct `likely_gone` failure that prompts the operator; it never retires a symbol on its own. N = 5 trading sessions, ASSUMED.**
  *Two things the table cannot yet say (flagged for the command's design, not decided):* it has no column for who closed it or why (`source` describes the opening row; the command needs a place for a reason, a migration or a convention); and **seeded membership starts on the seed date, so an as-of query before that date finds no members at all,
  retired or not**, which limits the survivorship protection to dates after the seed (already noted in CLAUDE.md for slice 6).
- **D3: DECIDED BY A MEASUREMENT MONDAY; both branches recorded now.** The measurement: *does yfinance return split-adjusted history with `auto_adjust=False`, checked against a known split on the pinned version?*
  - **Branch 1, split-adjusted: re-fetches rewrite old bars, so D3 is decided IN SLICE 6**, using the principle already recorded (pick the option that makes it impossible for a replay to read a post-split rewritten value without explicitly asking for it; if all three satisfy it, the fewest moving parts, naming the number that made the choice moot).
  - **Branch 2, truly raw: slice 6 never rewrites a bar, and D3 moves to slice 8.**
  - Existing evidence points at branch 1 (M0 spike 2, MEASURED 2026-07-31: `Close` is split-adjusted under `auto_adjust=False`, NVDA 10:1), but the yfinance version was not recorded then, so **it is not a measurement on the pinned version and does not decide the branch.**
- **IDEA-043 fix: APPROVED.** A named volume for Beat's schedule file; **the Dockerfile creates the mount path owned by uid 10001** (a compose-only fix hits the Errno 13 trap). It lands with a smoke that reruns case B, so it is not one trial forever (built this turn; see below).
  **Slice 7 (validation) requirement, logged: "Beat catch-up is not a gap check."** Beat running a missed entry once (or not at all) says nothing about which bars are missing; validation must detect gaps in the stored data itself, per symbol and interval.
- **Index result, scope recorded.** The falsification of "the cross-section needs an `(interval, ts)` index" **holds for single-interval tables only.** If the schema puts 15-minute and daily bars in one table, **re-measure before relying on it.** That, and the daily table's chunk interval, are **step 2 schema choices**.

### Step 1(b), Monday: the live yfinance probe (market hours). **RUN 2026-11-16; answers in "Addendum 2026-11-16 — step 1(b) measured" below.** Step 2's schema does not start until every answer below is in.

Every answer is recorded as MEASURED with its date, and this plan's table is updated, including any number it falsifies (rule 10 tally is four now).
1. Whether intraday bars are labelled by **open or close**.
2. Whether history comes back **split-adjusted with `auto_adjust=False`**, compared against a known split (decides D3's branch).
3. How the **in-progress bar** shows up, so it can be excluded.
4. The **60-day 15-minute history limit** (ASSUMED until then).
5. The **batch size and spacing that does not get throttled** (feeds `RATE_BUDGETS["yfinance"]`).
6. The **pinned yfinance version** (not recorded in the M0 spike files).

### 2026-11-15 — IDEA-043 built (approved the same day)

- `backend/Dockerfile`: `/var/lib/beat` created and owned by `flowdesk` (uid 10001) in the image, so a new named volume inherits it. `docker-compose.yml`: `beat` mounts `beat_schedule:/var/lib/beat` and runs `--schedule=/var/lib/beat/celerybeat-schedule`; the `/tmp` path is gone.
- **`infra/smoke-m2-beat-schedule.sh` PASSED (2026-11-15, about 9 minutes):** the built image's `/var/lib/beat` is uid 10001 (also run against the pre-fix image: it fails there, so the check can fail); compose carries the path and the volume and no `/tmp` schedule;
  **the real dev `beat`, force-recreated twice, still has its schedule file, owned by `flowdesk`**; and the throwaway test, **now with NO `chown` (the image alone provides the ownership)**: case A (control, `/tmp`) did not catch up (0 firings), case B (fresh named volume) did (1 firing). One trial per case per run; the smoke is how it is repeated.
- Two mistakes of mine on the way, both caught by the harness and fixed: the first Beat trial restarted 10 s after a firing, so no boundary fell in the downtime (invalid, caught by reading the timestamps), and the first smoke run used an image id `docker compose images -q` returned that Docker could not find (the smoke failed rather than passing on nothing).
- Gates: `ci-checks.sh lint` passes (ruff, format, mypy over 113 files); `/health/ready` 200 after the recreate.
- **Slice 7 (validation) requirement, in force: "Beat catch-up is not a gap check".** Catch-up runs a missed entry once; it says nothing about which bars are missing. Validation must find gaps in the stored data, per symbol and interval.
- Not run here: `ci-checks.sh test` (no Python changed) and the arm64 build of the Dockerfile change (a `RUN mkdir && chown`, nothing architecture-specific; CI's arm64 job is the gate).

### Addendum 2026-11-15 (third) — Kyle's decisions: D4 close reason, the pre-seed gap and hindsight as named limitations, gap-driven polling, one more probe item

Written the turn they were given.

- **D4 close reason: DECIDED.** A `close_reason` column on `universe_membership`, **added by migration in step 2, alongside the `bars` schema.** **Required whenever the operator command sets `valid_to`.**
  Allowed values: **`delisted`, `merged`, `removed_by_operator`.** **No "who" column: there is one operator.**
  *(Mine, for step 2 to settle, not decided: how "required whenever `valid_to` is set" is enforced. The natural form is a CHECK that `valid_to IS NULL OR close_reason IS NOT NULL`, with a second CHECK on the three values. Rows closed before the column exists: none, the seed never closes an interval, so the CHECK can go on without a backfill. To be confirmed against the live table in step 2.)*
- **D4 pre-seed gap: a NAMED LIMITATION, not a fix. Do not backdate `valid_from`.** Two different facts: *"FlowDesk tracked this from date X"* (what `valid_from` records) and *"this was tradable from date Y"* (what a backtest needs). Backdating would record the second as if it were the first.
  An as-of query before the seed date finding no members is the honest answer; **do not "fix" the empty pre-seed result by backdating.** Decided when backtesting is built.
- **Known limitation 2, recorded with the first: the universe was chosen in 2026, so its composition is hindsight-selected whatever the dates say.** Small for broad ETFs, but real. Together these are **known limitations of any backtest over this universe**, to be decided when backtesting is built.
  (Both are also the survivorship caveat already in CLAUDE.md for slice 6: the membership dates only protect against survivorship after the seed.)
- **Step 2 DESIGN REQUIREMENT: the price poll is gap-driven, not interval-driven.** Each run fetches **from the last stored bar per symbol** (subject to the provider's lookback limit), not "the last interval".
  So the number of missed Beat firings does not matter: **one run after any outage fills the gap.** That also makes the untested multi-firing catch-up (IDEA-043's "not tested" item) **a non-issue for correctness.**
  **If the gap exceeds yfinance's intraday lookback limit (MEASURED Monday), the run records an unrecoverable gap explicitly, rather than silently storing a partial window.**
  *(Mine, to settle in step 2/3, not decided: where an unrecoverable gap is recorded (a row naming symbol, interval, and the missing range; `ingestion_runs` alone cannot hold per-symbol ranges), and what a symbol with no stored bar yet fetches from (the backfill's window, not the lookback). Slice 7's gap detection reads that record.)*
- **Monday's probe (step 1(b)): "exactly as listed in the plan", plus one item (7).** When it checks split adjustment, it **records which symbol and which split date it compared against**, so the result can be re-run on a future yfinance version.
  The list is now seven items: (1) open- vs close-labelling, (2) split-adjusted or raw with `auto_adjust=False`, (3) the in-progress bar, (4) the 60-day 15-minute limit, (5) batch size and spacing, (6) the pinned yfinance version, **(7) the symbol and split date used for (2).**
  **Monday's date: the next market open after 2026-11-15, which is 2026-11-16 (a Monday).** Step 2's schema still does not start until all seven are in.

### Addendum 2026-11-15 (fourth) — Kyle's decisions on the three step 2 additions

Written the turn they were given. These settle the three items marked "mine, not decided" in the previous addendum.

- **`close_reason` check: DECIDED, two-way.** `CHECK ((valid_to IS NULL) = (close_reason IS NULL))` **plus** the three-value check (`close_reason IN ('delisted', 'merged', 'removed_by_operator')`, which a NULL passes).
  A reason on an open membership is as wrong as a closed one with no reason. Added by migration in step 2, alongside the `bars` schema.
- **Unrecoverable-gap record: DECIDED, in scope for step 2.** A table carrying **symbol, interval, the missing range, and when it was recorded.** **If a gap is later filled, the record is marked resolved with a timestamp. It is never deleted:**
  the history of what was missing is itself point-in-time data. (Column names, the key, and whether "range" is two columns or a range type are step 2 choices, not decided here.)
- **First poll: REVERSED from my proposal. The poll never fetches deep history, mirroring FRED's precedent.** A symbol with no stored bars **fails with a distinct `not_backfilled` reason, visible in the run**, just as the FRED poll refuses a series with no captured history (`NoBaseline`).
  **Deep history comes from a separate, recorded bars backfill command, the counterpart to `python -m app.backfill`, built in slice 6 so prices can actually flow.** The poll only extends what is already there.
  That keeps poll runs small and bounded and keeps every deep fetch on the record. This replaces my "a symbol with no stored bar fetches from the backfill window" proposal, which is withdrawn.
  (Consequence for the step list: step 4, "daily backfill, 2 years x 24, as an entry point", is that command; step 6's Beat entries come after it has run. The poll's per-symbol start is "the last stored bar", from the gap-driven requirement above.)

**State: nothing further until Monday's probe (2026-11-16).** Step 2's schema does not start until all seven probe answers are in.

### Addendum 2026-11-16 — step 1(b) measured: the live yfinance probe

**How (all MEASURED 2026-11-16):** two scratch scripts run in the pinned base image (`python:3.12-slim@sha256:5e2bdb1b…`, Python 3.12.16, linux/amd64), with yfinance installed
fresh from PyPI. Run 1 went from 17:10 to 17:12 UTC (**12:10 to 12:12 ET, regular session open**). Run 2 went from 17:13 to 17:28 UTC and repeated one fetch across a 15-minute boundary. The scripts and their output are kept for re-running:
`docs/evidence/2026-11-16-yfinance-probe-script{1,2}.py.txt` (renamed `.txt` so no linter picks them up; they are measurements, not repo code, as `probe_fred.py` was) and
`docs/evidence/2026-11-16-yfinance-probe-run{1,2}.txt`, plus `-run2.json`. Run 1 crashed at its batch section (`yf.shared` no longer exists in 1.8.0); everything before that point is in its output, and run 2 redid the batches.
Every call used `auto_adjust=False` and `prepost=False` unless noted. **Note on the clock:** in this session's first message I said the market was closed. That was wrong: Git Bash's `date` ignored `TZ`. The container's UTC clock is the record.

**The seven answers.**

1. **Open or close labelling: intraday bars are labelled by their OPEN.** MEASURED on SPY 15m: the session's first bar is `09:30` (a close label would start at `09:45`). 60m bars are `09:30`, `10:30`, `11:30`. At 12:10 ET the forming bar was labelled `12:00`, and at 12:28 ET a `12:15` bar had appeared.
   The index is tz-aware `America/New_York`. **D5 rule 1 holds as written; the client does not shift.** D5's "(i) ASSUMED open" is now MEASURED. The end-of-session `15:45` label was not observed, because the probe ran mid-session.
2. **Split adjustment with `auto_adjust=False`: `Close` IS split-adjusted (branch 1).** MEASURED twice, on two code paths (`Ticker.history` and `yf.download`):
   - **NVDA, 10-for-1, effective 2024-06-10** (yfinance `.splits`: `2024-06-10 09:30 -04:00: 10.0`). The 2024-06-07 `Close` is **120.888**. The as-traded close that day was 1,208.88 (public record, not fetched by this probe).
     The series runs straight through the split date (120.888 to 121.79). Raw prices would drop about 90%. This matches M0 spike 2's `Close` to the last digit.
   - **XLK, 2-for-1, effective 2025-12-05** (yfinance `.splits`), a universe member. The 2025-12-04 `Close` is 145.535 and the 2025-12-05 `Close` is 146.60, with no halving.
   - The `Stock Splits` column carries the ratio on the effective date (10.0, 2.0), so raw can be reconstructed from it inside a window.
3. **The in-progress bar is returned as an ordinary last row, with no flag, and its values change between fetches.** MEASURED: at 12:10:22 and 12:11:29 ET the `12:00` bar's Low, Close and Volume differed (744.32/744.37/481,054, then 744.23/744.61/523,803).
   **Today's daily bar is also returned during the session**, labelled `2026-11-16 00:00 -05:00`, with partial values (Volume 18.02 M at 12:10, 18.23 M at 12:13).
   **Exclusion rule, from the measurement:** drop an intraday row unless `ts + interval <= fetch instant`. Drop a daily row unless that session's `closes_at <= fetch instant`. **Nothing in the frame marks the row; only the clock can.**
4. **15m look-back: a `start=` request is refused when it reaches back more than 60 days.** MEASURED on SPY:
   - `start` = today − 59 days (2026-09-18) returned 1,077 bars from `2026-09-18 09:30`.
   - `start` = today − 60 days (2026-09-17, which yfinance sent as 04:00 UTC, 60.55 days before the end) raised `YFPricesMissingError: … The requested range must be within the last 60 days.` So did −61, −62 and −70 days.
   - **But `period="60d"` returned 1,545 bars back to 2026-08-24 (84 calendar days, about 60 trading sessions), and `period="61d"` was refused.** So the limit is 60 calendar days on the `start=` path and 60 sessions on the `period=` path.
   - Not measured: whether the refusal comes from yfinance or from Yahoo, and whether `period=` reaching older bars holds on other days. **The gap-driven poll fetches from a `start`, so its operative limit is 60 calendar days.** Data older than that may still be reachable through `period=`. Whether the unrecoverable-gap record uses the conservative 60 calendar days is a step 2/3 choice and not decided here.
5. **Batch and spacing: NO throttling was seen; the threshold was NOT found** (deliberately not provoked). MEASURED:
   - One `yf.download` of all 24 tickers (`threads=True`, `group_by="ticker"`): daily 2y took **0.96 s** (500 rows, 0 empty), 15m 60d took **1.12 s** (1,545 rows), and 15m 5d took **0.73 s**.
   - Five more 15m 5d batches at 2 s spacing took 0.61 to 0.68 s each, with 0 empty. `threads=False` took 2.54 s.
   - In total, about 10 batches of 24 in run 2 (a few hundred per-ticker requests) plus about 45 single calls in run 1, all within about 20 minutes. No 429, no empty frame and no error except the deliberate dead ticker.
   - **What this supports, and no more:** one 24-ticker batch per 15 minutes is far inside anything observed. `RATE_BUDGETS["yfinance"]` is step 3's to set, and any figure above about one batch per 2 s is unmeasured.
6. **Pinned version: yfinance 1.8.0**, the newest on PyPI on 2026-11-16 (`pip index versions`). It brought pandas 3.1.1, numpy 2.5.5, requests 2.34.2, curl_cffi 0.17.0, peewee 4.5.3, platformdirs 4.14.5, beautifulsoup4 4.15.0, protobuf 7.36.2, websockets 17.4 and pytz 2026.6.
   It is not in any lock yet. Step 3 adds it hash-pinned with the licence and arm64 checks. **curl_cffi has compiled wheels: that is the arm64 check's target.** Also seen in 1.8.0: `raise_errors=` is deprecated (use `yf.config.debug.hide_exceptions`), and `yf.shared._ERRORS` is gone.
7. **Re-run reference for (2): NVDA, 10:1, effective 2024-06-10, compare the 2024-06-07 `Close` (120.888 adjusted; 1,208.88 as traded). Also XLK, 2:1, effective 2025-12-05, compare 2025-12-04 with 2025-12-05.**
   To re-run on a future version: script 1's section 2 and script 2's section D. A version that returns about 1,208.88 for NVDA on 2024-06-07 is truly raw and flips the branch.

**D3: BRANCH 1, split-adjusted. D3 is decided IN SLICE 6**, by the recorded rule ("split-adjusted means D3 is decided in slice 6"). A re-fetch rewrites old bars' prices whenever a split lands after them. **Only the branch is decided here.** Choosing among (a), (b) and (c) is Kyle's, under the recorded principle, and comes before step 2's schema.
Measured facts that bear on it, without choosing:
- **Five universe members split inside the 2-year backfill window**: XLK, XLE, XLY, XLU and XLB, all 2:1 on 2025-12-05, as yfinance reports. So the first backfill already spans a split, and `raw_basis = reconstructed` is needed in slice 6, not only in slice 8.
- **`Adj Close` is rewritten after the fact even when `Close` is not**: NVDA 2024-06-07 `Adj Close` was 120.679 in M0 spike 2 (2026-07-31) and is 120.544 today, while `Close` is identical. That is D2's premise, now measured.

**Findings outside the seven, which bear on step 2/3 and are flagged, not resolved:**
- **F-a. A completed bar is not final.** MEASURED on SPY 15m: the `11:45` bar (ended 12:00) had Volume 772,495 at 12:10, 787,021 at 12:11, 794,162 at 12:13 and **845,187 at 12:28**, still changing 28 minutes after it closed.
  The `12:00` bar's **Open** changed after it ended (745.420 to 745.410, between 12:13 and 12:28).
  - This falsifies the premise under D5's "only completed bars are stored", namely that a completed bar's values are final. It also bears on the M2 gate's "a re-run writes 0 rows" for recent intraday bars, and on D5 (iv)'s "differing rows fail that symbol".
  - How long revisions continue was not measured. **Rule 10 tally: five.** The premise was implicit, not a written number; I count it because code would have been built on it. Kyle may rule otherwise.
- **F-b. Some 15m volumes don't add up.** MEASURED on SPY: today's 15m bars from `10:30` to `11:30` carry Volume 12.04 M, 14.06 M, 15.28 M, 16.12 M and 16.85 M, rising every bar and identical in both fetches. The other bars carry 0.7 to 4.7 M.
  The 15m bars sum to about 86 M; the daily bar said 18.2 M. Seen on SPY only, on one day; other symbols were not checked. This is a slice 7 validation case (an intraday-vs-daily volume reconciliation); nothing is decided here.
- **F-c. `yf.download` returns a timezone-NAIVE index for daily bars** (`2024-11-18 00:00:00`, tz `None`). Its 15m index is tz-aware, and `Ticker.history` daily is tz-aware midnight `America/New_York`.
  D5 says a naive provider index is rejected (`schema_mismatch`), so a batched daily fetch would always be rejected as written. The naive value is the exchange-local session date (D5 (ii)). Whether the client accepts a naive *daily* date and maps it to `trading_sessions`, or fetches daily another way, is a D5 amendment for Kyle.
- **F-d. Columns, by name.** A batch with `actions=True` returns `Open, High, Low, Close, Adj Close, Volume, Dividends, Stock Splits` **and `Capital Gains`** (fund distributions). `Ticker.history` 15m with `actions=False` returns the first six. Step 3's by-name mapping must expect `Capital Gains`, or `schema_mismatch` fires on every batch.
- **F-e. A dead ticker in a batch** (`ZZZQXFAKE` with three live ones): the batch succeeds. The dead ticker appears as an all-NaN column group, yfinance logs a 404 and "1 Failed download", and the live tickers are complete. This is the shape D4's "name the dead symbols" detects. It matches M0 (MEASURED 2026-07-31).
- A 1m prepost fetch's last row was a live quote (Volume 0, O=H=L=C). 1m is out of scope (D7), so this is recorded only.

**Plan numbers updated.** "Where I expect this plan's numbers to be wrong":
- The 60-day limit is **confirmed for `start=`** and **exceeded on `period=`** (not counted as a falsification).
- The batch figure is **bounded below and not found**.
- The primary key result was already falsified at step 1(a).
- New: F-a, counted (tally five).

**State: probe complete, D3 branch 1. Step 2's schema has NOT started.** Before it can start: Kyle's D3 option, and Kyle's calls on F-a (revised completed bars), F-c (naive daily index) and the gap-limit choice in item 4.

### Addendum 2026-11-16 (second) — Kyle's decisions on D3, F-a, F-c, the gap limit, F-b and F-d

Written the turn they were given. **Status after this addendum: D3 DECIDED (option b). D5 AMENDED twice (F-a settle-then-store, F-c naive daily index). Gap limit DECIDED. Step 2's schema waits only on S being MEASURED, then on Kyle.**

- **D3: option (b). Raw storage, splits applied on read.** This also decides IDEA-035's version home (`docs/SPEC-DEFECTS.md`).
  - **Stored prices are as-traded**, rebuilt at ingest as yfinance's split-adjusted price × the ratios of every split whose effective date is after the bar's session date. **Volume is divided by the same ratios.** These rows get `raw_basis = reconstructed`.
    **The rebuilt values inherit the provider's rounding** (yfinance returns the adjusted value to its own precision; multiplying back does not restore digits the provider never sent).
  - **A minimal `splits` table lands in slice 6:** symbol, effective date, ratio, first-seen timestamp. Everything else about corporate actions (confirmation workflow, dividends, the split detector, `corporate_actions`) stays in slice 8.
  - **The adjusted read as of T applies only splits with an effective date on or before T.** Stored bars never change when a split happens: a new split is a new `splits` row, not a rewrite.
  - **After a split, a re-fetch must rebuild identical raw prices. A mismatch is a validation failure, not an overwrite.**
  - **Reference check, recorded:** NVDA 2024-06-07 rebuilds to about **1,208.88** (adjusted `Close` 120.888 × 10, split effective 2024-06-10; MEASURED 2026-11-16, step 1(b) item 2). Alongside it, from the same measurement: XLK 2025-12-04 rebuilds to about 291.07 (145.535 × 2) and 2025-12-05 stays 146.60 (no ratio applies on or after the effective date).
  - Rejected: **(c)**, because its default read returns rewritten values; **(a)**, because every split rewrites full history.
- **F-a: settle-then-store. This amends D5.**
  - **An intraday bar is stored only after its settle point: the session close plus a settle window S.** `available_at` is the settle point, not `ts` + interval: the settled values did not exist before then.
    (This replaces D5's addition "for intraday bars `available_at` is no earlier than `ts` + interval".)
  - **The same rule applies to daily bars and to the backfill.**
  - **S is UNMEASURED.** Protocol (Kyle): re-fetch today's SPY and two other symbols' 15m bars at the close, at close + 1 h and tomorrow before the open; record when the values stop changing; set S with a margin; mark it MEASURED. Progress is below.
  - **Spec check, done this turn: §6.1 and §11 DO need intraday bars during the session. Conflicts reported to Kyle, not resolved** (list below).
  - **Rule 10 count stands at five. F-a counts** (Kyle's ruling; the "Kyle may rule otherwise" note is settled).
- **F-c: accepted, daily only. This amends D5 rule 2.** For daily bars, a timezone-naive index is read as the **exchange-local session date** and **must match a `trading_sessions` row, otherwise `schema_mismatch`**. A timezone-naive intraday index is still rejected.
- **Gap limit: 59 calendar days on `start=`.** The poll records an unrecoverable gap beyond that. **No `period=` fallback path** (step 1(b) item 4's `period="60d"` reach is not used).
- **F-b** (15m volumes that don't sum to the daily bar) **goes to slice 7's validation list.**
- **F-d** (`Capital Gains`) **goes into step 3's column mapping.**

#### Settle measurement, first points (MEASURED 2026-11-16; S still UNMEASURED)

**The protocol's first two points were missed:** this turn started at 18:25 ET (PowerShell `Get-TimeZone` and the UTC clock; Git Bash's `TZ=` output is wrong again and must not be used), after both the close and close + 1 h.
What was done instead:
- **Post-close snapshot, 23:26:46 UTC (18:26 ET, close + 2 h 26 min):** SPY, QQQ, XLK, 15m and 1d, `period="5d"`, yfinance 1.8.0 in the pinned base image (`docs/evidence/settle/`: `probe.py.txt`, `Dockerfile`, `run.sh`, `snap-*.json`, `run.log`).
- **Compared with step 1(b)'s midday SPY fetches** (12:13 and 12:28 ET, `2026-11-16-yfinance-probe-run2.json`):
  - The `09:30` to `11:30` bars are **identical** in all three fetches.
  - `11:45` (ended 12:00) changed between 12:13 and 12:28 (Volume 794,162 to 845,187), then **held** to 18:26.
  - **`12:00` (ended 12:15) changed after 12:28** (Volume 728,992 to 762,747): still revising at least 13 minutes after it ended, consistent with F-a.
  - So mid-session, revision of a completed bar was seen up to 28 minutes after it ended and not beyond. **This is a lower bound on revision time, not S**: what happens across the close and overnight is what the scheduled snapshots measure.
- **The end-of-session label is now MEASURED:** 26 bars today, `09:30` to `15:45` (step 1(b) item 1's open note).
- **A hint, not a finding:** every earlier day's daily volume in the snapshot is a round hundred (SPY 40,698,600; 39,151,000; …) and today's is not (SPY 67,827,833; QQQ 41,810,606; XLK 12,985,041). That suggests the daily bar is replaced by a consolidated figure overnight. The pre-open snapshot tests it.
- **F-b recurs, wider than SPY** (same snapshot): 15m-volume sum / daily volume is 2.54 for SPY today and **11.55 for QQQ today**, 0.81 to 1.36 on earlier days for SPY and QQQ, and 0.92 to 1.10 for XLK. For slice 7.
- **Scheduled** (UTC; a background loop in the Claude session, because registering a Windows scheduled task was refused): 2026-11-17 14:00 (09:00 ET, pre-open), then Tuesday's close at 21:01, 21:10, 21:20, 21:35, 22:05, 23:05, 2026-11-18 01:00, and Wednesday pre-open at 14:00. Tuesday's run covers the at-close and close + 1 h points that today's missed.
  The loop dies if the session ends; a sleeping laptop delays a snapshot, and each snapshot records its real UTC time.

#### Spec conflicts with settle-then-store (reported, NOT resolved)

Kyle's rule for anything that needs intraday bars during the session: **read a provisional live fetch, labelled provisional, never stored.** Consumers the spec names during the session:
1. **§6.1**: "15-minute-cadence quote/bar polling during market hours for the ETF universe". Polling is compatible; *storing* from it is not, under settle-then-store.
2. **§11 `ingest_bars_intraday`**: `*/15 min 9:30–16:00 Mon–Fri`. As written it is a storing ingest during the session. Under F-a it becomes one post-settle run per session (plus, possibly, a provisional live read that stores nothing). §11's schedule must change; that is a spec correction.
3. **§11 Phase 1 compute tasks on intraday data:** `compute_breadth` "hourly intraday", `compute_rotation` "every 30 min intraday", `compute_regime` after each breadth run, `compute_alerts` chained after each, `compute_premiums` "after each foreign/US/fx ingest tick", and `ingest_foreign_bars` `*/15 min during each configured foreign exchange's hours` (XKRX; whether settle-then-store covers foreign bars is not stated). §6.12's rate-budget paragraph also says "intraday breadth is computed from the 11 sector ETFs + RSP/SPY".
4. **Phase 2 (not M2, listed for completeness):** `compute_themes` and `compute_pairs` every 30 min intraday, `compute_sympathy` "each intraday bars tick", `compute_scanner` `*/5 min market hours`.
5. **The direct conflict: hard rule §17.1, "every number shown in the UI or a brief exists in Postgres first. Never compute ad hoc in a handler."** An intraday rotation, breadth or premium figure computed from a never-stored provisional fetch either is not shown, or is shown without existing in Postgres. Storing the *computed* figure while its inputs are unstored breaks replay comparability (`signals/` purity exists so live and replay agree): a replay at T cannot reproduce it, because its inputs were never kept.
   **Options I can see, none chosen:** (i) no intraday signals in Phase 1; signals run after the daily settle only. (ii) Store provisional bars in a separate, clearly provisional table that no replay reads, with intraday outputs marked provisional too. (iii) Show intraday figures as provisional and unstored, as an explicit exception to the "exists in Postgres" rule.
   Each is Kyle's; (ii) contradicts "never stored" as given and is listed only so the trade-off is visible.

**State: decisions recorded. S UNMEASURED; the pre-open snapshot comes first (2026-11-17 09:00 ET). Step 2's schema has NOT started, and will not until S is MEASURED and Kyle has seen it.**

### Addendum 2026-11-17 — Kyle's decision on the settle-then-store spec conflicts; what the M2 gate says

Written the turn it was given. **Settle-then-store is narrowed, not dropped. The rule: provisional values never pass for settled ones.** "Never stored" (previous addendum) is replaced.

- **`bars` (settled): unchanged.** Stored only after its settle point; `available_at` is the settle point. **Every read and every backtest at daily or slower granularity reads only this table.**
- **A provisional table (named in step 2)** holds intraday bars fetched during the session. **Append-only**; each row's `available_at` is its fetch time; **only rows that changed since the previous fetch are stored**.
  Only intraday computations read it (rotation, breadth, premiums, alerts), and their replays read it as of T, which is exactly what was known at T.
  **Nothing ever moves a row from provisional into `bars`**; the settled ingest fetches independently after the settle point.
- **§17.1 is preserved:** every intraday figure is computed from stored provisional rows, so it exists in Postgres first and can be reproduced. **Option (iii) rejected; option (i) not needed.**
- **Foreign bars (XKRX): the same two-table rule**, settle point from that market's `trading_sessions` close.
- **§11 correction logged as IDEA-045** (`docs/SPEC-DEFECTS.md`): `ingest_bars_intraday` becomes a provisional ingest every 15 minutes during the session plus a settled ingest once per session after the settle point. Flagged for the next correction release.
- **Build order (Kyle): steps 2–4 build the settled bars and the backfill first.** Whether the provisional table is a later step of slice 6 or waits for the milestone that builds the intraday computations depends on the M2 gate.
- **Settle snapshots:** now run from a Windows scheduled task Kyle registered. The in-session loop was stopped by Claude Code on 2026-11-16 at about 19:00 ET (low memory while idle) before any scheduled point, and is not restarted.
  When the Wednesday 09:00 ET snapshot is in: compare everything, set S with a margin, mark it MEASURED, report. Step 2's schema starts after that.

#### What the M2 gate says (read 2026-11-17, `docs/architecture.md` §16 line 1109; reported, ambiguities NOT resolved)

**The M2 gate does not require intraday ingest.** Its clauses: re-ingesting identical payloads yields identical canonical rows; synthetic split ≠ dilution; extreme move quarantines, never silently accepts;
confirmed split creates a new data version without deleting raw; holiday/half-day fixtures pass; backfill progress survives worker restart. Plus the relocated IDEA-032 clause (killing the worker and a provider each give a documented health state).
M2's scope text names "raw + split-adjusted + total-return price fields" and "rate-budget enforcement" and nothing intraday. **By Kyle's rule, the provisional table therefore waits for the milestone that builds the intraday computations.**

Ambiguities for Kyle, not resolved:
1. **Which milestone that is.** §16 does not say "intraday" for M4 (Rotation, the first vertical slice), but §11 schedules `compute_rotation` "every 30 min intraday" as Phase 1. **M6 is the first to say it explicitly:** "daily constituent breadth + sector-level intraday". M7 (Premium Monitor) asks for "per-input timestamps (US price, foreign price, FX) each displayed", which reads as in-session but does not say so. Candidates: M4 (if §11 binds rotation's intraday cadence), M6 (the first explicit mention), or M7.
2. **"Half-day fixtures pass"** applies to the settled 15m bars too (D5 (iii): only bars inside an early close's window). Satisfiable without the provisional table; noted only because "half-day" is the one gate clause that touches intraday bars.
3. **"Re-ingesting identical payloads yields identical canonical rows"** is about `bars`. The provisional table is append-only by design and is not "canonical"; if a later reader applies the clause to it, its change-only rule is what makes a re-run write 0 rows. Recorded so it is not mistaken for a gap.

### Addendum 2026-11-17 (second) — Kyle's ruling on the provisional table's milestone

- **Not decided by milestone. Rule: the provisional table is built by the first milestone that implements a computation §11 schedules intraday.**
- **M4 planning item, logged:** decide whether `compute_rotation`'s 30-minute intraday cadence (§11) binds M4. Recorded in IDEA-045 (`docs/SPEC-DEFECTS.md`) and in `CLAUDE.md` so M4 planning finds it.
- Gate ambiguities 2 (half-day fixtures) and 3 (re-ingest clause vs. the provisional table): **noted, no action.**

### Addendum 2026-11-17 (third) — settle measurement, interim comparison (S NOT set)

Committed now on Kyle's instruction, so the evidence does not live on one machine only. **S is not set and no revision policy is chosen; both are Kyle's, from Wednesday's numbers.**
Evidence: `docs/evidence/settle/snap-*.json` (three snapshots), `compare.py.txt` (diffs each consecutive pair, by symbol, interval, session date and field) and its output `compare-2026-11-17-interim.txt`.
Snapshots (MEASURED): Mon 2026-11-16 18:26 ET (in-session loop), Tue 01:55 ET (Kyle's scheduled task, first run), Tue 09:00 ET (pre-open).

**Mon 18:26 → Tue 01:55 ET (overnight):**
- **15m, Monday's session: Volume only, no price changed.** SPY 17/26 rows, QQQ 21/26, XLK 13/26. Largest: SPY 14:45 32,445,065 → 1,482,190 (−95.4%), QQQ 11:15 62,254,615 → 1,084,932 (max rel −98.6%), XLK 09:45 3,157,665 → 938,154 (−70.3%). The large ones are the F-b inflated bars being corrected; others moved by tens of shares.
- **15m, sessions Nov 10–13: no change.**
- **1d, Monday: replaced.** Volume to a round hundred (SPY +1.79%, QQQ +2.37%, XLK +1.33%); QQQ Open and High by 0.01, XLK Low by 0.0001.
- **1d, Friday Nov 13: Volume revised a session later** (SPY +0.86%, QQQ +0.72%; XLK unchanged). A round-hundred daily volume is therefore not final either.
- **Not every F-b case was corrected:** SPY Nov 10 (15m sum / daily 1.35) and QQQ Nov 13 (1.35) still disagree. Still slice 7.

**Tue 01:55 → 09:00 ET: no value changed in any common row.**

**Limit for Wednesday's report:** each snapshot fetches `period="5d"`, so how far back daily revisions reach is visible only within five sessions (Wednesday's snapshot covers about Nov 11–17). A revision older than that cannot be seen by this probe.

**Wednesday's report (Kyle, 2026-11-17), expected per interval, not one number:** S for 15m prices and 15m volume separately; S for daily bars, including whether Monday's daily bar changes again on Tuesday night and how far back daily revisions reach; and, after each proposed S, the count of changed rows and the size of the changes, by field. Kyle decides from that whether post-S revisions are stored as dated versions (like macro) or flagged by validation. **Step 2 still waits.**

### Addendum 2026-11-17 (fourth) — daily look-back widened; revision policy for `bars` decided

- **Probe change, made at 09:40 ET, before the 16:01 ET run:** `docs/evidence/settle/probe.py.txt`'s daily fetch is now `period="3mo"`; the 15m fetch stays `period="5d"`.
  **Snapshots from 2026-11-17 16:01 ET onward carry about 3 months of daily bars; earlier ones carry 5 sessions.** A revision to an older daily date therefore becomes visible only from the 16:01 baseline onward, never against the three earlier snapshots.
  `compare.py.txt` diffs only rows present in both snapshots, so mixing the two periods is safe; its "rows only in one snapshot" line now names the widening.
- **Decided (Kyle), so step 2 is not blocked by the revision policy: `bars` holds the FIRST settled value, and it is never overwritten.**
  Whether later revisions are kept as dated versions in a separate table (like D2's `bar_total_return`) or only flagged by validation is **decided from a two-week tail measurement. Either answer leaves `bars` unchanged.**
  Consequence for the M2 gate's "re-ingesting identical payloads yields identical canonical rows": a re-fetch that returns a revised value does not change `bars` under either answer.
- **Gate for step 2:** Wednesday's report still sets S per interval (15m prices, 15m volume, daily). **Step 2's schema starts once Kyle has approved S for both 15m and daily. It does not wait for the two-week tail.**

### Addendum 2026-11-17 (fifth) — the two-week tail measurement is scheduled

**Kyle registered a Windows scheduled task, `FlowDesk-settle-daily`:** daily at 09:00 ET from 2026-11-19 through 2026-12-03, running the same `docs/evidence/settle/run.sh` (so the same probe: 15m `5d`, 1d `3mo`).
Read back 2026-11-17 (`Get-ScheduledTask`, read-only): start `2026-11-19T09:00:00`, every 1 day, end boundary `2026-12-03T09:30:00`, action `bash.exe …/docs/evidence/settle/run.sh`.
It follows `FlowDesk-settle-probe`, whose last trigger is 2026-11-18 09:00 ET. The tail decides whether post-S revisions are kept as dated versions or only flagged by validation; `bars` is unchanged either way.
**The snapshots land on this machine only until committed:** commit them as they arrive, not just at the end.

### Addendum 2026-11-18 — settle measurement: the S proposal per interval (S NOT set; Kyle's)

Evidence (`docs/evidence/settle/`): five snapshots, `compare-2026-11-18.txt` (consecutive diffs, `compare.py.txt`), and
`settle-by-s-2026-11-18.txt` from the new `settle_by_s.py.txt`, which also reads step 1(b)'s Monday 12:13 / 12:28 ET SPY fetches.
It brackets each revision of a **completed** bar as (previous look, next look], ages from the session's 16:00 ET close, then counts,
for each candidate settle point P, revisions certainly after P ("after"), possibly after ("straddles"), and the rows whose post-P
window was looked at twice ("tested", with the gap from P to the first look).

#### What ran and what didn't (MEASURED 2026-11-18, System event log and `Get-ScheduledTaskInfo`)

- **Tuesday's post-close points (16:01, 16:10, 16:20, 16:35, 17:05, 18:05 ET) and Wednesday 09:00 ET never ran.** The laptop slept
  15:08–18:54, 19:04–19:59 and 20:10–11:12 (Wed); the 16:01–18:05 and Wed 09:00 triggers did not wake it (wake source "Unknown").
- **The 20:00 trigger did wake it** (wake source names `FlowDesk-settle-probe`), but Docker Desktop did not come up within `run.sh`'s
  10-minute wait (`run.log` 01:10Z: `failed to connect to the docker API`), and the machine slept again at 20:10, when the script ended.
- **A substitute was taken by hand: Wed 2026-11-18 11:15 ET** (same `run.sh`, after starting Docker Desktop; in session, so
  Wednesday's rows are still forming; `settle_by_s.py.txt` drops observations made before a bar ended, `compare.py.txt` does not).
  It is the **first snapshot with 3 months of daily bars** (the 16:01 run that was to be the first never happened).
- **A fifth snapshot, Wed 11:18 ET, was not taken by hand:** `FlowDesk-settle-probe`'s StartWhenAvailable catch-up of the missed 09:00
  run (LastRunTime 11:18:17, result 0), which had been waiting for Docker since the 11:12 wake. Committed with the others.
  Being 3 minutes after the 11:15 one, it tests nothing about settling (`settle_by_s.py.txt` counts a row as tested only when its
  post-P looks are at least 12 h apart). **In-session, it shows SPY's 11:00 bar (ended 11:15) going from 735,252 to 15,549,598 shares
  within 3 minutes of ending** (XLK's 11:00: +6.75%): F-b's inflated volume appears on a just-completed bar, not only later.
- **Consequence:** the 16:00–18:26 window is unobserved for every session, and **Tuesday's session has one look only, so it measures
  nothing.** Night-1 behaviour (the first overnight after a session) is observed for **one session (Monday 11-16), three symbols.**
- **Risk to the two-week tail (`FlowDesk-settle-daily`, from 2026-11-19 09:00):** same settings as the probe task (WakeToRun,
  StartWhenAvailable, DisallowStartIfOnBatteries, StopIfGoingOnBatteries, Interactive). The same failure is expected unless Docker
  Desktop is running before 09:00. Not changed: the task is Kyle's.

#### Findings (MEASURED, from the five snapshots and the two midday fetches)

- **15m prices: no revision of a completed 15m bar's price observed at any age after the close** (earliest look close+2h26, Monday;
  390 rows over five sessions, but only Monday's looked at within hours). 16:00–18:26 unobserved.
- **15m volume: revised once, in night 1, never again.** Monday's 51 rows changed in (close+2h26, close+9h55]; none changed in
  (+9h55, +17h00] or (+17h00, +43h15]. Friday 11-13's rows did not change in its night 2 (Mon night) or night 3 (Tue night).
  Sizes: 22 rows are the F-b inflated bars corrected (−49.6% to −98.6%, max abs 61.2 M shares, QQQ 11:15); the rest move by
  under 0.5% (median of all 51: 0.14%). In-session, a completed bar's volume was seen changing up to 28 min after it ended (F-a).
- **Daily: revised in night 1 AND night 2, not in night 3.**
  - Night 1 (Monday's bar, Mon night): Volume all three (SPY +1.79%, QQQ +2.37%, XLK +1.33%; to round hundreds); prices by at most
    0.01 (QQQ Open, High) and 0.0001 (XLK Low).
  - **Night 2, Volume only:** Monday's bar again on Tue night, bracket (Tue 09:00, Wed 11:15] (SPY +0.24%, QQQ +1.81%, XLK +0.10%;
    still round hundreds); Friday's bar on **Mon** night, bracket (Mon 18:26, Tue 01:55] (SPY +0.86%, QQQ +0.72%, XLK none).
    **Friday's revision followed the next session, not the calendar,** so S is counted in sessions, not hours.
  - Night 3 onward: no change (Friday's bar on Tue night; Thursday 11-12's on Mon and Tue nights). Tested on 9 rows, 3 sessions.
  - **Every revision whose bracket closes before Wednesday fell between 18:26 and 01:55 ET; none in 01:55–09:00.** That the Tuesday-night
    revision also fell before 09:00 Wed is ASSUMED from this (its bracket reaches 11:15).
- **How far back daily revisions reach: unmeasured beyond five sessions.** Only one 3-month snapshot exists; the tail measures it.

#### Proposal per interval (Claude's; NOT set. Settle point P for session D, in ET, counted in sessions)

| Interval | Proposed P | Margin over the last observed revision | After P (certain / possible) | Tested |
|---|---|---|---|---|
| 15m prices | **09:00 on D+1** (same as volume: a `bars` row stores prices and volume together, so a row settles at the later of the two) | none observed at all after close+2h26 | 0 / 0 | 390 rows; Monday from P+0h |
| 15m volume | **09:00 on D+1** | ≥ 7h05 past the latest bracket end (01:55) | 0 / 0 | 390 rows; Monday from P+0h, Friday from P+9.4h |
| Daily | **09:00 on D+3** | one full session and night past night 2 | 0 / 0 | 9 rows (11-10, 11-11, 11-12); Monday's D+3 is 11-20 |

Counts and sizes at the other candidates (`settle-by-s-2026-11-18.txt`):
- 15m volume at close+2h25: **51 rows after** (max rel −98.61%, median 0.14%); plus 1 straddling (SPY 12:00, +4.63%, in session). At D+1, D+2, D+3: 0 / 0.
- 15m prices: 0 / 0 at every candidate.
- Daily prices at close+2h25: 2 rows after (Open, High, Low; max abs 0.01). At D+1 and later: 0 / 0.
- Daily volume at close+2h25: 5 rows after (max rel 2.37%, median 1.33%); at **D+1 09:00: 5 rows after** (max rel 1.81%, median 0.72%: the
  night-2 revisions); at **D+2 09:00: 0 after, 3 straddling** (the Tuesday-night bracket; 0 if the ASSUMED overnight timing holds);
  at D+3: 0 / 0.

**For Kyle to weigh (not decided):**
1. **Daily latency.** P = D+3 means a session's daily bar reaches `bars` three sessions later (Monday's on Thursday morning); D+2 means two.
   Every daily read and backtest reads only `bars`. The cost of a shorter P is volume only: prices settle by D+1 (max move 0.01).
   Shortening P for daily is possible only if a first-settled volume that later moves by up to ~2.4% (observed) is acceptable, or if the
   tail's answer (dated versions) absorbs it.
2. **Evidence is thin:** night 1 is one session, three large ETFs; weekend and holiday behaviour is one Friday. The tail (if it runs) adds
   about ten sessions of night-2/night-3 evidence for daily and none for 15m night 1 except where a snapshot lands both before and after it,
   which a 09:00-only schedule never does (it brackets a whole close-to-open span).
3. **The P rule needs `trading_sessions` for D+k**, which the seed already holds.

**State: S proposed, NOT set. Step 2's schema has NOT started; it starts once Kyle approves S for 15m and daily.**

### Addendum 2026-11-18 (second) — Kyle's decision: S is set (MEASURED 2026-11-18)

Written the turn it was given. **S is MEASURED 2026-11-18** (five snapshots and two midday fetches; `docs/evidence/settle/settle-by-s-2026-11-18.txt`).
Counted in **sessions**, not calendar days (Friday's daily bar was revised after Monday's session, not over the weekend); D+k is the k-th
session after D in `trading_sessions`. All times ET.

- **15m bars: settle point = 09:00 on D+1** (the next session). Prices and volume together: a `bars` row settles at the later of the two.
  **Zero changes after it in 390 rows.** Limits: rests on **one session's night 1 (Monday 2026-11-16) and three symbols** (SPY, QQQ, XLK);
  **the 16:00–18:26 window is unobserved.**
- **Daily bars: settle point = 09:00 on D+2.** Prices are final by D+1 (night-1 moves of at most 0.01). Only volume revises after that,
  by 0.1–1.8% (night 2). At D+2: 0 certain revisions after it, 3 straddling (Monday's night-2 revision, ASSUMED overnight like every
  other bracketed revision, so before Wednesday 09:00).
  **D+3 rejected (Kyle):** it delays every daily read by a session to guard against a revision measured on 9 rows.
  **Reopen and move to D+3 if the two-week tail shows post-D+2 revisions are frequent.**
- **Principle (Kyle): S controls freshness, not correctness.** The first settled value, with `available_at` = the settle point, is always
  a true record of what was known then. Revisions after S are handled by the pending revision policy (dated versions or validation flags,
  decided from the tail), and `bars` does not change either way.
- **The two-week tail:** `run.sh`'s Docker wait goes from 10 to 30 minutes (60 × 30 s). Kyle has set the PC not to sleep on mains power
  until 2026-12-03, so the tail does not rely on wake timers, which fired once in seven tries (Kyle's count). **Each 09:00 snapshot also
  re-tests the 15m S for the previous sessions** (it is a look after D+1 09:00 for every earlier session in the 5-day window).
- **Finding for the milestone that builds the provisional table, recorded in IDEA-045:** in session, a just-completed 15m bar's volume
  jumped about 20× within 3 minutes of the bar ending (SPY 11:00 on 2026-11-18, 735,252 → 15,549,598), and such inflated volumes are
  corrected overnight (Monday: 22 bars, −50% to −99%). **Provisional intraday volume is unreliable by up to an order of magnitude**; any
  intraday computation that uses volume must treat it that way. Prices showed no such problem.

**Next: step 2's schema, proposal only. No migration until Kyle approves it.**

### Step 2 proposal (2026-11-18) — the price schema. PROPOSAL, NOT BUILT; waiting on Kyle

Nothing below is migrated. Numbers are MEASURED (date, how) or ASSUMED, per rule 10.

#### Measured first: one table or two, and the chunk interval (MEASURED 2026-11-18)

`SCENARIO=step2 bash infra/bench-bars.sh` (new scenario in the step 1(a) harness, same throwaway container, both disk guards; this run
used about 2.0 GB of the VM disk). Evidence: `docs/evidence/2026-11-18-bars-step2-benchmark.txt`. TimescaleDB 2.31.0 / PG 16.15,
synthetic data, uncompressed, median of 5 `EXPLAIN ANALYZE`, PK plus the hypertable's default time index only.
The mixed table reproduces the real collision: **a daily bar's instant is its session's 09:30 open, the same instant as the first 15m bar.**

| Table | Rows | Chunk | B/row | Latest per symbol (ms) | 1 symbol, 30 d | 1 symbol, all | Cross-section |
|---|---|---|---|---|---|---|---|
| daily only, 24 sym, 700 d | 12,024 | 7 d (101 chunks) | 755.6 | 2.35 | 4.98 | 3.27 | 0.047 |
| daily only | 12,024 | 28 d (26) | 430.6 | 0.49 | 1.84 | 1.34 | 0.044 |
| **daily only** | 12,024 | **365 d (2)** | **253.4** | **0.44** | **0.98** | **0.56** | 0.054 |
| 15m only, 24 sym, 365 d | 162,888 | 7 d (53) | 286.7 | 0.92 | 3.12 | 7.91 | 0.049 |
| **15m only** | 162,888 | **28 d (14)** | **250.3** | **0.69** | **2.20** | **6.68** | 0.061 |
| MIXED, 24 sym, 1d + 15m | 174,912 | 7 d (101) | 289.3 | 1d 1.48 / 15m 1.50 | 3.82 / 4.28 | 3.76 / 9.05 | 0.061 / 0.054 |
| MIXED, 500 sym, 1d + 15m | 3,644,000 | 7 d (101) | 242.3 | 1d 6.99 / **15m 43.1** | 3.96 / 6.65 | 4.68 / 31.6 | 0.240 / 0.194 |

- **The cross-section in a mixed table is fine**: 0.05 to 0.24 ms, correct row counts (24, 500), via the time index plus the `interval` filter.
  So step 1(a)'s result (no `(interval, ts)` index needed) **holds for a mixed table too**, and the cross-section does not decide the question.
- **What differs is the chunk interval.** One hypertable has one. Daily bars want long chunks (365 d: about a third of the 7 d size on disk, every
  read under 1 ms); 15m wants about 28 d. A mixed table must use the 15m size, and its daily rows then sit at about 120 a chunk.
- At 500 symbols (Phase 2's scanner size, ASSUMED) the mixed table's 15m latest-per-symbol is 43 ms, against 4.2 ms for a 15m-only table of
  3.4 M rows at step 1(a) (a different run; not a controlled comparison).
- **A first run was discarded as an artifact and is not in the evidence file:** its 15m history ended 335 days before the daily history, so
  15m reads walked empty chunks (61 ms). The fill now puts the 15m history at the end, as it will be in reality (daily backfilled 2 years,
  15m from go-live).

#### Recommendation: two tables

**`bars_daily` and `bars_intraday`, both settled, both insert-only.** Reasons, in order:
1. They need different chunk intervals (measured above).
2. They have different settle points (D+2 vs D+1) and write paths.
3. Total return exists for daily only: 15m `Adj Close` equals `Close` in all 1,842 15m rows across the five settle snapshots, while daily
   differs in 68 of 198 rows of the 3-month window (MEASURED 2026-11-18, the snapshots).
4. The 60-day limit and the unrecoverable-gap record are intraday concerns.

**This departs from §8's single `bars` (with an `interval` column): flagged, not silently reinterpreted.** `bars_intraday` keeps `interval`
(CHECK `'15m'` only) so 30m or 1m can join later without a new table. If Kyle prefers one table, the cross-section is measured fine; the cost
is daily rows on 28-day or 7-day chunks.

#### `bars_daily` (hypertable, chunk 365 days)

| Column | Type | Notes |
|---|---|---|
| `symbol_id` | BIGINT NOT NULL FK `symbols.id` | `symbols.id` is BIGINT (MEASURED 2026-11-18, `information_schema`) |
| `effective_at` | TIMESTAMPTZ NOT NULL | the session's `opens_at` (D5). **Named by the binding vocabulary, not D5's `ts`**: Q2 |
| `o_raw`, `h_raw`, `l_raw`, `c_raw` | NUMERIC NOT NULL | as traded (D3 b): live, or adjusted × later split ratios. The provider's float by its shortest repr, no rounding (Q7) |
| `v_raw` | BIGINT NOT NULL | as traded: adjusted volume ÷ later split ratios (Q6) |
| `raw_basis` | TEXT NOT NULL, CHECK IN (`live`, `reconstructed`, `unreconstructed`) | D1. `live` = no split after the session at fetch time; `reconstructed` = a later split was multiplied back |
| `source` | TEXT NOT NULL | `'yfinance'` |
| `feed` | TEXT NOT NULL | `yfinance.daily` for the poll, `yfinance.daily.backfill` for the backfill, so a reader can tell them apart (Q4) |
| `venue_scope` | TEXT NOT NULL, CHECK IN (`provider_default`) | D1: never `consolidated` until measured; widening the CHECK is a migration |
| `available_at` | TIMESTAMPTZ NOT NULL | **the settle point, 09:00 ET on D+2** (S, MEASURED 2026-11-18). CHECK `available_at > effective_at`: a cheap guard against an in-session value, safe here (unlike macro, where a value can be known before its period) |
| `ingested_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | never used for point-in-time filtering |
| `ingestion_run_id` | BIGINT NULL FK `ingestion_runs.id` ON DELETE SET NULL | as `macro_metrics` |

**Key: PRIMARY KEY (`symbol_id`, `effective_at`).** No other index (measured). **Immutable, enforced by the database** with the `macro_metrics`
trigger pattern (UPDATE, DELETE and TRUNCATE refused; only `ingestion_run_id` may go to NULL). That is what "`bars` holds the first settled value
and is never overwritten" means in DDL. A re-fetch inserts with ON CONFLICT DO NOTHING (`rows_written` counts real inserts, so a re-run writes 0);
a re-fetch that disagrees with a stored row is a validation failure (D3), detected in code, never an overwrite.

**Dropped from §8's `bars`, each a departure, flagged:** `c_split_adj`, `v_split_adj`, `adjustment_factor` (D3 b derives adjusted values on read
from `splits`; storing them would store the rewritten value D3 exists to avoid); `dollar_volume` (derived from `c_raw × v_raw`, no M2 consumer);
**`c_total_return`, Q3.**

#### `bars_intraday` (hypertable, chunk 28 days)

The same columns as `bars_daily`, plus **`interval` TEXT NOT NULL CHECK IN (`15m`)**. **Key: PRIMARY KEY (`symbol_id`, `interval`, `effective_at`)**,
§8's shape. `effective_at` = the bar's open instant (open-labelled, MEASURED 2026-11-16). **`available_at` = 09:00 ET on D+1.** `feed` =
`yfinance.15m`. Immutable by the same trigger. Not built here: the provisional table (IDEA-045's milestone rule).

#### `bar_total_return` (plain table; D2, now daily only)

`symbol_id` FK, `effective_at` TIMESTAMPTZ, `revision` INT NOT NULL DEFAULT 0, `value` NUMERIC NOT NULL, `available_at`, `ingested_at`, `source`,
`ingestion_run_id`. **PRIMARY KEY (`symbol_id`, `effective_at`, `revision`)**, immutable trigger, as `macro_metrics`.
Revision 0's `available_at` is the bar's settle point; a later revision's is the fetch that saw it (dated by the poll, because no provider vintage
exists: the ADR-0003 F1 situation). Plain table, not a hypertable: at most about 48,000 rows a year (ASSUMED, the plan's figure), following the
`macro_metrics` precedent. Written in the same transaction as the bar (D2).

#### `splits` (plain table; D3 b)

`symbol_id` FK; **`effective_at` DATE** (the exchange-local session date the split takes effect: a date, like a macro period); `ratio` NUMERIC
NOT NULL CHECK `ratio > 0` (10.0, 2.0; a reverse split is below 1); `available_at` TIMESTAMPTZ NOT NULL; `ingested_at`; `source`; `ingestion_run_id`.
**PRIMARY KEY (`symbol_id`, `effective_at`).** Immutable trigger: a re-fetch reporting a different ratio for the same date is a validation failure,
never an overwrite.
- **Kyle's "first-seen timestamp" is `ingested_at`**: the table is insert-only, so a row's write time is when we first saw it.
- **`available_at` = the effective session's `opens_at`.** yfinance gives no announcement date; a split is certainly known when it takes effect.
  With that, **the adjusted read as of T uses `splits.available_at <= T`**, the same filter as every other table, and it is exactly Kyle's rule
  "only splits effective on or before T".
- Reference values for step 4's tests: NVDA 2024-06-07 rebuilds to 1,208.88; XLK 2025-12-04 to 291.07; XLK 2025-12-05 stays 146.60.

#### `bar_gaps`: the unrecoverable-gap record (plain table)

`id` BIGINT identity PK; `symbol_id` FK; `interval` TEXT CHECK IN (`1d`, `15m`); **`missing_from`, `missing_to` TIMESTAMPTZ NOT NULL, half-open
`[missing_from, missing_to)` over bar open instants**, CHECK `missing_to > missing_from`; `recorded_at` TIMESTAMPTZ NOT NULL DEFAULT now();
`ingestion_run_id` FK (the run that found it) ON DELETE SET NULL; `resolved_at` TIMESTAMPTZ NULL. **UNIQUE (`symbol_id`, `interval`,
`missing_from`)**, so a re-run cannot record the same gap twice.
- **Two columns, not a range type**: the `universe_membership` `valid_from`/`valid_to` precedent. An exclusion constraint against overlapping
  ranges would need `btree_gist`, a new extension (0003 declined it for the same reason).
- **Never deleted; resolving sets `resolved_at` once.** A trigger refuses DELETE, TRUNCATE and any UPDATE except `resolved_at` from NULL to a
  value (and `ingestion_run_id` to NULL).
- `recorded_at`, not `ingested_at`: a gap is a fact about our own data, not a provider observation (the `universe_membership` tripwire's reasoning).
- **The gap is computed against `trading_sessions`**, so holidays and early closes are never gaps. The limit is 59 calendar days on `start=` (decided).
- Expected use: intraday. A daily gap is reachable through the backfill's deep history, so a daily row would mean the backfill itself failed.

#### `universe_membership.close_reason` (D4, as decided 2026-11-15)

`ALTER TABLE universe_membership ADD COLUMN close_reason TEXT NULL`, plus `CHECK ((valid_to IS NULL) = (close_reason IS NULL))` and
`CHECK (close_reason IN ('delisted', 'merged', 'removed_by_operator'))`.
**Confirmed against the live table (MEASURED 2026-11-18, dev database): 24 rows, 0 with `valid_to` set**, so both CHECKs go on without a backfill.

#### Also step 2 (from the plan), not schema

24 `provider_mappings` rows for yfinance, through the seed (insert-only), not the migration. MEASURED 2026-11-18: 0 exist; alembic head is 0009.

#### Migrations and tests

`0010` the price tables (`bars_daily`, `bars_intraday`, `bar_total_return`, `splits`), `0011` `bar_gaps`, `0012` `close_reason`. Each with column
comments, a downgrade, the migration smoke and `alembic check`. Tests: every trigger refuses UPDATE, DELETE and TRUNCATE and allows only its
carve-out; every CHECK; **rule 9 for every keyed write** (a second symbol whose rows would win every ordering, left exactly as it was, checked by
mutation; `bars_intraday` cannot yet have a second interval value, so the second key is a symbol).

#### Questions for Kyle (each blocks the migration; recommendations, not decisions)

1. **One table or two.** Recommend two (above).
2. **`ts` or `effective_at`.** The vocabulary entry (IDEA-034, settled 2026-11-11) binds names on every observation table and says `bars`
   "does not re-decide it"; D5 wrote `ts`. Recommend `effective_at` (an instant here, a DATE in `macro_metrics`, as that entry already allows).
3. **`c_total_return`: two decisions conflict.** D2: "`bars.c_total_return` holds the latest vintage". 2026-11-17: "`bars` holds the FIRST settled
   value and is never overwritten". Both cannot hold. Recommend **no `c_total_return` column on `bars_daily`**: total return lives only in
   `bar_total_return`, which also removes D2's "two homes for one number" risk.
4. **`available_at` for backfilled rows.** No vintage exists for a 2-year backfill. Recommend the settle point (D+2 09:00) like the poll, with
   `feed` saying `backfill`; its hindsight is bounded by post-S revisions, which the tail is measuring. The alternative, the fetch instant, is
   honest but makes all history invisible to any replay before 2026. **It is a statement about what was known, so it is yours.**
5. **Database-enforced immutability on `bars_*`** (the `macro_metrics` triggers). Recommend yes.
6. **Volume reconstruction exactness (ASSUMED):** adjusted volume ÷ ratio is assumed to be an integer. Step 4 checks it on the five 2025-12-05
   splits; a non-integer fails the symbol and is never rounded.
7. **Prices stored as the float's shortest repr** (yfinance sends float32-looking values such as 749.6599731445312), never rounded. Recommend yes.
8. **Not schema, but it decides whether `bar_total_return` ever gets a revision 1:** the gap-driven poll fetches only from the last stored bar,
   so a dividend that rewrites old `Adj Close` is never seen. Detecting it needs a periodic history re-fetch. That is step 3/4's; raised now.
9. **15m across a split (ASSUMED):** intraday prices are assumed split-adjusted like daily. No split falls inside the 60-day window now, so it
   cannot be measured; the client reconstructs from `splits` either way.

**State: S set. Step 2 PROPOSED, not built. No migration until Kyle approves.**

### Addendum 2026-11-18 (third) — Kyle's decisions on the step 2 proposal

Written the turn they were given.

- **Two tables: APPROVED.** `bars_daily` (365-day chunks) and `bars_intraday` (28-day chunks, `interval` limited to `15m` for now).
  The departure from §8's single `bars` is **IDEA-046** (`docs/SPEC-DEFECTS.md`), citing the step 2 benchmark, for the next correction release.
- **The time column is `effective_at`**, per the settled vocabulary. **Terminology correction: D5's `ts` was shorthand.** Every D5 rule reads
  with `effective_at` for `ts` (the bar's open instant; a daily bar's is its session's `opens_at`).
- **No total-return column on `bars_daily`.** Total return lives only in `bar_total_return`, with revisions. **This supersedes D2's
  `bars.c_total_return`** and resolves its contradiction with "first settled value, never overwritten". **"Current total return" is the latest
  revision there.**
- **Backfill `available_at` = the settle point, a RECORDED EXCEPTION to the unifying rule** (`available_at` = when the value became knowable).
  - Rationale (Kyle): market bars are not published later like macro figures; the trades were public at the session close. Only provider corrections
    are uncertain, and those are measured (prices: none observed after night 1; volume: up to about 2% so far, bounded further by the two-week tail).
  - **Limitation: backfilled values may include corrections made after the settle point.**
  - **Every backfilled row carries a backfill `feed`, so a strict replay can exclude them.**
  - Rejected: the fetch instant, because no replay before 2026 could see any history.
- **`splits.available_at` = the open of the effective day: APPROVED. First-seen is `ingested_at`.**
- **Dividends and late corrections: one periodic history re-fetch job, planned for step 3 or 4.** It re-fetches trailing daily history and compares
  against the stored first-settled values. It feeds the revision policy (late volume corrections), `bar_total_return` (dividend rewrites) and `splits`
  (new splits). Frequency and window are decided when it is built. (This answers the proposal's question 8.)
- **The other open questions: Claude takes its own recommendation unless one changes a recorded decision.** Checked; none does:
  - Q5, database-enforced immutability on `bars_daily`/`bars_intraday`: **yes**, the `macro_metrics` triggers. It is the DDL form of "never overwritten".
  - Q6, volume reconstruction exactness: **ASSUMED exact; step 4 checks it on the five 2025-12-05 splits; a non-integer fails the symbol, never rounds.**
    Consistent with D3 ("volume is divided by the same ratios").
  - Q7, prices as the float's shortest repr, never rounded: **yes.** Consistent with D3 ("the rebuilt values inherit the provider's rounding").
  - Q8: answered by Kyle (the re-fetch job above).
  - Q9, 15m across a split: **ASSUMED split-adjusted like daily; the client reconstructs from `splits` either way.** Not measurable now (no split
    inside the 60-day window).

**Then: build step 2** (migrations, triggers, comments, rule 9 tests, mutation checks, container gate).

### 2026-11-18 — M2 slice 6 step 2 built: the price schema (migrations 0010–0012)

Built as approved (addendum 2026-11-18 third). Alembic head is now **0012**.

- **`0010_price_tables`**: `bars_daily` (hypertable, 365-day chunks, PK `(symbol_id, effective_at)`), `bars_intraday` (hypertable, 28-day chunks,
  PK `(symbol_id, interval, effective_at)`, `interval` CHECK `'15m'`), `bar_total_return` (plain, PK `(symbol_id, effective_at, revision)`), `splits`
  (plain, PK `(symbol_id, effective_at DATE)`, `ratio > 0`). CHECKs on the bars: `raw_basis` (three values), **`feed IN ('poll', 'backfill')`**,
  `venue_scope IN ('provider_default')`, `available_at > effective_at`. The hypertables are created with `create_default_indexes => false` plus a named
  plain btree on `effective_at` (TimescaleDB's DESC default cannot be declared on a model, so `alembic check` would report drift).
- **`0011_bar_gaps`**: identity PK, UNIQUE `(symbol_id, interval, missing_from)`, half-open `[missing_from, missing_to)`, CHECKs on interval,
  non-empty range and `resolved_at >= recorded_at`.
- **`0012_universe_membership_close_reason`**: the column plus the two-way CHECK and the three-value CHECK, as decided.
- **Insert-only triggers** (UPDATE, DELETE, TRUNCATE) on all four 0010 tables and on `bar_gaps`. **DECISION, one deviation from 0005's form, not its
  rule:** the function compares the whole row as `jsonb` minus `ingestion_run_id`, instead of 0005's hand-written column list, so a column added later
  is covered without anyone remembering to list it. The carve-out is the same (clearing `ingestion_run_id`, which ON DELETE SET NULL needs).
  `bar_gaps`' function also lets `resolved_at` go from NULL to a value, once.
- **Comments on every column and table**; each time column's comment starts `TIME COLUMN.` and says what the instant means, including the settle points
  (D+1, D+2) and the backfill exception. Models in `app/models/prices.py` carry the same literals (`alembic check` compares them).
- **`feed` is the closed pair `poll | backfill`** (DECISION, from Kyle's "every backfilled row carries feed = backfill"): a strict replay's
  `feed <> 'backfill'` cannot be defeated by a spelling. The proposal's `yfinance.daily.backfill` form is not used.

**Measured while building (MEASURED 2026-11-18, the tests on TimescaleDB 2.31.0):** a BEFORE TRUNCATE trigger on a hypertable fires (TRUNCATE is
refused), and the row triggers are cloned onto chunks (an UPDATE or DELETE addressed to a chunk table directly is refused too).
**Known ways around the triggers, all DDL, none a data path:** DROP TABLE, and TimescaleDB's `drop_chunks()` (drops chunk tables, no row trigger
fires). Nothing in FlowDesk calls `drop_chunks` (IDEA-044: no retention policy). Stated in 0010's docstring.

**Tests: `backend/tests/test_price_tables.py`, 125 tests.** Shape (columns, keys, hypertables and chunk intervals, indexes, comments), every CHECK both
ways, every trigger and its carve-out, the downgrade to 0009 and back. **Rule 9:** every key test has a second symbol whose rows would win every
ordering the key applies (the same instant and a later one, larger values; higher revisions; an earlier-recorded gap), and asserts those rows are
exactly as they were; the run-pointer test purges one run and asserts the other symbol's pointer is untouched; the gap-resolve and membership-close
writes are keyed and leave the other row alone.

**Mutation check: 38/38 caught** (`bash infra/mutation-check.sh m2-slice6-step2`, driver `backend/tools/mutate.py`; output
`docs/evidence/2026-11-18-step2-mutation-check.txt`). Each mutation breaks one snippet in a copy of `backend/` inside the container (the working tree is
never touched), runs only the tests named for it, and counts as caught only on a real test FAILURE with no ERROR, so a mutation that broke the
migration itself cannot pass for a catch. Only `test_price_tables.py` runs: `alembic check` would flag most schema mutations as drift, which would say
nothing about these tests. Covered: each key losing a column (5 tables), each CHECK loosened or removed, each trigger removed, the jsonb comparison
ignoring a column, the carve-out too loose and too strict, ON DELETE SET NULL removed, the chunk intervals, the default indexes, the comments.
**Not mutation-testable yet:** `bars_intraday`'s `interval` in the key (only one value is allowed, so no second interval can exist to collide).

**Gates:** `ci-checks.sh lint` passes (ruff, format, mypy over 119 files, banned phrases). `ci-checks.sh test`: **807 passed, 0 failed** (2026-11-18, third run). Two earlier runs failed: the first on two `test_reference_tables.py` tests that
0012 changes correctly (the pinned column list gains `close_reason`; a hand-closed interval must now carry a reason, so the test sets
`removed_by_operator`; what it checks, that the seed never reopens a closed interval, is unchanged); the second on
**`test_task_heartbeat.py::test_a_task_longer_than_three_intervals_keeps_its_heartbeat_advancing`, a PRE-EXISTING INTERMITTENT FAILURE, not caused
by this step**: under full-suite load a read of `task_runs.heartbeat_at` went backwards (17:30:37.302 then 17:30:36.848). It passed 5 of 5 alone and in
the other two full runs. Not investigated or fixed here (out of scope); flagged for Kyle.
The dev database was rebuilt and migrated to 0012 (`docker compose up -d`; `/health/ready` 200; the 24 memberships untouched, `close_reason` NULL).

**Not done here, by design:** no ingest writes these tables yet (steps 3–4), the 24 yfinance `provider_mappings` (step 3, through the seed), the
periodic history re-fetch job (step 3 or 4), the operator command that sets `close_reason`.

### 2026-11-18 — the heartbeat flake: the cause is this machine's wall clock, and the fix is in the tests

`test_task_heartbeat.py::test_a_task_longer_than_three_intervals_keeps_its_heartbeat_advancing` failed once in the step 2 gate (a `heartbeat_at` read
went from 17:30:37.302 to 17:30:36.848). Kyle: diagnosis first; a flaky test in the gate is a bug, never a re-run.

**Diagnosis (each of Kyle's three questions):**
- **Where the timestamp is computed:** in Python, `datetime.now(UTC)` (`app.tasks.runs.utcnow`), through the injected `clock`, for the insert, every beat and
  the final write. Postgres's `now()` is never used; it would not help, because Postgres runs in the same VM on the same kernel clock.
- **Two writers racing:** no. The backward read happened during the body, before any final write; the heartbeat thread is the only writer and is
  sequential; the scratch database held one row.
- **A timestamp computed before a slow UPDATE landing after a later one:** impossible with one sequential writer. And the bad value (36.848) is EARLIER
  than the fixture's own first log line (36.900), written by the same process before the row existed. No ordering of writes can produce that; only a
  clock that went backwards can.

**Cause, MEASURED 2026-11-18 (`docs/evidence/clock-step-probe.py.txt`, CLOCK_REALTIME against CLOCK_MONOTONIC every 2 ms in a container):**
**Docker Desktop's WSL2 VM steps the wall clock back about 2.2 to 2.5 s every 30 s**, idle (3 steps in 95 s) and under the full suite (11 in 330 s,
worst 2.48 s). Kernel `5.15.167.4-microsoft-standard-WSL2`, clocksource `tsc`. Compared with the Windows clock (coarse: each reading bracketed within
about ±0.8 s) the VM clock runs about 8% fast between steps and stays within roughly 0 to −2.5 s of it. ASSUMED explanation, not verified: a
mis-calibrated TSC in the VM, corrected by the host's periodic time sync.

**Reproduced before the fix:** the unchanged test run 150 times with the probe alongside (`docs/evidence/heartbeat-repeat.sh.txt`):
**5 failed, 145 passed. Every failing iteration's end timestamp is earlier than its start, and each straddles a step the probe recorded**; the 2 steps
that caused no failure fell outside a test body (`docs/evidence/heartbeat-repeat-before.txt`).

**Fix: the test, because the false assumption lived there.** It asserted `beats == sorted(beats)` over real `datetime.now()` readings, i.e. that the OS
wall clock is monotonic, which on this machine it is not. It now injects `tests/clocks.py`'s `stepless_clock()` (anchored once to the real clock, then
advanced by `time.monotonic()`), so it pins what the code guarantees: each beat written from the clock, in order, by the one thread. **After:
150 of 150 passed across 7 measured steps** (`heartbeat-repeat-after.txt`).

**`GREATEST(heartbeat_at, new)` rejected, and pinned by a new test** (`test_a_clock_step_backwards_is_written_as_is_never_held_at_the_maximum`, 50 of
50 passed, `heartbeat-repeat-after-step-test.txt`): it would mask the step and turn a FORWARD clock error into a sticky one (a clock an hour ahead,
corrected, would keep a dead task looking alive for that hour). A backward step costs at most ~2.5 s of apparent age against a 120 s freshness
threshold, judged on the same clock. Mutation-checked: `bash infra/mutation-check.sh heartbeat-clock` applies GREATEST to the beat and the new test
catches it (1/1).

**Same class, found by grepping the tests:** `test_ingest_runner.py::test_a_later_failure_does_not_erase_the_last_success` asserts
`last_error_at >= last_success_at` for two runs milliseconds apart: the same latent flake (never observed). It now uses one `stepless_clock()` for
both runs; `run_feed` already takes a `clock`. Gate after both: **808 passed**, lint clean (120 files).

**Flagged for Kyle, NOT changed (a design change, not a flake fix):** `/health/providers` derives `last_attempt_failed` from
`last_error_at > last_success_at` (`app/services/health.py`). On this clock, two attempts less than ~2.5 s apart can be ordered wrongly. Attempts on
one feed are normally minutes apart, so the practical risk is small, but the inference orders events by wall clock, which this machine does not
support. The robust form records the last attempt's outcome directly instead of inferring it.

**Environment finding, for every later slice:** on this machine two wall-clock instants less than ~2.5 s apart cannot be ordered, and a duration
measured by the wall clock runs ~8% long. Nothing in the design today depends on that precision (heartbeat 120 s, settle points hours, FRED 72 h).
**But the rate limiter's spacing is a Redis key TTL on the same VM clock, so a configured spacing is about 8% shorter in real time** (step 3's yfinance
budget must allow for it). Whether to fix the VM clock (Docker Desktop / WSL settings) is Kyle's; it is not changed.

### 2026-11-18 — recorded: `bars_intraday`'s `interval` key column has no mutation test (Kyle)

While `ck_bars_intraday_interval` allows only `'15m'`, no test can put two intervals side by side, so dropping `interval` from `bars_intraday`'s primary
key would pass every test (the one gap in step 2's 38/38). **Rule: whoever widens that CHECK adds, in the same commit, a rule 9 test with a second
interval whose rows would win the ordering, and the matching mutation in `backend/tools/mutate.py`.** Written beside the CHECK in
`app/models/prices.py`, in the mutation suite, and in CLAUDE.md.

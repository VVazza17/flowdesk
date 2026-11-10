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
   Details and what changes: see the reply recorded in the session; summary below.

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

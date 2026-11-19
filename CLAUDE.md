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
records M1 slice 10's heartbeat decisions. **ADR-0003 is the first ADR whose own rationale was falsified by a later
measurement** (decision 3: revisions are dated by the poll, which `output_type=3` disproves; Addendum 2026-11-13). That is
not a spec defect and goes in the ADR, not in `SPEC-DEFECTS.md`: the assumption is quoted, what survives is listed,
and the superseded text stays in place with an inline `[Falsified: ...]` marker. **Read the addendum before touching
FRED ingest.** Its decision 3 and the backfill's refusal check are rewritten there, and built (see the M2 slice 5 bullet
below).

## Current state

- **M0 CLOSED** (2026-08-16). Seven ADRs written; premium oracle fixtures recorded.
- **M1 CLOSED** (gate passed 2026-11-08, Kyle's call after a fresh-clone review; `docs/BUILD-LOG.md`).
  Slices 1–10 built and closed: compose (Postgres/TimescaleDB + Redis), FastAPI +
  `/health/live`, Alembic baseline, §8 ops tables, structlog JSON + correlation-ID middleware,
  `/health/ready` · `/health/data` · `/health/providers` (states documented in `docs/HEALTH.md`),
  private/public compose profiles with the IDEA-019 route invariant, GitHub Actions CI with
  hash-pinned locks, backup/restore scripts proven on a throwaway hypertable (`docs/BACKUP.md`),
  external heartbeat sender (ADR-0008, `docs/HEARTBEAT.md`).
- **The external heartbeat is proven end to end** (drill passed, recorded 2026-11-05 in `docs/BUILD-LOG.md`): the real
  provider took the pings, the DOWN alert arrived 44 minutes after the stack stopped, and the UP notice followed.
  `HEARTBEAT_URL` stays blank on development machines; a sleeping laptop would page constantly. Re-run the drill
  on any deployed instance (quarterly, and after a provider notice).
- **Any new `degraded` condition on `/health/ready` also pages the owner** (ADR-0008 section 4). The sender
  pings only on exactly `ok`.
- **Scheduled backups are built (M2 slice 2) but the slice is not closed** (IDEA-029). The `backup` compose
  service dumps nightly (03:00 `America/New_York`, with catch-up after sleep), encrypts with `age` to the
  public key in `infra/backup/recipients.txt`, keeps 14 dailies plus 8 weeklies locally, and then copies to
  Kyle's Windows desktop over SFTP (Windows OpenSSH Server, a dedicated non-admin account, restricted by NTFS
  permissions, not rsync), as a separate step, so a sleeping desktop is a failed copy and not a failed backup. With no real age key in `recipients.txt` it records `NoRecipient`
  and writes nothing: **the placeholder is committed, and backups fail closed until Kyle adds the key.** The
  age private key is made on the desktop and kept in the password manager, never on the laptop. A stale
  backup shows on `/health/data` (`backups`) and never degrades `/health/ready`; the page is the external
  monitor missing the copy's pings (`BACKUP_PING_URL`). Proven against stand-in sshd containers by
  `infra/smoke-m2-slice2.sh`; **nothing has run against Windows OpenSSH Server**, and
  `infra/backup/verify-destination.sh` is how the real destination gets checked. **It closes only when a real
  unattended dump lands on the real desktop and restores from there** (`docs/BACKUP.md`, "Closing the
  slice"). Known gaps, all in `docs/BACKUP.md` "Limits": **retention (14 daily + 8 weekly) is enforced on
  the laptop's copy only; nothing prunes the desktop** (the account cannot delete, by design), so on the
  destination it is aspirational until a Task Scheduler job runs `prune.sh` there; **the backup image is
  not covered by CI's vulnerability scan** (`security` builds `backend/` only), so its Alpine packages
  float, which is a hole next to what slice 0 closed; **arm64 is untested** for it; **the SFTP account is not
  chrooted, so a leaked key reaches more than the backups** (on a default Windows install, `Authenticated Users`
  can modify ordinary top-level folders; `infra/backup/acl-survey.ps1` sizes it on the desktop). A plain `pg_restore`
  silently damages hypertables: use `infra/restore.sh`, or `infra/restore-backup.sh` for a scheduled backup.
- **Reference tables, universes and calendars are built (M2 slice 3, 2026-11-10).** Migration 0003 adds `currencies`, `exchanges`, `symbols`, `provider_mappings`,
  `universes`, `universe_membership` and `trading_sessions`; `python -m app.seed` (run by the `migrate` service
  after Alembic) fills them from `settings.py`'s §6.2 lists and `exchange_calendars`. The seed is idempotent
  and **insert-only**: it never closes an interval or rewrites a stored session, and reports disagreement as
  drift (IDEA-040 is why). Seeded membership starts on the seed date, so **no historical query against these
  universes returns anything before it**; revisit at slice 6. `BREADTH_UNIVERSE` exists and is empty (§6.2 gives
  no list). `provider_mappings` holds CIK (a departure from §6.5's text), with uniqueness that depends on
  `id_kind`, and a deliberate CHECK on it (IDEA-022 addendum). **The new dependency `exchange-calendars`
  brings numpy, whose licence expression includes `0BSD`, `Zlib` and `CC0-1.0`; the owner allowed it as an
  exception keyed to the package and that exact expression, not those licences in general (IDEA-027
  addendum); a different string from a future numpy fails the gate.** Session math
  (windows of N sessions) is a follow-on, not built. Sessions computing on arm64 is untested; the imports
  load under QEMU.
- **The provider framework and a fake provider are built (M2 slice 4, 2026-11-11).** `app/ingest/`: a five-kind
  failure taxonomy that every client maps into, a Redis spacing rate budget (every attempt takes a slot; a
  provider with no budget, or Redis down, is refused and never run unthrottled), and `run_feed`, which wraps a
  provider call with tenacity retries and writes `ingestion_runs` and `providers_health`. **Celery retries are
  off for ingest tasks: Beat is the outer retry**, because a Celery retry would write a second
  `ingestion_runs` row for one logical run (IDEA-042). **`rows_written` is a verification signal, not a
  statistic: rows created or changed, never attempted; an identical re-ingest writes 0.** Never "fix" it to
  count attempts. **`providers_health.last_error` stores the kind and exception class only, never the error
  text** (FRED's key rides in the URL and `/health/providers` serves that column); a test reads the stored rows
  back to prove it. `providers_health.first_seen_at` (migration 0004, a column §8 lacks) lets a feed that never
  succeeds go `stale` at its own threshold. **The readiness rule (`docs/HEALTH.md`): `/health/ready` degrades
  when the machinery has stopped (Postgres, Redis, worker, Beat), never when a source is unavailable (a
  provider, a feed, the backup destination).** A killed provider is defined and tested in HEALTH.md; it is
  proven against the fake only, off by default (`FAKE_PROVIDER_ENABLED`). FRED and yfinance clients, their
  rate numbers and their error mappings are slices 5 and 6.
- **FRED revision history: built and run for real, including the daily poll (M2 slice 5, step 3, 2026-11-13).**
  `python -m app.backfill [--series A,B] [--force]` captures every series' **whole revision history** (`output_type=3`,
  each revision dated by FRED's vintage date) into `macro_metrics`, judged by the six rules of the ADR-0003 addendum
  (`app/ingest/macro.py`, `check_history`). It is an **entry point, not a Celery task** (Kyle's decision: one-time plus
  deliberate re-capture, about 90 minutes, so it must not live under the 30-minute task limit that the ingest canary's
  threshold is built from), but it calls the same `run_feed` per series **and `run_recorded`: a manual backfill is a
  task execution and has a `task_runs` row like any other.** **The skip is not resume**: a series whose
  `series_history_state` row has the current `FRED_RULE_VERSION` is not fetched; an interrupted series restarts from its
  first request. Changing the rules means bumping `FRED_RULE_VERSION`. **Two exclusions, never summed:** A, "no vintage
  exists" (no publication date; not recoverable) and B, "revised but pre-vintage" (every revision has a real date, the
  chain has no beginning; recoverable data we chose not to model). The window is 100 vintage dates (FRED's gateway
  504s at 60 s; about 0.16 s per vintage date), and a 504 halves the window. Migration 0006 is `series_history_state`
  (a table section 8 lacks), 0007 renames its gap count and adds the gap-first-print count; part C's `task_run_id`
  migration is 0008 (built). **The first real backfill is complete: all 17 series captured.** `output_type=4` does not always
  report a `"."` first print, so a chain starting with `"."` and absent from it is stored as a gap first print, counted apart
  (ADR-0003 F2). `FRED_RULE_VERSION` bumps when a change makes the rules stricter or changes what is stored; for a
  relaxation, count first. The entry point warns that the machine must stay awake. See `docs/BUILD-LOG.md`.
- **Per-series data age is built (M2 slice 5, step 5, 2026-11-14; not yet committed).** `/health/data` has `data_age`: each macro series' age (newest
  **first print**, revisions excluded) against its own threshold, `fresh` / `stale` / `unjudged` / `no_data`. Thresholds are in `settings.FRED_DATA_STALE_AFTER`,
  **measured from the gaps between publication days, not from lag** (3 x p95 over five years); regenerate with `python -m app.measure_cadence`. It never degrades
  `/health/ready` and nothing pages on it. `tests/fakeprobes.py` is the complete fake of the probes: new tests use it.
- **The point-in-time read is built (M2 slice 5, step 4, 2026-11-14), committed `e636804` and `7966d94`.** `app/services/macro_read.py`: `series_as_of`,
  `value_as_of`, `latest_as_of`, `revisions_of`. A row is visible when `available_at <= as_of` (inclusive; `as_of` must be timezone-aware), a filter on
  row **existence**; a stored NULL is a published gap (an `Observation` with `value=None`), never `Missing`. **No answer comes in five distinct kinds**
  (`Missing.reason`, decided in this order): `UNKNOWN_METRIC`, `BEFORE_VINTAGE_RECORD` (T precedes the series' first vintage), `PREDATES_VINTAGE_RECORD`
  (the period is earlier than `first_covered_period`: a value exists upstream but cannot be dated), `NOT_PUBLISHED_AS_OF` (we know the period, T is too
  early), `UNKNOWN_PERIOD` (no revision of that date exists at any time). It reads no clock and has no route. **`revisions_of(..., as_of=None)` returns the whole chain and can leak post-T knowledge (it is not a point-in-time read; never in a signal path); `as_of` is a REQUIRED keyword, so that read is spelled out at every call site.** **Timed only on ~5,600 rows of daily macro; slice 6 must re-measure, not inherit.** **Tests include a live-data oracle that checks the stored CPIAUCSL
  against M0 spike 3b.** It runs wherever the series are captured (`ci-checks.sh test` reads the dev stack's database) and skips on a fresh one (GitHub), where
  a banner prints and `tools/junit_skips.py` allows exactly that marked skip and prints it; any other skip still fails the gate.
- **The daily FRED poll is built (2026-11-13), committed `2947902`.** `ingest.fred_poll`, Beat 08:00 America/New_York: per series, ask FRED only for the
  vintages since `captured_through` (inclusive), merge onto the stored chains, run the same six rules, append the new tail. Revisions are dated by FRED's
  vintage, never by the poll. It refuses a series with no captured history (`NoBaseline`). Run it by hand: `docker compose exec worker celery -A app.worker
  call ingest.fred_poll`. FRED feeds are judged at 72 h (three missed polls): poll health, not data freshness. Beat's first scheduled firing is unobserved.
- **Task liveness: heartbeat, `task_run_id` and the sweep are built (M2 slice 5, step 3, part C, 2026-11-13).** `run_recorded`
  beats `task_runs.heartbeat_at` every 30 s from a daemon thread (fresh for 120 s; `app/tasks/liveness.py` is the one
  definition) and publishes its row id; **`run_feed` refuses to run outside a recorded task** (`NotRecorded`) and links each
  `ingestion_runs` row to it (`task_run_id`, migration 0008); `ops.sweep` (ops queue, every 120 s) fails `running` rows
  nothing is alive behind (`Orphaned`), in `task_runs` and `ingestion_runs` in one transaction. **Excluded, each with its
  reason (`app/tasks/sweep.py`, `docs/HEALTH.md`): `backfill_runs` (resume is slice 10) and `ops.backup` / `ops.backup_copy`
  (psql-written, they never heartbeat).** A swept row whose task finishes later keeps the sweep's verdict plus a note. `checks.ingest_worker`
  has a `busy` state (alive, not degraded) and its threshold is three canary intervals again, not the task limit. Tests that call
  `run_feed` directly use `tests/recorded.py`. A manual backfill is a task execution and has a `task_runs` row.
- **Slice 5 gate review is written (2026-11-14, `docs/BUILD-LOG.md`); it closes with two named gaps:** Beat's 08:00 firing on an awake machine is unobserved
  (schedule, queue and DST pinned by test; a Beat container recreate dropping a missed firing was ASSUMED, then MEASURED 2026-11-15 (it does, with the schedule file in /tmp) and fixed: IDEA-043, schedule file on the `beat_schedule` named volume at `/var/lib/beat`, owned by uid 10001 in the Dockerfile; `infra/smoke-m2-beat-schedule.sh` reruns the test. Beat catching up is NOT a gap check: slice 7 must detect gaps in the data), and no real FRED 429/423/500 has ever been seen
  (handling is tested: `infra/smoke-m2-slice5-fred-failures.sh` runs each through the real worker and proves the api_key is in no stored or served text). Step 6 is done.
- **M2 slice 6 (bars): the settle point S is set, MEASURED 2026-11-18** (`docs/BUILD-LOG.md`, addendum 2026-11-18 second). A bar is stored in
  `bars` only after its settle point, `available_at` = that point, counted in sessions: **15m at 09:00 ET on D+1, daily at 09:00 ET on D+2.**
  Thin evidence (one session's night 1, three symbols; 16:00–18:26 unobserved); reopen daily to D+3 if the two-week tail (to 2026-12-03) shows
  frequent post-D+2 revisions. **S controls freshness, not correctness:** `bars` keeps the first settled value and never changes.
  **Step 2 built (2026-11-18, migrations 0010-0012):** `bars_daily` (365-day chunks) and `bars_intraday` (28-day, `15m` only) instead of §8's one
  `bars` (IDEA-046); time column `effective_at`; total return only in `bar_total_return` (latest revision = current); `splits`
  (`available_at` = effective open); `bar_gaps` (never deleted, resolved once); `universe_membership.close_reason` (two-way CHECK). All
  insert-only by trigger. `feed` is `poll` | `backfill`; **backfilled rows' `available_at` is the settle point, a recorded exception** (may
  include later corrections; a strict replay excludes `feed = 'backfill'`). Mutation-checked: `bash infra/mutation-check.sh m2-slice6-step2`.
  **Untested by design: `interval` in `bars_intraday`'s key** (only `15m` is allowed, so no second interval can collide). **Whoever widens
  `ck_bars_intraday_interval` adds the rule 9 test with a second interval and its mutation in the same commit.**
  **Step 3 built (2026-11-18): the yfinance client** (`app/ingest/yfinance_client.py`, pure parsing in `app/ingest/bars.py`), yfinance 1.8.0 pinned.
  `yf.download` swallows per-ticker errors (a rate limit looks like a dead symbol), so the client calls the PRIVATE `_download_impl` with its own
  context; a test pins its signature, and `tests/test_yfinance_live.py` (dev stack only, loud skip elsewhere) checks its behaviour: **run it after
  any yfinance upgrade**. Budget one 24-ticker batch a minute (safety factor ASSUMED). Nothing writes bars
  yet (step 4 backfill, step 6 poll). **Step 6 requirement (Kyle, 2026-11-18): the 15m feed is self-contained, not ordering-dependent.** Every
  15m run fetches its own daily actions=True batch (same symbols, same window plus the next session) and checks any new split against stored
  ones before accepting a 15m bar: an insert-only table cannot fix a 15m bar stored split-adjusted but labelled `live`. `mutation-check.sh m2-slice6-step3`.
  **Step 4 built and run (2026-11-18): `python -m app.bars_backfill`**, 2 years daily, `feed = 'backfill'`, ON CONFLICT DO NOTHING (a re-run writes
  0), a re-fetch that differs fails the symbol and overwrites nothing (D3), batches of at most 24. Dev stack: 24 x 499 bars (2024-11-19 to
  2026-11-16), 5 splits, verified in SQL. **A re-fetched PRICE that differs fails the symbol; a VOLUME-only difference is a post-settle
  revision, counted and never written** (Kyle, 2026-11-18). **Re-fetch job requirement: total return differing by more than 16 float32 ULP
  is a revision** (measured jitter at most 4 ULP; margin ASSUMED). yfinance caches live in `/var/cache/yfinance` (made in the image).
  **Step 5 built (2026-11-18): `app/services/bars_read.py`**, the point-in-time read: `as_of` AND `include_backfill` are required
  keywords (strict mode = `include_backfill=False`), visible iff `available_at <= as_of`, adjusted on read by splits knowable at T, total
  return = latest revision knowable at T, seven absence reasons. 2-year read of one symbol 23.5 ms on the real 12k rows; no index added.
- **The base image is pinned by digest** (M2 slice 0, 2026-11-08). The `FROM` line in `backend/Dockerfile` is
  the one definition. The CI tooling container, `lock-deps.sh` and the smokes read it through
  `infra/base-image.sh`, and lint fails on a second definition. The base no longer updates itself: the daily
  image scan is the tripwire and `docs/RUNBOOK-IMAGE-CVE.md` is the bump procedure. Postgres and Redis stay on
  exact version tags by decision; a tag like `2.31.0-pg16` is already a version pin, and the drift was in
  `python:3.12-slim`, which floated. **Not covered:** the backup image (`infra/backup/`) has floating Alpine
  packages and is outside CI's vulnerability scan, which builds `backend/` only (`docs/RUNBOOK-IMAGE-CVE.md`).
- **The public profile is not internet-ready.** `api-public` binds to loopback; strict CORS,
  request-size limits and rate limits have no owning slice and are required before any internet
  exposure (IDEA-019 addendum in `docs/SPEC-DEFECTS.md`).
- **arm64 is only proven under QEMU.** The image builds, loads its compiled wheels and answers
  `/health/live` under emulation. Migrations 0001-0002 and the smoke-9 hypertable, continuous aggregate and
  policy DDL also run clean under QEMU (2026-11-08, TimescaleDB 2.31.0 on aarch64). Native arm64 hardware, backup/restore
  on arm64 and the full compose stack on arm64 are still unproven.
- **The worker and provider half of the M1 gate moves to M2's gate** (IDEA-032, a relocation and not a waiver). The gate says "killing Redis/worker/provider", and
  no worker or provider exists until M2, so it can't pass at M1. Only Redis was killed and verified. M2's gate
  must not pass until killing the worker and a provider each produce a documented health state (add them to
  `docs/HEALTH.md`). The frozen §16 still says the old thing until a correction release. M2 is in progress (slices 0 and 1 done 2026-11-08: the base-image pin, and the Celery worker and Beat with a
  health check keyed on a canary task's recent completions, so a wedged worker degrades `/health/ready` and
  pages; the worker half of IDEA-032 is therefore done, the provider half is slice 4's; slice 2, the
  scheduled backups, is built but open until the desktop drill; slice 3, reference tables, is built (2026-11-10);
  slice 4, the provider framework and fake provider, is built (2026-11-11); the slice plan
  and the decisions behind it are in `docs/BUILD-LOG.md`). M2 builds yfinance, FRED and a fake provider only
  (IDEA-037); the slice 9 number is vacant.

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

Tracked in `docs/SPEC-DEFECTS.md` (IDEA-013 … IDEA-042, IDEA-045 and IDEA-046; IDEA-039, IDEA-043, IDEA-044, IDEA-047 and IDEA-048 are in `docs/IDEAS.md`).
IDEA-040, IDEA-041, IDEA-042, IDEA-045 and IDEA-046 block nothing (IDEA-046, §8's single `bars` built as `bars_daily` and `bars_intraday`, and
IDEA-045, the §11 settled/provisional bar ingest split, are flagged for the next correction release; the provisional bar table is built by the first milestone that implements a computation §11 schedules
intraday, and **M4 planning must decide whether `compute_rotation`'s 30-minute intraday cadence binds M4**). Eight block a milestone or a slice:

- **IDEA-013 → blocks M3.** `natural_key` has no time component, but §7.16's episode example
  requires five daily rows to exist. One of the two sections is wrong.
- **IDEA-017 → blocks M3 tests.** The M3 gate promises exactly-once delivery to Discord, which
  no implementation can achieve — webhooks expose no idempotency key.
- **IDEA-016 → blocks M4/M8.** `alerts` has no FK back to `events`; the M4 gate requires that join.
- **IDEA-029 → blocks M2 slice 6, the first irrecoverable observation.** Backups have no scheduler, retention
  or off-box copy. What a lost machine would erase for good is what only a live run captures: bars as traded
  at the time, and `c_total_return` as it stood on a given day. FRED vintages are not at risk; they can be
  re-fetched with `output_type=4` (ADR-0003). Slice 2 has built the backups; it must still close, with a
  restore drill from the real off-box copy on the real desktop, before slice 6 persists its first scheduled
  bar. The smoke does not satisfy that.
- **IDEA-023 → blocks M2 slice 7.** `providers_health` has no quarantine columns, but §6 says quarantine
  counts feed it.
- **IDEA-034 → blocks M2 slice 6.** `bars` has no `provider/feed/venue_scope` and no provenance or revision
  columns. Split: the shared provenance vocabulary (`effective_at`, `available_at`, `ingested_at`, `revision`,
  `source`) is settled at slice 5 planning and binds every observation table; the `bars`-specific parts
  (where `c_total_return` revisions live) wait for slice 6 planning, with IDEA-035.
- **IDEA-038 → blocks M2 slice 5.** §8 calls the observation timestamp `effective_ts`; the ADRs and §16 call
  it `effective_at`. Settled by the vocabulary decision at slice 5 planning.
- **IDEA-035 → blocks M2 slice 8.** The gate's "new data version" has nowhere to live in §8. **Version home
  decided 2026-11-16 (slice 6 D3, option b):** `bars` stores as-traded prices (rebuilt from yfinance's adjusted
  price × later split ratios, `raw_basis = reconstructed`), a minimal `splits` table lands in slice 6, and the
  adjusted read as of T applies only splits effective on or before T. A re-fetch that rebuilds different raw
  prices is a validation failure, never an overwrite. The rest of corporate actions stays slice 8.

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
spec. Thirteen are parked there (IDEA-001 … IDEA-012 and IDEA-039: Tailscale for the backup copy, triggered by
the first deployed instance). IDEA-043 was unfrozen and built 2026-11-15; IDEA-044 (15-minute bar retention) was closed the same day: no retention policy, no compression; reopen if the universe grows about 10x or C: free space falls below about 100 GB.

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
   **Push right after every commit**, unless Kyle has said otherwise for that commit (standing rule, 2026-11-15). Then check that CI starts.
9. **Test keyed code against a second key whose data would WIN.** Anything keyed per series, feed, metric or provider
   (a read, an `UPDATE`, a `DELETE`) needs a test in which a *second* key's rows could **beat the right ones in every ordering
   or comparison the code applies**: a higher revision of the same period, a newer period, a larger value. One key cannot catch
   a lost `WHERE` (the one row it touches is the one row there is), and two keys of the same shape cannot either (a query that
   lost its filter returns the right answer by luck of ordering). The test changes or reads one key and asserts the other is
   exactly as it was. You check it by **mutation**: delete the `WHERE` or filter and watch the test fail, for every keyed read
   and write. This shipped once (M2 slice 5: the poll's `UPDATE series_history_state` had no `WHERE series_id`, every test
   had a single series, and in production it would have advanced all seventeen rows); the same hole was then found in
   `providers_health`, and again in step 4, where `value_as_of` and `latest_as_of` could drop `WHERE metric` and pass a
   two-metric test whose chains had the same shape. A green suite over one-key or same-shape fixtures says nothing about this.
10. **Mark every number in a plan as MEASURED or ASSUMED.** MEASURED means the date and how (`measured 2026-11-12, six requests`);
    ASSUMED means it says so (`assumed, not verified`). The risk is not the wrong number, it is a later reader treating an
    assumption as settled and building on it. Three plan numbers have been falsified by measurement so far, each written as fact:
    ADR-0003 **F1** (a revision's date can only be known to a poll interval), ADR-0003 **F2** (a `"."` published in no vintage) and
    step 5's thresholds (sized from **lag**; the gap between publications is the quantity). When a measurement falsifies one,
    say so in the plan entry (an addendum, for an ADR) and keep this count. **Count: five (2026-11-16).** Fourth: slice 6 step 1(a), "the cross-section needs an
    `(interval, ts)` index" (the primary key served it). Fifth: slice 6 step 1(b) F-a, the implicit premise that a completed yfinance bar is final (its volume was still
    changing 28 minutes after it ended; counted though it was never written as a number; Kyle ruled 2026-11-16 that it counts).

11. **A script that writes files stops on the first failure, and writes nothing until every change is known to apply.** Twice on
    2026-11-18 a step ran after the one before it had failed (a lint failure pushed; a doc script applied half its edits and the
    commit went through). Edit files through `backend/tools/edits.py` (all or nothing; every miss named). In the shell: `set -euo
    pipefail` and ONE COMMAND PER LINE, because bash ignores `set -e` for every command of an `&&` list except the last. Judge a
    gate by its exit code, never by piped output, and commit only after every gate's exit code is 0.

## Commands

```
cp .env.example .env              # first time only; set a real POSTGRES_PASSWORD
docker compose up -d              # private profile (COMPOSE_PROFILES in .env): stack + migrate + api + worker + beat
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
bash infra/smoke-m2-slice1.sh     # M2 slice 1: worker stopped/paused, Beat stopped, fresh start, Redis down -> documented states
bash infra/smoke-m2-slice4.sh     # M2 slice 4: fake provider on the real stack; killed -> failed runs, stale feed, ready stays ok; recovers
bash infra/smoke-m2-slice3.sh     # M2 slice 3: migrate + seed (24 symbols, 5 universes, sessions), idempotent, provider_mappings rules
bash infra/smoke-m2-slice5-step0.sh # M2 slice 5 step 0: two workers on two queues; ingest saturation never delays the ops canary; each worker wedged degrades ready
bash infra/smoke-m2-slice5-step3-sweep.sh # M2 slice 5 step 3 part C: SIGKILL the ingest worker mid-task -> busy, then stale, then the sweep fails both rows (~5 min)
bash infra/smoke-m2-slice5-fred-failures.sh # M2 slice 5: FRED 500/423/429 via a URL-echoing stub through the real worker; the api_key is in no stored or served text
bash infra/smoke-m2-beat-schedule.sh # IDEA-043: image owns /var/lib/beat, compose volume, a recreated Beat catches up a missed firing with the volume and not without it (~9 min)
bash infra/smoke-m2-slice2.sh     # M2 slice 2: encrypt, SFTP copy to stand-in sshd (3 permission policies), restore with the private key only
docker compose run --rm worker python -m app.backfill   # FRED revision history, every series not yet captured; --series A,B [--force]
docker compose run --rm worker python -m app.bars_backfill   # 2 years of settled daily bars, every mapped symbol; --symbols A,B. A re-run writes 0
bash infra/backup.sh              # dump + manifest into dumps/. Restoring and the version trap: docs/BACKUP.md
bash infra/restore.sh dumps/<name>.dump --db <new-or-empty-db>   # refuses a non-empty target
bash infra/restore-backup.sh <name>.tar.age --identity <age key> --db <new-or-empty-db>   # a scheduled backup, on the desktop
bash infra/mutation-check.sh m2-slice6-step2   # rule 9 mutations in a copy of backend/ (backend/tools/mutate.py); each must be caught
bash infra/mutation-check.sh m2-slice6-step3   # the yfinance client and parser (also: heartbeat-clock, m2-slice6-step4, -step5)
bash infra/ci-checks.sh lint      # also: test | audit | license | arm64. Same code ci.yml runs
                                  # LINT COVERAGE (pinned by backend/tests/test_lint_coverage.py): the gate runs INSIDE backend/ as
                                  #   `ruff check .`, `ruff format --check .`, `mypy` (config: backend/pyproject.toml; mypy `files` =
                                  #   app, tests, alembic, tools = every directory of Python we own; 129 files). NEVER check a list of
                                  #   paths (`ruff check app tests` let alembic/ through). The repo-root ruff.toml is only a guard for a
                                  #   run from the repo root (it keeps the FROZEN spec and spikes/ out of `ruff format .`, which rewrites
                                  #   Markdown code blocks); it does not change what backend/ lints (the nearest config wins). Not
                                  #   linted by anything: infra/*.sh (use `bash -n`), docs/ and spikes/ (frozen), repo-root tests/ (fixtures).
bash infra/lock-deps.sh           # after editing backend/requirements*.txt; regenerates the locks
docker compose down               # add -v to wipe the pgdata volume; inactive profiles are skipped,
                                  # so a public stack needs `docker compose --profile public down`
# NOTE: smoke scripts use infra/lib.sh wait_ready, not `up --wait`. Compose v5.6.1 fails --wait on
# the migrate one-shot even when it exits 0: seen on the other machine (2026-10-28, per Kyle) and on
# this one (2026-11-03). Other versions untested, including the v2.29.2-desktop.2 Kyle read here
# before Docker Desktop's 2026-11-03 upgrade. `docker compose up -d` is still the one command.
# NOTE: a native Windows postgresql-x64-17 service (automatic start) holds 5432 on this machine and
# stops the stack from starting. Stop it, or set POSTGRES_PORT in .env.
# NOTE: this machine does run arm64 under emulation (`docker run --platform linux/arm64 alpine uname -m` prints
# aarch64, checked 2026-11-08); the earlier "no emulation" note was wrong. Kyle's choice stands that local
# emulation is not the arm64 gate: CI's arm64 job installs its own QEMU and is. Re-run 2026-11-08:
# smoke-slice2.sh builds both architectures here and `ci-checks.sh arm64` passes (via smoke-slice8.sh), so
# the old claims that either skips or fails with `exec format error` no longer hold.
# NOTE: this machine's wall clock is NOT monotonic. Docker Desktop's WSL2 VM steps it back ~2.3 s every 30 s and runs it ~8% fast
# (MEASURED 2026-11-18, docs/BUILD-LOG.md). A test that orders two datetime.now() readings is flaky here: inject tests/clocks.py's
# stepless_clock(). Durations by wall clock (and Redis TTL spacing) run ~8% long/short. `bash infra/mutation-check.sh heartbeat-clock`.
# Migration tests (skipped without DATABASE_URL), from backend/:
#   DATABASE_URL=postgresql+asyncpg://flowdesk:<pw>@127.0.0.1:5432/flowdesk pytest
```

## Repo layout

```
docs/
  architecture.md      # v4.9.10 FROZEN spec — grep, never read whole
  adr/                 # ADR-0001 … 0008, binding
  IDEAS.md             # parked features, IDEA-001…012, each with an unfreeze trigger
  SPEC-DEFECTS.md      # flagged spec defects, IDEA-013…038
  BUILD-LOG.md         # narrative history: what shipped, what was decided, what broke
  HEALTH.md            # /health/* response shapes and the documented degraded states
  BACKUP.md            # restore runbook, the version trap, why plain pg_restore is forbidden
  HEARTBEAT.md         # external monitor setup, the alert drill, where it is armed, swapping provider
  RUNBOOK-IMAGE-CVE.md # the CI image scan goes red: re-pin the base or upgrade one package; never a threshold or ignore change
spikes/                # M0 proof scripts + evidence JSON (historical — do not modify)
tests/
  fixtures/            # oracle/golden test data (premium_oracle.json)
```

`backend/`, `frontend/` and `infra/` get created during M1.
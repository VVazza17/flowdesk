"""Task names, in a module with no imports so the API can read them without loading Celery."""

# The worker canary (M2 slice 1): see app/settings.py for the staleness rule.
CANARY = "ops.canary"

# The ingest worker's canary (M2 slice 5, step 0). Same body as CANARY, a different name
# (task_runs is keyed by task name, so each health check reads its own history) and a
# different queue.
INGEST_CANARY = "ops.canary_ingest"

# Two queues, two worker processes (docker-compose.yml). `ops` carries CANARY and nothing
# else, and its worker takes nothing else, so no amount of ingest work can make it wait.
# `ingest` is the default for every other task: a long task can safely land there, because the
# only thing it can delay is other ingest work and INGEST_CANARY, whose threshold is sized to
# the longest allowed task.
OPS_QUEUE = "ops"
INGEST_QUEUE = "ingest"

# The fake provider's ingest (M2 slice 4). Only scheduled when FAKE_PROVIDER_ENABLED is true.
FAKE_INGEST = "ingest.fake"

# The scheduled backup (M2 slice 2). Two tasks, not one, so that a sleeping off-box machine reads as
# a failed copy and not as a failed backup: the dump is encrypted and kept locally first, the copy
# is attempted afterwards and retried on its own. Both are written by the backup container (psql),
# not by Celery, and read by /health/data.
BACKUP = "ops.backup"
BACKUP_COPY = "ops.backup_copy"

# task_runs.status vocabulary (a stopgap under IDEA-022; §8 gives none). Kept here, with no imports,
# so the API can read it without loading SQLAlchemy. `skipped` is for a step that was not configured
# to run: it is neither a success nor a failure and must never be read as either.
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# The FRED history backfill (M2 slice 5, step 3). NOT a Celery task: it is the entry point
# `python -m app.backfill`, run by hand (BUILD-LOG, 2026-11-13: one-time plus deliberate
# re-capture, and at about 90 minutes it must not live under the task limit). This is only its
# name in the `task_runs` ledger, where it belongs like any other execution: the entry point
# goes through `run_recorded`, so a manual backfill has a real row and the sweep's rule applies
# unchanged.
FRED_BACKFILL = "ingest.fred_backfill"

# The 2-year daily bars backfill (M2 slice 6, step 4): `python -m app.bars_backfill`, the
# counterpart to `python -m app.backfill`. Not a Celery task, for the same reasons; this is its
# name in `task_runs`.
BARS_BACKFILL = "ingest.bars_backfill"

# The settled bars polls (M2 slice 6, step 6). Celery tasks on the ingest queue; Beat schedules them
# only when BARS_POLL_ENABLED is true (app/settings.py: off until IDEA-029 closes).
BARS_DAILY_POLL = "ingest.bars_daily_poll"
BARS_15M_POLL = "ingest.bars_15m_poll"

# The daily FRED poll (M2 slice 5, step 3). A Celery task on the ingest queue, scheduled by Beat at
# 08:00 America/New_York (6.4): it asks FRED only for what changed since each series was captured.
FRED_POLL = "ingest.fred_poll"

# The sweep (M2 slice 5, step 3, part C): fails the `running` rows nothing is alive behind.
# Runs on the `ops` queue, so a saturated ingest queue cannot starve it.
SWEEP = "ops.sweep"

# Tasks the sweep must NOT touch, with the reason (a reasoned exclusion, not a gap). The backup
# container writes these rows with psql, not through `run_recorded`, so nothing beats their
# heartbeat: a healthy dump or an SFTP copy that takes longer than HEARTBEAT_FRESH_SECONDS would be
# failed as orphaned, and the script's own final UPDATE (which is unconditional) would then
# overwrite the sweep's verdict. Their staleness is already read from /health/data and the
# external monitor's missed-run signal. Revisit if the backup scripts ever heartbeat.
# Two writers of task_runs, on two protocols: the backup scripts' final write (task_finish in
# infra/backup/lib.sh) is an unconditional `UPDATE ... WHERE id = :id`, while run_recorded's is
# `AND status = 'running'` so that a swept row keeps the sweep's verdict. If the backups ever join
# the sweep, that write becomes conditional IN THE SAME CHANGE.
UNSWEPT_TASKS = (BACKUP, BACKUP_COPY)

# Celery tasks that run IN THE INGEST WORKER. A fresh running row of one of these proves that worker
# is alive (checks.ingest_worker reports `busy`). Deliberately not FRED_BACKFILL: the backfill is a
# separate process (`python -m app.backfill`), and its heartbeat says nothing about the ingest
# worker; counting it would let a wedged ingest worker look busy for the length of a backfill.
# The bars polls (step 6) run in the ingest worker and count; the bars backfill is a separate
# process, like FRED_BACKFILL, and does not.
INGEST_WORKER_TASKS = (FAKE_INGEST, FRED_POLL, BARS_DAILY_POLL, BARS_15M_POLL)

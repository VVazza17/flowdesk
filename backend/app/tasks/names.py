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

# The FRED backfill (M2 slice 5, step 3). Started by hand (`celery call`), never by Beat: it fetches
# every series' whole first-print history, which is slow and is not something to repeat on a timer.
FRED_BACKFILL = "ingest.fred_backfill"

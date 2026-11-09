"""Task names, in a module with no imports so the API can read them without loading Celery."""

# The worker canary (M2 slice 1): see app/settings.py for the staleness rule.
CANARY = "ops.canary"

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

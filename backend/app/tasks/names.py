"""Task names, in a module with no imports so the API can read them without loading Celery."""

# The worker canary (M2 slice 1): see app/settings.py for the staleness rule.
CANARY = "ops.canary"

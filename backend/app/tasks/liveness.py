"""What "a running task is alive" means, in one place (M2 slice 5, step 3, part C).

The heartbeat writer (`app.tasks.runs`), the readiness check (`app.services.health_probes`) and the
sweep (`app.tasks.sweep`) all import from here; none of them restates a number. A disagreement
between them is the failure this module exists to prevent: a task the health check calls alive
and the sweep calls dead, or the reverse.

Pure: no database, no clock. `now` is always passed in.
"""

from datetime import datetime, timedelta

# How often a running task writes its heartbeat (from a background thread, so a task body does not
# have to remember to).
HEARTBEAT_INTERVAL_SECONDS = 30

# How old a heartbeat may be and still count as alive: four intervals, so that three consecutive
# beats can be lost to a slow commit or a GC pause before a live task looks dead.
HEARTBEAT_FRESH_SECONDS = 120

# How often the sweep looks. An orphaned row is therefore failed within about
# HEARTBEAT_FRESH_SECONDS + SWEEP_INTERVAL_SECONDS of its last beat.
SWEEP_INTERVAL_SECONDS = 120


def stale_cutoff(now: datetime) -> datetime:
    """A heartbeat older than this is not fresh. The one place the rule is computed; the sweep and
    the health probe use it in SQL (`heartbeat_at < cutoff`), `is_fresh` uses it in Python."""
    return now - timedelta(seconds=HEARTBEAT_FRESH_SECONDS)


def is_fresh(heartbeat_at: datetime | None, now: datetime) -> bool:
    """True if the task has been heard from recently enough to be called alive. NULL never is."""
    return heartbeat_at is not None and heartbeat_at >= stale_cutoff(now)

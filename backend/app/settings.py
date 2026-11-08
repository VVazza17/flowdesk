"""Runtime configuration, read from the environment at call time.

# DECISION: plain os.environ, not pydantic-settings. Slice 6 needs two URLs and an (empty)
# threshold map; a settings library is a dependency for no gain yet. Revisit when config grows.
"""

import os
from datetime import timedelta
from typing import Literal

DeploymentMode = Literal["private", "public"]


def deployment_mode() -> DeploymentMode:
    # DECISION: unset means private, §14's stated default; compose sets it explicitly per profile
    # (ADR-0007). Anything else refuses to start, with no case-folding: a typo in the variable that
    # turns on redaction must not silently produce the unredacted mode.
    raw = os.environ.get("DEPLOYMENT_MODE", "private")
    if raw == "private":
        return "private"
    if raw == "public":
        return "public"
    raise ValueError(f"DEPLOYMENT_MODE must be 'private' or 'public', got {raw!r}")


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or None


def redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or None


def heartbeat_url() -> str | None:
    """The external monitor's ping URL (ADR-0008). Unset means this instance is not monitored.

    The URL is a credential: anyone holding it can fake this box being alive. So the error below
    deliberately does not echo the value, unlike DEPLOYMENT_MODE's.
    """
    raw = os.environ.get("HEARTBEAT_URL", "").strip()
    if not raw:
        return None
    if not raw.startswith(("https://", "http://")):
        raise ValueError("HEARTBEAT_URL must start with https:// or http://")
    return raw


def heartbeat_interval_seconds() -> int:
    # The monitor's period is set to 5 minutes (ADR-0008). Overridable because the smoke test can't
    # wait that long, not as a tuning knob: Healthchecks asks for at most 5 pings a minute.
    raw = os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "").strip() or "300"
    value = int(raw)
    if value <= 0:
        raise ValueError(f"HEARTBEAT_INTERVAL_SECONDS must be positive, got {raw!r}")
    return value


# Per-feed freshness thresholds (§6 line 369: "per-feed thresholds in config"), keyed by
# (provider, feed). Empty until M2 registers real feeds. §6.11 requires thresholds to follow
# each series' own publication cadence, so there is deliberately no global default: a feed
# without an entry reports stale=null rather than being judged against an invented number.
FEED_STALE_AFTER: dict[tuple[str, str], timedelta] = {}


# --- The worker canary (M2 slice 1) ---------------------------------------------------------------
# A trivial Beat task whose recorded completions are the evidence that Beat, Redis, the worker and
# Postgres all work together. /health/ready reads the age of the last success (keyed on task_runs,
# not on whether a process is alive, so a wedged worker degrades too).
#
# DECISION: stale after three missed runs. One miss can be a queued or slow run; three is not. The
# threshold is derived from the interval, so the API (which judges) and Beat (which schedules) read
# the same variable and cannot disagree. Revisit when long tasks share the worker: a canary stuck
# behind a long backfill would look like a dead worker.
WORKER_STALE_MULTIPLIER = 3


def canary_interval_seconds() -> int:
    # Overridable because the smoke test cannot wait minutes for a stale verdict, not for tuning.
    raw = os.environ.get("CANARY_INTERVAL_SECONDS", "").strip() or "60"
    value = int(raw)
    if value <= 0:
        raise ValueError(f"CANARY_INTERVAL_SECONDS must be positive, got {raw!r}")
    return value


def worker_stale_after() -> timedelta:
    return timedelta(seconds=canary_interval_seconds() * WORKER_STALE_MULTIPLIER)

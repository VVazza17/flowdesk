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


# Per-feed freshness thresholds (§6 line 369: "per-feed thresholds in config"), keyed by
# (provider, feed). Empty until M2 registers real feeds. §6.11 requires thresholds to follow
# each series' own publication cadence, so there is deliberately no global default: a feed
# without an entry reports stale=null rather than being judged against an invented number.
FEED_STALE_AFTER: dict[tuple[str, str], timedelta] = {}

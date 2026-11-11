"""Runtime configuration, read from the environment at call time.

# DECISION: plain os.environ, not pydantic-settings. Slice 6 needs two URLs and an (empty)
# threshold map; a settings library is a dependency for no gain yet. Revisit when config grows.
"""

import os
from dataclasses import dataclass
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


def backup_stale_after() -> timedelta:
    """How old the last successful backup (or copy) may be before /health/data calls it stale.

    DECISION: 36 hours. The backup runs daily; the extra twelve hours absorb a laptop that slept
    through its slot and caught up on waking. It is a statement on /health/data, not a page: a stale
    backup must not degrade /health/ready. The page comes from the external monitor noticing that
    the copy's pings stopped (docs/BACKUP.md).
    """
    raw = os.environ.get("BACKUP_STALE_AFTER_HOURS", "").strip() or "36"
    hours = float(raw)
    if hours <= 0:
        raise ValueError(f"BACKUP_STALE_AFTER_HOURS must be positive, got {raw!r}")
    return timedelta(hours=hours)


# --- Universes (§6.2) and the reference seed (M2 slice 3, IDEA-033) ---------------------------
# DECISION: these live in settings.py, not config.py. §6.2 says "defined in `config.py`", but this
# repo's configuration module is settings.py, and a second module would split one concern. They are
# plain constants, not environment variables: §6.2's lists are the definition, and the seeder is
# insert-only (below), so an override could only add members and report drift, never close anything.


@dataclass(frozen=True)
class UniverseSpec:
    slug: str
    name: str
    description: str
    tickers: tuple[str, ...]


# §6.2, verbatim. The rotation list is 14 funds; §3 line 117 says "12-ETF" and §6.12 and §7.1 say
# "11 sector ETFs". That contradiction is IDEA-041. We seed §6.2 because it is the only place that
# names tickers, and we do not add XLC: that would be inventing a member.
ROTATION_UNIVERSE = (
    "XLK",
    "SMH",
    "IGV",
    "XLF",
    "XLE",
    "XLI",
    "XLV",
    "XLY",
    "XLP",
    "XLU",
    "XLB",
    "XLRE",
    "IWM",
    "ARKK",
)
BENCHMARK = ("SPY",)
STYLE_EXTRAS = ("QQQ", "RSP")  # RSP is needed for breadth (§7.1)
COMMODITY_PROXIES = ("USO", "UNG", "GLD", "SLV", "CPER", "URA", "DBA")  # ETF proxies, label as such
# DECISION: empty, on purpose. §6.2 says "S&P 500 constituents" and gives no list, and M2 has no
# constituent provider (yfinance, FRED and a fake provider only). Seeding it would mean inventing
# about 500 tickers. The universe row exists; its members arrive with a constituent source.
BREADTH_UNIVERSE: tuple[str, ...] = ()

UNIVERSES: tuple[UniverseSpec, ...] = (
    UniverseSpec("rotation", "Rotation universe", "§6.2 ROTATION_UNIVERSE", ROTATION_UNIVERSE),
    UniverseSpec("benchmark", "Benchmark", "§6.2 BENCHMARK", BENCHMARK),
    UniverseSpec("style_extras", "Style extras", "§6.2 style extras (QQQ, RSP)", STYLE_EXTRAS),
    UniverseSpec(
        "commodity_proxies",
        "Commodity proxies",
        "§6.2 COMMODITY_PROXIES: commodity exposure through ETFs, shown as ETF proxies",
        COMMODITY_PROXIES,
    ),
    UniverseSpec(
        "breadth",
        "Breadth universe",
        "§6.2 BREADTH_UNIVERSE: S&P 500 constituents. No members until a source exists.",
        BREADTH_UNIVERSE,
    ),
)

# Trading sessions are materialised for every calendar the seeded exchanges name, from this date
# to a year past today. DECISION: 2015-01-01 covers the two years of daily bars (§6.1) and a long
# backtest with room to spare, and costs about 250 rows a year per calendar. Each seed run extends
# the horizon; rows already present are never rewritten (see app.services.sessions).
SESSIONS_START_YEAR = 2015
SESSIONS_HORIZON_DAYS = 365

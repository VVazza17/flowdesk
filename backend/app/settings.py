"""Runtime configuration, read from the environment at call time.

# DECISION: plain os.environ, not pydantic-settings. Slice 6 needs two URLs and an (empty)
# threshold map; a settings library is a dependency for no gain yet. Revisit when config grows.
"""

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from app.ingest.budget import RateBudget

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


def feed_stale_after() -> dict[tuple[str, str], timedelta]:
    """FEED_STALE_AFTER plus the fake provider's feed when it is switched on. Read at call time, so
    the API (which judges) and the worker (which ingests) share one environment variable."""
    thresholds = dict(FEED_STALE_AFTER)
    if fake_provider_enabled():
        thresholds[FAKE_FEED] = timedelta(
            seconds=fake_provider_interval_seconds() * WORKER_STALE_MULTIPLIER
        )
    return thresholds


# --- The worker canary (M2 slice 1) ---------------------------------------------------------------
# A trivial Beat task whose recorded completions are the evidence that Beat, Redis, the worker and
# Postgres all work together. /health/ready reads the age of the last success (keyed on task_runs,
# not on whether a process is alive, so a wedged worker degrades too).
#
# DECISION: stale after three missed runs. One miss can be a queued or slow run; three is not. The
# threshold is derived from the interval, so the API (which judges) and Beat (which schedules) read
# the same variable and cannot disagree.
#
# Long tasks (M2 slice 5, step 0): the canary cannot queue behind them. It has its own queue and its
# own worker process (`ops`, one slot), and ingest work runs on another (`ingest`). A second canary
# runs on the ingest queue so that a wedged ingest worker is still machinery stopping and still
# degrades /health/ready; see ingest_worker_stale_after for why its threshold is different.
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


def ingest_task_max_seconds() -> int:
    """The longest any task may run: Celery kills it past this (celery_app.py, task_time_limit).

    # DECISION: 30 minutes. A guess, not a measurement: no real feed exists yet, and the FRED
    # backfill (slice 5) and the bar backfill (slice 10) are the first tasks that could approach
    # it. A task that needs longer is chunked, or given its own limit when it is built; raising
    # this raises the time a wedged ingest worker takes to show, because the ingest canary's
    # threshold is built from it.
    # A task killed by this limit leaves its task_runs and ingestion_runs rows 'running' (see the
    # DECISION in app/tasks/runs.py: the sweep arrives with the first long task, which is now).
    """
    raw = os.environ.get("INGEST_TASK_MAX_SECONDS", "").strip() or "1800"
    value = int(raw)
    if value <= 0:
        raise ValueError(f"INGEST_TASK_MAX_SECONDS must be positive, got {raw!r}")
    return value


def ingest_worker_stale_after() -> timedelta:
    """How old the ingest canary's last success may be before /health/ready calls the ingest worker
    stale: the longest allowed task, plus the usual three missed canaries.

    A task running for the whole limit can legitimately starve the ingest canary for that long (both
    slots busy), so a tighter number would page on a healthy backfill. A looser one would let a
    wedged worker hide. This is the one threshold derived from a limit that is enforced, not
    guessed.
    """
    return timedelta(
        seconds=ingest_task_max_seconds() + canary_interval_seconds() * WORKER_STALE_MULTIPLIER
    )


# --- Ingest framework (M2 slice 4) -------------------------------------------------------------
# Rate budgets by provider (app.ingest.budget). A provider with no entry cannot be called: the
# limiter refuses rather than run unthrottled. yfinance (>=1 s spacing, ~100 per batch, §6.12) is
# added by slice 6 and FRED by slice 5, each with a number it has verified.
RATE_BUDGETS: dict[str, RateBudget] = {"fake": RateBudget(min_interval_seconds=0.2, max_batch=100)}


def ingest_retry_initial_seconds() -> float:
    # Overridable because the smoke test cannot wait out a real backoff, not for tuning.
    raw = os.environ.get("INGEST_RETRY_INITIAL_SECONDS", "").strip() or "1"
    value = float(raw)
    if value <= 0:
        raise ValueError(f"INGEST_RETRY_INITIAL_SECONDS must be positive, got {raw!r}")
    return value


# The fake provider (app/ingest/fake.py) exists to exercise the framework and to give the gate's
# "kill a provider" clause something to kill. It is OFF unless FAKE_PROVIDER_ENABLED=true, and then
# it only registers one Beat task and one feed. It never runs in a normal stack.
FAKE_FEED = ("fake", "ticks")
FAKE_MODES = ("ok", "transient", "rate_limited", "permanent", "empty", "schema", "down", "slow")


def fake_provider_enabled() -> bool:
    return os.environ.get("FAKE_PROVIDER_ENABLED", "").strip().lower() == "true"


def fake_provider_mode() -> str:
    mode = os.environ.get("FAKE_PROVIDER_MODE", "").strip() or "ok"
    if mode not in FAKE_MODES:
        raise ValueError(f"FAKE_PROVIDER_MODE must be one of {FAKE_MODES}, got {mode!r}")
    return mode


def fake_provider_slow_seconds() -> float:
    """How long a `slow` fake call hangs before it succeeds (M2 slice 5, step 0)."""
    raw = os.environ.get("FAKE_PROVIDER_SLOW_SECONDS", "").strip() or "60"
    value = float(raw)
    if value <= 0:
        raise ValueError(f"FAKE_PROVIDER_SLOW_SECONDS must be positive, got {raw!r}")
    return value


def fake_provider_interval_seconds() -> int:
    raw = os.environ.get("FAKE_PROVIDER_INTERVAL_SECONDS", "").strip() or "60"
    value = int(raw)
    if value <= 0:
        raise ValueError(f"FAKE_PROVIDER_INTERVAL_SECONDS must be positive, got {raw!r}")
    return value


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

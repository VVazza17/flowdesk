"""Runtime configuration, read from the environment at call time.

# DECISION: plain os.environ, not pydantic-settings. Slice 6 needs two URLs and an (empty)
# threshold map; a settings library is a dependency for no gain yet. Revisit when config grows.
"""

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.ingest.budget import RateBudget
from app.ingest.fred import BASE_URL
from app.ingest.macro import FredSeries

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

# The daily FRED poll runs at this time, America/New_York (6.4; Beat's timezone is the same).
FRED_POLL_HOUR, FRED_POLL_MINUTE = 8, 0
FRED_POLL_STALE_AFTER = timedelta(hours=72)  # three missed daily runs


def feed_stale_after() -> dict[tuple[str, str], timedelta]:
    """FEED_STALE_AFTER plus the fake provider's feed when it is switched on. Read at call time, so
    the API (which judges) and the worker (which ingests) share one environment variable."""
    thresholds = dict(FEED_STALE_AFTER)
    # DECISION: every FRED feed is judged by the POLL, three missed daily runs (72 h), the same
    # "three missed" rule as the canaries. This is poll health, not data freshness: last_success_at
    # moves whenever a poll or backfill of the series succeeds, changed data or not, so a quarterly
    # series polled daily is fresh. How stale the DATA is against its own publication cadence
    # (ADR-0003 decision 5) is a separate question, answered by step 4's point-in-time read.
    for item in FRED_SERIES:
        thresholds[("fred", item.series_id)] = FRED_POLL_STALE_AFTER
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
    stale: the usual three missed canaries, the same as the ops worker.

    It used to be INGEST_TASK_MAX_SECONDS plus three intervals, because a long task could starve the
    canary for that long and a tighter number would have paged on a healthy backfill. Part C removed
    the reason: a task now beats `heartbeat_at` from a background thread, and the ingest worker is
    `busy` (alive, not degraded) while a task of its own has a fresh beat. So a canary stuck behind
    a
    long task no longer has to be waited out, and a wedged ingest worker shows in minutes.
    `INGEST_TASK_MAX_SECONDS` stays as Celery's hard `task_time_limit`, nothing more.
    """
    return worker_stale_after()


# --- Ingest framework (M2 slice 4) -------------------------------------------------------------
# Rate budgets by provider (app.ingest.budget). A provider with no entry cannot be called: the
# limiter refuses rather than run unthrottled. §6.12's yfinance figure (>=1 s spacing, ~100 per
# batch) is not used: slice 6 set its own from a measurement, and FRED's is slice 5's.
#
# FRED: one request a second, 60 a minute, half the documented cap. The cap is DOCUMENTED, NOT
# MEASURED (no 429 has been observed). Kyle read FRED's errors page on 2026-11-12: "429 Too
# Many Requests (Up to 120 requests per minute are allowed before being served a 429 error
# code. Not complying with the throttling can result in a temporary block.)" The half is
# headroom for that being wrong and for a page that is not the whole story; the "temporary
# block" is the cost of finding out.
#
# yfinance: one 24-ticker batch a minute (Kyle, 2026-11-18). MEASURED 2026-11-16 (step 1(b)): ten
# 24-ticker batches 2 s apart and about 45 single calls in 20 minutes, no throttling; the threshold
# was not found (deliberately not provoked). One a minute is 30x slower than what was measured, and
# that safety factor is ASSUMED. The VM's ~8% fast clock (IDEA-048) is irrelevant at this spacing.
# max_batch is the measured batch size; a bigger batch is unmeasured. The margin also assumes the
# yfinance cache as it runs (yfinance_cache_dir, inside the container): a fresh `docker compose run`
# starts cold, so its first batch costs about double (50 requests against 24 warm, MEASURED
# 2026-11-18); the long-lived worker warms after one batch. Step 1(b) measured under the same.
RATE_BUDGETS: dict[str, RateBudget] = {
    "fake": RateBudget(min_interval_seconds=0.2, max_batch=100),
    "fred": RateBudget(min_interval_seconds=1.0, max_batch=1),
    "yfinance": RateBudget(min_interval_seconds=60.0, max_batch=24),
}


def yfinance_cache_dir() -> str:
    """Where yfinance keeps its timezone, cookie and ISIN caches: a writable directory the image
    creates for the app user (backend/Dockerfile). Without one, yfinance runs uncached and makes
    extra requests per batch (2026-11-18)."""
    return os.environ.get("YFINANCE_CACHE_DIR", "/var/cache/yfinance")


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


# --- FRED (M2 slice 5, step 2) -----------------------------------------------------------------
# FRED's own words for a malformed key are "not a 32 character alpha-numeric lower-case string",
# so a key that is not one is rejected here, loudly and early, instead of becoming a 400
# mid-ingest. The value is never put in the message: it is a credential.
_FRED_KEY = re.compile(r"[a-z0-9]{32}")


def fred_api_key() -> str | None:
    """FRED_API_KEY, or None when unset. A malformed value raises, without echoing it."""
    raw = os.environ.get("FRED_API_KEY", "").strip()
    if not raw:
        return None
    if not _FRED_KEY.fullmatch(raw):
        raise ValueError(
            "FRED_API_KEY must be 32 lowercase alphanumeric characters (value not shown)"
        )
    return raw


def fred_user_agent() -> str:
    """The User-Agent the FRED client sends: explicit, never the HTTP library's default.

    # DECISION (Kyle, 2026-11-11): FRED documents no User-Agent requirement, unlike EDGAR
    # (§6.5), so a blank value falls back to this default and does not stop startup. The
    # default names the app and nobody else: no email, no personal contact. Set
    # FRED_USER_AGENT to change it.
    """
    return os.environ.get("FRED_USER_AGENT", "").strip() or "FlowDesk/0.1 (macro-data ingest)"


def fred_base_url() -> str:
    """FRED_BASE_URL, for pointing the client at a local stub (a smoke) and nowhere else."""
    return os.environ.get("FRED_BASE_URL", "").strip() or BASE_URL


# --- Data age: how long without a NEW period is too long, per FRED series (M2 slice 5, step 5) ---
# `/health/data` judges each series by the age of its newest first print against these. MEASURED,
# not derived at request time: `python -m app.measure_cadence` prints the gaps between publication
# days over the last five years and the threshold the rule (app/ingest/cadence.py: 3 x p95, rounded
# up to whole days, at least 2) would choose; this table is updated from it by a person. A
# live-data test (tests/test_data_age_live.py) fails loudly in a development stack if one drifts.
#
# MEASURED 2026-11-14 on the dev database (5 years of first prints). Daily series publish every
# business day (gap p95 3 days); weekly: 7 to 8; monthly: 28 to 36; DRCCLACBS quarterly: 91.
FRED_DATA_STALE_AFTER: dict[str, timedelta] = {
    "DGS10": timedelta(days=9),
    "DGS2": timedelta(days=9),
    "T10Y2Y": timedelta(days=9),
    "MORTGAGE30US": timedelta(days=21),
    "BAMLH0A0HYM2": timedelta(days=9),
    "BAMLC0A0CM": timedelta(days=9),
    "CPIAUCSL": timedelta(days=105),
    "UNRATE": timedelta(days=105),
    "HOUST": timedelta(days=109),  # its longest gap, 114 days, is the 2025 US government shutdown
    "CSUSHPINSA": timedelta(days=105),
    "DRCCLACBS": timedelta(days=286),
    "VIXCLS": timedelta(days=9),
    "DTWEXBGS": timedelta(days=24),
    "DFII10": timedelta(days=9),
    "T10YIE": timedelta(days=9),
    "SOFR": timedelta(days=9),
    "IORB": timedelta(days=9),
}


def data_stale_after(metric: str) -> timedelta | None:
    return FRED_DATA_STALE_AFTER.get(metric)


def data_stale_after_all() -> dict[str, timedelta]:
    return dict(FRED_DATA_STALE_AFTER)


# The series of §6.4, with the two dates that bound each one's point-in-time record. MEASURED
# 2026-11-12 against the real API (docs/BUILD-LOG.md, step 2c), not derived and not documented by
# FRED: `first_vintage` is the first date from series/vintagedates; `first_covered_period` is the
# earliest period output_type=4 returns. Every earlier period exists in FRED's latest values with no
# publication date, so it is NOT in macro_metrics (nothing enters a point-in-time table without a
# real publication date). A read that finds no row for a period before `first_covered_period` is
# looking at a value that predates the vintage record, which is not the same as no value existing.
# The boundary differs by decades between series. Adding a series is one row; the backfill warns
# if what it observes no longer matches.
FRED_SERIES: tuple[FredSeries, ...] = (
    FredSeries("DGS10", date(2005, 6, 28), date(2005, 6, 27)),
    FredSeries("DGS2", date(2005, 6, 28), date(2005, 6, 27)),
    FredSeries("T10Y2Y", date(2014, 1, 27), date(2014, 1, 27)),
    FredSeries("MORTGAGE30US", date(2010, 6, 17), date(2010, 6, 24)),
    FredSeries("BAMLH0A0HYM2", date(2023, 11, 13), date(2023, 11, 14)),
    FredSeries("BAMLC0A0CM", date(2023, 11, 13), date(2023, 11, 14)),
    FredSeries("CPIAUCSL", date(1972, 7, 21), date(1972, 7, 1)),
    FredSeries("UNRATE", date(1960, 3, 15), date(1960, 3, 1)),
    FredSeries("HOUST", date(1960, 7, 21), date(1960, 7, 1)),
    FredSeries("CSUSHPINSA", date(2014, 11, 25), date(2014, 10, 1)),
    FredSeries("DRCCLACBS", date(2011, 5, 24), date(2011, 4, 1)),
    FredSeries("VIXCLS", date(2010, 11, 22), date(2010, 11, 22)),
    FredSeries("DTWEXBGS", date(2019, 2, 4), date(2019, 2, 4)),
    FredSeries("DFII10", date(2005, 10, 12), date(2005, 10, 10)),
    FredSeries("T10YIE", date(2014, 1, 27), date(2014, 1, 27)),
    FredSeries("SOFR", date(2019, 3, 29), date(2019, 3, 29)),
    FredSeries("IORB", date(2021, 7, 28), date(2021, 7, 30)),
)

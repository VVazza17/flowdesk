"""Health-endpoint logic: dependency checks, feed-state classification, response assembly.

Deliberately free of sqlalchemy/redis imports so the /health/live import path stays
dependency-free (tests/test_health_live.py checks this). The real probes live in
app.services.health_probes and are loaded lazily by app.deps; everything here takes a
`HealthProbes` so tests can substitute fakes.

Documented states: docs/HEALTH.md.
"""
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

CHECK_TIMEOUT_S = 2.0
LAST_ERROR_MAX_CHARS = 500


@dataclass(frozen=True)
class FeedRow:
    """One providers_health row (§8 line 778)."""

    provider: str
    feed: str
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None


class HealthProbes(Protocol):
    """What the endpoints need from the outside world. Probes raise on failure."""

    async def ping_postgres(self) -> None: ...
    async def ping_redis(self) -> None: ...
    async def migration_revisions(self) -> tuple[str | None, str]:
        """(revision recorded in the database or None, revision the code expects)."""
        ...
    async def provider_rows(self) -> list[FeedRow]: ...
    async def data_state(self) -> dict[str, list[dict[str, Any]]]: ...


async def run_check(name: str, probe: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    """Run one probe; a failure becomes data, never an exception.

    Only the exception *class* is reported. Messages can carry connection strings (and the
    password inside them), and this body may one day be public (slice 7).
    """
    try:
        await asyncio.wait_for(probe(), timeout=CHECK_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - the whole point is that nothing escapes
        err = type(exc).__name__
        log.warning("health_check_failed", check=name, error=err)
        return {"status": "down", "error": err}
    return {"status": "ok"}


async def assemble_ready(probes: HealthProbes) -> tuple[int, dict[str, Any]]:
    """Readiness: ok (200) | degraded (200, Redis down) | not_ready (503).

    Redis is a cache/bus, never the source of truth, so losing it degrades; losing Postgres
    or running against an unmigrated schema means the API cannot serve real data.
    """
    pg, redis = await asyncio.gather(
        run_check("postgres", probes.ping_postgres),
        run_check("redis", probes.ping_redis),
    )
    migrations = await _check_migrations(probes, pg_ok=pg["status"] == "ok")
    checks = {"postgres": pg, "redis": redis, "migrations": migrations}

    if pg["status"] != "ok" or migrations["status"] != "ok":
        return 503, {"status": "not_ready", "checks": checks}
    if redis["status"] != "ok":
        return 200, {"status": "degraded", "checks": checks}
    return 200, {"status": "ok", "checks": checks}


async def _check_migrations(probes: HealthProbes, *, pg_ok: bool) -> dict[str, Any]:
    if not pg_ok:
        return {"status": "unknown", "reason": "postgres_down"}
    revisions: tuple[str | None, str] | None = None

    async def probe() -> None:
        nonlocal revisions
        revisions = await probes.migration_revisions()

    result = await run_check("migrations", probe)
    if result["status"] != "ok" or revisions is None:
        return {"status": "unknown", "error": result.get("error", "NoResult")}
    current, head = revisions
    return {"status": "ok" if current == head else "behind", "current": current, "head": head}


def classify_feed(row: FeedRow, now: datetime, threshold: timedelta | None) -> dict[str, Any]:
    """One feed's state. `now` is passed in: no clock in the logic.

    fresh            succeeded within its threshold
    stale            succeeded, but longer ago than its threshold -> something broke
    never_succeeded  registered, no success yet. NOT stale: before M2's first run this is the
                     normal state. `last_error_at` set means it has been failing since the start.
    no_threshold     has succeeded but no threshold is configured, so it cannot be judged
                     (stale is null; we do not invent a number, §6.11)

    DECISION: providers_health has no first-seen column, so "never succeeded" carries no age
    and cannot escalate to a failure on its own. If that is wanted, §8 needs the column.
    """
    last_error = row.last_error[:LAST_ERROR_MAX_CHARS] if row.last_error else None
    out: dict[str, Any] = {
        "provider": row.provider,
        "feed": row.feed,
        "last_success_at": _iso(row.last_success_at),
        "last_error_at": _iso(row.last_error_at),
        # DECISION: last_error is raw provider text. Fine on the private profile; slice 7
        # must decide (with IDEA-019) whether the public profile may expose it.
        "last_error": last_error,
        "threshold_seconds": int(threshold.total_seconds()) if threshold else None,
        "last_attempt_failed": bool(
            row.last_error_at
            and (row.last_success_at is None or row.last_error_at > row.last_success_at)
        ),
    }
    if row.last_success_at is None:
        out.update(status="never_succeeded", stale=False)
    elif threshold is None:
        out.update(status="no_threshold", stale=None)
    elif now - row.last_success_at > threshold:
        out.update(status="stale", stale=True)
    else:
        out.update(status="fresh", stale=False)
    return out


def assemble_providers(
    rows: list[FeedRow], now: datetime, thresholds: dict[tuple[str, str], timedelta]
) -> dict[str, Any]:
    """Top-level status, in precedence order. Only `stale` means something broke:

    no_feeds   nothing registered (fresh install, pre-M2)  -> expected
    stale      at least one feed succeeded before and is now overdue  -> broken
    pending    no stale feed, but some have never succeeded  -> not broken, not done
    unjudged   no stale feed, but some have no threshold configured
    ok         every feed is fresh
    """
    feeds = [classify_feed(r, now, thresholds.get((r.provider, r.feed))) for r in rows]
    states = {f["status"] for f in feeds}
    if not feeds:
        status = "no_feeds"
    elif "stale" in states:
        status = "stale"
    elif "never_succeeded" in states:
        status = "pending"
    elif "no_threshold" in states:
        status = "unjudged"
    else:
        status = "ok"
    return {"status": status, "feeds": feeds}


def assemble_data(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Pipeline state as recorded; no verdict.

    # DECISION: this reports and does not judge. §8 gives ingestion_runs/task_runs/backfill_runs
    # no status vocabulary (IDEA-022), so any "failed" test here would be invented.
    #
    # ASSUMPTION (IDEA-024, unresolved): backfill progress is read from backfill_runs, which is
    # §16's reading (line 1109). §10 line 1003 says it comes from providers_health instead. If
    # IDEA-024 is resolved the other way, the `backfills` key here and its probe move.
    """
    empty = not any(state.get(k) for k in ("ingestion", "backfills", "tasks"))
    return {"status": "no_runs" if empty else "reported", **state}


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None

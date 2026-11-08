"""Slice 6: /health/ready, /health/data, /health/providers degrade instead of returning 500.

No infrastructure needed: probes are faked via dependency_overrides. TestClient re-raises
server exceptions, so any path that would be a 500 fails these tests loudly.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.deps import get_now, get_probes
from app.main import create_app
from app.services import health as svc
from app.services.health import FeedRow, HealthProbes

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


class FakeProbes:
    def __init__(
        self,
        *,
        postgres: Exception | None = None,
        redis: Exception | None = None,
        revisions: tuple[str | None, str] = ("0002", "0002"),
        rows: list[FeedRow] | None = None,
        state: dict[str, list[dict[str, Any]]] | None = None,
        redis_delay: float = 0,
        # Default: the canary completed ten seconds ago, so a test about something else sees a
        # healthy worker and "ok" keeps meaning "everything is fine".
        last_success: datetime | None = NOW - timedelta(seconds=10),
        worker_error: Exception | None = None,
    ) -> None:
        self.postgres, self.redis, self.revisions = postgres, redis, revisions
        self.rows = rows or []
        self.state = state or {"ingestion": [], "backfills": [], "tasks": []}
        self.redis_delay = redis_delay
        self.last_success, self.worker_error = last_success, worker_error

    async def ping_postgres(self) -> None:
        if self.postgres:
            raise self.postgres

    async def ping_redis(self) -> None:
        await asyncio.sleep(self.redis_delay)
        if self.redis:
            raise self.redis

    async def migration_revisions(self) -> tuple[str | None, str]:
        return self.revisions

    async def provider_rows(self) -> list[FeedRow]:
        if self.postgres:
            raise self.postgres
        return self.rows

    async def last_task_success(self, task_name: str) -> datetime | None:
        if self.worker_error:
            raise self.worker_error
        return self.last_success

    async def data_state(self) -> dict[str, list[dict[str, Any]]]:
        if self.postgres:
            raise self.postgres
        return self.state


def client_for(probes: HealthProbes) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_probes] = lambda: probes
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


# --- /health/ready -------------------------------------------------------------------------


def test_ready_ok() -> None:
    r = client_for(FakeProbes()).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["checks"]["migrations"] == {"status": "ok", "current": "0002", "head": "0002"}


def test_redis_down_is_degraded_200_not_500() -> None:
    r = client_for(FakeProbes(redis=ConnectionError("redis://:hunter2@redis:6379"))).get(
        "/health/ready"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == {"status": "down", "error": "ConnectionError"}
    assert body["checks"]["postgres"] == {"status": "ok"}
    assert "hunter2" not in r.text  # exception messages (DSNs) must not leak


def test_postgres_down_is_503_not_ready() -> None:
    r = client_for(FakeProbes(postgres=OSError("boom"))).get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"] == {"status": "down", "error": "OSError"}
    assert body["checks"]["migrations"]["status"] == "unknown"


def test_unmigrated_database_is_503_behind() -> None:
    r = client_for(FakeProbes(revisions=(None, "0002"))).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["migrations"] == {"status": "behind", "current": None, "head": "0002"}


def test_old_revision_is_503_behind() -> None:
    r = client_for(FakeProbes(revisions=("0001", "0002"))).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["migrations"]["status"] == "behind"


def test_hung_probe_times_out_as_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "CHECK_TIMEOUT_S", 0.05)
    r = client_for(FakeProbes(redis_delay=5)).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["redis"] == {"status": "down", "error": "TimeoutError"}


def test_unexpected_exception_type_is_still_not_a_500() -> None:
    r = client_for(FakeProbes(redis=ValueError("anything"))).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"


def test_live_is_unaffected_by_everything_down() -> None:
    probes = FakeProbes(postgres=OSError(), redis=ConnectionError())
    assert client_for(probes).get("/health/live").json() == {"status": "ok"}


def test_real_clients_pointed_at_nothing_report_down_not_500() -> None:
    """The real probe class against closed ports: the failure path of the actual clients."""
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.health_probes import SqlRedisProbes

    probes = SqlRedisProbes(
        create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/x", connect_args={"timeout": 1}),
        Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=1),
    )
    c = client_for(probes)
    ready = c.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["redis"]["status"] == "down"
    for path in ("/health/providers", "/health/data"):
        r = c.get(path)
        assert r.status_code == 503
        assert r.json()["status"] == "unavailable"


def test_unconfigured_probes_report_down() -> None:
    from app.services.health_probes import SqlRedisProbes

    r = client_for(SqlRedisProbes(None, None)).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["postgres"] == {"status": "down", "error": "RuntimeError"}


def test_migration_head_is_read_once_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """The directory walk happens in __init__ (once), never inside the async probe, where
    wait_for could not cancel it."""
    from alembic.script import ScriptDirectory

    from app.services import health_probes

    calls: list[int] = []
    real = ScriptDirectory.from_config

    def counting(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(ScriptDirectory, "from_config", counting)
    probes = health_probes.SqlRedisProbes(None, None)
    assert len(calls) == 1
    for _ in range(3):  # no engine: fails after the head check, which must not walk again
        with pytest.raises(RuntimeError):
            asyncio.run(probes.migration_revisions())
    assert len(calls) == 1


def test_unreadable_head_is_reported_by_the_check_not_raised_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_probes is lru_cached; an __init__ that raised would 500 every request."""
    from alembic.script import ScriptDirectory

    from app.services import health_probes

    def boom(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("alembic.ini")

    monkeypatch.setattr(ScriptDirectory, "from_config", boom)
    probes = health_probes.SqlRedisProbes(None, None)  # must not raise
    with pytest.raises(FileNotFoundError):
        asyncio.run(probes.migration_revisions())


# --- /health/providers: the three situations -------------------------------------------------


def row(**kw: Any) -> FeedRow:
    base: dict[str, Any] = dict(
        provider="p", feed="f", last_success_at=None, last_error_at=None, last_error=None
    )
    return FeedRow(**{**base, **kw})


HOUR = timedelta(hours=1)


def test_no_feeds_at_all() -> None:
    r = client_for(FakeProbes()).get("/health/providers")
    assert r.status_code == 200
    assert r.json() == {"status": "no_feeds", "feeds": []}


def test_never_succeeded_is_not_stale() -> None:
    out = svc.assemble_providers([row()], NOW, {("p", "f"): HOUR})
    assert out["status"] == "pending"
    feed = out["feeds"][0]
    assert feed["status"] == "never_succeeded"
    assert feed["stale"] is False
    assert feed["last_attempt_failed"] is False


def test_never_succeeded_but_erroring_is_visible_yet_still_pending() -> None:
    out = svc.assemble_providers(
        [row(last_error_at=NOW, last_error="HTTP 403")], NOW, {("p", "f"): HOUR}
    )
    assert out["status"] == "pending"
    assert out["feeds"][0]["last_attempt_failed"] is True
    assert out["feeds"][0]["last_error"] == "HTTP 403"


def test_succeeded_too_long_ago_is_stale() -> None:
    out = svc.assemble_providers([row(last_success_at=NOW - 2 * HOUR)], NOW, {("p", "f"): HOUR})
    assert out["status"] == "stale"
    assert out["feeds"][0]["status"] == "stale"
    assert out["feeds"][0]["stale"] is True


def test_fresh_feed() -> None:
    out = svc.assemble_providers([row(last_success_at=NOW - HOUR / 2)], NOW, {("p", "f"): HOUR})
    assert out["status"] == "ok"
    assert out["feeds"][0]["stale"] is False


def test_exactly_at_threshold_is_not_stale() -> None:
    out = svc.assemble_providers([row(last_success_at=NOW - HOUR)], NOW, {("p", "f"): HOUR})
    assert out["feeds"][0]["status"] == "fresh"


def test_no_threshold_is_unjudged_not_invented() -> None:
    out = svc.assemble_providers([row(last_success_at=NOW)], NOW, {})
    assert out["status"] == "unjudged"
    assert out["feeds"][0]["stale"] is None
    assert out["feeds"][0]["threshold_seconds"] is None


def test_stale_outranks_pending() -> None:
    rows = [row(feed="a"), row(feed="b", last_success_at=NOW - 3 * HOUR)]
    out = svc.assemble_providers(rows, NOW, {("p", "a"): HOUR, ("p", "b"): HOUR})
    assert out["status"] == "stale"


def test_error_after_success_inside_threshold_is_fresh_but_flagged() -> None:
    out = svc.assemble_providers(
        [row(last_success_at=NOW - HOUR / 2, last_error_at=NOW - HOUR / 4, last_error="x")],
        NOW,
        {("p", "f"): HOUR},
    )
    assert out["feeds"][0]["status"] == "fresh"
    assert out["feeds"][0]["last_attempt_failed"] is True


def test_long_last_error_is_truncated() -> None:
    out = svc.assemble_providers([row(last_error_at=NOW, last_error="x" * 5000)], NOW, {})
    assert len(out["feeds"][0]["last_error"]) == svc.LAST_ERROR_MAX_CHARS


def test_providers_survives_redis_down() -> None:
    c = client_for(FakeProbes(redis=ConnectionError(), rows=[row()]))
    assert c.get("/health/providers").status_code == 200


def test_providers_postgres_down_is_503_unavailable() -> None:
    r = client_for(FakeProbes(postgres=OSError())).get("/health/providers")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "error": "OSError"}


# --- /health/data ----------------------------------------------------------------------------


def test_data_empty_is_no_runs() -> None:
    r = client_for(FakeProbes()).get("/health/data")
    assert r.status_code == 200
    assert r.json() == {"status": "no_runs", "ingestion": [], "backfills": [], "tasks": []}


def test_data_reports_without_judging() -> None:
    state = {
        "ingestion": [],
        "backfills": [
            {
                "feed": "f",
                "status": "running",
                "total_units": 10,
                "completed_units": 3,
                "current_unit": "2020",
                "started_at": None,
                "updated_at": None,
            }
        ],
        "tasks": [],
    }
    body = client_for(FakeProbes(state=state)).get("/health/data").json()
    assert body["status"] == "reported"
    assert body["backfills"][0]["completed_units"] == 3


def test_data_postgres_down_is_503_unavailable() -> None:
    r = client_for(FakeProbes(postgres=OSError())).get("/health/data")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"

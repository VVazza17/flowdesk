"""M2 slice 2: backup states on /health/data (IDEA-029).

A stale or failing backup is stated here and never degrades /health/ready: it can wait until
morning, and anything that degrades ready pages (ADR-0008 section 4). No infrastructure: probes are
faked.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.deps import get_now, get_probes, get_started_at
from app.main import create_app
from app.services.health import FeedRow, classify_backup_task
from app.tasks.names import BACKUP, BACKUP_COPY

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
STALE = timedelta(hours=36)


def run(name: str, status: str, error_class: str | None = None) -> dict[str, Any]:
    return {
        "task_name": name,
        "status": status,
        "started_at": NOW.isoformat(),
        "heartbeat_at": None,
        "completed_at": NOW.isoformat(),
        "attempt": 1,
        "error_class": error_class,
    }


class FakeProbes:
    def __init__(
        self,
        tasks: list[dict[str, Any]] | None = None,
        successes: dict[str, datetime | None] | None = None,
    ) -> None:
        self.tasks = tasks or []
        self.successes = successes or {}

    async def ping_postgres(self) -> None:
        return None

    async def ping_redis(self) -> None:
        return None

    async def migration_revisions(self) -> tuple[str | None, str]:
        return ("0002", "0002")

    async def provider_rows(self) -> list[FeedRow]:
        return []

    async def last_task_success(self, task_name: str) -> datetime | None:
        # The canary is healthy so /health/ready can be asserted ok alongside a bad backup.
        if task_name == "ops.canary":
            return NOW - timedelta(seconds=10)
        return self.successes.get(task_name)

    async def data_state(self) -> dict[str, list[dict[str, Any]]]:
        return {"ingestion": [], "backfills": [], "tasks": self.tasks}


def client(probes: FakeProbes) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_probes] = lambda: probes
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_started_at] = lambda: NOW - timedelta(days=1)
    return TestClient(app)


# --- classify_backup_task: the pure rule ----------------------------------------------------------


def test_no_rows_at_all_is_no_runs() -> None:
    assert classify_backup_task(None, None, NOW, STALE)["state"] == "no_runs"


def test_a_recent_success_is_ok_and_reports_its_age() -> None:
    out = classify_backup_task(run(BACKUP, "succeeded"), NOW - timedelta(hours=5), NOW, STALE)
    assert out["state"] == "ok"
    assert out["age_seconds"] == 5 * 3600
    assert out["stale_after_seconds"] == 36 * 3600
    assert out["last_attempt_failed"] is False


def test_the_edge_of_the_window() -> None:
    assert classify_backup_task(None, NOW - STALE, NOW, STALE)["state"] == "ok"
    just_past = NOW - STALE - timedelta(seconds=1)
    assert classify_backup_task(None, just_past, NOW, STALE)["state"] == "stale"


def test_never_succeeded_after_failures() -> None:
    out = classify_backup_task(run(BACKUP, "failed", "NoRecipient"), None, NOW, STALE)
    assert out["state"] == "never_succeeded"
    assert out["last_attempt_failed"] is True
    assert out["last_error_class"] == "NoRecipient"


def test_a_failed_copy_is_still_ok_while_an_earlier_copy_is_recent_enough() -> None:
    # The point of separating the steps: last night's copy failed, the desktop was asleep, and the
    # picture is "ok, but the last attempt failed", not an alarm.
    out = classify_backup_task(
        run(BACKUP_COPY, "failed", "CopyFailed"), NOW - timedelta(hours=20), NOW, STALE
    )
    assert out["state"] == "ok"
    assert out["last_attempt_failed"] is True


def test_skipped_means_not_configured_not_failed_and_not_succeeded() -> None:
    out = classify_backup_task(run(BACKUP_COPY, "skipped", "NotConfigured"), None, NOW, STALE)
    assert out["state"] == "not_configured"
    assert out["last_attempt_failed"] is False
    # Even with an old success, the newest word is that the step is switched off.
    old = NOW - timedelta(days=30)
    skipped = run(BACKUP_COPY, "skipped", "NotConfigured")
    assert classify_backup_task(skipped, old, NOW, STALE)["state"] == "not_configured"


# --- /health/data and /health/ready ---------------------------------------------------------------


def test_data_shows_both_steps_side_by_side() -> None:
    probes = FakeProbes(
        tasks=[run(BACKUP, "succeeded"), run(BACKUP_COPY, "failed", "CopyFailed")],
        successes={BACKUP: NOW - timedelta(hours=3), BACKUP_COPY: NOW - timedelta(hours=27)},
    )
    body = client(probes).get("/health/data").json()
    assert body["backups"]["backup"]["state"] == "ok"
    assert body["backups"]["copy"]["state"] == "ok"
    assert body["backups"]["copy"]["last_attempt_failed"] is True
    assert body["backups"]["backup"]["last_attempt_failed"] is False


def test_a_stale_backup_is_stated_on_data_and_does_not_touch_ready() -> None:
    probes = FakeProbes(
        tasks=[run(BACKUP, "succeeded"), run(BACKUP_COPY, "succeeded")],
        successes={BACKUP: NOW - timedelta(days=5), BACKUP_COPY: NOW - timedelta(days=5)},
    )
    c = client(probes)
    data = c.get("/health/data")
    assert data.status_code == 200
    assert data.json()["backups"]["backup"]["state"] == "stale"
    assert data.json()["backups"]["copy"]["state"] == "stale"
    ready = c.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ok"  # would page if this said degraded


def test_a_never_succeeding_backup_still_leaves_ready_ok() -> None:
    probes = FakeProbes(tasks=[run(BACKUP, "failed", "NoRecipient")])
    c = client(probes)
    assert c.get("/health/data").json()["backups"]["backup"]["state"] == "never_succeeded"
    assert c.get("/health/ready").json()["status"] == "ok"


def test_the_public_view_keeps_states_and_error_classes_only() -> None:
    probes = FakeProbes(tasks=[run(BACKUP_COPY, "failed", "CopyFailed")])
    app = create_app("public")
    app.dependency_overrides[get_probes] = lambda: probes
    app.dependency_overrides[get_now] = lambda: NOW
    body = TestClient(app).get("/health/data").json()
    assert body["backups"]["copy"]["last_error_class"] == "CopyFailed"
    assert "error_message" not in str(body)


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_a_non_positive_stale_window_is_rejected(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    from app import settings

    monkeypatch.setenv("BACKUP_STALE_AFTER_HOURS", bad)
    with pytest.raises(ValueError, match="BACKUP_STALE_AFTER_HOURS"):
        settings.backup_stale_after()


def test_the_default_stale_window_is_36_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import settings

    monkeypatch.delenv("BACKUP_STALE_AFTER_HOURS", raising=False)
    assert settings.backup_stale_after() == timedelta(hours=36)

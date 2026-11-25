"""M2 slice 2: backup states on /health/data (IDEA-029).

A stale or failing backup is stated here and never degrades /health/ready: it can wait until
morning, and anything that degrades ready pages (ADR-0008 section 4). No infrastructure: probes are
faked.
"""

from collections.abc import Sequence
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

    async def running_task_fresh(self, task_names: Sequence[str], now: datetime) -> str | None:
        return getattr(self, "busy_task", None)

    async def macro_age(self) -> list[dict[str, Any]]:
        return []

    async def provider_rows(self) -> list[FeedRow]:
        return []

    async def last_task_success(self, task_name: str) -> datetime | None:
        # The canary is healthy so /health/ready can be asserted ok alongside a bad backup.
        if task_name in ("ops.canary", "ops.canary_ingest"):
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


# --- the B2 copy's fields (2026-11-25) ------------------------------------------------------------


def with_bytes(
    row: dict[str, Any], output_bytes: int | None, watermark: str | None = None
) -> dict[str, Any]:
    return {**row, "output_bytes": output_bytes, "output_watermark": watermark}


def test_budget_states_follow_the_free_tier_line() -> None:
    from app.services.health import budget_state

    assert budget_state(None) is None
    assert budget_state(0) == "ok"
    assert budget_state(7_999_999_999) == "ok"
    assert budget_state(8_000_000_000) == "warn"
    assert budget_state(9_499_999_999) == "warn"
    assert budget_state(9_500_000_000) == "over"


def test_the_copy_reports_its_own_measurement_not_the_backups() -> None:
    """Rule 9: the backup row carries a LARGER output_bytes (and a later watermark) than the copy
    row.
    A lookup that lost its task name would report the backup's numbers as the bucket's."""
    probes = FakeProbes(
        tasks=[
            with_bytes(run(BACKUP, "succeeded"), 9_900_000_000, "2026-11-09T03:00:00+00:00"),
            with_bytes(run(BACKUP_COPY, "succeeded"), 4_000_000, "2026-11-08T03:00:00+00:00"),
        ],
        successes={BACKUP: NOW, BACKUP_COPY: NOW},
    )
    copy = client(probes).get("/health/data").json()["backups"]["copy"]
    assert copy["destination"] == "b2"
    assert copy["bucket_bytes"] == 4_000_000
    assert copy["budget_bytes"] == 10_000_000_000
    assert copy["budget_state"] == "ok"
    assert copy["copied_through"] == "2026-11-08T03:00:00+00:00"
    assert copy["tampered"] is False


def test_an_over_budget_failure_still_reports_what_it_measured() -> None:
    probes = FakeProbes(
        tasks=[with_bytes(run(BACKUP_COPY, "failed", "OverBudget"), 9_600_000_000)],
        successes={BACKUP_COPY: NOW - timedelta(hours=30)},
    )
    copy = client(probes).get("/health/data").json()["backups"]["copy"]
    assert copy["budget_state"] == "over"
    assert copy["last_error_class"] == "OverBudget"
    assert (
        copy["state"] == "ok"
    )  # an earlier copy is recent enough; the page comes from the monitor's /fail


def test_tampered_is_the_copys_own_verdict() -> None:
    """Rule 9: the BACKUP row is the one that says Tampered here; only the copy's row may set
    the flag."""
    probes = FakeProbes(
        tasks=[run(BACKUP, "failed", "Tampered"), run(BACKUP_COPY, "succeeded")],
        successes={BACKUP: NOW, BACKUP_COPY: NOW},
    )
    assert client(probes).get("/health/data").json()["backups"]["copy"]["tampered"] is False
    probes = FakeProbes(
        tasks=[run(BACKUP, "succeeded"), run(BACKUP_COPY, "failed", "Tampered")],
        successes={BACKUP: NOW, BACKUP_COPY: NOW},
    )
    assert client(probes).get("/health/data").json()["backups"]["copy"]["tampered"] is True


def test_weekly_sizes_are_shown_newest_first() -> None:
    class WithSizes(FakeProbes):
        async def data_state(self) -> dict[str, list[dict[str, Any]]]:
            return {
                "ingestion": [],
                "backfills": [],
                "tasks": self.tasks,
                "backup_sizes": [
                    {"week_start": "2026-11-09T00:00:00+00:00", "bytes": 1_800_000, "at": "x"},
                    {"week_start": "2026-11-02T00:00:00+00:00", "bytes": 1_720_000, "at": "y"},
                ],
            }

    backup = client(WithSizes()).get("/health/data").json()["backups"]["backup"]
    assert backup["sizes_weekly"] == [
        {"week_start": "2026-11-09T00:00:00+00:00", "bytes": 1_800_000},
        {"week_start": "2026-11-02T00:00:00+00:00", "bytes": 1_720_000},
    ]


def test_no_measurement_yet() -> None:
    probes = FakeProbes(tasks=[run(BACKUP_COPY, "skipped", "NotConfigured")])
    copy = client(probes).get("/health/data").json()["backups"]["copy"]
    assert (copy["bucket_bytes"], copy["budget_state"], copy["copied_through"]) == (
        None,
        None,
        None,
    )

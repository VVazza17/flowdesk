"""The daily FRED poll's schedule, pinned without waiting for 08:00.

Beat's first real firing was only ever seen late (after a laptop slept 18 h). This pins what can be
pinned deterministically: the entry exists, it is 08:00 America/New_York across the DST change, it
lands on the ingest queue (a wrong queue would silently starve the canary), and it expires.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from celery import Celery
from celery.schedules import crontab

from app.celery_app import create_celery
from app.tasks.names import FRED_POLL, INGEST_QUEUE

NY = ZoneInfo("America/New_York")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Celery:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    return create_celery()


def due_at(app: Celery, local: datetime) -> bool:
    """Would Beat's own crontab fire at `local`? Beat asks `is_due(last_run_at)` once a tick; the
    clock is pinned to `local` and the last run was 30 s earlier."""
    schedule = app.conf.beat_schedule["fred_poll"]["schedule"]
    assert isinstance(schedule, crontab)
    setattr(schedule, "nowfun", lambda: local.astimezone(UTC))  # noqa: B010
    return bool(schedule.is_due(local - timedelta(seconds=30)).is_due)


def test_the_poll_is_scheduled_for_fred_poll_on_the_ingest_queue(app: Celery) -> None:
    entry: dict[str, Any] = app.conf.beat_schedule["fred_poll"]
    assert entry["task"] == FRED_POLL
    # No per-entry queue, so the task goes where the routes and the default say: ingest.
    assert "queue" not in entry["options"]
    assert app.conf.task_routes.get(FRED_POLL, {}).get("queue", INGEST_QUEUE) == INGEST_QUEUE
    assert app.conf.task_default_queue == INGEST_QUEUE
    assert entry["options"]["expires"] == 6 * 3600


def test_it_runs_at_0800_new_york_in_winter_and_in_summer(app: Celery) -> None:
    assert str(app.conf.timezone) == "America/New_York"
    # 08:00 local is 13:00Z in winter and 12:00Z in summer: the UTC hour moves with DST, the local
    # hour does not, and Beat evaluates the crontab in its own timezone.
    winter = datetime(2026, 1, 15, 8, 0, 10, tzinfo=NY)
    summer = datetime(2026, 7, 15, 8, 0, 10, tzinfo=NY)
    assert (winter.astimezone(UTC).hour, summer.astimezone(UTC).hour) == (13, 12)
    assert due_at(app, winter)
    assert due_at(app, summer)
    assert not due_at(app, datetime(2026, 1, 15, 13, 0, 10, tzinfo=NY))  # 13:00 local: no
    assert not due_at(app, datetime(2026, 7, 15, 7, 59, 40, tzinfo=NY))  # not yet

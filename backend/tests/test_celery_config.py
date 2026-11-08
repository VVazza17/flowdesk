"""M2 slice 1: Celery is configured the way the health check assumes. No broker or database."""

import pytest

from app import settings
from app.celery_app import create_celery
from app.tasks import canary as canary_module
from app.tasks.names import CANARY


def test_beat_schedules_the_canary_at_the_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setenv("CANARY_INTERVAL_SECONDS", "7")
    app = create_celery()
    entry = app.conf.beat_schedule["canary"]
    assert entry["task"] == CANARY
    assert entry["schedule"] == 7.0
    # Stale canaries expire instead of replaying after a stall.
    assert entry["options"] == {"expires": 14.0}
    assert str(app.timezone) == "America/New_York"
    assert app.conf.task_ignore_result is True


def test_the_task_registered_under_the_name_the_health_check_reads() -> None:
    assert canary_module.canary.name == CANARY == "ops.canary"


def test_no_redis_url_refuses_to_start_instead_of_falling_back_to_amqp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        create_celery()


def test_the_staleness_threshold_follows_the_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANARY_INTERVAL_SECONDS", raising=False)
    assert settings.canary_interval_seconds() == 60
    assert settings.worker_stale_after().total_seconds() == 180
    monkeypatch.setenv("CANARY_INTERVAL_SECONDS", "5")
    assert settings.worker_stale_after().total_seconds() == 15


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_a_non_positive_interval_is_rejected(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("CANARY_INTERVAL_SECONDS", bad)
    with pytest.raises(ValueError, match="CANARY_INTERVAL_SECONDS"):
        settings.canary_interval_seconds()

"""M2 slice 1: Celery is configured the way the health check assumes. No broker or database."""

import pytest
from celery import Celery

from app import settings
from app.celery_app import create_celery
from app.tasks import canary as canary_module
from app.tasks.names import CANARY, FAKE_INGEST, INGEST_CANARY, INGEST_QUEUE, OPS_QUEUE


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


# --- M2 slice 5, step 0: the canary must not be able to queue behind ingest work -----------------
# One worker on one queue meant a long backfill could hold every slot and the canary would wait,
# expire, and read as a dead worker (docs/HEALTH.md). The ops canary has its own queue and process;
# everything else defaults to the ingest queue, where starvation is survivable.


def _app(monkeypatch: pytest.MonkeyPatch) -> Celery:
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.delenv("CANARY_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("INGEST_TASK_MAX_SECONDS", raising=False)
    return create_celery()


def test_the_ops_canary_is_routed_to_the_ops_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch)
    queue = app.amqp.router.route({}, CANARY)["queue"]
    assert queue.name == OPS_QUEUE == "ops"


def test_anything_unrouted_defaults_to_the_ingest_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default is where a long task can safely land. A new task nobody routed must never end up
    # on the queue that has to stay free.
    app = _app(monkeypatch)
    assert app.conf.task_default_queue == INGEST_QUEUE == "ingest"
    assert app.amqp.router.route({}, "ingest.some_future_task")["queue"].name == "ingest"
    assert app.amqp.router.route({}, FAKE_INGEST)["queue"].name == "ingest"


def test_the_ingest_canary_is_scheduled_on_the_ingest_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CANARY_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    app = create_celery()
    entry = app.conf.beat_schedule["canary_ingest"]
    assert entry["task"] == INGEST_CANARY == "ops.canary_ingest"
    assert entry["schedule"] == 7.0
    # A canary that waited out a full set of slots is no evidence of anything now: it expires.
    assert entry["options"] == {"expires": 14.0, "queue": "ingest"}
    assert app.amqp.router.route({}, INGEST_CANARY)["queue"].name == "ingest"


def test_the_two_canaries_are_two_tasks_under_two_names() -> None:
    # task_runs is keyed by task name, so the two health checks read two histories.
    assert canary_module.canary.name == CANARY
    assert canary_module.canary_ingest.name == INGEST_CANARY
    assert CANARY != INGEST_CANARY


def test_ingest_worker_takes_one_task_at_a_time_and_has_a_hard_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch)
    # Prefetch 1: a worker must not reserve tasks it cannot start behind a long one.
    assert app.conf.worker_prefetch_multiplier == 1
    # The ingest canary's staleness threshold is sized to this limit, so it has to be real.
    assert app.conf.task_time_limit == 1800


def test_ingest_task_max_is_overridable_and_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INGEST_TASK_MAX_SECONDS", raising=False)
    assert settings.ingest_task_max_seconds() == 1800
    monkeypatch.setenv("INGEST_TASK_MAX_SECONDS", "30")
    assert settings.ingest_task_max_seconds() == 30
    monkeypatch.setenv("INGEST_TASK_MAX_SECONDS", "0")
    with pytest.raises(ValueError, match="INGEST_TASK_MAX_SECONDS"):
        settings.ingest_task_max_seconds()


def test_ingest_worker_threshold_is_three_canary_intervals_not_the_task_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # It used to be INGEST_TASK_MAX_SECONDS + 3 intervals, because a long task could starve the
    # canary for that long. Part C (a heartbeat thread, and `busy` while a task of the ingest worker
    # beats) removed the reason, so a wedged ingest worker shows in minutes, not half an hour.
    monkeypatch.delenv("CANARY_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("INGEST_TASK_MAX_SECONDS", raising=False)
    assert settings.ingest_worker_stale_after().total_seconds() == 180
    monkeypatch.setenv("CANARY_INTERVAL_SECONDS", "3")
    monkeypatch.setenv("INGEST_TASK_MAX_SECONDS", "30")
    assert settings.ingest_worker_stale_after().total_seconds() == 9
    # It does not move with the hard time limit any more.
    monkeypatch.setenv("INGEST_TASK_MAX_SECONDS", "36000")
    assert settings.ingest_worker_stale_after() == settings.worker_stale_after()

"""M2 slice 1: the worker check on /health/ready (IDEA-032: killing the worker).

Keyed on recent canary completions, not on a process being alive, so a wedged worker degrades too.
No infrastructure: probes are faked, the clock is passed in.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.deps import get_now, get_probes, get_started_at
from app.main import create_app
from app.services.health import FeedRow, classify_worker
from app.tasks.names import CANARY, INGEST_CANARY

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


class FakeProbes:
    """Just enough of HealthProbes for /health/ready. Defaults: everything up, canary 10 s ago."""

    def __init__(
        self,
        *,
        postgres: Exception | None = None,
        redis: Exception | None = None,
        last_success: datetime | None = NOW - timedelta(seconds=10),
        ingest_last_success: datetime | None = NOW - timedelta(seconds=10),
        worker_error: Exception | None = None,
        ingest_error: Exception | None = None,
        busy_task: str | None = None,
    ) -> None:
        self.postgres, self.redis = postgres, redis
        self.last_success, self.worker_error = last_success, worker_error
        self.ingest_last_success, self.ingest_error = ingest_last_success, ingest_error
        self.busy_task = busy_task

    async def ping_postgres(self) -> None:
        if self.postgres:
            raise self.postgres

    async def ping_redis(self) -> None:
        if self.redis:
            raise self.redis

    async def migration_revisions(self) -> tuple[str | None, str]:
        return ("0002", "0002")

    async def running_task_fresh(self, task_names: Sequence[str], now: datetime) -> str | None:
        return self.busy_task

    async def provider_rows(self) -> list[FeedRow]:
        return []

    async def last_task_success(self, task_name: str) -> datetime | None:
        # Two canaries, two histories: ops.canary (the ops worker) and ops.canary_ingest.
        if task_name == INGEST_CANARY:
            if self.ingest_error:
                raise self.ingest_error
            return self.ingest_last_success
        assert task_name == CANARY, task_name
        if self.worker_error:
            raise self.worker_error
        return self.last_success

    async def data_state(self) -> dict[str, list[dict[str, Any]]]:
        return {"ingestion": [], "backfills": [], "tasks": []}


STALE_AFTER = timedelta(seconds=180)  # 3 x the default 60 s canary
LONG_AGO = NOW - timedelta(days=1)  # the API "has been up" for a day unless a test says otherwise


def client(probes: FakeProbes, *, started_at: datetime = LONG_AGO) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_probes] = lambda: probes
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_started_at] = lambda: started_at
    return TestClient(app)


# --- classify_worker: the pure rule ---------------------------------------------------------------


def test_recent_success_is_ok() -> None:
    out = classify_worker(NOW - timedelta(seconds=60), NOW, LONG_AGO, STALE_AFTER)
    assert out["status"] == "ok"
    assert out["age_seconds"] == 60
    assert out["stale_after_seconds"] == 180
    assert out["task"] == "ops.canary"


def test_success_exactly_at_the_threshold_is_still_ok_one_second_past_is_stale() -> None:
    assert classify_worker(NOW - STALE_AFTER, NOW, LONG_AGO, STALE_AFTER)["status"] == "ok"
    just_past = NOW - STALE_AFTER - timedelta(seconds=1)
    assert classify_worker(just_past, NOW, LONG_AGO, STALE_AFTER)["status"] == "stale"


def test_never_completed_is_in_grace_while_the_api_is_young() -> None:
    young = classify_worker(None, NOW, NOW - timedelta(seconds=30), STALE_AFTER)
    assert young == {
        "task": "ops.canary",
        "stale_after_seconds": 180,
        "status": "never_completed",
        "in_grace": True,
    }
    # The edge: up for exactly the stale window is still grace, one second more is not.
    assert classify_worker(None, NOW, NOW - STALE_AFTER, STALE_AFTER)["in_grace"] is True
    old = NOW - STALE_AFTER - timedelta(seconds=1)
    assert classify_worker(None, NOW, old, STALE_AFTER)["in_grace"] is False


# --- /health/ready: what each state does to the verdict -------------------------------------------


def test_healthy_worker_keeps_ready_ok() -> None:
    r = client(FakeProbes()).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["checks"]["worker"]["status"] == "ok"


def test_stale_worker_degrades_ready_with_a_200() -> None:
    r = client(FakeProbes(last_success=NOW - timedelta(minutes=10))).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["worker"]["status"] == "stale"
    # Everything else is fine: the degradation is the worker's alone.
    assert r.json()["checks"]["redis"] == {"status": "ok"}


def test_fresh_stack_with_no_completion_yet_is_ok_inside_the_grace() -> None:
    r = client(FakeProbes(last_success=None), started_at=NOW - timedelta(seconds=30)).get(
        "/health/ready"
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["checks"]["worker"]["status"] == "never_completed"
    assert r.json()["checks"]["worker"]["in_grace"] is True


def test_never_completed_past_the_grace_degrades() -> None:
    r = client(FakeProbes(last_success=None)).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["worker"]["in_grace"] is False


def test_postgres_down_is_not_ready_and_the_worker_is_unknown_not_dead() -> None:
    r = client(FakeProbes(postgres=OSError())).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["worker"] == {"status": "unknown", "reason": "postgres_down"}


def test_a_failing_worker_probe_is_unknown_and_does_not_degrade() -> None:
    # e.g. task_runs missing. Not being able to ask is not evidence; that failure shows elsewhere.
    r = client(FakeProbes(worker_error=RuntimeError("boom"))).get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["checks"]["worker"] == {"status": "unknown", "error": "RuntimeError"}


def test_redis_down_and_a_stale_worker_both_show_and_the_verdict_is_degraded() -> None:
    probes = FakeProbes(redis=ConnectionError(), last_success=NOW - timedelta(minutes=10))
    body = client(probes).get("/health/ready").json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "down"
    assert body["checks"]["worker"]["status"] == "stale"


def test_live_never_looks_at_the_worker() -> None:
    probes = FakeProbes(last_success=None, worker_error=RuntimeError("boom"))
    assert client(probes).get("/health/live").json() == {"status": "ok"}


def test_started_at_is_set_by_the_app_factory() -> None:
    assert create_app().state.started_at.tzinfo is UTC


# --- The ingest worker's canary (M2 slice 5, step 0, option b) ------------------------------
# Without it a wedged ingest worker would show only as stale feeds, and stale feeds never page: a
# machinery failure wearing a source failure's costume, breaking the readiness rule in the
# one case it was written for. Since part C its threshold is the same three intervals as the ops
# worker's, and a canary stuck behind a long task is covered by `busy` (a running task of the ingest
# worker with a fresh heartbeat), not by a threshold sized to the task limit.

INGEST_STALE_AFTER = timedelta(seconds=180)  # 3 x the 60 s canary


def test_ingest_canary_classification_names_its_own_task_and_threshold() -> None:
    out = classify_worker(
        NOW - timedelta(seconds=60), NOW, LONG_AGO, INGEST_STALE_AFTER, task=INGEST_CANARY
    )
    assert out["status"] == "ok"
    assert out["task"] == "ops.canary_ingest"
    assert out["stale_after_seconds"] == 180


def test_ready_reports_both_workers_and_is_ok_when_both_are_healthy() -> None:
    body = client(FakeProbes()).get("/health/ready").json()
    assert body["status"] == "ok"
    assert body["checks"]["worker"]["task"] == "ops.canary"
    assert body["checks"]["ingest_worker"]["task"] == "ops.canary_ingest"
    assert body["checks"]["ingest_worker"]["status"] == "ok"


def test_a_wedged_ingest_worker_degrades_ready_even_though_the_ops_worker_is_fine() -> None:
    probes = FakeProbes(ingest_last_success=NOW - timedelta(hours=2))
    r = client(probes).get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["worker"]["status"] == "ok"
    assert body["checks"]["ingest_worker"]["status"] == "stale"


def test_a_stuck_canary_with_no_live_task_is_stale_at_three_intervals() -> None:
    # Ten minutes without an ingest canary and nothing running: wedged, and no longer excused by a
    # half-hour allowance.
    probes = FakeProbes(ingest_last_success=NOW - timedelta(minutes=10))
    body = client(probes).get("/health/ready").json()
    assert body["status"] == "degraded"
    assert body["checks"]["ingest_worker"]["status"] == "stale"


def test_a_stale_canary_behind_a_running_task_with_a_fresh_heartbeat_is_busy_not_degraded() -> None:
    # Twenty minutes without an ingest canary, but a task of the ingest worker is running and
    # beating: the worker is alive, doing its job. Alive is not degraded.
    probes = FakeProbes(ingest_last_success=NOW - timedelta(minutes=20), busy_task="ingest.fake")
    body = client(probes).get("/health/ready").json()
    assert body["status"] == "ok"
    assert body["checks"]["ingest_worker"]["status"] == "busy"
    assert body["checks"]["ingest_worker"]["running_task"] == "ingest.fake"
    assert body["checks"]["ingest_worker"]["stale_after_seconds"] == 180


def test_busy_also_covers_a_worker_whose_canary_has_never_completed() -> None:
    probes = FakeProbes(ingest_last_success=None, busy_task="ingest.fake")
    body = client(probes).get("/health/ready").json()
    assert body["status"] == "ok"
    assert body["checks"]["ingest_worker"]["status"] == "busy"


def test_a_healthy_canary_is_ok_and_the_busy_probe_is_not_even_asked() -> None:
    class Spy(FakeProbes):
        asked = False

        async def running_task_fresh(self, task_names: Sequence[str], now: datetime) -> str | None:
            Spy.asked = True
            return "ingest.fake"

    body = client(Spy()).get("/health/ready").json()
    assert body["checks"]["ingest_worker"]["status"] == "ok"
    assert Spy.asked is False


def test_the_ops_worker_is_never_busy_a_stuck_ops_canary_always_degrades() -> None:
    # `busy` is for the ingest worker only: the ops worker runs the canary and nothing else, so
    # there is no long task that could excuse it.
    probes = FakeProbes(last_success=NOW - timedelta(minutes=10), busy_task="ingest.fake")
    body = client(probes).get("/health/ready").json()
    assert body["checks"]["worker"]["status"] == "stale"
    assert body["status"] == "degraded"


def test_failing_to_ask_whether_a_task_is_running_leaves_the_canarys_verdict() -> None:
    class Broken(FakeProbes):
        async def running_task_fresh(self, task_names: Sequence[str], now: datetime) -> str | None:
            raise RuntimeError("boom")

    body = (
        client(Broken(ingest_last_success=NOW - timedelta(minutes=10))).get("/health/ready").json()
    )
    assert body["checks"]["ingest_worker"]["status"] == "stale"
    assert body["status"] == "degraded"


def test_ingest_canary_one_second_past_three_intervals_is_stale() -> None:
    edge = classify_worker(
        NOW - INGEST_STALE_AFTER, NOW, LONG_AGO, INGEST_STALE_AFTER, task=INGEST_CANARY
    )
    past = classify_worker(
        NOW - INGEST_STALE_AFTER - timedelta(seconds=1),
        NOW,
        LONG_AGO,
        INGEST_STALE_AFTER,
        task=INGEST_CANARY,
    )
    assert edge["status"] == "ok"
    assert past["status"] == "stale"


def test_a_stuck_ops_worker_degrades_even_while_the_ingest_worker_is_fine() -> None:
    # The mirror case: the ops canary has its own process, so a healthy ingest worker cannot mask a
    # dead ops worker either.
    probes = FakeProbes(last_success=NOW - timedelta(minutes=10))
    body = client(probes).get("/health/ready").json()
    assert body["status"] == "degraded"
    assert body["checks"]["worker"]["status"] == "stale"
    assert body["checks"]["ingest_worker"]["status"] == "ok"


def test_ingest_worker_never_completed_is_ok_in_grace_and_degrades_after_it() -> None:
    young = client(
        FakeProbes(ingest_last_success=None), started_at=NOW - timedelta(seconds=30)
    ).get("/health/ready")
    assert young.json()["status"] == "ok"
    assert young.json()["checks"]["ingest_worker"]["in_grace"] is True
    # The grace is this check's own threshold (three intervals): two minutes up is inside it.
    still_young = client(
        FakeProbes(ingest_last_success=None), started_at=NOW - timedelta(minutes=2)
    ).get("/health/ready")
    assert still_young.json()["checks"]["ingest_worker"]["in_grace"] is True
    old = client(FakeProbes(ingest_last_success=None)).get("/health/ready")
    assert old.json()["status"] == "degraded"
    assert old.json()["checks"]["ingest_worker"]["in_grace"] is False


def test_ingest_worker_is_unknown_not_dead_when_postgres_is_down_or_the_read_fails() -> None:
    down = client(FakeProbes(postgres=OSError())).get("/health/ready")
    assert down.json()["checks"]["ingest_worker"] == {
        "status": "unknown",
        "reason": "postgres_down",
    }
    failed = client(FakeProbes(ingest_error=RuntimeError("boom"))).get("/health/ready")
    assert failed.status_code == 200
    assert failed.json()["status"] == "ok"
    assert failed.json()["checks"]["ingest_worker"] == {
        "status": "unknown",
        "error": "RuntimeError",
    }

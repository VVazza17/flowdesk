"""Celery application factory (M2 slice 1).

Importing this module has no side effects; the worker and Beat get their app from app.worker, which
calls create_celery(). Timezone America/New_York is CLAUDE.md's choice for Beat (§11). Celery stores
times in UTC internally (enable_utc), so the zone only decides what a crontab schedule means.
"""

from celery import Celery
from celery.schedules import crontab

from app import settings
from app.logging import configure_logging
from app.tasks.liveness import SWEEP_INTERVAL_SECONDS
from app.tasks.names import (
    CANARY,
    FAKE_INGEST,
    FRED_POLL,
    INGEST_CANARY,
    INGEST_QUEUE,
    OPS_QUEUE,
    SWEEP,
)


def create_celery() -> Celery:
    broker = settings.redis_url()
    if broker is None:
        # Celery would otherwise fall back to a local AMQP broker and fail confusingly.
        raise RuntimeError("REDIS_URL must be set to start the worker or Beat")
    interval = float(settings.canary_interval_seconds())

    configure_logging()
    app = Celery(
        "flowdesk",
        broker=broker,
        include=[
            "app.tasks.canary",
            "app.tasks.fake_ingest",
            "app.tasks.fred_ingest",
            "app.tasks.sweep",
        ],
    )
    app.conf.update(
        timezone="America/New_York",
        enable_utc=True,
        # DECISION (M2 slice 5, step 0): the canary cannot queue behind ingest work. It is routed to
        # its own queue, which only its own worker process consumes (docker-compose.yml,
        # worker-ops).
        # Everything unrouted defaults to `ingest`: a new task nobody thought about lands where
        # starvation is survivable, not on the queue that has to stay free.
        task_default_queue=INGEST_QUEUE,
        task_routes={
            CANARY: {"queue": OPS_QUEUE},
            INGEST_CANARY: {"queue": INGEST_QUEUE},
            # The sweep fails the rows of dead tasks, so it must not wait behind ingest work.
            SWEEP: {"queue": OPS_QUEUE},
        },
        # Prefetch 1: a worker reserves only what it can start. The default (4 per process) lets a
        # worker hold tasks in its own memory behind a long one, where no other worker can
        # take them.
        worker_prefetch_multiplier=1,
        # The longest any task may run. Not a tuning knob: the ingest canary's staleness
        # threshold is built from it (settings.ingest_worker_stale_after), so the limit has
        # to be enforced.
        task_time_limit=settings.ingest_task_max_seconds(),
        # Postgres owns durable state (task_runs); Redis is only the bus. No result backend.
        task_ignore_result=True,
        # Redis may not be up yet when the stack starts; keep retrying instead of exiting.
        broker_connection_retry_on_startup=True,
        # Leave the root logger alone, so Celery's own lines come out as the same JSON as the API's.
        worker_hijack_root_logger=False,
        beat_schedule={
            "canary": {
                "task": CANARY,
                "schedule": interval,
                # A canary that sat in the queue through a stall is no evidence of anything now.
                # Without this, unpausing a wedged worker replays every missed run at once.
                "options": {"expires": interval * 2},
            },
            # The ingest worker's canary: same body, the ingest queue. When every slot is busy it
            # waits, expires, and the next one tries; it is judged against the longest allowed task.
            "canary_ingest": {
                "task": INGEST_CANARY,
                "schedule": interval,
                "options": {"expires": interval * 2, "queue": INGEST_QUEUE},
            },
            # The daily FRED poll, 08:00 America/New_York (Beat's timezone, above), on the default
            # `ingest` queue. It expires after six hours: a poll that sat in the queue through an
            # outage is not worth running at 3 pm, and the next morning's poll catches up anyway
            # (it asks for everything since the last captured vintage).
            "fred_poll": {
                "task": FRED_POLL,
                "schedule": crontab(hour=settings.FRED_POLL_HOUR, minute=settings.FRED_POLL_MINUTE),
                "options": {"expires": 6 * 3600},
            },
            # Fails `running` rows nothing is alive behind (docs/HEALTH.md, "The sweep").
            "sweep": {
                "task": SWEEP,
                "schedule": float(SWEEP_INTERVAL_SECONDS),
                "options": {"expires": SWEEP_INTERVAL_SECONDS * 2, "queue": OPS_QUEUE},
            },
        },
    )
    if settings.fake_provider_enabled():
        fake_interval = float(settings.fake_provider_interval_seconds())
        app.conf.beat_schedule["fake_ingest"] = {
            "task": FAKE_INGEST,
            "schedule": fake_interval,
            "options": {"expires": fake_interval * 2},
        }
    return app

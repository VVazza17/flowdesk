"""Celery application factory (M2 slice 1).

Importing this module has no side effects; the worker and Beat get their app from app.worker, which
calls create_celery(). Timezone America/New_York is CLAUDE.md's choice for Beat (§11). Celery stores
times in UTC internally (enable_utc), so the zone only decides what a crontab schedule means.
"""

from celery import Celery

from app import settings
from app.logging import configure_logging
from app.tasks.names import CANARY


def create_celery() -> Celery:
    broker = settings.redis_url()
    if broker is None:
        # Celery would otherwise fall back to a local AMQP broker and fail confusingly.
        raise RuntimeError("REDIS_URL must be set to start the worker or Beat")
    interval = float(settings.canary_interval_seconds())

    configure_logging()
    app = Celery("flowdesk", broker=broker, include=["app.tasks.canary"])
    app.conf.update(
        timezone="America/New_York",
        enable_utc=True,
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
            }
        },
    )
    return app

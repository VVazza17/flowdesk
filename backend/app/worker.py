"""Entry point for the worker and Beat:

    celery -A app.worker worker      celery -A app.worker beat

The variable must be called `celery` or `app` for Celery's -A lookup to find it.
"""

from app.celery_app import create_celery

celery = create_celery()

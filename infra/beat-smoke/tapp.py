from celery import Celery
from celery.schedules import crontab

app = Celery("tapp", broker="redis://tredis:6379/0")
app.conf.timezone = "America/New_York"
app.conf.beat_schedule = {"ping": {"task": "tapp.ping", "schedule": crontab(minute="*/2")}}

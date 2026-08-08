"""Celery application configuration with beat schedule."""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "kidschores",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.generate_instances",
        "app.workers.notify_digest",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Periodic task schedule
celery_app.conf.beat_schedule = {
    # Nightly at 00:05 UTC — per-household timezone logic is inside the task
    "nightly-instance-generation": {
        "task": "workers.generate_instances",
        "schedule": crontab(hour=0, minute=5),
    },
    # Daily parent nudge digest
    "daily-parent-digest": {
        "task": "workers.notify_digest",
        "schedule": crontab(hour=9, minute=0),  # 9am UTC; households handle local time
    },
}

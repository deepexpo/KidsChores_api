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
        "app.workers.reminders",
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
    # Task due-soon reminder (PRD §6.7 P0) — every 15 min so a configurable
    # lead time (default 60 min) doesn't drift far from when it should fire.
    "task-due-soon-reminders": {
        "task": "workers.send_task_reminders",
        "schedule": crontab(minute="*/15"),
    },
    # Series-expiring-with-tasks-outstanding reminder (PRD §6.7 P1) — coarser
    # cadence is fine, this is a "heads up" not a time-sensitive alert.
    "series-expiring-reminders": {
        "task": "workers.send_series_expiring_reminders",
        "schedule": crontab(hour="*/6", minute=30),
    },
}

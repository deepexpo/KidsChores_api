"""
Scheduled push notifications — PRD §6.7.

- send_task_reminders_task: teen reminder before a task's due time (P0). Runs
  on a short interval (celery_app.py beat schedule); each instance fires at
  most once, guarded by reminder_sent_at, once due_at falls within the
  household's configured lead time (task_reminder_minutes_before_due).
- send_series_expiring_task: teen notified a series window is ending with
  tasks still outstanding (P1). Runs less often; guarded the same way
  (expiring_reminder_sent_at) per series_instance, and skips windows that are
  already fully resolved (nothing left to nag about).
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import AsyncSessionLocal, run_and_dispose
from app.db.models import (
    Series,
    SeriesInstance,
    SeriesStatus,
    TaskDefinition,
    TaskInstance,
    TaskStatus,
)
from app.services.notifications import notify_series_expiring, notify_task_reminder
from app.workers.celery_app import celery_app

# Upper bound for the due-soon query, evaluated broadly then filtered in
# Python per-household (household.task_reminder_minutes_before_due is capped
# at 24h by HouseholdUpdate's field validator, so this always covers it —
# same iterate-in-Python pattern as generate_instances.py / notify_digest.py,
# reasonable at this app's data volumes).
_REMINDER_OUTER_BOUND_HOURS = 24
_SERIES_EXPIRING_LEAD_HOURS = 24
_OUTSTANDING_STATUSES = (
    TaskStatus.pending,
    TaskStatus.overdue,
    TaskStatus.review_pending,
    TaskStatus.excuse_pending,
)


@celery_app.task(name="workers.send_task_reminders")  # type: ignore[untyped-decorator]
def send_task_reminders_task() -> None:
    asyncio.run(run_and_dispose(_send_task_reminders()))


async def _send_task_reminders() -> None:
    now = datetime.now(UTC)
    outer_bound = now + timedelta(hours=_REMINDER_OUTER_BOUND_HOURS)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(TaskInstance)
            .join(TaskDefinition)
            .where(
                TaskInstance.status == TaskStatus.pending,
                TaskInstance.reminder_sent_at.is_(None),
                TaskInstance.due_at > now,
                TaskInstance.due_at <= outer_bound,
            )
            .options(
                selectinload(TaskInstance.definition).selectinload(TaskDefinition.household),
                selectinload(TaskInstance.assignee),
            )
        )
        instances = list(result.scalars().all())

        for instance in instances:
            household = instance.definition.household
            lead = timedelta(minutes=household.task_reminder_minutes_before_due)
            if instance.due_at - now > lead:
                continue  # not inside this household's lead window yet
            await notify_task_reminder(instance)
            instance.reminder_sent_at = now
            db.add(instance)

        await db.commit()


@celery_app.task(name="workers.send_series_expiring_reminders")  # type: ignore[untyped-decorator]
def send_series_expiring_task() -> None:
    asyncio.run(run_and_dispose(_send_series_expiring()))


async def _send_series_expiring() -> None:
    now = datetime.now(UTC)
    cutoff = now + timedelta(hours=_SERIES_EXPIRING_LEAD_HOURS)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SeriesInstance)
            .join(Series)
            .where(
                SeriesInstance.status == SeriesStatus.active,
                SeriesInstance.expiring_reminder_sent_at.is_(None),
                SeriesInstance.window_end > now,
                SeriesInstance.window_end <= cutoff,
            )
            .options(selectinload(SeriesInstance.series).selectinload(Series.assignee))
        )
        series_instances = list(result.scalars().all())

        for series_instance in series_instances:
            outstanding = await db.execute(
                select(TaskInstance.id)
                .where(
                    TaskInstance.series_instance_id == series_instance.id,
                    TaskInstance.status.in_(_OUTSTANDING_STATUSES),
                )
                .limit(1)
            )
            if outstanding.scalar_one_or_none() is None:
                continue  # nothing left outstanding in this window — no reason to nag

            await notify_series_expiring(series_instance)
            series_instance.expiring_reminder_sent_at = now
            db.add(series_instance)

        await db.commit()

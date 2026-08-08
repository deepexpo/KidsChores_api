"""
Nightly instance generation worker.

Runs at 00:05 local time per household.
Materialises TaskInstances for a rolling 14-day horizon.
Also transitions:
  - pending → overdue (due time has passed)
  - overdue → missed (grace period expired, no pending review/excuse)

The UNIQUE(definition_id, due_at) constraint makes all inserts idempotent —
safe to re-run after outages or during backfills without duplicating data.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytz
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal, run_and_dispose
from app.db.models import (
    Household,
    ScheduleType,
    TaskDefinition,
    TaskInstance,
    TaskStatus,
)
from app.services.state_machine import grace_remaining
from app.workers.celery_app import celery_app

GENERATION_HORIZON_DAYS = 14


@celery_app.task(name="workers.generate_instances")  # type: ignore[untyped-decorator]
def generate_instances_task() -> None:
    asyncio.run(run_and_dispose(_generate_all_households()))


async def _generate_all_households() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Household))
        households = list(result.scalars().all())

    for household in households:
        await _generate_for_household(household.id)


async def _generate_for_household(household_id: str) -> None:
    async with AsyncSessionLocal() as db:
        household_result = await db.execute(
            select(Household).where(Household.id == household_id)
        )
        household = household_result.scalar_one()
        tz = pytz.timezone(household.timezone)
        now_local = datetime.now(tz)
        today_local = now_local.date()

        # Generate instances for the next 14 days
        definitions_result = await db.execute(
            select(TaskDefinition).where(
                TaskDefinition.household_id == household_id,
                TaskDefinition.archived_at.is_(None),
            )
        )
        definitions = list(definitions_result.scalars().all())

        for defn in definitions:
            for day_offset in range(GENERATION_HORIZON_DAYS):
                target_date = today_local + timedelta(days=day_offset)
                await upsert_instance_for_date(db, defn, target_date, tz)

        # Transition pending → overdue
        await db.execute(
            update(TaskInstance)
            .where(
                TaskInstance.status == TaskStatus.pending,
                TaskInstance.due_at <= datetime.now(UTC),
                TaskInstance.definition_id.in_(
                    [d.id for d in definitions]
                ),
            )
            .values(status=TaskStatus.overdue)
        )

        # Transition overdue → missed. Evaluated per-instance (not a single bulk
        # UPDATE ... WHERE due_at <= cutoff) because the grace clock can have
        # been paused while an excuse sat in excuse_pending — grace_remaining()
        # applies the same suspended-clock math used interactively in
        # state_machine.py, so a teen is never penalised for a parent's slow
        # response (PRD §7.0, §7.2).
        now = datetime.now(UTC)
        overdue_result = await db.execute(
            select(TaskInstance).where(
                TaskInstance.status == TaskStatus.overdue,
                TaskInstance.definition_id.in_([d.id for d in definitions]),
            )
        )
        for instance in overdue_result.scalars().all():
            if grace_remaining(instance, household.grace_period_hours, now) <= timedelta(0):
                instance.status = TaskStatus.missed
                db.add(instance)

        await db.commit()


async def upsert_instance_for_date(
    db: AsyncSession, defn: TaskDefinition, target_date: date, tz: pytz.BaseTzInfo
) -> None:
    """
    Insert a TaskInstance for `defn` on `target_date` if the schedule calls for
    it on that date; a no-op otherwise. Idempotent via the DB's
    UNIQUE(definition_id, due_at) constraint — safe to call for a date that
    already has an instance (the nightly job and on-demand creation, see
    app/routers/definitions.py, both call this and may overlap on today).
    """
    if not should_generate_for_date(defn, target_date):
        return

    due_hour, due_min = map(int, defn.due_time.split(":"))
    due_local = tz.localize(
        datetime(target_date.year, target_date.month, target_date.day, due_hour, due_min)
    )
    due_utc = due_local.astimezone(pytz.utc)
    # An on-demand instance for today can already be past its due time (e.g.
    # created at 2pm for a 9am due_time) — land it directly in the correct
    # status rather than waiting for the next pending->overdue sweep.
    initial_status = TaskStatus.overdue if due_utc <= datetime.now(UTC) else TaskStatus.pending

    stmt = (
        pg_insert(TaskInstance)
        .values(
            definition_id=defn.id,
            assignee_id=defn.assignee_id,
            due_at=due_utc,
            point_value=defn.point_value,  # frozen at creation
            status=initial_status,
        )
        .on_conflict_do_nothing(constraint="uq_instance_def_due")
    )
    await db.execute(stmt)


def should_generate_for_date(defn: TaskDefinition, target_date: date) -> bool:
    """Determine if a definition should generate an instance on target_date."""
    start = datetime.strptime(defn.start_date, "%Y-%m-%d").date()
    if target_date < start:
        return False
    if defn.end_date:
        end = datetime.strptime(defn.end_date, "%Y-%m-%d").date()
        if target_date > end:
            return False

    match defn.schedule_type:
        case ScheduleType.one_time:
            return target_date == start
        case ScheduleType.daily:
            return True
        case ScheduleType.weekdays:
            # weekday_mask: bit 0=Mon … bit 6=Sun
            # Python weekday(): Mon=0 … Sun=6
            if defn.weekday_mask is None:
                return False
            return bool(defn.weekday_mask & (1 << target_date.weekday()))
        case ScheduleType.weekly:
            # Any single day within the week — generate on the start weekday
            return target_date.weekday() == start.weekday()
        case _:
            return False

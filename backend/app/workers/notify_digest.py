"""
Parent nudge: notify parents if approvals have been pending > 24 hours.

PRD §6.7 P1: "Daily nudge if approvals are pending more than 24 hours.
Parent inaction is the primary way these systems die; this notification
is doing more work than it appears."
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import AsyncSessionLocal, run_and_dispose
from app.db.models import Household, Member, MemberRole, TaskDefinition, TaskInstance, TaskStatus
from app.services.notifications import notify_pending_approvals
from app.workers.celery_app import celery_app

NUDGE_AFTER_HOURS = 24


@celery_app.task(name="workers.notify_digest")  # type: ignore[untyped-decorator]
def notify_digest_task() -> None:
    asyncio.run(run_and_dispose(_send_digests()))


async def _send_digests() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=NUDGE_AFTER_HOURS)

    async with AsyncSessionLocal() as db:
        # Find households with approvals pending > 24h
        result = await db.execute(
            select(TaskInstance)
            .join(TaskDefinition)
            .join(Household, TaskDefinition.household_id == Household.id)
            .where(
                TaskInstance.status.in_(
                    [TaskStatus.review_pending, TaskStatus.excuse_pending]
                ),
                # Pending longer than the cutoff
                TaskInstance.completed_at <= cutoff,
            )
            .options(selectinload(TaskInstance.definition))
        )
        pending_instances = list(result.scalars().all())

        if not pending_instances:
            return

        # Group by household
        household_ids = {i.definition.household_id for i in pending_instances}

        for household_id in household_ids:
            # Find parents in this household
            parents_result = await db.execute(
                select(Member).where(
                    Member.household_id == household_id,
                    Member.role == MemberRole.parent,
                    Member.push_token.is_not(None),
                )
            )
            parents = list(parents_result.scalars().all())

            count = sum(
                1 for i in pending_instances
                if i.definition.household_id == household_id
            )

            for parent in parents:
                await notify_pending_approvals(parent, count)

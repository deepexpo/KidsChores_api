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

from app.db.database import AsyncSessionLocal
from app.db.models import Household, Member, MemberRole, TaskDefinition, TaskInstance, TaskStatus
from app.workers.celery_app import celery_app

NUDGE_AFTER_HOURS = 24


@celery_app.task(name="workers.notify_digest")  # type: ignore[untyped-decorator]
def notify_digest_task() -> None:
    asyncio.run(_send_digests())


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
                assert parent.push_token is not None  # query filtered on push_token IS NOT NULL
                await _send_push(
                    token=parent.push_token,
                    title="Approvals waiting",
                    body=f"You have {count} item{'s' if count != 1 else ''} "
                         "waiting for your review.",
                )


async def _send_push(token: str, title: str, body: str) -> None:
    """Send an APNs push notification. Stub — replace with aioapns in production."""
    # TODO: integrate aioapns
    # async with aiohttp.ClientSession() as session:
    #     await apns_client.send_notification(token, title=title, body=body)
    print(f"[PUSH] → {token[:20]}… | {title}: {body}")

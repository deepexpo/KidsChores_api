"""
Domain-level push notifications — PRD §6.7.

Recipient lookup and message copy live here so routers/workers stay focused
on their own logic; app/services/push.py only knows how to send an
already-composed (token, title, body) triple. Every function here is
best-effort — push.send_push never raises, so a notification failure can
never break the state change that triggered it.

Functions that take a TaskInstance/SeriesInstance expect the caller to have
already eager-loaded the relationships they read (`.assignee`, `.definition`,
`.series`) — this codebase has hit MissingGreenlet from lazy-loading under
async SQLAlchemy before (see _get_instance_or_404 in routers/tasks.py), so
loading is the caller's job, not silently re-queried here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Member, MemberRole, SeriesInstance, TaskInstance
from app.services.push import send_push


async def notify_new_excuse(
    db: AsyncSession, household_id: str, teen_name: str, task_title: str
) -> None:
    """PRD §6.7 P0 — parent notified when a teen submits an excuse."""
    result = await db.execute(
        select(Member).where(
            Member.household_id == household_id,
            Member.role == MemberRole.parent,
            Member.push_token.is_not(None),
        )
    )
    for parent in result.scalars().all():
        assert parent.push_token is not None  # query filtered on push_token IS NOT NULL
        await send_push(
            parent.push_token,
            title="New excuse submitted",
            body=f'{teen_name} submitted an excuse for "{task_title}".',
        )


async def notify_excuse_resolved(instance: TaskInstance, approved: bool) -> None:
    """PRD §6.7 P0 — teen notified their excuse was approved or denied."""
    assignee = instance.assignee
    if assignee.push_token is None:
        return
    title = "Excuse approved" if approved else "Excuse denied"
    verdict = "approved" if approved else "denied"
    await send_push(
        assignee.push_token,
        title=title,
        body=f'Your excuse for "{instance.definition.title}" was {verdict}.',
    )


async def notify_task_reminder(instance: TaskInstance) -> None:
    """PRD §6.7 P0 — teen reminder before a task's due time."""
    assignee = instance.assignee
    if assignee.push_token is None:
        return
    await send_push(
        assignee.push_token,
        title="Task due soon",
        body=f'"{instance.definition.title}" is due soon.',
    )


async def notify_series_expiring(series_instance: SeriesInstance) -> None:
    """PRD §6.7 P1 — teen notified a series window is ending with tasks outstanding."""
    assignee = series_instance.series.assignee
    if assignee.push_token is None:
        return
    await send_push(
        assignee.push_token,
        title="Series ending soon",
        body=f'"{series_instance.series.name}" ends soon — finish up to keep the bonus.',
    )


async def notify_pending_approvals(parent: Member, count: int) -> None:
    """PRD §6.7 P1 — daily nudge if approvals have been pending > 24h."""
    if parent.push_token is None:
        return
    await send_push(
        parent.push_token,
        title="Approvals waiting",
        body=f"You have {count} item{'s' if count != 1 else ''} waiting for your review.",
    )

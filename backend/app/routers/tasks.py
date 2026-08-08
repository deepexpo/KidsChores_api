"""Tasks router — today view, week view, complete, excuse, review, cancel."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytz
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_household, get_current_member, require_parent
from app.db.database import get_db
from app.db.models import (
    Household,
    Member,
    MemberRole,
    SeriesInstance,
    TaskDefinition,
    TaskInstance,
    TaskStatus,
)
from app.schemas.schemas import (
    CancelTaskRequest,
    CompleteTaskRequest,
    ExcuseTaskRequest,
    ReviewRequest,
    TaskInstanceResponse,
)
from app.services.idempotency import run_idempotent
from app.services.ledger import LedgerService
from app.services.notifications import notify_excuse_resolved, notify_new_excuse
from app.services.series_completion import SeriesCompletionService
from app.services.state_machine import InvalidTransitionError, StateMachineService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _get_services(db: AsyncSession) -> StateMachineService:
    ledger = LedgerService(db)
    return StateMachineService(db, ledger)


async def _idempotent(
    member: Member,
    idempotency_key: str,
    handler: Callable[[], Awaitable[TaskInstance]],
) -> TaskInstanceResponse:
    """
    Run a task-instance mutation at most once per (member, idempotency_key).
    A replayed request with the same key returns the original response
    instead of re-running the state transition (PRD §11.2).
    """
    result = await run_idempotent(
        member_id=member.id,
        idempotency_key=idempotency_key,
        handler=handler,
        serialize=lambda inst: TaskInstanceResponse.model_validate(inst).model_dump(mode="json"),
        deserialize=lambda d: TaskInstanceResponse.model_validate(d),
    )
    return TaskInstanceResponse.model_validate(result)


def _today_bounds(household: Household) -> tuple[datetime, datetime]:
    """Return (start_of_day, end_of_day) in UTC for the household's local today."""
    tz = pytz.timezone(household.timezone)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(pytz.utc), end_local.astimezone(pytz.utc)


def _week_bounds(start_date_str: str, household: Household) -> tuple[datetime, datetime]:
    tz = pytz.timezone(household.timezone)
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    start_local = tz.localize(start_date.replace(hour=0, minute=0, second=0))
    end_local = start_local + timedelta(days=7)
    return start_local.astimezone(pytz.utc), end_local.astimezone(pytz.utc)


async def _get_instance_or_404(
    instance_id: str,
    db: AsyncSession,
    member: Member,
    household: Household,
) -> TaskInstance:
    result = await db.execute(
        select(TaskInstance)
        .join(TaskDefinition)
        .where(
            TaskInstance.id == instance_id,
            TaskDefinition.household_id == household.id,
        )
        # award_task_completion / award_excuse_if_policy read instance.definition.title;
        # notify_excuse_resolved/notify_task_reminder read instance.assignee.push_token —
        # without eager loading, either lazy load fails under async SQLAlchemy.
        .options(selectinload(TaskInstance.definition), selectinload(TaskInstance.assignee))
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=404, detail="Task instance not found.")
    return instance


# ── GET /v1/tasks/today ───────────────────────────────────────────────────────

@router.get("/today", response_model=list[TaskInstanceResponse])
async def get_today_tasks(
    member_id: str | None = None,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[TaskInstance]:
    """
    Returns today's task instances for the requesting teen,
    or a specific member if the caller is a parent.
    """
    target_id = member.id
    if member_id and member_id != member.id:
        if member.role != MemberRole.parent:
            raise HTTPException(status_code=403, detail="Only parents can view other members.")
        target_id = member_id

    start, end = _today_bounds(household)
    result = await db.execute(
        select(TaskInstance)
        .where(
            TaskInstance.assignee_id == target_id,
            TaskInstance.due_at >= start,
            TaskInstance.due_at < end,
        )
        .order_by(TaskInstance.due_at)
    )
    return list(result.scalars().all())


# ── GET /v1/tasks/week ────────────────────────────────────────────────────────

@router.get("/week", response_model=list[TaskInstanceResponse])
async def get_week_tasks(
    start: str,  # YYYY-MM-DD
    member_id: str | None = None,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[TaskInstance]:
    target_id = member.id
    if member_id and member_id != member.id:
        if member.role != MemberRole.parent:
            raise HTTPException(status_code=403, detail="Only parents can view other members.")
        target_id = member_id

    week_start, week_end = _week_bounds(start, household)
    result = await db.execute(
        select(TaskInstance)
        .where(
            TaskInstance.assignee_id == target_id,
            TaskInstance.due_at >= week_start,
            TaskInstance.due_at < week_end,
        )
        .order_by(TaskInstance.due_at)
    )
    return list(result.scalars().all())


# ── POST /v1/tasks/instances/{id}/complete ───────────────────────────────────

@router.post("/instances/{instance_id}/complete", response_model=TaskInstanceResponse)
async def complete_task(
    instance_id: str,
    body: CompleteTaskRequest,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> TaskInstanceResponse:
    instance = await _get_instance_or_404(instance_id, db, member, household)

    # Teens can only complete their own tasks
    if member.role == MemberRole.teen and instance.assignee_id != member.id:
        raise HTTPException(status_code=403, detail="You can only complete your own tasks.")

    async def _do() -> TaskInstance:
        sm = _get_services(db)
        try:
            completed = await sm.complete(instance, member, household, note=body.note)
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Check series completion after any task completion
        if completed.series_instance_id:
            si_result = await db.execute(
                select(SeriesInstance)
                .where(SeriesInstance.id == completed.series_instance_id)
                # award_series_bonus / award_all_or_nothing_series read
                # series_instance.series.name; needs eager loading (see above).
                .options(selectinload(SeriesInstance.series))
            )
            series_instance = si_result.scalar_one_or_none()
            if series_instance:
                ledger = LedgerService(db)
                svc = SeriesCompletionService(db, ledger)
                await svc.evaluate(series_instance, member)

        return completed

    return await _idempotent(member, body.idempotency_key, _do)


# ── POST /v1/tasks/instances/{id}/excuse ─────────────────────────────────────

@router.post("/instances/{instance_id}/excuse", response_model=TaskInstanceResponse)
async def excuse_task(
    instance_id: str,
    body: ExcuseTaskRequest,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> TaskInstanceResponse:
    instance = await _get_instance_or_404(instance_id, db, member, household)

    if member.role == MemberRole.teen and instance.assignee_id != member.id:
        raise HTTPException(status_code=403, detail="You can only excuse your own tasks.")

    async def _do() -> TaskInstance:
        sm = _get_services(db)
        try:
            result = await sm.submit_excuse(instance, member, body.excuse_text)
        except (InvalidTransitionError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Inside the idempotent handler so a retried request doesn't re-notify.
        await notify_new_excuse(
            db, household.id, member.display_name, instance.definition.title
        )
        return result

    return await _idempotent(member, body.idempotency_key, _do)


# ── POST /v1/tasks/instances/{id}/review ─────────────────────────────────────

@router.post("/instances/{instance_id}/review", response_model=TaskInstanceResponse)
async def review_task(
    instance_id: str,
    body: ReviewRequest,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> TaskInstanceResponse:
    instance = await _get_instance_or_404(instance_id, db, parent, household)

    async def _do() -> TaskInstance:
        sm = _get_services(db)
        try:
            if instance.status == TaskStatus.review_pending:
                return await sm.review(instance, parent, household, body.approve, body.comment)
            elif instance.status == TaskStatus.excuse_pending:
                result = await sm.resolve_excuse(
                    instance, parent, household, body.approve, body.comment
                )
                # Inside the idempotent handler so a retried request doesn't re-notify.
                await notify_excuse_resolved(result, body.approve)
                return result
            raise HTTPException(
                status_code=422,
                detail=f"Task is not pending review (status: {instance.status}).",
            )
        except (InvalidTransitionError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await _idempotent(parent, body.idempotency_key, _do)


# ── POST /v1/tasks/instances/{id}/cancel ─────────────────────────────────────

@router.post("/instances/{instance_id}/cancel", response_model=TaskInstanceResponse)
async def cancel_task(
    instance_id: str,
    body: CancelTaskRequest,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> TaskInstanceResponse:
    """Parent voids an instance (e.g. family was away). Pays no points either way."""
    instance = await _get_instance_or_404(instance_id, db, parent, household)

    async def _do() -> TaskInstance:
        sm = _get_services(db)
        try:
            return await sm.cancel(instance, parent, reason=body.reason)
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await _idempotent(parent, body.idempotency_key, _do)

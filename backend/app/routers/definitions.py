"""Definitions router — TaskDefinition CRUD."""

from datetime import UTC, datetime

import pytz
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_household, get_current_member, require_parent
from app.db.database import get_db
from app.db.models import Household, Member, ScheduleType, TaskDefinition
from app.schemas.schemas import (
    TaskDefinitionCreate,
    TaskDefinitionResponse,
    TaskDefinitionUpdate,
)
from app.workers.generate_instances import upsert_instance_for_date

router = APIRouter(prefix="/v1/definitions", tags=["definitions"])


@router.get("", response_model=list[TaskDefinitionResponse])
async def list_definitions(
    include_archived: bool = False,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[TaskDefinition]:
    q = select(TaskDefinition).where(TaskDefinition.household_id == household.id)
    if not include_archived:
        q = q.where(TaskDefinition.archived_at.is_(None))
    result = await db.execute(q.order_by(TaskDefinition.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=TaskDefinitionResponse, status_code=201)
async def create_definition(
    body: TaskDefinitionCreate,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> TaskDefinition:
    # Without this, a parent could assign a task to any member_id at all —
    # including one belonging to a different household, injecting a task
    # straight into a stranger's teen's Today view (an IDOR found and fixed
    # session-wide; see wallet.py's _assert_wallet_access for the read-side
    # version of the same gap).
    assignee_result = await db.execute(
        select(Member.id).where(
            Member.id == body.assignee_id, Member.household_id == household.id
        )
    )
    if assignee_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=422, detail="assignee_id is not a member of this household."
        )

    definition = TaskDefinition(
        household_id=household.id,
        created_by=parent.id,
        **body.model_dump(),
    )
    db.add(definition)
    await db.flush()

    # Don't make a parent wait for the nightly job to see a task appear.
    # One-time tasks only ever have a single instance, so create it right away
    # regardless of date; recurring tasks get today's instance on demand if
    # today falls on the schedule — everything further out still comes from
    # the nightly 14-day-horizon generation (workers/generate_instances.py),
    # which will safely no-op on whatever this already created.
    tz = pytz.timezone(household.timezone)
    if definition.schedule_type == ScheduleType.one_time:
        target_date = datetime.strptime(definition.start_date, "%Y-%m-%d").date()
    else:
        target_date = datetime.now(tz).date()
    await upsert_instance_for_date(db, definition, target_date, tz)

    return definition


@router.patch("/{definition_id}", response_model=TaskDefinitionResponse)
async def update_definition(
    definition_id: str,
    body: TaskDefinitionUpdate,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> TaskDefinition:
    definition = await _get_or_404(definition_id, household.id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(definition, field, value)
    db.add(definition)
    return definition


@router.delete("/{definition_id}", status_code=204)
async def archive_definition(
    definition_id: str,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Archive (soft-delete) a definition. Stops future instance generation."""
    definition = await _get_or_404(definition_id, household.id, db)
    definition.archived_at = datetime.now(UTC)
    db.add(definition)


async def _get_or_404(
    definition_id: str, household_id: str, db: AsyncSession
) -> TaskDefinition:
    result = await db.execute(
        select(TaskDefinition).where(
            TaskDefinition.id == definition_id,
            TaskDefinition.household_id == household_id,
        )
    )
    definition = result.scalar_one_or_none()
    if definition is None:
        raise HTTPException(status_code=404, detail="Task definition not found.")
    return definition

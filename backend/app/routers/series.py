"""Series router — Series + SeriesInstance CRUD."""

from datetime import UTC, datetime, timedelta

import pytz
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_household, get_current_member, require_parent
from app.db.database import get_db
from app.db.models import (
    Household,
    Member,
    Series,
    SeriesInstance,
    SeriesStatus,
    SeriesWindowType,
    TaskDefinition,
)
from app.schemas.schemas import (
    SeriesCreate,
    SeriesInstanceResponse,
    SeriesResponse,
    SeriesUpdate,
)

router = APIRouter(prefix="/v1/series", tags=["series"])


@router.get("", response_model=list[SeriesResponse])
async def list_series(
    include_archived: bool = False,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[Series]:
    q = select(Series).where(Series.household_id == household.id)
    if not include_archived:
        q = q.where(Series.archived_at.is_(None))
    result = await db.execute(q.order_by(Series.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=SeriesResponse, status_code=201)
async def create_series(
    body: SeriesCreate,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> Series:
    # Validate that all task_definition_ids belong to this household
    result = await db.execute(
        select(TaskDefinition).where(
            TaskDefinition.id.in_(body.task_definition_ids),
            TaskDefinition.household_id == household.id,
        )
    )
    definitions = list(result.scalars().all())
    if len(definitions) != len(body.task_definition_ids):
        raise HTTPException(
            status_code=422,
            detail="One or more task definitions not found in this household.",
        )

    # Same check for assignee_id — otherwise a series (and its bonus payout)
    # could be pointed at a member_id belonging to a different household.
    assignee_result = await db.execute(
        select(Member.id).where(
            Member.id == body.assignee_id, Member.household_id == household.id
        )
    )
    if assignee_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=422, detail="assignee_id is not a member of this household."
        )

    series = Series(
        household_id=household.id,
        name=body.name,
        assignee_id=body.assignee_id,
        bonus_points=body.bonus_points,
        payout_mode=body.payout_mode,
        window_type=body.window_type,
    )
    db.add(series)
    await db.flush()

    # Link definitions to this series
    for defn in definitions:
        defn.series_id = series.id
        db.add(defn)

    # Create the first SeriesInstance based on the window type
    window_start, window_end = _compute_window(series, household)
    series_instance = SeriesInstance(
        series_id=series.id,
        window_start=window_start,
        window_end=window_end,
        status=SeriesStatus.active,
    )
    db.add(series_instance)
    await db.flush()

    return series


@router.patch("/{series_id}", response_model=SeriesResponse)
async def update_series(
    series_id: str,
    body: SeriesUpdate,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> Series:
    """
    Partial update. Does not affect an already-`complete` SeriesInstance's
    payout (already written to the ledger — PRD's value-freezing principle,
    §8.4), but a still-`active` instance picks up the new bonus_points/
    payout_mode immediately, since those aren't frozen anywhere until the
    series actually completes and pays out.
    """
    series = await _get_series_or_404(series_id, household.id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(series, field, value)
    db.add(series)
    return series


@router.get("/{series_id}/instances", response_model=list[SeriesInstanceResponse])
async def list_series_instances(
    series_id: str,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[SeriesInstanceResponse]:
    # Verify series belongs to household (raises 404 otherwise)
    await _get_series_or_404(series_id, household.id, db)

    result = await db.execute(
        select(SeriesInstance)
        .where(SeriesInstance.series_id == series_id)
        .order_by(SeriesInstance.window_start.desc())
    )
    instances = list(result.scalars().all())

    # Annotate with progress
    responses = []
    for si in instances:
        from app.db.models import TaskInstance, TaskStatus
        ti_result = await db.execute(
            select(TaskInstance).where(TaskInstance.series_instance_id == si.id)
        )
        task_instances = list(ti_result.scalars().all())
        total = len(task_instances)
        complete = sum(
            1 for t in task_instances if t.status == TaskStatus.complete
        )
        progress = f"{complete} of {total} complete" if total > 0 else None

        responses.append(
            SeriesInstanceResponse(
                id=si.id,
                series_id=si.series_id,
                window_start=si.window_start,
                window_end=si.window_end,
                status=si.status,
                completed_at=si.completed_at,
                progress=progress,
            )
        )
    return responses


@router.delete("/{series_id}", status_code=204)
async def archive_series(
    series_id: str,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> None:
    series = await _get_series_or_404(series_id, household.id, db)
    series.archived_at = datetime.now(UTC)
    db.add(series)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_series_or_404(
    series_id: str, household_id: str, db: AsyncSession
) -> Series:
    result = await db.execute(
        select(Series).where(
            Series.id == series_id,
            Series.household_id == household_id,
        )
    )
    series = result.scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404, detail="Series not found.")
    return series


def _compute_window(series: Series, household: Household) -> tuple[datetime, datetime]:
    """Compute window_start and window_end in UTC for the current period."""
    tz = pytz.timezone(household.timezone)
    now_local = datetime.now(tz)

    match series.window_type:
        case SeriesWindowType.weekly:
            # Monday of current week
            days_since_monday = now_local.weekday()
            monday = now_local - timedelta(days=days_since_monday)
            start_local = monday.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = start_local + timedelta(weeks=1)
        case SeriesWindowType.monthly:
            start_local = now_local.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            # First day of next month
            if now_local.month == 12:
                end_local = start_local.replace(year=now_local.year + 1, month=1)
            else:
                end_local = start_local.replace(month=now_local.month + 1)
        case _:
            # custom — caller must provide explicit dates (future enhancement)
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = start_local + timedelta(days=7)

    return (
        tz.localize(start_local.replace(tzinfo=None)).astimezone(pytz.utc),
        tz.localize(end_local.replace(tzinfo=None)).astimezone(pytz.utc),
    )

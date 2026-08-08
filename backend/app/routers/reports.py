"""Reports router — completion rate, excuse frequency, points earned per week.

Master PRD §10.2 "Reports" screen: "Completion rate over time, excuse
frequency, points earned per week." Bucketing logic lives in
app/services/reports.py (pure, unit-tested); this router only handles auth,
param validation, and fetching the raw rows for the requested range.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_household, get_current_member
from app.db.database import get_db
from app.db.models import (
    Household,
    LedgerEntry,
    LedgerEntryType,
    Member,
    MemberRole,
    TaskDefinition,
    TaskInstance,
)
from app.schemas.schemas import ReportResponse, ReportWeek
from app.services.reports import build_weekly_report

router = APIRouter(prefix="/v1/reports", tags=["reports"])

# Ledger entry types that represent points a member actually earned — not
# claims, reversals, or manual adjustments.
_EARNED_ENTRY_TYPES = [
    LedgerEntryType.task_completed,
    LedgerEntryType.series_bonus,
    LedgerEntryType.excused_partial,
]


@router.get("/{member_id}", response_model=ReportResponse)
async def get_report(
    member_id: str,
    weeks: int = 12,
    member: Member = Depends(get_current_member),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """Teen: own report only. Parent: any member in the household."""
    if member.role == MemberRole.teen and member.id != member_id:
        raise HTTPException(status_code=403, detail="You can only view your own report.")
    if not 1 <= weeks <= 52:
        raise HTTPException(status_code=422, detail="weeks must be between 1 and 52.")

    target_result = await db.execute(
        select(Member).where(Member.id == member_id, Member.household_id == household.id)
    )
    if target_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Member not found.")

    tz = pytz.timezone(household.timezone)
    now_local = datetime.now(tz)
    this_monday = (now_local - timedelta(days=now_local.weekday())).date()
    range_start = this_monday - timedelta(weeks=weeks - 1)
    range_start_utc = tz.localize(
        datetime(range_start.year, range_start.month, range_start.day)
    ).astimezone(pytz.utc)
    range_end_utc = tz.localize(
        datetime(this_monday.year, this_monday.month, this_monday.day)
    ).astimezone(pytz.utc) + timedelta(weeks=1)

    instances_result = await db.execute(
        select(TaskInstance)
        .join(TaskDefinition, TaskInstance.definition_id == TaskDefinition.id)
        .where(
            TaskDefinition.assignee_id == member_id,
            TaskInstance.due_at >= range_start_utc,
            TaskInstance.due_at < range_end_utc,
        )
    )
    instances = list(instances_result.scalars().all())

    ledger_result = await db.execute(
        select(LedgerEntry).where(
            LedgerEntry.member_id == member_id,
            LedgerEntry.created_at >= range_start_utc,
            LedgerEntry.created_at < range_end_utc,
            LedgerEntry.entry_type.in_(_EARNED_ENTRY_TYPES),
        )
    )
    ledger_entries = list(ledger_result.scalars().all())

    weeks_out = build_weekly_report(
        instances=instances,
        ledger_entries=ledger_entries,
        range_start=range_start,
        num_weeks=weeks,
        tz=tz,
    )
    return ReportResponse(member_id=member_id, weeks=[ReportWeek(**w) for w in weeks_out])

"""
Approvals router — parent inbox.

Aggregates all items awaiting parent action:
- Task completions with requires_review=True (status=review_pending)
- Excuses submitted by teens (status=excuse_pending)

Sorted by submission time ascending (oldest first, so the parent
sees what needs attention most urgently at the top).
"""

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_household, require_parent
from app.db.database import get_db
from app.db.models import Household, Member, TaskDefinition, TaskInstance, TaskStatus
from app.schemas.schemas import (
    ApprovalItem,
    BulkApproveRequest,
    BulkApproveResponse,
    BulkApproveResultItem,
)
from app.services.idempotency import run_idempotent
from app.services.ledger import LedgerService
from app.services.state_machine import InvalidTransitionError, StateMachineService

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalItem])
async def get_approval_inbox(
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalItem]:
    """Return all items awaiting parent action, oldest first."""

    result = await db.execute(
        select(TaskInstance)
        .join(TaskDefinition, TaskInstance.definition_id == TaskDefinition.id)
        .join(Member, TaskInstance.assignee_id == Member.id)
        .where(
            TaskDefinition.household_id == household.id,
            TaskInstance.status.in_(
                [TaskStatus.review_pending, TaskStatus.excuse_pending]
            ),
        )
        .options(
            selectinload(TaskInstance.definition),
            selectinload(TaskInstance.assignee),
        )
        .order_by(
            TaskInstance.excuse_submitted_at.asc().nullsfirst(),
            TaskInstance.completed_at.asc(),
        )
    )
    instances = list(result.scalars().all())

    items: list[ApprovalItem] = []
    for inst in instances:
        item_type: Literal["completion", "excuse"]
        if inst.status == TaskStatus.review_pending:
            item_type = "completion"
            submitted_at = inst.completed_at  # always set: written on teen completion
        else:
            item_type = "excuse"
            submitted_at = inst.excuse_submitted_at  # always set: written on excuse submission
        assert submitted_at is not None

        items.append(
            ApprovalItem(
                type=item_type,
                task_instance_id=inst.id,
                task_title=inst.definition.title,
                assignee_name=inst.assignee.display_name,
                point_value=inst.point_value,
                submitted_at=submitted_at,
                excuse_text=inst.excuse_text,
            )
        )

    return items


# ── POST /v1/approvals/bulk ─────────────────────────────────────────────────

@router.post("/bulk", response_model=BulkApproveResponse)
async def bulk_approve(
    body: BulkApproveRequest,
    parent: Member = Depends(require_parent),
    household: Household = Depends(get_current_household),
    db: AsyncSession = Depends(get_db),
) -> BulkApproveResponse:
    """
    Approve or deny a batch of review_pending / excuse_pending items in one call
    (PRD §6.4). A bad id in the batch fails that item only, not the whole call.
    """

    async def _run_batch() -> BulkApproveResponse:
        ledger = LedgerService(db)
        sm = StateMachineService(db, ledger)
        results: list[BulkApproveResultItem] = []

        for item in body.items:
            result = await db.execute(
                select(TaskInstance)
                .join(TaskDefinition)
                .where(
                    TaskInstance.id == item.task_instance_id,
                    TaskDefinition.household_id == household.id,
                )
                # review()/resolve_excuse() -> ledger reads instance.definition.title;
                # without eager loading, that lazy load fails under async SQLAlchemy.
                .options(selectinload(TaskInstance.definition))
            )
            instance = result.scalar_one_or_none()
            if instance is None:
                results.append(
                    BulkApproveResultItem(
                        task_instance_id=item.task_instance_id,
                        success=False,
                        error="Task instance not found.",
                    )
                )
                continue

            try:
                if instance.status == TaskStatus.review_pending:
                    instance = await sm.review(
                        instance, parent, household, item.approve, item.comment
                    )
                elif instance.status == TaskStatus.excuse_pending:
                    instance = await sm.resolve_excuse(
                        instance, parent, household, item.approve, item.comment
                    )
                else:
                    results.append(
                        BulkApproveResultItem(
                            task_instance_id=item.task_instance_id,
                            success=False,
                            error=f"Task is not pending review (status: {instance.status}).",
                        )
                    )
                    continue
            except (InvalidTransitionError, ValueError) as exc:
                results.append(
                    BulkApproveResultItem(
                        task_instance_id=item.task_instance_id,
                        success=False,
                        error=str(exc),
                    )
                )
                continue

            results.append(
                BulkApproveResultItem(
                    task_instance_id=item.task_instance_id,
                    success=True,
                    status=instance.status,
                )
            )

        return BulkApproveResponse(results=results)

    return await run_idempotent(
        member_id=parent.id,
        idempotency_key=body.idempotency_key,
        handler=_run_batch,
        serialize=lambda r: r.model_dump(mode="json"),
        deserialize=lambda d: BulkApproveResponse.model_validate(d),
    )

"""
Series completion tests — verifies PRD §8.3 business rules.

Rules tested:
1. Series completes when all tasks are complete or excused (not all excused).
2. Excused tasks do NOT block series completion.
3. All-excused series → no completion.
4. individual_plus_bonus: bonus written separately.
5. all_or_nothing: single entry = completed task values + bonus (excused excluded).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import SeriesPayoutMode, SeriesStatus, TaskStatus
from app.services.series_completion import SeriesCompletionService


def make_task_instance(status: TaskStatus, point_value: int = 30) -> MagicMock:
    inst = MagicMock()
    inst.status = status
    inst.point_value = point_value
    return inst


def make_series_instance(
    payout_mode: SeriesPayoutMode = SeriesPayoutMode.individual_plus_bonus,
    bonus_points: int = 100,
    assignee_id: str = "teen-1",
    status: SeriesStatus = SeriesStatus.active,
) -> MagicMock:
    si = MagicMock()
    si.id = "si-1"
    si.status = status
    si.series = MagicMock()
    si.series.name = "Weekend Reset"
    si.series.payout_mode = payout_mode
    si.series.bonus_points = bonus_points
    si.series.assignee_id = assignee_id
    return si


@pytest.fixture
def svc() -> SeriesCompletionService:
    db = AsyncMock()
    db.add = MagicMock()
    ledger = AsyncMock()
    ledger.award_series_bonus = AsyncMock()
    ledger.award_all_or_nothing_series = AsyncMock()
    return SeriesCompletionService(db=db, ledger=ledger)


@pytest.mark.asyncio
async def test_all_complete_individual_plus_bonus(svc: SeriesCompletionService) -> None:
    """All tasks complete + individual_plus_bonus → bonus written."""
    instances = [
        make_task_instance(TaskStatus.complete, 40),
        make_task_instance(TaskStatus.complete, 30),
        make_task_instance(TaskStatus.complete, 50),
    ]
    si = make_series_instance(SeriesPayoutMode.individual_plus_bonus, bonus_points=100)
    actor = MagicMock()

    svc._db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=instances)))
    ))

    completed = await svc.evaluate(si, actor)
    assert completed is True
    assert si.status == SeriesStatus.complete
    svc._ledger.award_series_bonus.assert_awaited_once_with(
        series_instance=si,
        member_id="teen-1",
        actor=actor,
        bonus_points=100,
    )


@pytest.mark.asyncio
async def test_excused_does_not_block_completion(svc: SeriesCompletionService) -> None:
    """One excused task, rest complete → series completes. Excused excluded from all_or_nothing."""
    instances = [
        make_task_instance(TaskStatus.complete, 40),
        make_task_instance(TaskStatus.complete, 30),
        make_task_instance(TaskStatus.excused, 50),  # excused, not blocking
    ]
    si = make_series_instance(SeriesPayoutMode.all_or_nothing, bonus_points=100)
    actor = MagicMock()

    svc._db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=instances)))
    ))

    completed = await svc.evaluate(si, actor)
    assert completed is True
    # Payout = completed tasks (40+30) + bonus (100) = 170, excused (50) excluded
    svc._ledger.award_all_or_nothing_series.assert_awaited_once_with(
        series_instance=si,
        member_id="teen-1",
        actor=actor,
        total_points=170,
    )


@pytest.mark.asyncio
async def test_all_excused_no_completion(svc: SeriesCompletionService) -> None:
    """All tasks excused → series does NOT complete, nothing paid."""
    instances = [
        make_task_instance(TaskStatus.excused, 40),
        make_task_instance(TaskStatus.excused, 30),
    ]
    si = make_series_instance()
    actor = MagicMock()

    svc._db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=instances)))
    ))

    completed = await svc.evaluate(si, actor)
    assert completed is False
    svc._ledger.award_series_bonus.assert_not_awaited()
    svc._ledger.award_all_or_nothing_series.assert_not_awaited()


@pytest.mark.asyncio
async def test_outstanding_tasks_no_completion(svc: SeriesCompletionService) -> None:
    """Some tasks still pending → series cannot complete yet."""
    instances = [
        make_task_instance(TaskStatus.complete, 40),
        make_task_instance(TaskStatus.pending, 30),  # still outstanding
    ]
    si = make_series_instance()
    actor = MagicMock()

    svc._db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=instances)))
    ))

    completed = await svc.evaluate(si, actor)
    assert completed is False


@pytest.mark.asyncio
async def test_already_complete_series_not_re_evaluated(svc: SeriesCompletionService) -> None:
    """Already-complete series_instance → returns False immediately."""
    si = make_series_instance(status=SeriesStatus.complete)
    actor = MagicMock()
    completed = await svc.evaluate(si, actor)
    assert completed is False
    svc._db.execute.assert_not_awaited()

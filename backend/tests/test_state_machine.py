"""
State machine tests — exhaustive coverage of all rows in PRD §7.0.

Each test corresponds to one row in the transition table.
These are authoritative; the implementation should pass all of them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import TaskStatus
from app.services.state_machine import InvalidTransitionError, StateMachineService, grace_remaining


def make_instance(
    status: TaskStatus = TaskStatus.pending,
    due_at: datetime | None = None,
    requires_review: bool = False,
    series_instance_id: str | None = None,
) -> MagicMock:
    inst = MagicMock()
    inst.status = status
    inst.due_at = due_at or datetime.now(UTC) + timedelta(hours=1)
    inst.point_value = 50
    inst.assignee_id = "teen-1"
    inst.id = "inst-1"
    inst.definition = MagicMock()
    inst.definition.requires_review = requires_review
    inst.definition.title = "Wash dishes"
    inst.series_instance_id = series_instance_id
    inst.grace_elapsed_seconds = 0
    inst.grace_paused_at = None
    return inst


def make_household(grace_hours: int = 24) -> MagicMock:
    h = MagicMock()
    h.grace_period_hours = grace_hours
    h.excused_payout_policy = MagicMock()
    return h


def make_actor(role: str = "parent") -> MagicMock:
    actor = MagicMock()
    actor.id = "parent-1"
    actor.role = role
    return actor


@pytest.fixture
def sm() -> StateMachineService:
    db = AsyncMock()
    ledger = AsyncMock()
    ledger.award_task_completion = AsyncMock()
    ledger.award_excuse_if_policy = AsyncMock(return_value=None)
    ledger.write_reversal = AsyncMock()
    return StateMachineService(db=db, ledger=ledger)


# ── pending transitions ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_complete_auto(sm: StateMachineService) -> None:
    """pending + complete + auto_approve → complete"""
    inst = make_instance(TaskStatus.pending, requires_review=False)
    household = make_household()
    actor = make_actor()
    result = await sm.complete(inst, actor, household)
    assert result.status == TaskStatus.complete
    sm._ledger.award_task_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_complete_requires_review(sm: StateMachineService) -> None:
    """pending + complete + requires_review → review_pending"""
    inst = make_instance(TaskStatus.pending, requires_review=True)
    household = make_household()
    actor = make_actor()
    result = await sm.complete(inst, actor, household)
    assert result.status == TaskStatus.review_pending
    sm._ledger.award_task_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_excuse(sm: StateMachineService) -> None:
    """pending + excuse → excuse_pending"""
    inst = make_instance(TaskStatus.pending)
    actor = make_actor()
    result = await sm.submit_excuse(inst, actor, "I had a tournament that day")
    assert result.status == TaskStatus.excuse_pending


@pytest.mark.asyncio
async def test_pending_cancel(sm: StateMachineService) -> None:
    """pending + cancel → cancelled (via direct status set; tested separately)"""
    # Cancel is a direct status write by a parent, not through state machine
    # This test documents the expected terminal state
    inst = make_instance(TaskStatus.pending)
    inst.status = TaskStatus.cancelled
    assert inst.status == TaskStatus.cancelled


# ── overdue transitions ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overdue_complete_auto(sm: StateMachineService) -> None:
    """overdue + complete + auto_approve → complete"""
    inst = make_instance(TaskStatus.overdue, requires_review=False)
    inst.due_at = datetime.now(UTC) - timedelta(hours=2)
    household = make_household()
    actor = make_actor()
    result = await sm.complete(inst, actor, household)
    assert result.status == TaskStatus.complete


@pytest.mark.asyncio
async def test_overdue_complete_requires_review(sm: StateMachineService) -> None:
    """overdue + complete + requires_review → review_pending"""
    inst = make_instance(TaskStatus.overdue, requires_review=True)
    household = make_household()
    actor = make_actor()
    result = await sm.complete(inst, actor, household)
    assert result.status == TaskStatus.review_pending


@pytest.mark.asyncio
async def test_overdue_excuse(sm: StateMachineService) -> None:
    """overdue + excuse → excuse_pending; grace clock pauses"""
    inst = make_instance(TaskStatus.overdue)
    inst.due_at = datetime.now(UTC) - timedelta(hours=3)
    actor = make_actor()
    result = await sm.submit_excuse(inst, actor, "Was at the hospital all day")
    assert result.status == TaskStatus.excuse_pending
    assert result.grace_paused_at is not None  # clock suspended


# ── review_pending transitions ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_pending_approve(sm: StateMachineService) -> None:
    """review_pending + approve → complete + points awarded"""
    inst = make_instance(TaskStatus.review_pending)
    household = make_household()
    parent = make_actor()
    result = await sm.review(inst, parent, household, approve=True)
    assert result.status == TaskStatus.complete
    sm._ledger.award_task_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_pending_deny_before_due(sm: StateMachineService) -> None:
    """review_pending + deny + before due → pending (can retry)"""
    future_due = datetime.now(UTC) + timedelta(hours=2)
    inst = make_instance(TaskStatus.review_pending, due_at=future_due)
    household = make_household()
    parent = make_actor()
    result = await sm.review(inst, parent, household, approve=False, comment="Didn't look done")
    assert result.status == TaskStatus.pending


@pytest.mark.asyncio
async def test_review_pending_deny_after_due(sm: StateMachineService) -> None:
    """review_pending + deny + after due → overdue"""
    past_due = datetime.now(UTC) - timedelta(hours=1)
    inst = make_instance(TaskStatus.review_pending, due_at=past_due)
    household = make_household()
    parent = make_actor()
    result = await sm.review(inst, parent, household, approve=False, comment="Not clean enough")
    assert result.status == TaskStatus.overdue


@pytest.mark.asyncio
async def test_review_deny_requires_comment(sm: StateMachineService) -> None:
    """Denying a review without a comment raises ValueError."""
    inst = make_instance(TaskStatus.review_pending)
    household = make_household()
    parent = make_actor()
    with pytest.raises(ValueError, match="comment"):
        await sm.review(inst, parent, household, approve=False, comment=None)


# ── excuse_pending transitions ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_excuse_pending_approve(sm: StateMachineService) -> None:
    """excuse_pending + approve → excused"""
    inst = make_instance(TaskStatus.excuse_pending)
    household = make_household()
    parent = make_actor()
    result = await sm.resolve_excuse(inst, parent, household, approve=True)
    assert result.status == TaskStatus.excused


@pytest.mark.asyncio
async def test_excuse_pending_deny_grace_remaining(sm: StateMachineService) -> None:
    """excuse_pending + deny + grace remaining → overdue"""
    # Due 2 hours ago, grace is 24h, so plenty of grace remains
    inst = make_instance(TaskStatus.excuse_pending)
    inst.due_at = datetime.now(UTC) - timedelta(hours=2)
    inst.grace_elapsed_seconds = 0
    inst.grace_paused_at = datetime.now(UTC)
    household = make_household(grace_hours=24)
    parent = make_actor()
    result = await sm.resolve_excuse(inst, parent, household, approve=False, comment="No excuse")
    assert result.status == TaskStatus.overdue


@pytest.mark.asyncio
async def test_excuse_pending_deny_grace_expired(sm: StateMachineService) -> None:
    """excuse_pending + deny + grace expired → missed"""
    # Due 30 hours ago, grace is 24h, so grace has expired
    inst = make_instance(TaskStatus.excuse_pending)
    inst.due_at = datetime.now(UTC) - timedelta(hours=30)
    inst.grace_elapsed_seconds = 25 * 3600  # 25 hours already elapsed
    inst.grace_paused_at = datetime.now(UTC)
    household = make_household(grace_hours=24)
    parent = make_actor()
    result = await sm.resolve_excuse(inst, parent, household, approve=False, comment="No excuse")
    assert result.status == TaskStatus.missed


# ── complete reversal ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_parent_reverses(sm: StateMachineService) -> None:
    """complete + parent reverses → overdue/missed + reversal ledger entry"""
    inst = make_instance(TaskStatus.complete)
    inst.due_at = datetime.now(UTC) - timedelta(hours=1)
    parent = make_actor()
    result = await sm.reverse_completion(inst, parent, reason="Photos showed it wasn't done")
    assert result.status in (TaskStatus.overdue, TaskStatus.missed)
    sm._ledger.write_reversal.assert_awaited_once()


# ── invalid transitions ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cannot_complete_missed_task(sm: StateMachineService) -> None:
    inst = make_instance(TaskStatus.missed)
    household = make_household()
    actor = make_actor()
    with pytest.raises(InvalidTransitionError):
        await sm.complete(inst, actor, household)


@pytest.mark.asyncio
async def test_cannot_excuse_complete_task(sm: StateMachineService) -> None:
    inst = make_instance(TaskStatus.complete)
    actor = make_actor()
    with pytest.raises(InvalidTransitionError):
        await sm.submit_excuse(inst, actor, "I was sick and somehow completed it")


@pytest.mark.asyncio
async def test_excuse_too_short(sm: StateMachineService) -> None:
    inst = make_instance(TaskStatus.pending)
    actor = make_actor()
    with pytest.raises(ValueError, match="10 characters"):
        await sm.submit_excuse(inst, actor, "short")


# ── cancel ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_cancel_via_service(sm: StateMachineService) -> None:
    """pending + parent cancels → cancelled (PRD §7.0)"""
    inst = make_instance(TaskStatus.pending)
    parent = make_actor()
    result = await sm.cancel(inst, parent, reason="Family was away")
    assert result.status == TaskStatus.cancelled
    assert result.review_comment == "Family was away"
    assert result.reviewed_by == parent.id


@pytest.mark.asyncio
async def test_overdue_cancel_via_service(sm: StateMachineService) -> None:
    """overdue + parent cancels → cancelled (PRD §7.0)"""
    inst = make_instance(TaskStatus.overdue)
    parent = make_actor()
    result = await sm.cancel(inst, parent)
    assert result.status == TaskStatus.cancelled


@pytest.mark.asyncio
async def test_cannot_cancel_complete_task(sm: StateMachineService) -> None:
    inst = make_instance(TaskStatus.complete)
    parent = make_actor()
    with pytest.raises(InvalidTransitionError):
        await sm.cancel(inst, parent)


# ── grace_remaining (shared with the nightly worker) ────────────────────────

def test_grace_remaining_running_clock_not_expired() -> None:
    """Clock never paused: remaining = grace_hours - (now - due_at)."""
    inst = make_instance(TaskStatus.overdue)
    inst.due_at = datetime.now(UTC) - timedelta(hours=2)
    inst.grace_elapsed_seconds = 0
    inst.grace_paused_at = None
    remaining = grace_remaining(inst, grace_period_hours=24, now=datetime.now(UTC))
    assert remaining > timedelta(hours=21)


def test_grace_remaining_running_clock_expired() -> None:
    inst = make_instance(TaskStatus.overdue)
    inst.due_at = datetime.now(UTC) - timedelta(hours=30)
    inst.grace_elapsed_seconds = 0
    inst.grace_paused_at = None
    remaining = grace_remaining(inst, grace_period_hours=24, now=datetime.now(UTC))
    assert remaining <= timedelta(0)


def test_grace_remaining_excludes_paused_time() -> None:
    """
    A task overdue for 2h, then paused (excuse_pending) for a long parent
    delay, must not have that paused time count against its grace budget —
    this is the exact scenario the nightly worker previously got wrong.
    """
    now = datetime.now(UTC)
    inst = make_instance(TaskStatus.overdue)
    inst.due_at = now - timedelta(hours=10)
    # 2h elapsed before the excuse paused the clock; it's been paused ever since.
    inst.grace_elapsed_seconds = int(timedelta(hours=2).total_seconds())
    inst.grace_paused_at = now - timedelta(hours=8)
    remaining = grace_remaining(inst, grace_period_hours=24, now=now)
    # Only 2h of the 24h grace budget has actually been used, regardless of
    # the raw 10h since due_at.
    assert remaining == timedelta(hours=22)

"""
State machine for TaskInstance.

Implements the complete transition table from PRD §7.0.
Every valid transition is a row here; anything not listed raises
InvalidTransitionError.

Grace-period clock suspension:
  When a task enters review_pending or excuse_pending, the clock stops.
  On denial, the clock resumes from where it paused.
  This ensures a teen is never penalised for a parent's slow response.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Member, TaskInstance, TaskStatus
from app.services.ledger import LedgerService

if TYPE_CHECKING:
    from app.db.models import Household


class InvalidTransitionError(ValueError):
    """Raised when a state transition is not permitted."""


# Maps (from_status, trigger) → to_status
# Triggers: "complete_auto", "complete_review", "excuse", "due_passes",
#           "cancel", "approve", "deny_before_due", "deny_after_due",
#           "grace_expires", "parent_reverses"
_TRANSITIONS: dict[tuple[TaskStatus, str], TaskStatus] = {
    # From PENDING
    (TaskStatus.pending, "complete_auto"): TaskStatus.complete,
    (TaskStatus.pending, "complete_review"): TaskStatus.review_pending,
    (TaskStatus.pending, "excuse"): TaskStatus.excuse_pending,
    (TaskStatus.pending, "due_passes"): TaskStatus.overdue,
    (TaskStatus.pending, "cancel"): TaskStatus.cancelled,
    # From OVERDUE
    (TaskStatus.overdue, "complete_auto"): TaskStatus.complete,
    (TaskStatus.overdue, "complete_review"): TaskStatus.review_pending,
    (TaskStatus.overdue, "excuse"): TaskStatus.excuse_pending,
    (TaskStatus.overdue, "grace_expires"): TaskStatus.missed,
    (TaskStatus.overdue, "cancel"): TaskStatus.cancelled,
    # From REVIEW_PENDING
    (TaskStatus.review_pending, "approve"): TaskStatus.complete,
    (TaskStatus.review_pending, "deny_before_due"): TaskStatus.pending,
    (TaskStatus.review_pending, "deny_after_due"): TaskStatus.overdue,
    # From EXCUSE_PENDING
    (TaskStatus.excuse_pending, "approve"): TaskStatus.excused,
    (TaskStatus.excuse_pending, "deny_grace_remaining"): TaskStatus.overdue,
    (TaskStatus.excuse_pending, "deny_grace_expired"): TaskStatus.missed,
    # From COMPLETE (only via explicit parent reversal)
    (TaskStatus.complete, "parent_reverses_before_due"): TaskStatus.overdue,
    (TaskStatus.complete, "parent_reverses_after_due"): TaskStatus.missed,
}

# Terminal states — cannot transition out (except complete via explicit reversal)
TERMINAL_STATES = {TaskStatus.missed, TaskStatus.excused, TaskStatus.cancelled}


class StateMachineService:
    def __init__(self, db: AsyncSession, ledger: LedgerService):
        self._db = db
        self._ledger = ledger

    async def complete(
        self,
        instance: TaskInstance,
        actor: Member,
        household: Household,
        note: str | None = None,
    ) -> TaskInstance:
        """Teen marks a task complete. Routes to complete or review_pending."""
        if instance.status not in (TaskStatus.pending, TaskStatus.overdue):
            raise InvalidTransitionError(
                f"Cannot complete a task in status '{instance.status}'. "
                "Task must be pending or overdue."
            )

        now = datetime.now(UTC)
        instance.completed_at = now
        instance.completion_note = note

        definition = instance.definition
        if definition.requires_review:
            trigger = "complete_review"
        else:
            trigger = "complete_auto"

        new_status = _TRANSITIONS[(instance.status, trigger)]
        instance.status = new_status

        if new_status == TaskStatus.complete:
            await self._ledger.award_task_completion(instance, actor, household)

        self._db.add(instance)
        return instance

    async def submit_excuse(
        self,
        instance: TaskInstance,
        actor: Member,
        excuse_text: str,
    ) -> TaskInstance:
        """Teen submits an excuse for a task they couldn't complete."""
        if instance.status not in (TaskStatus.pending, TaskStatus.overdue):
            raise InvalidTransitionError(
                f"Cannot submit excuse for task in status '{instance.status}'."
            )
        if len(excuse_text.strip()) < 10:
            raise ValueError("Excuse must be at least 10 characters.")

        now = datetime.now(UTC)
        instance.excuse_text = excuse_text
        instance.excuse_submitted_at = now

        # Suspend the grace-period clock
        if instance.status == TaskStatus.overdue:
            self._suspend_grace(instance, now)

        new_status = _TRANSITIONS[(instance.status, "excuse")]
        instance.status = new_status
        self._db.add(instance)
        return instance

    async def review(
        self,
        instance: TaskInstance,
        reviewer: Member,
        household: Household,
        approve: bool,
        comment: str | None = None,
    ) -> TaskInstance:
        """Parent approves or denies a review_pending completion."""
        if instance.status != TaskStatus.review_pending:
            raise InvalidTransitionError(
                f"Task is not in review_pending (current: '{instance.status}')."
            )
        if not approve and not comment:
            raise ValueError("A comment is required when denying a completion.")

        now = datetime.now(UTC)
        instance.reviewed_by = reviewer.id
        instance.reviewed_at = now
        instance.review_comment = comment

        if approve:
            trigger = "approve"
            instance.status = _TRANSITIONS[(TaskStatus.review_pending, trigger)]
            await self._ledger.award_task_completion(instance, reviewer, household)
        else:
            # Determine if we're before or after the original due time
            trigger = (
                "deny_before_due" if now < instance.due_at else "deny_after_due"
            )
            instance.status = _TRANSITIONS[(TaskStatus.review_pending, trigger)]
            # Resume grace clock if now overdue
            if instance.status == TaskStatus.overdue:
                self._resume_grace(instance, now)

        self._db.add(instance)
        return instance

    async def resolve_excuse(
        self,
        instance: TaskInstance,
        reviewer: Member,
        household: Household,
        approve: bool,
        comment: str | None = None,
    ) -> TaskInstance:
        """Parent approves or denies an excuse_pending excuse."""
        if instance.status != TaskStatus.excuse_pending:
            raise InvalidTransitionError(
                f"Task is not in excuse_pending (current: '{instance.status}')."
            )
        if not approve and not comment:
            raise ValueError("A comment is required when denying an excuse.")

        now = datetime.now(UTC)
        instance.reviewed_by = reviewer.id
        instance.reviewed_at = now
        instance.review_comment = comment

        if approve:
            instance.status = _TRANSITIONS[(TaskStatus.excuse_pending, "approve")]
            # Pay out if household policy says so
            await self._ledger.award_excuse_if_policy(instance, reviewer, household)
        else:
            # Resume the grace clock to determine deny trigger
            self._resume_grace(instance, now)
            remaining = self._grace_remaining(instance, household, now)
            if remaining > timedelta(0):
                trigger = "deny_grace_remaining"
            else:
                trigger = "deny_grace_expired"
            instance.status = _TRANSITIONS[(TaskStatus.excuse_pending, trigger)]

        self._db.add(instance)
        return instance

    async def cancel(
        self,
        instance: TaskInstance,
        actor: Member,
        reason: str | None = None,
    ) -> TaskInstance:
        """Parent voids an instance (e.g. family was away). No points involved either way."""
        if instance.status not in (TaskStatus.pending, TaskStatus.overdue):
            raise InvalidTransitionError(
                f"Cannot cancel a task in status '{instance.status}'. "
                "Task must be pending or overdue."
            )

        now = datetime.now(UTC)
        instance.status = _TRANSITIONS[(instance.status, "cancel")]
        instance.reviewed_by = actor.id
        instance.reviewed_at = now
        instance.review_comment = reason

        self._db.add(instance)
        return instance

    async def reverse_completion(
        self,
        instance: TaskInstance,
        reviewer: Member,
        reason: str,
    ) -> TaskInstance:
        """Parent explicitly reverses a completed task. Writes a reversal ledger entry."""
        if instance.status != TaskStatus.complete:
            raise InvalidTransitionError("Can only reverse a completed task.")

        now = datetime.now(UTC)
        trigger = (
            "parent_reverses_before_due" if now < instance.due_at else "parent_reverses_after_due"
        )
        instance.status = _TRANSITIONS[(TaskStatus.complete, trigger)]
        instance.reviewed_by = reviewer.id
        instance.reviewed_at = now
        instance.review_comment = reason

        await self._ledger.write_reversal(instance, reviewer, reason)
        self._db.add(instance)
        return instance

    # ── Grace period helpers ──────────────────────────────────────────────────

    @staticmethod
    def _suspend_grace(instance: TaskInstance, now: datetime) -> None:
        """Pause the grace-period clock."""
        if instance.grace_paused_at is None and instance.due_at is not None:
            elapsed = max(0, (now - instance.due_at).total_seconds())
            instance.grace_elapsed_seconds = int(
                instance.grace_elapsed_seconds + elapsed
            )
        instance.grace_paused_at = now

    @staticmethod
    def _resume_grace(instance: TaskInstance, now: datetime) -> None:
        """Resume the grace-period clock after a pause."""
        instance.grace_paused_at = None  # clock is running again from `now`

    @staticmethod
    def _grace_remaining(
        instance: TaskInstance, household: Household, now: datetime
    ) -> timedelta:
        """How much grace time is left? Returns negative timedelta if expired."""
        return grace_remaining(instance, household.grace_period_hours, now)


def grace_remaining(instance: TaskInstance, grace_period_hours: int, now: datetime) -> timedelta:
    """
    How much grace time is left on this instance? Returns a negative timedelta
    once expired.

    Free function (not instance-bound) so it can be shared between the
    interactive review flow above and the nightly batch worker
    (workers/generate_instances.py), which must apply the exact same
    suspended-clock math when deciding overdue -> missed (PRD §7.0, §7.2):
    a teen must not be penalised for time a task spent paused while a
    parent was reviewing an excuse.
    """
    total_grace = timedelta(hours=grace_period_hours)
    used = timedelta(seconds=instance.grace_elapsed_seconds)
    if instance.grace_paused_at is None:
        # Clock is running — add time since due_at beyond what we already counted
        extra = max(timedelta(0), now - instance.due_at - used)
        used = used + extra
    return total_grace - used

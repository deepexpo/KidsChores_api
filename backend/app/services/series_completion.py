"""
Series completion logic — PRD §8.3.

Rules:
- A series completes when every member task instance reaches `complete`.
- Excused tasks do NOT block series completion.
- A series with EVERY member task excused does NOT complete and pays nothing.
- Under all_or_nothing: payout = sum of completed (non-excused) task values + bonus.
- Under individual_plus_bonus: each task pays on completion; series bonus pays on full completion.
- A series bonus writes a single series_bonus ledger entry (idempotency-enforced at DB level).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    SeriesInstance,
    SeriesPayoutMode,
    SeriesStatus,
    TaskInstance,
    TaskStatus,
)
from app.services.ledger import LedgerService

if TYPE_CHECKING:
    from app.db.models import Member


class SeriesCompletionService:
    def __init__(self, db: AsyncSession, ledger: LedgerService):
        self._db = db
        self._ledger = ledger

    async def evaluate(
        self,
        series_instance: SeriesInstance,
        actor: Member,
    ) -> bool:
        """
        Check if the series can be completed. If so, write the appropriate
        ledger entries and mark the series_instance complete.
        Returns True if the series completed in this call.
        """
        if series_instance.status != SeriesStatus.active:
            return False

        result = await self._db.execute(
            select(TaskInstance).where(
                TaskInstance.series_instance_id == series_instance.id
            )
        )
        instances = list(result.scalars().all())

        if not instances:
            return False

        completed = [i for i in instances if i.status == TaskStatus.complete]
        excused = [i for i in instances if i.status == TaskStatus.excused]
        other = [
            i for i in instances
            if i.status not in (TaskStatus.complete, TaskStatus.excused)
        ]

        # Must have no outstanding non-terminal tasks
        if other:
            return False

        # All tasks excused → no completion, no payout
        if not completed and excused:
            return False

        series = series_instance.series
        from datetime import UTC, datetime
        now = datetime.now(UTC)

        if series.payout_mode == SeriesPayoutMode.individual_plus_bonus:
            # Individual tasks already paid on completion; just write the bonus
            await self._ledger.award_series_bonus(
                series_instance=series_instance,
                member_id=series.assignee_id,
                actor=actor,
                bonus_points=series.bonus_points,
            )
        else:
            # all_or_nothing: one entry covering completed task values + bonus
            completed_value = sum(i.point_value for i in completed)
            total = completed_value + series.bonus_points
            await self._ledger.award_all_or_nothing_series(
                series_instance=series_instance,
                member_id=series.assignee_id,
                actor=actor,
                total_points=total,
            )

        series_instance.status = SeriesStatus.complete
        series_instance.completed_at = now
        self._db.add(series_instance)
        return True

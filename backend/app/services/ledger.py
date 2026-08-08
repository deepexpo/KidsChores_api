"""
Ledger service — all writes to ledger_entries.

Rules (from PRD §8.1 and §8.2):
- Append-only. Never update or delete a ledger entry.
- A Task Instance may generate AT MOST ONE task_completed entry, ever.
  Enforced via DB partial unique index on (task_instance_id, entry_type).
- Reversals write a compensating entry; the original stays.
- balance_after is a denormalised convenience, not the source of truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ExcusedPayoutPolicy,
    LedgerEntry,
    LedgerEntryType,
    TaskInstance,
)

if TYPE_CHECKING:
    from app.db.models import Claim, Household, Member, SeriesInstance


class DuplicateLedgerEntryError(Exception):
    """Raised when a point award has already been written (idempotency guard)."""


class LedgerService:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def current_balance(self, member_id: str) -> int:
        """Source-of-truth balance: SUM(delta) for the member."""
        result = await self._db.execute(
            select(func.coalesce(func.sum(LedgerEntry.delta), 0)).where(
                LedgerEntry.member_id == member_id
            )
        )
        return int(result.scalar_one())

    async def award_task_completion(
        self,
        instance: TaskInstance,
        actor: Member,
        household: Household,
    ) -> LedgerEntry:
        """Write a task_completed entry. Idempotent via DB constraint."""
        balance = await self.current_balance(instance.assignee_id)
        new_balance = balance + instance.point_value
        entry = LedgerEntry(
            member_id=instance.assignee_id,
            delta=instance.point_value,
            balance_after=new_balance,
            entry_type=LedgerEntryType.task_completed,
            task_instance_id=instance.id,
            reason=f"Completed: {instance.definition.title}",
            created_by=actor.id,
        )
        return await self._write(entry)

    async def award_excuse_if_policy(
        self,
        instance: TaskInstance,
        actor: Member,
        household: Household,
    ) -> LedgerEntry | None:
        """Optionally award points for an excused task, per household policy."""
        policy = household.excused_payout_policy

        if policy == ExcusedPayoutPolicy.excused_pays_nothing:
            return None

        if policy == ExcusedPayoutPolicy.excused_pays_partial:
            delta = instance.point_value // 2
            entry_type = LedgerEntryType.excused_partial
            reason = f"Excused (50%): {instance.definition.title}"
        else:  # excused_pays_full
            delta = instance.point_value
            entry_type = LedgerEntryType.task_completed
            reason = f"Excused (full): {instance.definition.title}"

        balance = await self.current_balance(instance.assignee_id)
        entry = LedgerEntry(
            member_id=instance.assignee_id,
            delta=delta,
            balance_after=balance + delta,
            entry_type=entry_type,
            task_instance_id=instance.id,
            reason=reason,
            created_by=actor.id,
        )
        return await self._write(entry)

    async def award_series_bonus(
        self,
        series_instance: SeriesInstance,
        member_id: str,
        actor: Member,
        bonus_points: int,
    ) -> LedgerEntry:
        """Write a series_bonus entry. Idempotent via DB constraint."""
        balance = await self.current_balance(member_id)
        entry = LedgerEntry(
            member_id=member_id,
            delta=bonus_points,
            balance_after=balance + bonus_points,
            entry_type=LedgerEntryType.series_bonus,
            series_instance_id=series_instance.id,
            reason=f"Series bonus: {series_instance.series.name}",
            created_by=actor.id,
        )
        return await self._write(entry)

    async def award_all_or_nothing_series(
        self,
        series_instance: SeriesInstance,
        member_id: str,
        actor: Member,
        total_points: int,
    ) -> LedgerEntry:
        """
        For all_or_nothing series: write a single series_bonus entry
        covering the sum of completed task values + the bonus.
        """
        balance = await self.current_balance(member_id)
        entry = LedgerEntry(
            member_id=member_id,
            delta=total_points,
            balance_after=balance + total_points,
            entry_type=LedgerEntryType.series_bonus,
            series_instance_id=series_instance.id,
            reason=f"Series complete (all-or-nothing): {series_instance.series.name}",
            created_by=actor.id,
        )
        return await self._write(entry)

    async def fulfil_claim(
        self,
        claim: Claim,
        actor: Member,
    ) -> LedgerEntry:
        """Debit points when a parent marks a claim fulfilled."""
        balance = await self.current_balance(claim.member_id)
        entry = LedgerEntry(
            member_id=claim.member_id,
            delta=-claim.points,
            balance_after=balance - claim.points,
            entry_type=LedgerEntryType.claim_fulfilled,
            claim_id=claim.id,
            reason=f"Claim fulfilled: {claim.requested_item}",
            created_by=actor.id,
        )
        return await self._write(entry)

    async def manual_adjustment(
        self,
        member_id: str,
        delta: int,
        reason: str,
        actor: Member,
    ) -> LedgerEntry:
        """Parent manual point adjustment (positive or negative). Reason is required."""
        if not reason.strip():
            raise ValueError("A reason is required for manual adjustments.")
        balance = await self.current_balance(member_id)
        entry = LedgerEntry(
            member_id=member_id,
            delta=delta,
            balance_after=balance + delta,
            entry_type=LedgerEntryType.manual_adjustment,
            reason=reason,
            created_by=actor.id,
        )
        return await self._write(entry)

    async def write_reversal(
        self,
        instance: TaskInstance,
        actor: Member,
        reason: str,
    ) -> LedgerEntry:
        """
        Compensating entry when a parent reverses a completion.
        The original task_completed entry is NEVER deleted.
        """
        balance = await self.current_balance(instance.assignee_id)
        entry = LedgerEntry(
            member_id=instance.assignee_id,
            delta=-instance.point_value,
            balance_after=balance - instance.point_value,
            entry_type=LedgerEntryType.reversal,
            task_instance_id=instance.id,
            reason=f"Reversal: {reason}",
            created_by=actor.id,
        )
        return await self._write(entry)

    async def get_ledger(
        self, member_id: str, limit: int = 100, offset: int = 0
    ) -> list[LedgerEntry]:
        result = await self._db.execute(
            select(LedgerEntry)
            .where(LedgerEntry.member_id == member_id)
            .order_by(LedgerEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _write(self, entry: LedgerEntry) -> LedgerEntry:
        try:
            self._db.add(entry)
            await self._db.flush()  # Surface constraint violations before commit
            return entry
        except IntegrityError as exc:
            await self._db.rollback()
            raise DuplicateLedgerEntryError(
                f"A ledger entry of type '{entry.entry_type}' already exists "
                f"for this task/series instance. Points were already awarded."
            ) from exc

"""
SQLAlchemy models for KidsChores.

Design principles (from PRD):
- TaskDefinition and TaskInstance are SEPARATE — instances are historical facts
- Ledger is append-only; point_value frozen at instance creation
- All partial unique indexes enforced at DB level, not just application code
- All timestamps stored in UTC
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# ── Enums ─────────────────────────────────────────────────────────────────────


class MemberRole(enum.StrEnum):
    parent = "parent"
    teen = "teen"


class AuthProvider(enum.StrEnum):
    apple = "apple"
    email = "email"


class ExcusedPayoutPolicy(enum.StrEnum):
    excused_pays_nothing = "excused_pays_nothing"
    excused_pays_partial = "excused_pays_partial"
    excused_pays_full = "excused_pays_full"


class ScheduleType(enum.StrEnum):
    one_time = "one_time"
    daily = "daily"
    weekdays = "weekdays"    # specific weekdays via bitmask
    weekly = "weekly"         # any day within the week


class TaskStatus(enum.StrEnum):
    pending = "pending"
    review_pending = "review_pending"
    complete = "complete"
    overdue = "overdue"
    excuse_pending = "excuse_pending"
    excused = "excused"
    missed = "missed"
    cancelled = "cancelled"


class SeriesPayoutMode(enum.StrEnum):
    individual_plus_bonus = "individual_plus_bonus"
    all_or_nothing = "all_or_nothing"


class SeriesWindowType(enum.StrEnum):
    weekly = "weekly"
    monthly = "monthly"
    custom = "custom"


class SeriesStatus(enum.StrEnum):
    active = "active"
    complete = "complete"
    expired = "expired"


class LedgerEntryType(enum.StrEnum):
    task_completed = "task_completed"
    series_bonus = "series_bonus"
    excused_partial = "excused_partial"
    claim_fulfilled = "claim_fulfilled"
    manual_adjustment = "manual_adjustment"
    reversal = "reversal"


class ClaimStatus(enum.StrEnum):
    pending = "pending"
    fulfilled = "fulfilled"
    declined = "declined"


# ── Models ────────────────────────────────────────────────────────────────────


def _uuid() -> str:
    return str(uuid.uuid4())


class Household(Base):
    """Top-level tenant. All data lives within a household."""

    __tablename__ = "households"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    points_label: Mapped[str] = mapped_column(String(50), nullable=False, default="points")
    excused_payout_policy: Mapped[ExcusedPayoutPolicy] = mapped_column(
        Enum(ExcusedPayoutPolicy, name="excused_payout_policy"),
        nullable=False,
        default=ExcusedPayoutPolicy.excused_pays_nothing,
    )
    grace_period_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    # PRD §6.7 P0: "Teen: reminder at a configurable time before a task's due
    # time." Household-scoped, same pattern as grace_period_hours.
    task_reminder_minutes_before_due: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list["Member"]] = relationship(back_populates="household")
    task_definitions: Mapped[list["TaskDefinition"]] = relationship(back_populates="household")
    series: Mapped[list["Series"]] = relationship(back_populates="household")


class Member(Base):
    """A user within a household — either a parent (admin) or teen."""

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    household_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, name="member_role"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, name="auth_provider"), nullable=False
    )
    # Unique subject from the auth provider (Apple sub, or lowercased email)
    auth_subject: Mapped[str] = mapped_column(String(500), nullable=False)
    # Argon2id hash — only set for auth_provider == email
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # For shared-device PIN protection (teen profiles on family iPad)
    pin_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    birthdate: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    push_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft-delete only — see remove_member() in routers/household.py for why a
    # hard DELETE isn't viable: created_by/reviewed_by/resolved_by columns on
    # task_definitions/task_instances/ledger_entries/claims deliberately have no
    # ON DELETE CASCADE (cascading those would null out or destroy audit/ledger
    # history, which the ledger's append-only design forbids), so any member
    # with activity — including a teen completing their own tasks — trips a FK
    # violation on a real DELETE.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def pin_set(self) -> bool:
        """Not a mapped column — a computed convenience read by MemberResponse
        (docs/auth-endpoints.md §5) via Pydantic's from_attributes. Never expose
        pin_hash itself in an API response."""
        return self.pin_hash is not None

    household: Mapped["Household"] = relationship(back_populates="members")
    # TaskInstance/LedgerEntry each have a second FK to members (reviewed_by /
    # created_by) — foreign_keys must be explicit on both sides of these
    # relationships or SQLAlchemy can't resolve a unique join condition.
    task_instances: Mapped[list["TaskInstance"]] = relationship(
        back_populates="assignee", foreign_keys="TaskInstance.assignee_id"
    )
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="member", foreign_keys="LedgerEntry.member_id"
    )
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="member", foreign_keys="Claim.member_id"
    )
    savings_goals: Mapped[list["SavingsGoal"]] = relationship(back_populates="member")

    __table_args__ = (
        UniqueConstraint("household_id", "auth_subject", name="uq_member_auth"),
        # Global — an auth_subject (Apple sub, or email) must resolve to exactly one
        # member across the whole system, not just within one household. Sign-in/login
        # look up by (auth_provider, auth_subject) with no household filter, so without
        # this the same email/Apple-sub could get created under two different households.
        UniqueConstraint(
            "auth_provider", "auth_subject", name="uq_member_auth_provider_subject_global"
        ),
        Index("ix_member_household", "household_id"),
    )


class TaskDefinition(Base):
    """
    Reusable template for a task.
    Editing a definition affects future instances only — never historical ones.
    """

    __tablename__ = "task_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    household_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    assignee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    point_value: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_type: Mapped[ScheduleType] = mapped_column(
        Enum(ScheduleType, name="schedule_type"), nullable=False
    )
    # Bitmask for specific weekdays: bit 0=Mon … bit 6=Sun. Null unless weekdays.
    weekday_mask: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # HH:MM in 24h, evaluated in household timezone
    due_time: Mapped[str] = mapped_column(String(5), nullable=False, default="20:00")
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    series_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("series.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped["Household"] = relationship(back_populates="task_definitions")
    assignee: Mapped["Member"] = relationship(foreign_keys=[assignee_id])
    creator: Mapped["Member"] = relationship(foreign_keys=[created_by])
    instances: Mapped[list["TaskInstance"]] = relationship(back_populates="definition")
    series: Mapped["Series | None"] = relationship(back_populates="task_definitions")

    __table_args__ = (Index("ix_def_household", "household_id"),)


class TaskInstance(Base):
    """
    A single occurrence of a TaskDefinition on a specific date.
    This is an immutable historical record once created.
    point_value is FROZEN at generation time — changing the definition
    never alters existing instances.
    """

    __tablename__ = "task_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    definition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_definitions.id", ondelete="CASCADE"), nullable=False
    )
    assignee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    # Stored in UTC; all UI display in household timezone
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Frozen at generation — changing the definition never retroactively changes this
    point_value: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"),
        nullable=False,
        default=TaskStatus.pending,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    excuse_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    excuse_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When did the clock pause (for grace period suspension during review)?
    grace_paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Accumulated elapsed grace seconds before current pause
    grace_elapsed_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("members.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_instance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("series_instances.id", ondelete="SET NULL"), nullable=True
    )
    # Set once the due-soon reminder push has fired for this instance — the
    # reminder job runs on a short interval (workers/reminders.py) and must
    # not re-notify on every tick while an instance sits inside the lead window.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    definition: Mapped["TaskDefinition"] = relationship(back_populates="instances")
    assignee: Mapped["Member"] = relationship(
        back_populates="task_instances", foreign_keys=[assignee_id]
    )
    reviewer: Mapped["Member | None"] = relationship(foreign_keys=[reviewed_by])
    series_instance: Mapped["SeriesInstance | None"] = relationship(
        back_populates="task_instances"
    )

    __table_args__ = (
        # Idempotent generation: one instance per definition per due date
        UniqueConstraint("definition_id", "due_at", name="uq_instance_def_due"),
        Index("ix_instance_assignee_due", "assignee_id", "due_at"),
        Index("ix_instance_status", "status"),
    )


class Series(Base):
    """
    An ordered or unordered set of TaskDefinitions with a bonus payout
    on full completion within a time window.
    """

    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    household_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    assignee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    bonus_points: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_mode: Mapped[SeriesPayoutMode] = mapped_column(
        Enum(SeriesPayoutMode, name="series_payout_mode"), nullable=False
    )
    window_type: Mapped[SeriesWindowType] = mapped_column(
        Enum(SeriesWindowType, name="series_window_type"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped["Household"] = relationship(back_populates="series")
    assignee: Mapped["Member"] = relationship(foreign_keys=[assignee_id])
    task_definitions: Mapped[list["TaskDefinition"]] = relationship(back_populates="series")
    instances: Mapped[list["SeriesInstance"]] = relationship(back_populates="series")


class SeriesInstance(Base):
    """One window of a series (e.g. 'Weekend Reset — week of Jul 28')."""

    __tablename__ = "series_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    series_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("series.id", ondelete="CASCADE"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[SeriesStatus] = mapped_column(
        Enum(SeriesStatus, name="series_status"),
        nullable=False,
        default=SeriesStatus.active,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the "expiring soon with tasks outstanding" push has fired
    # (PRD §6.7 P1) — same single-fire-per-window guard as reminder_sent_at.
    expiring_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    series: Mapped["Series"] = relationship(back_populates="instances")
    task_instances: Mapped[list["TaskInstance"]] = relationship(back_populates="series_instance")

    __table_args__ = (
        UniqueConstraint("series_id", "window_start", name="uq_series_window"),
    )


class LedgerEntry(Base):
    """
    Immutable record of every point change.

    NEVER updated or deleted. Reversals write a compensating entry.
    balance_after is a denormalised convenience — the source of truth
    is SUM(delta) WHERE member_id = ?.

    Partial unique indexes (enforced at DB level):
      - UNIQUE (task_instance_id, entry_type) WHERE task_instance_id IS NOT NULL
      - UNIQUE (series_instance_id, entry_type) WHERE series_instance_id IS NOT NULL
    These prevent double-awarding points for the same event.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)  # signed; negative for debits
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        Enum(LedgerEntryType, name="ledger_entry_type"), nullable=False
    )
    task_instance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_instances.id"), nullable=True
    )
    series_instance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("series_instances.id"), nullable=True
    )
    claim_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("claims.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship(
        back_populates="ledger_entries", foreign_keys=[member_id]
    )
    task_instance: Mapped["TaskInstance | None"] = relationship(foreign_keys=[task_instance_id])
    series_instance: Mapped["SeriesInstance | None"] = relationship(
        foreign_keys=[series_instance_id]
    )
    claim: Mapped["Claim | None"] = relationship(foreign_keys=[claim_id])

    __table_args__ = (
        Index("ix_ledger_member_created", "member_id", "created_at"),
        # Partial unique indexes — see class docstring
        # Created via raw DDL in Alembic migration (SQLAlchemy doesn't model these directly)
    )


class Claim(Base):
    """A teen's request to convert points into a real-world reward."""

    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_item: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, name="claim_status"),
        nullable=False,
        default=ClaimStatus.pending,
    )
    parent_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("members.id"), nullable=True
    )

    member: Mapped["Member"] = relationship(
        back_populates="claims", foreign_keys=[member_id]
    )
    resolver: Mapped["Member | None"] = relationship(foreign_keys=[resolved_by])

    __table_args__ = (Index("ix_claim_member_status", "member_id", "status"),)


class SavingsGoal(Base):
    """P1: A teen names a target balance (e.g. 'AirPods — 2000 pts')."""

    __tablename__ = "savings_goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_points: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    member: Mapped["Member"] = relationship(back_populates="savings_goals")

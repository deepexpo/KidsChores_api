"""Pydantic v2 schemas for all API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator

from app.db.models import (
    ClaimStatus,
    ExcusedPayoutPolicy,
    LedgerEntryType,
    MemberRole,
    ScheduleType,
    SeriesPayoutMode,
    SeriesStatus,
    SeriesWindowType,
    TaskStatus,
)

# ── Auth ──────────────────────────────────────────────────────────────────────

class AppleSignInRequest(BaseModel):
    identity_token: str
    display_name: str = Field(min_length=1, max_length=100)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    member_id: str
    household_id: str
    role: MemberRole


class EmailRegisterRequest(BaseModel):
    """Self-registration — parent-only. Creates a new household (mirrors Apple sign-in)."""

    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=100)


class EmailLoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


# ── Household ─────────────────────────────────────────────────────────────────

class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", max_length=64)
    points_label: str = Field(default="points", max_length=50)


class HouseholdResponse(BaseModel):
    id: str
    name: str
    timezone: str
    points_label: str
    excused_payout_policy: ExcusedPayoutPolicy
    grace_period_hours: int
    created_at: datetime

    model_config = {"from_attributes": True}


class HouseholdUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
    points_label: str | None = None
    excused_payout_policy: ExcusedPayoutPolicy | None = None
    grace_period_hours: int | None = Field(None, ge=1, le=72)


# ── Member ────────────────────────────────────────────────────────────────────

class TeenProfileCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    birthdate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    pin: str | None = Field(None, min_length=4, max_length=4, pattern=r"^\d{4}$")


class MemberResponse(BaseModel):
    id: str
    household_id: str
    role: MemberRole
    display_name: str
    avatar: str | None
    birthdate: str | None
    pin_set: bool
    archived_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VerifyPinRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")


class VerifyPinResponse(BaseModel):
    valid: bool
    pin_set: bool


class LinkCodeResponse(BaseModel):
    code: str
    expires_at: datetime


class LinkAccountRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    email: EmailStr
    password: str = Field(min_length=8)


# ── TaskDefinition ────────────────────────────────────────────────────────────

class TaskDefinitionCreate(BaseModel):
    assignee_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    point_value: int = Field(ge=1, le=10_000)
    schedule_type: ScheduleType
    weekday_mask: int | None = Field(None, ge=0, le=127)  # 7 bits
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    due_time: str = Field(pattern=r"^\d{2}:\d{2}$", default="20:00")
    requires_review: bool = False
    series_id: str | None = None


class TaskDefinitionUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    point_value: int | None = Field(None, ge=1, le=10_000)
    schedule_type: ScheduleType | None = None
    weekday_mask: int | None = None
    end_date: str | None = None
    due_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    requires_review: bool | None = None


class TaskDefinitionResponse(BaseModel):
    id: str
    household_id: str
    assignee_id: str
    title: str
    description: str | None
    point_value: int
    schedule_type: ScheduleType
    weekday_mask: int | None
    start_date: str
    end_date: str | None
    due_time: str
    requires_review: bool
    series_id: str | None
    archived_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── TaskInstance ──────────────────────────────────────────────────────────────

class TaskInstanceResponse(BaseModel):
    id: str
    definition_id: str
    assignee_id: str
    due_at: datetime
    point_value: int
    status: TaskStatus
    completed_at: datetime | None
    completion_note: str | None
    excuse_text: str | None
    review_comment: str | None
    series_instance_id: str | None

    # Denormalised for convenience
    title: str | None = None
    description: str | None = None

    model_config = {"from_attributes": True}


class CompleteTaskRequest(BaseModel):
    idempotency_key: str
    note: str | None = None


class ExcuseTaskRequest(BaseModel):
    idempotency_key: str
    excuse_text: str = Field(min_length=10)


class ReviewRequest(BaseModel):
    idempotency_key: str
    approve: bool
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def comment_required_on_deny(cls, v: str | None, info: ValidationInfo) -> str | None:
        if not info.data.get("approve") and not v:
            raise ValueError("A comment is required when denying.")
        return v


class CancelTaskRequest(BaseModel):
    idempotency_key: str
    reason: str | None = None


class BulkApproveItem(BaseModel):
    task_instance_id: str
    approve: bool
    comment: str | None = None


class BulkApproveRequest(BaseModel):
    idempotency_key: str
    items: list[BulkApproveItem] = Field(min_length=1)


class BulkApproveResultItem(BaseModel):
    task_instance_id: str
    success: bool
    status: TaskStatus | None = None
    error: str | None = None


class BulkApproveResponse(BaseModel):
    results: list[BulkApproveResultItem]


# ── Series ────────────────────────────────────────────────────────────────────

class SeriesCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    assignee_id: str
    bonus_points: int = Field(ge=1)
    payout_mode: SeriesPayoutMode
    window_type: SeriesWindowType
    task_definition_ids: list[str] = Field(min_length=1)


class SeriesUpdate(BaseModel):
    """
    Deliberately excludes assignee_id and task_definition_ids (bundle
    membership) — reassigning a series or changing its member definitions is
    a more structural change than a simple field edit, matching how
    TaskDefinitionUpdate also excludes assignee_id/start_date/series_id.
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    bonus_points: int | None = Field(None, ge=1)
    payout_mode: SeriesPayoutMode | None = None
    window_type: SeriesWindowType | None = None


class SeriesResponse(BaseModel):
    id: str
    household_id: str
    name: str
    assignee_id: str
    bonus_points: int
    payout_mode: SeriesPayoutMode
    window_type: SeriesWindowType
    archived_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SeriesInstanceResponse(BaseModel):
    id: str
    series_id: str
    window_start: datetime
    window_end: datetime
    status: SeriesStatus
    completed_at: datetime | None
    progress: str | None = None  # e.g. "4 of 7 complete"

    model_config = {"from_attributes": True}


# ── Wallet / Ledger / Claims ───────────────────────────────────────────────────

class WalletResponse(BaseModel):
    member_id: str
    balance: int
    points_label: str
    active_savings_goal: SavingsGoalResponse | None = None


class LedgerEntryResponse(BaseModel):
    id: str
    delta: int
    balance_after: int
    entry_type: LedgerEntryType
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimCreate(BaseModel):
    points: int = Field(ge=1)
    requested_item: str = Field(min_length=1, max_length=500)


class ClaimResponse(BaseModel):
    id: str
    member_id: str
    member_name: str
    points: int
    requested_item: str
    status: ClaimStatus
    parent_note: str | None
    requested_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class ClaimResolveRequest(BaseModel):
    approve: bool
    parent_note: str | None = None


class ManualAdjustRequest(BaseModel):
    member_id: str
    delta: int
    reason: str = Field(min_length=5)


# ── Savings Goals (P1) ────────────────────────────────────────────────────────

class SavingsGoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    target_points: int = Field(ge=1)


class SavingsGoalResponse(BaseModel):
    id: str
    member_id: str
    title: str
    target_points: int
    created_at: datetime
    achieved_at: datetime | None

    model_config = {"from_attributes": True}


# ── Approvals (parent inbox) ──────────────────────────────────────────────────

class ApprovalItem(BaseModel):
    """A single item in the parent's approval inbox."""
    type: Literal["completion", "excuse"]
    task_instance_id: str
    task_title: str
    assignee_name: str
    point_value: int
    submitted_at: datetime
    excuse_text: str | None = None

    model_config = {"from_attributes": True}


# ── Reports ───────────────────────────────────────────────────────────────────

class ReportWeek(BaseModel):
    week_start: str  # YYYY-MM-DD, household-local Monday
    completed: int
    missed: int
    completion_rate: float | None  # null if no complete/missed tasks that week
    excuse_count: int
    points_earned: int


class ReportResponse(BaseModel):
    member_id: str
    weeks: list[ReportWeek]  # oldest first

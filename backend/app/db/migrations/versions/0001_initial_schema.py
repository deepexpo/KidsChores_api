"""Initial schema — all 9 tables + enums + partial unique indexes.

Revision ID: 0001
Revises:
Create Date: 2026-07-31

Mirrors app/db/models.py exactly. The two partial unique indexes on
ledger_entries (task_instance_id, entry_type) and (series_instance_id,
entry_type) are the DB-level idempotency backstop described in PRD §8.1 —
SQLAlchemy's ORM layer doesn't model partial indexes directly, so they're
created here via raw DDL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


member_role = postgresql.ENUM("parent", "teen", name="member_role")
auth_provider = postgresql.ENUM("apple", "email", name="auth_provider")
excused_payout_policy = postgresql.ENUM(
    "excused_pays_nothing", "excused_pays_partial", "excused_pays_full",
    name="excused_payout_policy",
)
schedule_type = postgresql.ENUM(
    "one_time", "daily", "weekdays", "weekly", name="schedule_type"
)
task_status = postgresql.ENUM(
    "pending", "review_pending", "complete", "overdue",
    "excuse_pending", "excused", "missed", "cancelled",
    name="task_status",
)
series_payout_mode = postgresql.ENUM(
    "individual_plus_bonus", "all_or_nothing", name="series_payout_mode"
)
series_window_type = postgresql.ENUM(
    "weekly", "monthly", "custom", name="series_window_type"
)
series_status = postgresql.ENUM(
    "active", "complete", "expired", name="series_status"
)
ledger_entry_type = postgresql.ENUM(
    "task_completed", "series_bonus", "excused_partial",
    "claim_fulfilled", "manual_adjustment", "reversal",
    name="ledger_entry_type",
)
claim_status = postgresql.ENUM(
    "pending", "fulfilled", "declined", name="claim_status"
)

ALL_ENUMS = [
    member_role,
    auth_provider,
    excused_payout_policy,
    schedule_type,
    task_status,
    series_payout_mode,
    series_window_type,
    series_status,
    ledger_entry_type,
    claim_status,
]

# Types are created/dropped explicitly below (checkfirst) — prevent
# SQLAlchemy from also trying to CREATE/DROP TYPE implicitly when the
# columns that reference these enums are created/dropped.
for _enum in ALL_ENUMS:
    _enum.create_type = False


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "households",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("points_label", sa.String(50), nullable=False, server_default="points"),
        sa.Column(
            "excused_payout_policy",
            excused_payout_policy,
            nullable=False,
            server_default="excused_pays_nothing",
        ),
        sa.Column("grace_period_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id",
            sa.String(36),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", member_role, nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("avatar", sa.String(500), nullable=True),
        sa.Column("auth_provider", auth_provider, nullable=False),
        sa.Column("auth_subject", sa.String(500), nullable=False),
        sa.Column("pin_hash", sa.String(200), nullable=True),
        sa.Column("birthdate", sa.String(10), nullable=True),
        sa.Column("push_token", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("household_id", "auth_subject", name="uq_member_auth"),
    )
    op.create_index("ix_member_household", "members", ["household_id"])

    op.create_table(
        "series",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id",
            sa.String(36),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "assignee_id",
            sa.String(36),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bonus_points", sa.Integer(), nullable=False),
        sa.Column("payout_mode", series_payout_mode, nullable=False),
        sa.Column("window_type", series_window_type, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "task_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id",
            sa.String(36),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assignee_id",
            sa.String(36),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("point_value", sa.Integer(), nullable=False),
        sa.Column("schedule_type", schedule_type, nullable=False),
        sa.Column("weekday_mask", sa.SmallInteger(), nullable=True),
        sa.Column("start_date", sa.String(10), nullable=False),
        sa.Column("end_date", sa.String(10), nullable=True),
        sa.Column("due_time", sa.String(5), nullable=False, server_default="20:00"),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "series_id",
            sa.String(36),
            sa.ForeignKey("series.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("members.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_def_household", "task_definitions", ["household_id"])

    op.create_table(
        "series_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "series_id",
            sa.String(36),
            sa.ForeignKey("series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", series_status, nullable=False, server_default="active"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("series_id", "window_start", name="uq_series_window"),
    )

    op.create_table(
        "task_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey("task_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assignee_id",
            sa.String(36),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("point_value", sa.Integer(), nullable=False),
        sa.Column("status", task_status, nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("excuse_text", sa.Text(), nullable=True),
        sa.Column("excuse_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_elapsed_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column(
            "series_instance_id",
            sa.String(36),
            sa.ForeignKey("series_instances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("definition_id", "due_at", name="uq_instance_def_due"),
    )
    op.create_index("ix_instance_assignee_due", "task_instances", ["assignee_id", "due_at"])
    op.create_index("ix_instance_status", "task_instances", ["status"])

    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "member_id",
            sa.String(36),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("requested_item", sa.Text(), nullable=False),
        sa.Column("status", claim_status, nullable=False, server_default="pending"),
        sa.Column("parent_note", sa.Text(), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(36), sa.ForeignKey("members.id"), nullable=True),
    )
    op.create_index("ix_claim_member_status", "claims", ["member_id", "status"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "member_id",
            sa.String(36),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("entry_type", ledger_entry_type, nullable=False),
        sa.Column(
            "task_instance_id", sa.String(36), sa.ForeignKey("task_instances.id"), nullable=True
        ),
        sa.Column(
            "series_instance_id", sa.String(36), sa.ForeignKey("series_instances.id"), nullable=True
        ),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("members.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ledger_member_created", "ledger_entries", ["member_id", "created_at"])
    # Partial unique indexes — the DB-level idempotency backstop (PRD §8.1, §8.3).
    op.create_index(
        "uq_ledger_task_instance_entry_type",
        "ledger_entries",
        ["task_instance_id", "entry_type"],
        unique=True,
        postgresql_where=sa.text("task_instance_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ledger_series_instance_entry_type",
        "ledger_entries",
        ["series_instance_id", "entry_type"],
        unique=True,
        postgresql_where=sa.text("series_instance_id IS NOT NULL"),
    )

    op.create_table(
        "savings_goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "member_id",
            sa.String(36),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("target_points", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("savings_goals")
    op.drop_index("uq_ledger_series_instance_entry_type", table_name="ledger_entries")
    op.drop_index("uq_ledger_task_instance_entry_type", table_name="ledger_entries")
    op.drop_index("ix_ledger_member_created", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_claim_member_status", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_instance_status", table_name="task_instances")
    op.drop_index("ix_instance_assignee_due", table_name="task_instances")
    op.drop_table("task_instances")
    op.drop_table("series_instances")
    op.drop_index("ix_def_household", table_name="task_definitions")
    op.drop_table("task_definitions")
    op.drop_table("series")
    op.drop_index("ix_member_household", table_name="members")
    op.drop_table("members")
    op.drop_table("households")

    bind = op.get_bind()
    for enum in reversed(ALL_ENUMS):
        enum.drop(bind, checkfirst=True)

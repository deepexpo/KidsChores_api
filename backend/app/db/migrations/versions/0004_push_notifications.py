"""Push notifications — reminder scheduling columns.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08

Adds the columns needed for the PRD §6.7 P0/P1 notification set:
- households.task_reminder_minutes_before_due: configurable lead time for the
  teen due-soon reminder (§6.7 P0), household-scoped like grace_period_hours.
- task_instances.reminder_sent_at / series_instances.expiring_reminder_sent_at:
  single-fire guards so the periodic reminder jobs (workers/reminders.py)
  don't re-notify on every tick while an instance/window sits inside its lead
  time. Member.push_token already existed (0001) but had no writer until now.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "households",
        sa.Column(
            "task_reminder_minutes_before_due",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    op.add_column(
        "task_instances",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "series_instances",
        sa.Column("expiring_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("series_instances", "expiring_reminder_sent_at")
    op.drop_column("task_instances", "reminder_sent_at")
    op.drop_column("households", "task_reminder_minutes_before_due")

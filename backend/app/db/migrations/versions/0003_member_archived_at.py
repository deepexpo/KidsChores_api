"""Member.archived_at — soft-delete for household members.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-02

DELETE /v1/household/members/{id} previously issued a hard DELETE, which fails
with a foreign-key violation for any member with activity: created_by,
reviewed_by, and resolved_by (on task_definitions, task_instances,
ledger_entries, claims) intentionally have no ON DELETE CASCADE, since
cascading those would null out or destroy ledger/audit history. Removing a
member is now a soft-delete (archived_at), matching the existing
TaskDefinition/Series archive pattern.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "archived_at")

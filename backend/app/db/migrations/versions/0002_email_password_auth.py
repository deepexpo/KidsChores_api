"""Email/password auth — password_hash column + global auth-subject uniqueness.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-01

Adds members.password_hash (argon2id, only set for auth_provider == email) and a
global UNIQUE(auth_provider, auth_subject) constraint. Sign-in/login for both Apple
and email providers looks a member up by (auth_provider, auth_subject) with no
household filter, so this closes a pre-existing gap where the same auth_subject
could otherwise be created under two different households.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("password_hash", sa.String(255), nullable=True))
    op.create_unique_constraint(
        "uq_member_auth_provider_subject_global",
        "members",
        ["auth_provider", "auth_subject"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_member_auth_provider_subject_global", "members", type_="unique"
    )
    op.drop_column("members", "password_hash")

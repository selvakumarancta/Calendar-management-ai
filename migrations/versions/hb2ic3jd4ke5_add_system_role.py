"""add system_role to users and timezone-aware event display

Revision ID: hb2ic3jd4ke5
Revises: ga1hb2ic3jd4
Create Date: 2026-05-09

Adds:
  - users.system_role  VARCHAR(20) DEFAULT 'user'  (RBAC: user|admin|superadmin)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "hb2ic3jd4ke5"
down_revision = "ga1hb2ic3jd4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add system_role to users table
    op.add_column(
        "users",
        sa.Column(
            "system_role",
            sa.String(length=20),
            nullable=False,
            server_default="user",
        ),
    )
    op.create_index("ix_users_system_role", "users", ["system_role"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_system_role", table_name="users")
    op.drop_column("users", "system_role")

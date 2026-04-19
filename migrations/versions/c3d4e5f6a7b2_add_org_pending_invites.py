"""add org_pending_invites table

Revision ID: c3d4e5f6a7b2
Revises: b2c3d4e5f6a1
Create Date: 2026-04-17 01:00:00.000000

Adds the pending-invite table for magic-link based org invitations:
  - Stores a one-time token sent to the invitee by email
  - Allows invited users to accept and activate their account without
    going through Google/Microsoft OAuth first
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b2"
down_revision = "b2c3d4e5f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_pending_invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("token", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_org_pending_invites_token"),
    )
    op.create_index("ix_org_pending_invites_org_id", "org_pending_invites", ["org_id"])
    op.create_index(
        "ix_org_pending_invites_user_id", "org_pending_invites", ["user_id"]
    )
    op.create_index("ix_org_pending_invites_token", "org_pending_invites", ["token"])


def downgrade() -> None:
    op.drop_index("ix_org_pending_invites_token", table_name="org_pending_invites")
    op.drop_index("ix_org_pending_invites_user_id", table_name="org_pending_invites")
    op.drop_index("ix_org_pending_invites_org_id", table_name="org_pending_invites")
    op.drop_table("org_pending_invites")

"""merge multiple heads

Revision ID: ga1hb2ic3jd4
Revises: a7b8c9d0e1f6, fb1c2d3e4f5a
Create Date: 2026-05-09

Merge migration to join the password_reset_tokens branch (a7b8c9d0e1f6)
and org_whatsapp_configs branch (fb1c2d3e4f5a) into a single head.
"""

from __future__ import annotations

from alembic import op

revision = "ga1hb2ic3jd4"
down_revision = ("a7b8c9d0e1f6", "fb1c2d3e4f5a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""add hashed_password to users

Revision ID: e5f6a7b8c9d4
Revises: d4e5f6a7b8c3
Create Date: 2026-04-17 03:00:00.000000

Adds the hashed_password column so users can authenticate with email + password
in addition to (or instead of) Google / Microsoft OAuth.
NULL = OAuth-only account (the default for all existing rows).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d4"
down_revision = "d4e5f6a7b8c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("hashed_password", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("hashed_password")

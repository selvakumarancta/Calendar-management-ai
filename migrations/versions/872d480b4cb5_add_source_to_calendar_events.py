"""add source to calendar_events

Revision ID: 872d480b4cb5
Revises: f6a7b8c9d0e5
Create Date: 2026-04-19 12:00:00.000000

Adds a source column to calendar_events to distinguish events created via
WhatsApp, Gmail, manual entry, etc.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "872d480b4cb5"
down_revision = "f6a7b8c9d0e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(30),
                nullable=False,
                server_default="manual",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.drop_column("source")

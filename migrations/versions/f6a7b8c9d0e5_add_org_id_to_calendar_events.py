"""add org_id to calendar_events

Revision ID: f6a7b8c9d0e5
Revises: e5f6a7b8c9d4
Create Date: 2026-04-17 03:30:00.000000

Adds a nullable org_id column to calendar_events so org-scoped events can be
queried separately from personal events.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e5"
down_revision = "e5f6a7b8c9d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_calendar_events_org_id", ["org_id"])


def downgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.drop_index("ix_calendar_events_org_id")
        batch_op.drop_column("org_id")

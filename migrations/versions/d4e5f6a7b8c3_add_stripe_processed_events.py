"""add stripe_processed_events table

Revision ID: d4e5f6a7b8c3
Revises: c3d4e5f6a7b2
Create Date: 2026-04-17 02:00:00.000000

Adds idempotency tracking for Stripe webhook events so retried deliveries
from Stripe don't cause duplicate plan changes or double-billing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c3"
down_revision = "c3d4e5f6a7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_processed_events",
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("stripe_processed_events")

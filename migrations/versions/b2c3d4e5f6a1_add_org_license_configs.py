"""add org_license_configs table

Revision ID: b2c3d4e5f6a1
Revises: f1a2b3c4d5e6
Create Date: 2026-04-17 00:00:00.000000

Adds per-organization license configuration:
  - seat_cost_cents: price per user seat (in cents)
  - max_seats: maximum licensed users per org
  - currency / billing_cycle: billing metadata
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a1"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_license_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("seat_cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_seats", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column(
            "billing_cycle", sa.String(10), nullable=False, server_default="monthly"
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_org_license_configs_org_id"),
    )
    op.create_index("ix_org_license_configs_org_id", "org_license_configs", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_org_license_configs_org_id", table_name="org_license_configs")
    op.drop_table("org_license_configs")

"""add microsoft tokens columns and audit log table

Revision ID: f1a2b3c4d5e6
Revises: e2a317726f0c
Create Date: 2026-04-17 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e2a317726f0c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Microsoft OAuth token columns on users table ---
    op.add_column(
        "users",
        sa.Column("microsoft_access_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("microsoft_refresh_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "microsoft_token_expiry",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # --- Audit log table (append-only) ---
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("target_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("action", sa.String(length=100), nullable=False, index=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_column("users", "microsoft_token_expiry")
    op.drop_column("users", "microsoft_refresh_token")
    op.drop_column("users", "microsoft_access_token")

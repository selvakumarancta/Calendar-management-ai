"""add org_whatsapp_configs table

Revision ID: f1a2b3c4d5e6
Revises: 872d480b4cb5
Create Date: 2026-04-20

Adds per-organization WhatsApp configuration, enabling Super Admin /
Org Owner to configure their own Meta Cloud API credentials.
Each org gets one row keyed by org_id.  The webhook router dispatches
incoming messages to the correct org based on the phone_number_id.
"""

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "872d480b4cb5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_whatsapp_configs",
        sa.Column("id", sa.CHAR(32), primary_key=True),
        sa.Column("org_id", sa.CHAR(32), nullable=False, unique=True),
        sa.Column("phone_number_id", sa.String(60), nullable=False, default=""),
        sa.Column("display_phone", sa.String(30), nullable=False, default=""),
        sa.Column("access_token", sa.Text, nullable=False, default=""),
        sa.Column(
            "verify_token",
            sa.String(255),
            nullable=False,
            default="calendar-agent-whatsapp",
        ),
        sa.Column("webhook_secret", sa.String(255), nullable=False, default=""),
        sa.Column("auto_reply", sa.Boolean, nullable=False, default=True),
        sa.Column("enabled", sa.Boolean, nullable=False, default=False),
        sa.Column("created_by", sa.CHAR(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_owc_phone_number_id", "org_whatsapp_configs", ["phone_number_id"]
    )
    op.create_index("idx_owc_org_id", "org_whatsapp_configs", ["org_id"])


def downgrade() -> None:
    op.drop_index("idx_owc_org_id", table_name="org_whatsapp_configs")
    op.drop_index("idx_owc_phone_number_id", table_name="org_whatsapp_configs")
    op.drop_table("org_whatsapp_configs")

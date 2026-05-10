"""
SQLAlchemy ORM models for multi-tenant organization tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class OrganizationModel(Base):
    """Organizations table — the tenant entity."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_members: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class OrgMembershipModel(Base):
    """Organization memberships — links users to orgs with roles."""

    __tablename__ = "org_memberships"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    invited_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProviderConnectionModel(Base):
    """Provider connections — OAuth links to Gmail, Outlook, etc."""

    __tablename__ = "provider_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # google | microsoft
    provider_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # OAuth tokens
    access_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Sync config
    calendar_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    email_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    webhook_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class OrgLicenseConfigModel(Base):
    """
    Per-organization license configuration — one row per org.

    Controls how many seats (user licenses) the org has purchased and the
    cost-per-seat used by the billing layer to compute invoices.
    """

    __tablename__ = "org_license_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, unique=True, index=True
    )
    seat_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_seats: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    billing_cycle: Mapped[str] = mapped_column(
        String(10), nullable=False, default="monthly"
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class OrgWhatsAppConfigModel(Base):
    """
    Per-organization WhatsApp configuration.

    Stored in DB (not .env) so Super Admin / Org Owner can manage
    credentials at runtime without server restarts.
    One row per organization. phone_number_id is indexed for fast
    webhook dispatch (Meta sends this in every payload).
    """

    __tablename__ = "org_whatsapp_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, unique=True, index=True
    )
    # Meta Cloud API identifiers
    phone_number_id: Mapped[str] = mapped_column(
        String(60), nullable=False, default="", index=True
    )
    display_phone: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    # Stored as plaintext here; wrap with encryption-at-rest (Fernet/KMS) in prod
    access_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verify_token: Mapped[str] = mapped_column(
        String(255), nullable=False, default="calendar-agent-whatsapp"
    )
    webhook_secret: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    auto_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

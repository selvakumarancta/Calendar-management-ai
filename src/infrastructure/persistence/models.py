"""
SQLAlchemy ORM models — database table definitions.
These map to domain entities but are infrastructure concerns.

Uses generic `sqlalchemy.Uuid` type so models work on both PostgreSQL and SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class UserModel(Base):
    """Users table — SaaS tenant."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(
        String(320), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    plan: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Google OAuth (encrypted at rest)
    google_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_token_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Microsoft OAuth (encrypted at rest)
    microsoft_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    microsoft_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    microsoft_token_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Stripe
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # User preferences & autopilot (added for gap implementation)
    autopilot_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    email_draft_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    scheduling_calendar_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    scheduling_guide_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    style_guide_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Email/password authentication (optional — users may sign in via OAuth only)
    # Salted + hashed with bcrypt. NULL means OAuth-only account.
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
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


class ConversationModel(Base):
    """Conversations table."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class MessageModel(Base):
    """Messages table — individual messages in conversations."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class UsageRecordModel(Base):
    """Usage tracking table — per-request metering for billing."""

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    estimated_cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class AuditLogModel(Base):
    """
    Immutable audit trail for privileged mutations.
    Written on every significant state change — org role changes, plan upgrades,
    autopilot toggles, account deletion attempts, etc.
    Records are NEVER updated or deleted (append-only).
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # The user who performed the action (None = system / background job)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    # The user/resource that was affected (may differ from actor in org admin ops)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    # Short machine-readable action name, e.g. "user.plan_upgraded"
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Human-readable description
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON snapshot of before/after state for forensics (optional)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Request correlation ID from X-Request-ID header
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Client IP address
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class OrgPendingInviteModel(Base):
    """
    Pending org membership invitations.

    Created when an OWNER/ADMIN invites a new user by email.
    Stores a one-time magic-link token so the invitee can accept without
    needing a Google/Microsoft OAuth session first.

    Cleared when:
      - The user clicks the accept link → membership activated, row deleted.
      - The invite expires (expires_at < now).
      - The admin revokes it explicitly.
    """

    __tablename__ = "org_pending_invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # The user row that was pre-created for the invitee
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    # Secure random token sent in the invite email link
    token: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Invite expiry — defaults to 7 days from creation
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class StripeProcessedEventModel(Base):
    """
    Idempotency table for Stripe webhook events.

    Stripe retries failed deliveries up to 3 days.  Recording the event ID on
    first receipt ensures that state mutations run exactly once even when a
    retry arrives.

    Rows are kept for 30 days then can be pruned by a maintenance job.
    """

    __tablename__ = "stripe_processed_events"

    # event.id is Stripe's globally unique identifier e.g. "evt_1Abc23..."
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class PasswordResetTokenModel(Base):
    """
    Single-use email password-reset tokens.

    Flow:
      POST /auth/forgot-password → creates a row (expires in 1h)
      POST /auth/reset-password  → marks ``used=True``, updates password

    Rows can be pruned after 90 days by the cleanup cron job.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

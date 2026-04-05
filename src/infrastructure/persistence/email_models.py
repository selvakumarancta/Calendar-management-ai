"""
SQLAlchemy ORM models for Email Intelligence tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class ScheduleSuggestionModel(Base):
    """Schedule suggestions derived from email analysis."""

    __tablename__ = "schedule_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # Source email
    email_provider_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    email_subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    email_sender: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    email_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Analysis
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="meeting_request"
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")

    # Proposed event
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    proposed_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    location: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    attendees_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Conflict info
    has_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conflict_details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    alternative_slots_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )

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
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EmailScanLogModel(Base):
    """Log of email scanning runs per user."""

    __tablename__ = "email_scan_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)

    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    emails_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actionable_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suggestions_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Track what was last scanned so we can resume
    last_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_message_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScannedEmailModel(Base):
    """Individual scanned emails — stores email details for browsing."""

    __tablename__ = "scanned_emails"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    provider_message_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    scan_log_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    sender_name: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    recipients_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    body_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    has_attachments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Analysis result
    is_actionable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    analysis_category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="non_actionable"
    )
    analysis_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    analysis_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# New tables added for gap implementation
# ---------------------------------------------------------------------------


class DraftReplyModel(Base):
    """Email draft replies created by the AI composer."""

    __tablename__ = "email_drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # Gmail references
    provider_draft_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    thread_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", index=True
    )
    original_email_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )

    # Recipients
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    cc_emails_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Draft content
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="text/plain"
    )

    # Proposed meeting windows stored as JSON list of {start, end, label}
    proposed_windows_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )

    # If a calendar invite should be sent after user confirms, store the pending
    # invite details here (JSON). Checked by InviteVerificationService.
    pending_invite_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scheduling link associated with this draft (if any)
    scheduling_link_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Whether the draft was sent by autopilot or user
    was_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_by_autopilot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ready", index=True
    )  # ready | sent | discarded | failed

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserGuideModel(Base):
    """AI-generated user preference guides (scheduling and style)."""

    __tablename__ = "user_guides"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # "scheduling_preferences" | "email_style"
    guide_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # The AI-generated guide text injected into prompts
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Metadata about how the guide was generated
    emails_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class SchedulingLinkModel(Base):
    """Self-serve scheduling links shared via email drafts."""

    __tablename__ = "scheduling_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    link_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="suggested")
    attendee_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Thread this link was created for
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # JSON list of {"start": ISO8601, "end": ISO8601}
    suggested_windows_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )

    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    booked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OnboardingStatusModel(Base):
    """Tracks the onboarding state per user."""

    __tablename__ = "onboarding_status"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, unique=True, index=True
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_started"
    )  # not_started | in_progress | completed | failed

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


class SchedulingAnalyticsModel(Base):
    """Analytics events for the scheduling pipeline."""

    __tablename__ = "scheduling_analytics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # draft_composed | draft_sent | invite_verified | invite_skipped | link_booked | autopilot_sent

    draft_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    was_autopilot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class PendingInviteModel(Base):
    """Pending calendar invites waiting for verification before being sent."""

    __tablename__ = "pending_invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    event_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attendees_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    location: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )  # pending | verified | skipped | expired

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

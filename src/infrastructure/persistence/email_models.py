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

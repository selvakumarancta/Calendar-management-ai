"""
CalendarEvent SQLAlchemy model — persistent storage for calendar events.
Replaces the in-memory dict so events survive server restarts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class CalendarEventModel(Base):
    """Calendar events table — stores events created/synced by the app."""

    __tablename__ = "calendar_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default="primary"
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)

    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed")

    # JSON serialized
    attendees_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reminders_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

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

    __table_args__ = (Index("ix_calendar_events_user_time", "user_id", "start_time"),)

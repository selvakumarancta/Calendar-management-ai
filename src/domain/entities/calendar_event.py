"""
CalendarEvent entity — represents a single calendar event.
Pure domain object, provider-agnostic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EventStatus(str, Enum):
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class RecurrenceFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


@dataclass
class Attendee:
    """An event attendee."""

    email: str
    name: str | None = None
    response_status: str = "needsAction"  # accepted, declined, tentative, needsAction
    is_organizer: bool = False


@dataclass
class Recurrence:
    """Recurrence rule for repeating events."""

    frequency: RecurrenceFrequency
    interval: int = 1
    count: int | None = None
    until: datetime | None = None


@dataclass
class Reminder:
    """Event reminder."""

    minutes_before: int = 15
    method: str = "popup"  # popup, email


@dataclass
class CalendarEvent:
    """Core calendar event entity — provider-agnostic."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    provider_event_id: str | None = None  # Google/Microsoft event ID
    user_id: uuid.UUID | None = None
    calendar_id: str = "primary"

    title: str = ""
    description: str | None = None
    location: str | None = None

    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_all_day: bool = False

    status: EventStatus = EventStatus.CONFIRMED
    attendees: list[Attendee] = field(default_factory=list)
    reminders: list[Reminder] = field(default_factory=list)
    recurrence: Recurrence | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def duration_minutes(self) -> int:
        """Event duration in minutes."""
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)

    def conflicts_with(self, other: CalendarEvent) -> bool:
        """Check if this event time-overlaps with another."""
        if (
            self.status == EventStatus.CANCELLED
            or other.status == EventStatus.CANCELLED
        ):
            return False
        return self.start_time < other.end_time and other.start_time < self.end_time

    def is_in_past(self) -> bool:
        """Check if event has already ended."""
        return self.end_time < datetime.now(timezone.utc)

    def add_attendee(self, email: str, name: str | None = None) -> None:
        """Add an attendee if not already present."""
        if not any(a.email == email for a in self.attendees):
            self.attendees.append(Attendee(email=email, name=name))
            self.updated_at = datetime.now(timezone.utc)

    def remove_attendee(self, email: str) -> None:
        """Remove an attendee by email."""
        self.attendees = [a for a in self.attendees if a.email != email]
        self.updated_at = datetime.now(timezone.utc)

    def reschedule(self, new_start: datetime, new_end: datetime) -> None:
        """Move the event to a new time slot."""
        self.start_time = new_start
        self.end_time = new_end
        self.updated_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        """Cancel the event."""
        self.status = EventStatus.CANCELLED
        self.updated_at = datetime.now(timezone.utc)

    def to_summary_string(self) -> str:
        """Compact string representation for LLM context (token-efficient)."""
        time_fmt = "%H:%M"
        date_fmt = "%Y-%m-%d"
        attendee_str = ",".join(a.email.split("@")[0] for a in self.attendees[:5])
        location_str = f" @{self.location}" if self.location else ""
        return (
            f"{self.start_time.strftime(date_fmt)} "
            f"{self.start_time.strftime(time_fmt)}-{self.end_time.strftime(time_fmt)} "
            f'"{self.title}"{location_str}'
            f"{f' [{attendee_str}]' if attendee_str else ''}"
        )

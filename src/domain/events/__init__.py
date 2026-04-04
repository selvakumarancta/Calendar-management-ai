"""
Domain events — emitted when significant state changes occur.
These decouple domain logic from side effects (notifications, logging, etc.).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class DomainEvent:
    """Base domain event."""

    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class EventCreated(DomainEvent):
    """Emitted when a calendar event is created."""

    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    calendar_event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = ""


@dataclass(frozen=True)
class EventUpdated(DomainEvent):
    """Emitted when a calendar event is modified."""

    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    calendar_event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    changes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventDeleted(DomainEvent):
    """Emitted when a calendar event is deleted."""

    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    calendar_event_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True)
class EventConflictDetected(DomainEvent):
    """Emitted when a scheduling conflict is found."""

    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    event_a_id: uuid.UUID = field(default_factory=uuid.uuid4)
    event_b_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True)
class UserQuotaExceeded(DomainEvent):
    """Emitted when a user exceeds their plan's request quota."""

    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan: str = ""
    current_usage: int = 0
    limit: int = 0


@dataclass(frozen=True)
class ConversationStarted(DomainEvent):
    """Emitted when a new conversation session begins."""

    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation_id: uuid.UUID = field(default_factory=uuid.uuid4)

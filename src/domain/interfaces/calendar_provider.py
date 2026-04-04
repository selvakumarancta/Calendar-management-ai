"""Port: Calendar Provider — abstract interface for any calendar API."""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from src.domain.entities.calendar_event import CalendarEvent
from src.domain.value_objects import TimeSlot


class CalendarProviderPort(abc.ABC):
    """Abstract calendar provider (Google, Microsoft, Apple, etc.)."""

    @abc.abstractmethod
    async def list_events(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """Fetch events within a date range."""
        ...

    @abc.abstractmethod
    async def get_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> CalendarEvent | None:
        """Get a single event by provider event ID."""
        ...

    @abc.abstractmethod
    async def create_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        """Create a new event on the calendar."""
        ...

    @abc.abstractmethod
    async def update_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        """Update an existing event."""
        ...

    @abc.abstractmethod
    async def delete_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        """Delete an event. Returns True on success."""
        ...

    @abc.abstractmethod
    async def find_free_slots(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
        calendar_id: str = "primary",
    ) -> list[TimeSlot]:
        """Find available time slots within a range."""
        ...

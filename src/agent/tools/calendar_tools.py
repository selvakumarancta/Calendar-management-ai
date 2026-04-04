"""
Calendar Tools — LangChain tools wrapping calendar service operations.
These are injected into the LangGraph agent for tool-calling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.tools import tool

from src.application.dto import CreateEventDTO, DateRangeDTO, UpdateEventDTO
from src.application.services.calendar_service import CalendarService


def create_calendar_tools(calendar_service: CalendarService) -> list[Any]:
    """Factory: create LangChain tools bound to a CalendarService instance."""

    @tool
    async def create_event(
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        attendee_emails: list[str] | None = None,
        user_id: str = "",
    ) -> str:
        """Create a new calendar event.

        Args:
            title: Event title/name.
            start_time: Start time in ISO 8601 format (e.g. 2026-03-30T09:00:00).
            end_time: End time in ISO 8601 format (e.g. 2026-03-30T10:00:00).
            description: Optional event description.
            location: Optional event location.
            attendee_emails: Optional list of attendee email addresses.
            user_id: The user ID (injected by agent).
        """
        dto = CreateEventDTO(
            title=title,
            start_time=datetime.fromisoformat(start_time),
            end_time=datetime.fromisoformat(end_time),
            description=description or None,
            location=location or None,
            attendee_emails=attendee_emails or [],
        )
        result = await calendar_service.create_event(UUID(user_id), dto)
        return f"✅ Created: \"{result.title}\" on {result.start_time.strftime('%b %d, %H:%M')}-{result.end_time.strftime('%H:%M')}"

    @tool
    async def list_events(
        start_date: str,
        end_date: str,
        user_id: str = "",
    ) -> str:
        """List calendar events in a date range.

        Args:
            start_date: Start of range in ISO 8601 format.
            end_date: End of range in ISO 8601 format.
            user_id: The user ID (injected by agent).
        """
        dto = DateRangeDTO(
            start=datetime.fromisoformat(start_date),
            end=datetime.fromisoformat(end_date),
        )
        events = await calendar_service.list_events(UUID(user_id), dto)
        if not events:
            return "No events found in this range."
        lines = []
        for e in events:
            lines.append(
                f"• {e.start_time.strftime('%b %d %H:%M')}-{e.end_time.strftime('%H:%M')} "
                f"\"{e.title}\"{f' @{e.location}' if e.location else ''}"
            )
        return "\n".join(lines)

    @tool
    async def update_event(
        event_id: str,
        title: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        location: str | None = None,
        user_id: str = "",
    ) -> str:
        """Update an existing calendar event.

        Args:
            event_id: The provider event ID to update.
            title: New title (optional).
            start_time: New start time in ISO 8601 (optional).
            end_time: New end time in ISO 8601 (optional).
            description: New description (optional).
            location: New location (optional).
            user_id: The user ID (injected by agent).
        """
        dto = UpdateEventDTO(
            event_id=event_id,
            title=title,
            description=description,
            location=location,
            start_time=datetime.fromisoformat(start_time) if start_time else None,
            end_time=datetime.fromisoformat(end_time) if end_time else None,
        )
        result = await calendar_service.update_event(UUID(user_id), dto)
        return f'✅ Updated: "{result.title}"'

    @tool
    async def delete_event(
        event_id: str,
        user_id: str = "",
    ) -> str:
        """Delete a calendar event.

        Args:
            event_id: The provider event ID to delete.
            user_id: The user ID (injected by agent).
        """
        success = await calendar_service.delete_event(UUID(user_id), event_id)
        return "✅ Event deleted." if success else "❌ Failed to delete event."

    @tool
    async def find_free_slots(
        start_date: str,
        end_date: str,
        duration_minutes: int = 30,
        user_id: str = "",
    ) -> str:
        """Find available free time slots in the calendar.

        Args:
            start_date: Start of search range in ISO 8601 format.
            end_date: End of search range in ISO 8601 format.
            duration_minutes: Minimum slot duration in minutes.
            user_id: The user ID (injected by agent).
        """
        slots = await calendar_service.find_free_slots(
            user_id=UUID(user_id),
            start=datetime.fromisoformat(start_date),
            end=datetime.fromisoformat(end_date),
            duration_minutes=duration_minutes,
        )
        if not slots:
            return "No free slots found in this range."
        lines = [
            f"• {s.start.strftime('%b %d %H:%M')}-{s.end.strftime('%H:%M')} ({s.duration_minutes}min)"
            for s in slots[:5]
        ]
        return "Available slots:\n" + "\n".join(lines)

    @tool
    async def check_conflicts(
        start_time: str,
        end_time: str,
        user_id: str = "",
    ) -> str:
        """Check if a proposed time slot conflicts with existing events.

        Args:
            start_time: Proposed start time in ISO 8601 format.
            end_time: Proposed end time in ISO 8601 format.
            user_id: The user ID (injected by agent).
        """
        conflicts = await calendar_service.check_conflicts(
            user_id=UUID(user_id),
            start=datetime.fromisoformat(start_time),
            end=datetime.fromisoformat(end_time),
        )
        if not conflicts:
            return "✅ No conflicts — time slot is free."
        lines = [
            f"⚠️ Conflicts with: \"{c.title}\" at {c.start_time.strftime('%H:%M')}"
            for c in conflicts
        ]
        return "\n".join(lines)

    return [
        create_event,
        list_events,
        update_event,
        delete_event,
        find_free_slots,
        check_conflicts,
    ]

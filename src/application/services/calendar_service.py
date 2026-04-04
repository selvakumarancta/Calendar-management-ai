"""
Calendar Service — application-level orchestration of calendar operations.
Coordinates between domain entities, calendar provider, and repository.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from src.application.dto import (
    CreateEventDTO,
    DateRangeDTO,
    EventResponseDTO,
    FreeSlotDTO,
    UpdateEventDTO,
)
from src.domain.entities.calendar_event import Attendee, CalendarEvent, Reminder
from src.domain.exceptions import (
    EventConflictError,
    EventInPastError,
    EventNotFoundError,
    InvalidTimeRangeError,
)
from src.domain.interfaces.cache import CachePort
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.interfaces.event_repository import EventRepositoryPort
from src.domain.value_objects import TimeSlot


class CalendarService:
    """Orchestrates calendar operations — the primary use case handler."""

    def __init__(
        self,
        calendar_provider: CalendarProviderPort,
        event_repository: EventRepositoryPort,
        cache: CachePort,
    ) -> None:
        self._provider = calendar_provider
        self._repo = event_repository
        self._cache = cache

    async def create_event(
        self, user_id: UUID, dto: CreateEventDTO
    ) -> EventResponseDTO:
        """Create a new calendar event with conflict detection."""
        # Validate time range
        if dto.start_time >= dto.end_time and not dto.is_all_day:
            raise InvalidTimeRangeError()

        if dto.start_time < datetime.now(dto.start_time.tzinfo):
            raise EventInPastError()

        # Check for conflicts
        existing = await self._provider.list_events(
            user_id=user_id,
            start=dto.start_time,
            end=dto.end_time,
            calendar_id=dto.calendar_id,
        )
        for event in existing:
            if event.start_time < dto.end_time and dto.start_time < event.end_time:
                raise EventConflictError(
                    f"Conflicts with '{event.title}' at {event.start_time.strftime('%H:%M')}"
                )

        # Build domain entity
        event = CalendarEvent(
            user_id=user_id,
            title=dto.title,
            description=dto.description,
            location=dto.location,
            start_time=dto.start_time,
            end_time=dto.end_time,
            is_all_day=dto.is_all_day,
            calendar_id=dto.calendar_id,
            attendees=[Attendee(email=e) for e in dto.attendee_emails],
            reminders=[Reminder(minutes_before=dto.reminder_minutes)],
        )

        # Create on provider (Google Calendar)
        created = await self._provider.create_event(user_id, event)

        # Persist locally
        await self._repo.create(created)

        # Invalidate cache
        await self._cache.delete(f"events:{user_id}:*")

        return self._to_response(created)

    async def update_event(
        self, user_id: UUID, dto: UpdateEventDTO
    ) -> EventResponseDTO:
        """Update an existing calendar event."""
        event = await self._provider.get_event(user_id, dto.event_id)
        if not event:
            raise EventNotFoundError(dto.event_id)

        # Apply updates
        if dto.title is not None:
            event.title = dto.title
        if dto.description is not None:
            event.description = dto.description
        if dto.location is not None:
            event.location = dto.location
        if dto.start_time is not None and dto.end_time is not None:
            event.reschedule(dto.start_time, dto.end_time)
        if dto.attendee_emails is not None:
            event.attendees = [Attendee(email=e) for e in dto.attendee_emails]

        updated = await self._provider.update_event(user_id, event)
        await self._repo.update(updated)
        await self._cache.delete(f"events:{user_id}:*")

        return self._to_response(updated)

    async def delete_event(self, user_id: UUID, event_id: str) -> bool:
        """Delete a calendar event."""
        success = await self._provider.delete_event(user_id, event_id)
        if success:
            await self._cache.delete(f"events:{user_id}:*")
        return success

    async def list_events(
        self, user_id: UUID, dto: DateRangeDTO
    ) -> list[EventResponseDTO]:
        """List events in a date range, with caching."""
        cache_key = f"events:{user_id}:{dto.start.isoformat()}:{dto.end.isoformat()}"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        events = await self._provider.list_events(
            user_id=user_id,
            start=dto.start,
            end=dto.end,
            calendar_id=dto.calendar_id,
        )
        result = [self._to_response(e) for e in events]
        await self._cache.set(cache_key, result, ttl_seconds=120)
        return result

    async def find_free_slots(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
    ) -> list[FreeSlotDTO]:
        """Find available time slots."""
        slots = await self._provider.find_free_slots(
            user_id=user_id,
            start=start,
            end=end,
            duration_minutes=duration_minutes,
        )
        return [
            FreeSlotDTO(
                start=s.start,
                end=s.end,
                duration_minutes=s.duration_minutes,
            )
            for s in slots
        ]

    async def check_conflicts(
        self, user_id: UUID, start: datetime, end: datetime
    ) -> list[EventResponseDTO]:
        """Return events that conflict with a proposed time."""
        events = await self._provider.list_events(user_id=user_id, start=start, end=end)
        conflicts = [e for e in events if e.start_time < end and start < e.end_time]
        return [self._to_response(e) for e in conflicts]

    @staticmethod
    def _to_response(event: CalendarEvent) -> EventResponseDTO:
        return EventResponseDTO(
            id=str(event.id),
            provider_event_id=event.provider_event_id,
            title=event.title,
            description=event.description,
            location=event.location,
            start_time=event.start_time,
            end_time=event.end_time,
            is_all_day=event.is_all_day,
            status=event.status.value,
            attendees=[a.email for a in event.attendees],
            duration_minutes=event.duration_minutes,
        )

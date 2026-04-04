"""Port: Event Repository — abstract persistence for CalendarEvent entities."""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from src.domain.entities.calendar_event import CalendarEvent


class EventRepositoryPort(abc.ABC):
    """Abstract event persistence interface."""

    @abc.abstractmethod
    async def get_by_id(self, event_id: UUID) -> CalendarEvent | None: ...

    @abc.abstractmethod
    async def get_by_provider_id(
        self, provider_event_id: str, user_id: UUID
    ) -> CalendarEvent | None: ...

    @abc.abstractmethod
    async def list_by_user(
        self,
        user_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]: ...

    @abc.abstractmethod
    async def create(self, event: CalendarEvent) -> CalendarEvent: ...

    @abc.abstractmethod
    async def update(self, event: CalendarEvent) -> CalendarEvent: ...

    @abc.abstractmethod
    async def delete(self, event_id: UUID) -> bool: ...

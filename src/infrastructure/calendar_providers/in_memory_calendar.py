"""
DB-backed Calendar Adapter — persistent replacement for pure in-memory store.
Implements both CalendarProviderPort and EventRepositoryPort using SQLAlchemy.
Falls back to an in-memory dict when no DB session factory is provided.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from src.domain.entities.calendar_event import (
    Attendee,
    CalendarEvent,
    EventStatus,
    Reminder,
)
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.interfaces.event_repository import EventRepositoryPort
from src.domain.value_objects import TimeSlot

logger = logging.getLogger("calendar_agent")


class InMemoryCalendarAdapter(CalendarProviderPort, EventRepositoryPort):
    """
    DB-backed calendar store with in-memory fallback.
    Events are persisted to the calendar_events table when a DB session
    factory is available.
    """

    def __init__(self) -> None:
        self._events: dict[UUID, CalendarEvent] = {}
        self._db_session_factory = None

    def set_db_session_factory(self, factory: object) -> None:
        """Set the async session factory for DB persistence."""
        self._db_session_factory = factory

    # ---- DB Conversion helpers ----------------------------------------

    @staticmethod
    def _model_to_entity(row) -> CalendarEvent:  # type: ignore[no-untyped-def]
        """Convert a CalendarEventModel row to a CalendarEvent domain entity."""
        attendees = []
        try:
            for a in json.loads(row.attendees_json or "[]"):
                if isinstance(a, dict):
                    attendees.append(
                        Attendee(email=a.get("email", ""), name=a.get("name"))
                    )
                elif isinstance(a, str):
                    attendees.append(Attendee(email=a))
        except (json.JSONDecodeError, TypeError):
            pass

        reminders = []
        try:
            for r in json.loads(row.reminders_json or "[]"):
                if isinstance(r, dict):
                    reminders.append(
                        Reminder(minutes_before=r.get("minutes_before", 15))
                    )
        except (json.JSONDecodeError, TypeError):
            pass

        return CalendarEvent(
            id=row.id if isinstance(row.id, uuid.UUID) else uuid.UUID(str(row.id)),
            provider_event_id=row.provider_event_id,
            user_id=(
                row.user_id
                if isinstance(row.user_id, uuid.UUID)
                else uuid.UUID(str(row.user_id))
            ),
            calendar_id=row.calendar_id or "primary",
            title=row.title or "",
            description=row.description,
            location=row.location,
            start_time=row.start_time,
            end_time=row.end_time,
            is_all_day=row.is_all_day or False,
            status=EventStatus(row.status) if row.status else EventStatus.CONFIRMED,
            attendees=attendees,
            reminders=reminders,
            created_at=row.created_at or datetime.now(tz=UTC),
            updated_at=row.updated_at or datetime.now(tz=UTC),
        )

    @staticmethod
    def _entity_to_model_dict(event: CalendarEvent) -> dict:
        """Convert a CalendarEvent to a dict suitable for CalendarEventModel."""
        attendees = [{"email": a.email, "name": a.name} for a in event.attendees]
        reminders = [
            {"minutes_before": r.minutes_before, "method": r.method}
            for r in event.reminders
        ]
        return {
            "id": event.id,
            "user_id": event.user_id,
            "provider_event_id": event.provider_event_id,
            "calendar_id": event.calendar_id,
            "title": event.title,
            "description": event.description,
            "location": event.location,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "is_all_day": event.is_all_day,
            "status": (
                event.status.value
                if isinstance(event.status, EventStatus)
                else event.status
            ),
            "attendees_json": json.dumps(attendees),
            "reminders_json": json.dumps(reminders),
        }

    # ---- DB persistence layer -----------------------------------------

    async def _db_save(self, event: CalendarEvent) -> None:
        """Save an event to the database (upsert)."""
        if not self._db_session_factory:
            return
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.calendar_event_model import (
                CalendarEventModel,
            )

            async with self._db_session_factory() as session:
                existing = await session.execute(
                    select(CalendarEventModel).where(CalendarEventModel.id == event.id)
                )
                row = existing.scalar_one_or_none()
                data = self._entity_to_model_dict(event)
                if row:
                    for k, v in data.items():
                        if k != "id":
                            setattr(row, k, v)
                    row.updated_at = datetime.now(tz=UTC)
                else:
                    session.add(CalendarEventModel(**data))
                await session.commit()
        except Exception as e:
            logger.warning("Failed to save event to DB: %s", e)

    async def _db_delete(self, event_id: UUID) -> bool:
        """Delete an event from the database."""
        if not self._db_session_factory:
            return False
        try:
            from sqlalchemy import delete

            from src.infrastructure.persistence.calendar_event_model import (
                CalendarEventModel,
            )

            async with self._db_session_factory() as session:
                result = await session.execute(
                    delete(CalendarEventModel).where(CalendarEventModel.id == event_id)
                )
                await session.commit()
                return result.rowcount > 0  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("Failed to delete event from DB: %s", e)
            return False

    async def _db_list(
        self,
        user_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 50,
        exclude_cancelled: bool = True,
    ) -> list[CalendarEvent]:
        """List events from the database."""
        if not self._db_session_factory:
            return []
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.calendar_event_model import (
                CalendarEventModel,
            )

            async with self._db_session_factory() as session:
                q = select(CalendarEventModel).where(
                    CalendarEventModel.user_id == user_id,
                )
                if start:
                    q = q.where(CalendarEventModel.end_time > start)
                if end:
                    q = q.where(CalendarEventModel.start_time < end)
                if exclude_cancelled:
                    q = q.where(CalendarEventModel.status != "cancelled")
                q = q.order_by(CalendarEventModel.start_time).limit(max_results)
                result = await session.execute(q)
                return [self._model_to_entity(r) for r in result.scalars().all()]
        except Exception as e:
            logger.warning("Failed to list events from DB: %s", e)
            return []

    async def _db_get(self, event_id: UUID) -> CalendarEvent | None:
        """Get a single event from the database."""
        if not self._db_session_factory:
            return None
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.calendar_event_model import (
                CalendarEventModel,
            )

            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(CalendarEventModel).where(CalendarEventModel.id == event_id)
                )
                row = result.scalar_one_or_none()
                return self._model_to_entity(row) if row else None
        except Exception as e:
            logger.warning("Failed to get event from DB: %s", e)
            return None

    # ---- CalendarProviderPort -----------------------------------------

    async def list_events(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        # Try DB first
        db_events = await self._db_list(user_id, start, end, max_results)
        if db_events:
            return db_events

        # Fallback to in-memory
        results = [
            e
            for e in self._events.values()
            if e.user_id == user_id
            and e.status != EventStatus.CANCELLED
            and e.start_time < end
            and e.end_time > start
        ]
        results.sort(key=lambda e: e.start_time)
        return results[:max_results]

    async def get_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> CalendarEvent | None:
        # Try DB
        try:
            uid = uuid.UUID(event_id)
            db_event = await self._db_get(uid)
            if db_event and db_event.user_id == user_id:
                return db_event
        except ValueError:
            pass

        # Fallback to in-memory
        for e in self._events.values():
            if e.user_id == user_id and (
                str(e.id) == event_id or e.provider_event_id == event_id
            ):
                return e
        return None

    async def create_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        event.user_id = user_id
        if not event.provider_event_id:
            event.provider_event_id = f"mem-{uuid.uuid4().hex[:12]}"
        self._events[event.id] = event
        await self._db_save(event)
        return event

    async def update_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        event.updated_at = datetime.now(tz=UTC)
        self._events[event.id] = event
        await self._db_save(event)
        return event

    async def delete_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        # Remove from in-memory
        for eid, e in list(self._events.items()):
            if e.user_id == user_id and (
                str(e.id) == event_id or e.provider_event_id == event_id
            ):
                del self._events[eid]
                return True

        # Try DB by UUID
        try:
            uid = uuid.UUID(event_id)
            return await self._db_delete(uid)
        except ValueError:
            pass
        return False

    async def find_free_slots(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
        calendar_id: str = "primary",
    ) -> list[TimeSlot]:
        busy = await self.list_events(user_id, start, end, calendar_id)
        busy.sort(key=lambda e: e.start_time)

        free: list[TimeSlot] = []
        cursor = start
        for ev in busy:
            if (ev.start_time - cursor).total_seconds() >= duration_minutes * 60:
                free.append(TimeSlot(start=cursor, end=ev.start_time))
            cursor = max(cursor, ev.end_time)
        if (end - cursor).total_seconds() >= duration_minutes * 60:
            free.append(TimeSlot(start=cursor, end=end))
        return free

    # ---- EventRepositoryPort ------------------------------------------

    async def get_by_id(self, event_id: UUID) -> CalendarEvent | None:
        db_event = await self._db_get(event_id)
        if db_event:
            return db_event
        return self._events.get(event_id)

    async def get_by_provider_id(
        self, provider_event_id: str, user_id: UUID
    ) -> CalendarEvent | None:
        # Try DB
        if self._db_session_factory:
            try:
                from sqlalchemy import select

                from src.infrastructure.persistence.calendar_event_model import (
                    CalendarEventModel,
                )

                async with self._db_session_factory() as session:
                    result = await session.execute(
                        select(CalendarEventModel).where(
                            CalendarEventModel.provider_event_id == provider_event_id,
                            CalendarEventModel.user_id == user_id,
                        )
                    )
                    row = result.scalar_one_or_none()
                    if row:
                        return self._model_to_entity(row)
            except Exception:
                pass

        for e in self._events.values():
            if e.provider_event_id == provider_event_id and e.user_id == user_id:
                return e
        return None

    async def list_by_user(
        self,
        user_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        db_events = await self._db_list(
            user_id, start, end, limit, exclude_cancelled=False
        )
        if db_events:
            return db_events

        results = [e for e in self._events.values() if e.user_id == user_id]
        if start:
            results = [e for e in results if e.end_time > start]
        if end:
            results = [e for e in results if e.start_time < end]
        results.sort(key=lambda e: e.start_time)
        return results[:limit]

    async def create(self, event: CalendarEvent) -> CalendarEvent:
        self._events[event.id] = event
        await self._db_save(event)
        return event

    async def update(self, event: CalendarEvent) -> CalendarEvent:
        event.updated_at = datetime.now(tz=UTC)
        self._events[event.id] = event
        await self._db_save(event)
        return event

    async def delete(self, event_id: UUID) -> bool:
        deleted_mem = self._events.pop(event_id, None) is not None
        deleted_db = await self._db_delete(event_id)
        return deleted_mem or deleted_db

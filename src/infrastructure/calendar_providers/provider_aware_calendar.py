"""
Provider-Aware Calendar Adapter — fetches real events from Google/Microsoft
when real OAuth tokens exist in provider_connections, otherwise falls back
to the in-memory store for dev/demo usage.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from uuid import UUID

from src.domain.entities.calendar_event import CalendarEvent
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.interfaces.event_repository import EventRepositoryPort
from src.domain.value_objects import TimeSlot
from src.infrastructure.calendar_providers.in_memory_calendar import (
    InMemoryCalendarAdapter,
)

logger = logging.getLogger("calendar_agent")


class ProviderAwareCalendarAdapter(CalendarProviderPort, EventRepositoryPort):
    """
    Hybrid calendar adapter:
    - When a user has a real Google/Microsoft OAuth connection in the DB
      (access_token != 'dev-token'), delegates to the real API adapter.
    - Otherwise, falls back to an in-memory store.

    Token lookup is done per-request via the _get_provider_tokens callback,
    which is wired at construction time to query provider_connections.
    """

    def __init__(
        self,
        google_client_id: str = "",
        google_client_secret: str = "",
    ) -> None:
        self._google_client_id = google_client_id
        self._google_client_secret = google_client_secret
        self._in_memory = InMemoryCalendarAdapter()
        # Lazily-loaded DB session factory (set by container)
        self._db_session_factory = None

    def set_db_session_factory(self, factory: object) -> None:
        """Set the async session factory for DB token lookups."""
        self._db_session_factory = factory
        # Also give the in-memory adapter DB access for persistence
        self._in_memory.set_db_session_factory(factory)

    async def _get_google_tokens(self, user_id: UUID) -> dict | None:
        """Look up real Google tokens from provider_connections for this user."""
        if not self._db_session_factory:
            return None
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.org_models import (
                ProviderConnectionModel,
            )

            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(ProviderConnectionModel).where(
                        ProviderConnectionModel.user_id == user_id,
                        ProviderConnectionModel.provider == "google",
                        ProviderConnectionModel.status == "active",
                        ProviderConnectionModel.access_token != "dev-token",
                    )
                )
                rows = result.scalars().all()
                for row in rows:
                    if row.access_token and row.access_token != "dev-token":
                        from src.infrastructure.security.token_encryption import (
                            decrypt_token,
                        )

                        return {
                            "access_token": decrypt_token(row.access_token),
                            "refresh_token": decrypt_token(row.refresh_token or ""),
                            "provider_email": row.provider_email,
                        }
        except Exception as e:
            logger.warning("Failed to look up Google tokens: %s", e)
        return None

    def _build_google_service(self, tokens: dict):  # type: ignore[no-untyped-def]
        """Build an authorized Google Calendar API service from tokens."""
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(
            token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._google_client_id,
            client_secret=self._google_client_secret,
        )
        return build("calendar", "v3", credentials=credentials)

    # ---- CalendarProviderPort -----------------------------------------

    async def list_events(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """List events — try real provider first, fall back to in-memory."""
        tokens = await self._get_google_tokens(user_id)
        if tokens:
            try:
                return await self._list_google_events(
                    tokens, user_id, start, end, calendar_id, max_results
                )
            except Exception as e:
                logger.warning(
                    "Google Calendar API failed, falling back to in-memory: %s", e
                )

        # Fallback: in-memory store
        return await self._in_memory.list_events(
            user_id, start, end, calendar_id, max_results
        )

    async def _list_google_events(
        self,
        tokens: dict,
        user_id: UUID,
        start: datetime,
        end: datetime,
        calendar_id: str,
        max_results: int,
    ) -> list[CalendarEvent]:
        """Fetch events from Google Calendar API."""
        from src.infrastructure.calendar_providers.google_calendar import (
            GoogleCalendarAdapter,
        )

        service = self._build_google_service(tokens)

        # Ensure timezone-aware ISO format
        start_iso = start.isoformat()
        end_iso = end.isoformat()
        if "T" not in start_iso:
            start_iso += "T00:00:00Z"
        if "T" not in end_iso:
            end_iso += "T23:59:59Z"
        if not start_iso.endswith("Z") and "+" not in start_iso:
            start_iso += "Z"
        if not end_iso.endswith("Z") and "+" not in end_iso:
            end_iso += "Z"

        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=start_iso,
                timeMax=end_iso,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = []
        for item in result.get("items", []):
            try:
                events.append(GoogleCalendarAdapter._parse_event(item, user_id))
            except Exception as e:
                logger.warning("Failed to parse event %s: %s", item.get("id"), e)
        return events

    async def get_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> CalendarEvent | None:
        tokens = await self._get_google_tokens(user_id)
        if tokens:
            try:
                service = self._build_google_service(tokens)
                result = (
                    service.events()
                    .get(calendarId=calendar_id, eventId=event_id)
                    .execute()
                )
                from src.infrastructure.calendar_providers.google_calendar import (
                    GoogleCalendarAdapter,
                )

                return GoogleCalendarAdapter._parse_event(result, user_id)
            except Exception:
                pass
        return await self._in_memory.get_event(user_id, event_id, calendar_id)

    async def create_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        tokens = await self._get_google_tokens(user_id)
        if tokens:
            try:
                from src.infrastructure.calendar_providers.google_calendar import (
                    GoogleCalendarAdapter,
                )

                service = self._build_google_service(tokens)
                body = GoogleCalendarAdapter._to_google_event(event)
                result = (
                    service.events()
                    .insert(calendarId=event.calendar_id or "primary", body=body)
                    .execute()
                )
                event.provider_event_id = result["id"]
                return event
            except Exception as e:
                logger.warning("Failed to create Google event, using in-memory: %s", e)
        return await self._in_memory.create_event(user_id, event)

    async def update_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        return await self._in_memory.update_event(user_id, event)

    async def delete_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        return await self._in_memory.delete_event(user_id, event_id, calendar_id)

    async def find_free_slots(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
        calendar_id: str = "primary",
    ) -> list[TimeSlot]:
        events = await self.list_events(user_id, start, end, calendar_id)
        events.sort(key=lambda e: e.start_time)

        free: list[TimeSlot] = []
        cursor = start
        for ev in events:
            if (ev.start_time - cursor).total_seconds() >= duration_minutes * 60:
                free.append(TimeSlot(start=cursor, end=ev.start_time))
            cursor = max(cursor, ev.end_time)
        if (end - cursor).total_seconds() >= duration_minutes * 60:
            free.append(TimeSlot(start=cursor, end=end))
        return free

    # ---- EventRepositoryPort (delegates to in-memory) ------------------

    async def get_by_id(self, event_id: uuid.UUID) -> CalendarEvent | None:
        return await self._in_memory.get_by_id(event_id)

    async def get_by_provider_id(
        self, provider_event_id: str, user_id: UUID
    ) -> CalendarEvent | None:
        return await self._in_memory.get_by_provider_id(provider_event_id, user_id)

    async def list_by_user(
        self,
        user_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        return await self._in_memory.list_by_user(user_id, start, end, limit)

    async def create(self, event: CalendarEvent) -> CalendarEvent:
        return await self._in_memory.create(event)

    async def update(self, event: CalendarEvent) -> CalendarEvent:
        return await self._in_memory.update(event)

    async def delete(self, event_id: uuid.UUID) -> bool:
        return await self._in_memory.delete(event_id)

    # ---- Scheduling calendar helpers -----------------------------------

    async def get_or_create_scheduling_calendar(
        self,
        user_id: UUID,
        calendar_name: str = "CalendarAgent",
    ) -> str:
        """
        Return the Google Calendar ID for the dedicated scheduling calendar.

        If a calendar named ``calendar_name`` already exists it is reused.
        Otherwise a new secondary calendar is created on behalf of the user.
        Falls back to "primary" when no real Google tokens are available.
        """
        tokens = await self._get_google_tokens(user_id)
        if not tokens:
            return "primary"

        try:
            service = self._build_google_service(tokens)

            # 1. Check if the calendar already exists
            cal_list = service.calendarList().list().execute()
            for entry in cal_list.get("items", []):
                if entry.get("summary") == calendar_name:
                    return entry["id"]

            # 2. Create a new secondary calendar
            new_cal = (
                service.calendars()
                .insert(body={"summary": calendar_name, "description": "Managed by CalendarAgent"})
                .execute()
            )
            cal_id: str = new_cal["id"]
            logger.info(
                "Created dedicated scheduling calendar '%s' (id=%s) for user %s",
                calendar_name,
                cal_id,
                user_id,
            )
            return cal_id

        except Exception as e:
            logger.warning(
                "Could not get/create scheduling calendar for user %s: %s — using primary",
                user_id,
                e,
            )
            return "primary"

    async def persist_scheduling_calendar_id(
        self, user_id: UUID, calendar_id: str
    ) -> None:
        """Persists the scheduling_calendar_id to the user record in DB."""
        if not self._db_session_factory:
            return
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.models import UserModel

            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(UserModel).where(UserModel.id == user_id)
                )
                user_row = result.scalars().first()
                if user_row:
                    user_row.scheduling_calendar_id = calendar_id
                    await session.commit()
        except Exception as e:
            logger.warning(
                "Could not persist scheduling_calendar_id for user %s: %s", user_id, e
            )

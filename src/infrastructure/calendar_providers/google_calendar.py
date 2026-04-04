"""
Google Calendar Adapter — implements CalendarProviderPort for Google Calendar API.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.domain.entities.calendar_event import (
    Attendee,
    CalendarEvent,
    EventStatus,
)
from src.domain.exceptions import CalendarProviderError
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.interfaces.user_repository import UserRepositoryPort
from src.domain.value_objects import TimeSlot


class GoogleCalendarAdapter(CalendarProviderPort):
    """Concrete adapter for Google Calendar API v3."""

    def __init__(
        self,
        user_repository: UserRepositoryPort,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._user_repo = user_repository
        self._client_id = client_id
        self._client_secret = client_secret

    async def _get_service(self, user_id: uuid.UUID) -> Any:
        """Build an authorized Google Calendar service for a user."""
        user = await self._user_repo.get_by_id(user_id)
        if not user or not user.google_access_token:
            raise CalendarProviderError("Google", "No valid credentials for user")

        credentials = Credentials(
            token=user.google_access_token,
            refresh_token=user.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return build("calendar", "v3", credentials=credentials)

    async def list_events(
        self,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        try:
            service = await self._get_service(user_id)
            result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return [self._parse_event(e, user_id) for e in result.get("items", [])]
        except Exception as e:
            raise CalendarProviderError("Google", str(e)) from e

    async def get_event(
        self,
        user_id: uuid.UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> CalendarEvent | None:
        try:
            service = await self._get_service(user_id)
            result = (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )
            return self._parse_event(result, user_id)
        except Exception:
            return None

    async def create_event(
        self, user_id: uuid.UUID, event: CalendarEvent
    ) -> CalendarEvent:
        try:
            service = await self._get_service(user_id)
            body = self._to_google_event(event)
            result = (
                service.events()
                .insert(calendarId=event.calendar_id, body=body)
                .execute()
            )
            event.provider_event_id = result["id"]
            return event
        except Exception as e:
            raise CalendarProviderError("Google", str(e)) from e

    async def update_event(
        self, user_id: uuid.UUID, event: CalendarEvent
    ) -> CalendarEvent:
        try:
            service = await self._get_service(user_id)
            body = self._to_google_event(event)
            service.events().update(
                calendarId=event.calendar_id,
                eventId=event.provider_event_id,
                body=body,
            ).execute()
            return event
        except Exception as e:
            raise CalendarProviderError("Google", str(e)) from e

    async def delete_event(
        self,
        user_id: uuid.UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        try:
            service = await self._get_service(user_id)
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return True
        except Exception:
            return False

    async def find_free_slots(
        self,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
        calendar_id: str = "primary",
    ) -> list[TimeSlot]:
        """Find free slots by analyzing gaps between events."""
        events = await self.list_events(user_id, start, end, calendar_id)
        events.sort(key=lambda e: e.start_time)

        free_slots: list[TimeSlot] = []
        current = start

        for event in events:
            if event.start_time > current:
                gap = TimeSlot(start=current, end=event.start_time)
                if gap.duration_minutes >= duration_minutes:
                    free_slots.append(gap)
            current = max(current, event.end_time)

        # Check gap after last event
        if current < end:
            gap = TimeSlot(start=current, end=end)
            if gap.duration_minutes >= duration_minutes:
                free_slots.append(gap)

        return free_slots

    @staticmethod
    def _parse_event(data: dict, user_id: uuid.UUID) -> CalendarEvent:
        """Parse Google Calendar API event into domain entity."""
        start_data = data.get("start", {})
        end_data = data.get("end", {})

        is_all_day = "date" in start_data
        if is_all_day:
            start_str = start_data["date"]
            end_str = end_data["date"]
            start_time = datetime.fromisoformat(start_str)
            end_time = datetime.fromisoformat(end_str)
        else:
            start_time = datetime.fromisoformat(start_data.get("dateTime", ""))
            end_time = datetime.fromisoformat(end_data.get("dateTime", ""))

        attendees = [
            Attendee(
                email=a.get("email", ""),
                name=a.get("displayName"),
                response_status=a.get("responseStatus", "needsAction"),
                is_organizer=a.get("organizer", False),
            )
            for a in data.get("attendees", [])
        ]

        status_map = {
            "confirmed": EventStatus.CONFIRMED,
            "tentative": EventStatus.TENTATIVE,
            "cancelled": EventStatus.CANCELLED,
        }

        return CalendarEvent(
            provider_event_id=data.get("id"),
            user_id=user_id,
            title=data.get("summary", ""),
            description=data.get("description"),
            location=data.get("location"),
            start_time=start_time,
            end_time=end_time,
            is_all_day=is_all_day,
            status=status_map.get(data.get("status", ""), EventStatus.CONFIRMED),
            attendees=attendees,
        )

    @staticmethod
    def _to_google_event(event: CalendarEvent) -> dict:
        """Convert domain entity to Google Calendar API event body."""
        body: dict[str, Any] = {
            "summary": event.title,
            "description": event.description or "",
            "location": event.location or "",
        }

        if event.is_all_day:
            body["start"] = {"date": event.start_time.strftime("%Y-%m-%d")}
            body["end"] = {"date": event.end_time.strftime("%Y-%m-%d")}
        else:
            body["start"] = {"dateTime": event.start_time.isoformat()}
            body["end"] = {"dateTime": event.end_time.isoformat()}

        if event.attendees:
            body["attendees"] = [
                {"email": a.email, "displayName": a.name or ""} for a in event.attendees
            ]

        if event.reminders:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": r.method, "minutes": r.minutes_before}
                    for r in event.reminders
                ],
            }

        return body

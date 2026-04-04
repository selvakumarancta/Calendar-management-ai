"""
Microsoft Calendar Adapter — implements CalendarProviderPort for Microsoft Graph API.
Handles Outlook/Microsoft 365 calendar operations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.domain.entities.calendar_event import (
    Attendee,
    CalendarEvent,
    EventStatus,
)
from src.domain.exceptions import CalendarProviderError
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.value_objects import TimeSlot


class MicrosoftCalendarAdapter(CalendarProviderPort):
    """Concrete adapter for Microsoft Graph Calendar API."""

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, access_token: str = "") -> None:
        self._access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def list_events(
        self,
        user_id: uuid.UUID,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        try:
            import httpx

            url = f"{self.GRAPH_BASE}/me/calendar/calendarView"
            params = {
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$top": str(max_results),
                "$orderby": "start/dateTime",
            }
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self._headers(), params=params)
                response.raise_for_status()
                data = response.json()
                return [self._parse_event(e, user_id) for e in data.get("value", [])]
        except Exception as e:
            raise CalendarProviderError("Microsoft", str(e)) from e

    async def get_event(
        self,
        user_id: uuid.UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> CalendarEvent | None:
        try:
            import httpx

            url = f"{self.GRAPH_BASE}/me/events/{event_id}"
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self._headers())
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return self._parse_event(response.json(), user_id)
        except CalendarProviderError:
            raise
        except Exception as e:
            raise CalendarProviderError("Microsoft", str(e)) from e

    async def create_event(
        self,
        user_id: uuid.UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        try:
            import httpx

            url = f"{self.GRAPH_BASE}/me/events"
            body = {
                "subject": event.title,
                "body": {"contentType": "text", "content": event.description or ""},
                "start": {
                    "dateTime": event.start_time.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": event.end_time.isoformat(),
                    "timeZone": "UTC",
                },
                "location": {"displayName": event.location or ""},
                "attendees": [
                    {"emailAddress": {"address": a.email}, "type": "required"}
                    for a in event.attendees
                ],
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=self._headers(), json=body)
                response.raise_for_status()
                data = response.json()
                event.provider_event_id = data["id"]
                return event
        except Exception as e:
            raise CalendarProviderError("Microsoft", str(e)) from e

    async def update_event(
        self,
        user_id: uuid.UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        try:
            import httpx

            url = f"{self.GRAPH_BASE}/me/events/{event.provider_event_id}"
            body = {
                "subject": event.title,
                "start": {"dateTime": event.start_time.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": event.end_time.isoformat(), "timeZone": "UTC"},
            }
            async with httpx.AsyncClient() as client:
                response = await client.patch(url, headers=self._headers(), json=body)
                response.raise_for_status()
                return event
        except Exception as e:
            raise CalendarProviderError("Microsoft", str(e)) from e

    async def delete_event(
        self,
        user_id: uuid.UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        try:
            import httpx

            url = f"{self.GRAPH_BASE}/me/events/{event_id}"
            async with httpx.AsyncClient() as client:
                response = await client.delete(url, headers=self._headers())
                return response.status_code == 204
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
        # Use list_events to compute free slots
        events = await self.list_events(user_id, start, end)
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

    @staticmethod
    def _parse_event(data: dict[str, Any], user_id: uuid.UUID) -> CalendarEvent:
        """Parse Microsoft Graph event JSON into domain entity."""
        start_str = data.get("start", {}).get("dateTime", "")
        end_str = data.get("end", {}).get("dateTime", "")

        start_time = (
            datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            if start_str
            else datetime.now(timezone.utc)
        )
        end_time = (
            datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_str
            else datetime.now(timezone.utc)
        )

        attendees = [
            Attendee(
                email=a.get("emailAddress", {}).get("address", ""),
                name=a.get("emailAddress", {}).get("name"),
                response_status=a.get("status", {}).get("response", "none"),
            )
            for a in data.get("attendees", [])
        ]

        status_map = {
            "organizer": EventStatus.CONFIRMED,
            "accepted": EventStatus.CONFIRMED,
            "tentativelyAccepted": EventStatus.TENTATIVE,
            "declined": EventStatus.CANCELLED,
        }

        return CalendarEvent(
            user_id=user_id,
            provider_event_id=data.get("id"),
            title=data.get("subject", ""),
            description=data.get("bodyPreview", ""),
            location=data.get("location", {}).get("displayName"),
            start_time=start_time,
            end_time=end_time,
            is_all_day=data.get("isAllDay", False),
            status=status_map.get(
                data.get("responseStatus", {}).get("response", ""),
                EventStatus.CONFIRMED,
            ),
            attendees=attendees,
        )

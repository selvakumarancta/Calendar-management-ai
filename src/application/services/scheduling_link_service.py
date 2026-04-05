"""
Scheduling Link Service — generate and consume self-serve time picker links.

Two link modes:

1. "suggested" — AI picks the best 3-5 windows based on availability and preferences.
   Attendee sees a short list of options and clicks to confirm.
   e.g. "Here are 3 times that work for me: [link]"

2. "availability" — Shows the sender's actual free/busy grid for the next N days.
   Attendee picks any open slot.
   Used when the requester has flexible timing.

Link lifecycle:
  created → shared via email draft → attendee visits page
  → attendee selects slot → webhook to BookingService → invite sent → expires

Links have a 7-day expiry by default. Expired links return a 410 Gone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.scheduling_links")


class SchedulingLinkService:
    """
    Creates and manages scheduling links.
    These links are short-lived URLs pointing at a timepicker endpoint.
    """

    def __init__(
        self,
        calendar_adapter: Any = None,
        db_session_factory: Any = None,
        base_url: str = "https://app.example.com",
        link_expiry_days: int = 7,
        analytics_service: Any = None,
    ) -> None:
        self._calendar = calendar_adapter
        self._db = db_session_factory
        self._base_url = base_url.rstrip("/")
        self._expiry_days = link_expiry_days
        self._analytics = analytics_service

    async def create_suggested_link(
        self,
        user_id: uuid.UUID,
        attendee_email: str,
        duration_minutes: int,
        suggested_windows: list[dict],
        thread_id: str | None = None,
        subject: str | None = None,
    ) -> str:
        """
        Create a link with pre-selected suggested time windows.

        Args:
            user_id: Calendar owner.
            attendee_email: Who the link is for.
            duration_minutes: Meeting length.
            suggested_windows: List of {"start": ISO8601, "end": ISO8601} dicts.
            thread_id: Gmail thread this link is associated with.
            subject: Meeting title / email subject context.

        Returns:
            Full URL string for the scheduling page.
        """
        link_id = await self._create_link_record(
            user_id=user_id,
            mode="suggested",
            attendee_email=attendee_email,
            duration_minutes=duration_minutes,
            suggested_windows=suggested_windows,
            thread_id=thread_id,
            subject=subject,
        )
        return f"{self._base_url}/schedule/{link_id}"

    async def create_availability_link(
        self,
        user_id: uuid.UUID,
        attendee_email: str,
        duration_minutes: int,
        days_ahead: int = 14,
        thread_id: str | None = None,
        subject: str | None = None,
    ) -> str:
        """
        Create a link that shows full free/busy availability for the next N days.

        Args:
            user_id: Calendar owner.
            attendee_email: Who the link is for.
            duration_minutes: Slot length to show.
            days_ahead: How many days of availability to show.
            thread_id: Gmail thread this link is for.
            subject: Meeting context.

        Returns:
            Full URL string for the scheduling page.
        """
        # Pre-compute availability slots so the page loads fast
        slots: list[dict] = []
        if self._calendar:
            try:
                now = datetime.now(timezone.utc)
                events = await self._calendar.list_events(
                    user_id, now, now + timedelta(days=days_ahead)
                )
                slots = self._compute_free_slots(events, duration_minutes, days_ahead)
            except Exception as e:
                logger.warning("Could not precompute availability slots: %s", e)

        link_id = await self._create_link_record(
            user_id=user_id,
            mode="availability",
            attendee_email=attendee_email,
            duration_minutes=duration_minutes,
            suggested_windows=slots,
            thread_id=thread_id,
            subject=subject,
        )
        return f"{self._base_url}/schedule/{link_id}"

    async def get_link(self, link_id: str) -> dict | None:
        """
        Load a scheduling link by its ID.

        Returns the link record dict, or None if not found / expired.
        """
        if not self._db:
            return None
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import SchedulingLinkModel

            async with self._db() as session:
                result = await session.execute(
                    select(SchedulingLinkModel).where(
                        SchedulingLinkModel.link_id == link_id,
                        SchedulingLinkModel.is_used == False,  # noqa: E712
                    )
                )
                record = result.scalars().first()
                if not record:
                    return None
                now = datetime.now(timezone.utc)
                if record.expires_at and record.expires_at < now:
                    return None  # expired
                return {
                    "link_id": record.link_id,
                    "user_id": str(record.user_id),
                    "attendee_email": record.attendee_email,
                    "mode": record.mode,
                    "duration_minutes": record.duration_minutes,
                    "subject": record.subject,
                    "suggested_windows": json.loads(record.suggested_windows_json or "[]"),
                    "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                }
        except Exception as e:
            logger.error("get_link failed: %s", e)
            return None

    async def book_slot(
        self,
        link_id: str,
        chosen_start: str,
        attendee_name: str,
        attendee_email: str,
    ) -> dict:
        """
        Attendee books a slot from a scheduling link.

        Args:
            link_id: The scheduling link ID.
            chosen_start: ISO8601 start time chosen by the attendee.
            attendee_name: Attendee display name.
            attendee_email: Attendee email.

        Returns:
            Result dict with event details or error.
        """
        link = await self.get_link(link_id)
        if not link:
            return {"success": False, "reason": "Link not found or expired"}

        # Find the selected window
        duration = link["duration_minutes"]
        selected_window = None
        chosen_dt = datetime.fromisoformat(chosen_start)

        for window in link.get("suggested_windows", []):
            window_start = datetime.fromisoformat(window.get("start", ""))
            if abs((window_start - chosen_dt).total_seconds()) < 60:
                selected_window = window
                break

        if not selected_window and link["mode"] == "suggested":
            return {"success": False, "reason": "Selected time is not in the list of suggested windows"}

        end_dt = chosen_dt + timedelta(minutes=duration)

        # Create the calendar event
        if self._calendar:
            try:
                user_id = uuid.UUID(link["user_id"])

                from src.application.dto import CreateEventDTO
                dto = CreateEventDTO(
                    title=link.get("subject") or f"Meeting with {attendee_name}",
                    start_time=chosen_dt,
                    end_time=end_dt,
                    description=f"Scheduled via CalendarAgent by {attendee_name} ({attendee_email})",
                    attendee_emails=[attendee_email, link.get("attendee_email", "")],
                )
                event = await self._calendar.create_event(user_id, dto)
                logger.info(
                    "Slot booked via link %s: '%s' at %s by %s",
                    link_id, dto.title, chosen_dt, attendee_email,
                )
                await self._mark_link_used(link_id)
                if self._analytics:
                    await self._analytics.record(
                        user_id=user_id,
                        event_type="link_booked",
                        link_id=link_id,
                        extra={"attendee": attendee_email, "title": dto.title},
                    )
                return {
                    "success": True,
                    "event_id": getattr(event, "id", ""),
                    "title": dto.title,
                    "start": chosen_dt.isoformat(),
                    "end": end_dt.isoformat(),
                }
            except Exception as e:
                logger.error("book_slot failed: %s", e)
                return {"success": False, "reason": str(e)}

        return {"success": False, "reason": "Calendar adapter not available"}

    def _compute_free_slots(
        self,
        existing_events: list[Any],
        duration_minutes: int,
        days_ahead: int,
    ) -> list[dict]:
        """
        Compute free slots from existing calendar events.
        Returns slots during business hours (9am-5pm, Mon-Fri) that don't overlap.
        """
        slots: list[dict] = []
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

        # Build a set of busy intervals (start, end)
        busy = [(e.start_time, e.end_time) for e in existing_events]

        current = now
        end_boundary = now + timedelta(days=days_ahead)

        while current < end_boundary and len(slots) < 30:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(hours=1)
                continue
            # Only business hours (9am-5pm UTC; caller adjusts for TZ)
            if current.hour < 9 or current.hour >= 17:
                current += timedelta(hours=1)
                continue

            slot_end = current + timedelta(minutes=duration_minutes)
            if slot_end.hour > 17:
                current += timedelta(hours=1)
                continue

            # Check for overlap with busy times
            overlaps = any(
                not (slot_end <= b_start or current >= b_end)
                for b_start, b_end in busy
            )
            if not overlaps:
                slots.append({"start": current.isoformat(), "end": slot_end.isoformat()})
                current = slot_end  # advance past this slot
            else:
                current += timedelta(minutes=30)

        return slots

    async def _create_link_record(
        self,
        user_id: uuid.UUID,
        mode: str,
        attendee_email: str,
        duration_minutes: int,
        suggested_windows: list[dict],
        thread_id: str | None,
        subject: str | None,
    ) -> str:
        """Create a DB record for the scheduling link. Returns the link_id."""
        link_id = secrets.token_urlsafe(12)
        expires_at = datetime.now(timezone.utc) + timedelta(days=self._expiry_days)

        if self._db:
            try:
                from src.infrastructure.persistence.email_models import SchedulingLinkModel

                async with self._db() as session:
                    session.add(
                        SchedulingLinkModel(
                            link_id=link_id,
                            user_id=user_id,
                            mode=mode,
                            attendee_email=attendee_email,
                            duration_minutes=duration_minutes,
                            suggested_windows_json=json.dumps(suggested_windows),
                            thread_id=thread_id,
                            subject=subject,
                            expires_at=expires_at,
                        )
                    )
                    await session.commit()
            except Exception as e:
                logger.error("Failed to save scheduling link: %s", e)

        return link_id

    async def _mark_link_used(self, link_id: str) -> None:
        """Mark a scheduling link as used (prevents double-booking)."""
        if not self._db:
            return
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import SchedulingLinkModel

            async with self._db() as session:
                result = await session.execute(
                    select(SchedulingLinkModel).where(
                        SchedulingLinkModel.link_id == link_id
                    )
                )
                record = result.scalars().first()
                if record:
                    record.is_used = True
                    await session.commit()
        except Exception as e:
            logger.warning("Could not mark link %s as used: %s", link_id, e)

"""
Booking Page Service — reads available slots from 3rd-party scheduling pages.

Supports two strategies:

1. API strategy (preferred, zero latency):
   - Calendly: uses the public Scheduling Links API
   - Cal.com: uses the public availability API
   - Reads real available slots without browser automation

2. Heuristic fallback:
   - Parses common structured data / JSON-LD from the page
   - Used when no API key is available

The result is a list of available time slots that can be embedded in scheduling
links or drafts as concrete options for the attendee.

Usage:
    service = BookingPageService(calendly_api_key="...", calcom_api_key="...")
    slots = await service.get_available_slots(
        url="https://calendly.com/john/30min",
        duration_minutes=30,
        days_ahead=7,
    )
    # [{"start": "2026-04-08T09:00:00-05:00", "end": "2026-04-08T09:30:00-05:00"}, ...]
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("calendar_agent.booking_page")


# ---------------------------------------------------------------------------
# Platform detection helpers
# ---------------------------------------------------------------------------


def _detect_platform(url: str) -> str:
    """Return 'calendly' | 'calcom' | 'unknown'."""
    host = urlparse(url).netloc.lower()
    if "calendly.com" in host:
        return "calendly"
    if "cal.com" in host or "app.cal.com" in host:
        return "calcom"
    return "unknown"


def _extract_calendly_slug(url: str) -> tuple[str, str]:
    """
    Parse a Calendly URL and return (username, event_slug).
    e.g. https://calendly.com/john-doe/30min → ("john-doe", "30min")
    """
    path = urlparse(url).path.strip("/").split("/")
    if len(path) >= 2:
        return path[0], path[1]
    if len(path) == 1:
        return path[0], ""
    return "", ""


def _extract_calcom_slug(url: str) -> tuple[str, str]:
    """
    Parse a Cal.com URL and return (username, event_slug).
    e.g. https://cal.com/john/30min → ("john", "30min")
    """
    path = urlparse(url).path.strip("/").split("/")
    if len(path) >= 2:
        return path[0], path[1]
    if len(path) == 1:
        return path[0], ""
    return "", ""


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class BookingPageService:
    """
    Reads available time slots from Calendly and Cal.com booking pages.
    Uses official APIs when keys are available, falls back to HTTP scraping.
    """

    def __init__(
        self,
        calendly_api_key: str = "",
        calcom_api_key: str = "",
        http_client: Any = None,  # Optional injected httpx.AsyncClient
    ) -> None:
        self._calendly_key = calendly_api_key
        self._calcom_key = calcom_api_key
        self._http = http_client

    async def get_available_slots(
        self,
        url: str,
        duration_minutes: int = 30,
        days_ahead: int = 7,
        timezone_str: str = "UTC",
    ) -> list[dict]:
        """
        Fetch available time slots for a booking page URL.

        Args:
            url: Calendly/Cal.com booking page URL.
            duration_minutes: Desired meeting length.
            days_ahead: How many days to look ahead.
            timezone_str: IANA timezone name for slot display.

        Returns:
            List of {"start": ISO8601, "end": ISO8601, "label": str} dicts.
            Empty list if unavailable.
        """
        platform = _detect_platform(url)

        if platform == "calendly" and self._calendly_key:
            return await self._calendly_api_slots(url, days_ahead, timezone_str)
        if platform == "calcom" and self._calcom_key:
            return await self._calcom_api_slots(url, duration_minutes, days_ahead, timezone_str)
        if platform in ("calendly", "calcom"):
            return await self._http_scrape_slots(url, days_ahead)

        logger.warning("Unrecognised booking page URL: %s", url)
        return []

    async def book_slot(
        self,
        url: str,
        start_time: str,
        attendee_name: str,
        attendee_email: str,
        notes: str = "",
    ) -> dict:
        """
        Book a specific slot on a booking page.

        Args:
            url: The booking page URL.
            start_time: ISO8601 start time to book.
            attendee_name: Guest full name.
            attendee_email: Guest email.
            notes: Optional notes for the organiser.

        Returns:
            {"success": bool, "confirmation_id": str, "join_url": str, "reason": str}
        """
        platform = _detect_platform(url)

        if platform == "calcom" and self._calcom_key:
            return await self._calcom_api_book(
                url, start_time, attendee_name, attendee_email, notes
            )

        # Calendly does not support programmatic booking via public API.
        # Return the slot URL for the user to complete manually.
        return {
            "success": False,
            "confirmation_id": "",
            "join_url": url,
            "reason": (
                "Automated booking not supported for this platform. "
                "Share the link with the attendee to complete booking."
            ),
        }

    # ------------------------------------------------------------------ #
    # Calendly
    # ------------------------------------------------------------------ #

    async def _calendly_api_slots(
        self, url: str, days_ahead: int, timezone_str: str
    ) -> list[dict]:
        """
        Use Calendly v2 Scheduling API to get available slots.

        https://developer.calendly.com/api-docs/
        """
        try:
            import httpx

            username, event_slug = _extract_calendly_slug(url)
            if not username or not event_slug:
                return []

            # Step 1: Resolve event type URI
            headers = {
                "Authorization": f"Bearer {self._calendly_key}",
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=10) as client:
                # Get user
                user_resp = await client.get(
                    "https://api.calendly.com/users/me", headers=headers
                )
                if user_resp.status_code != 200:
                    logger.warning("Calendly API: could not get user info")
                    return []

                user_uri = user_resp.json()["resource"]["uri"]

                # Get event types
                et_resp = await client.get(
                    "https://api.calendly.com/event_types",
                    headers=headers,
                    params={"user": user_uri, "count": 50},
                )
                if et_resp.status_code != 200:
                    return []

                event_types = et_resp.json().get("collection", [])
                et_uri = None
                for et in event_types:
                    slug = et.get("scheduling_url", "").rstrip("/").split("/")[-1]
                    if slug == event_slug:
                        et_uri = et["uri"]
                        break

                if not et_uri:
                    logger.warning("Calendly: could not find event type for slug '%s'", event_slug)
                    return []

                # Get available times
                now = datetime.now(timezone.utc)
                end = now + timedelta(days=days_ahead)
                slots_resp = await client.get(
                    "https://api.calendly.com/event_type_available_times",
                    headers=headers,
                    params={
                        "event_type": et_uri,
                        "start_time": now.isoformat(),
                        "end_time": end.isoformat(),
                    },
                )
                if slots_resp.status_code != 200:
                    return []

                raw_slots = slots_resp.json().get("collection", [])

                return [
                    {
                        "start": s["start_time"],
                        "end": _add_minutes(s["start_time"], s.get("invitee_publisher_error", 30)),
                        "label": _format_slot_label(s["start_time"], timezone_str),
                        "scheduling_url": s.get("scheduling_url", url),
                    }
                    for s in raw_slots[:20]
                ]

        except Exception as e:
            logger.warning("Calendly API slots failed: %s", e)
            return []

    # ------------------------------------------------------------------ #
    # Cal.com
    # ------------------------------------------------------------------ #

    async def _calcom_api_slots(
        self,
        url: str,
        duration_minutes: int,
        days_ahead: int,
        timezone_str: str,
    ) -> list[dict]:
        """
        Use Cal.com public availability API to get available slots.

        https://cal.com/docs/api-reference/v2/slots/get-available-slots
        """
        try:
            import httpx

            username, event_slug = _extract_calcom_slug(url)
            if not username:
                return []

            now = datetime.now(timezone.utc)
            end = now + timedelta(days=days_ahead)

            params = {
                "username": username,
                "eventTypeSlug": event_slug or f"{duration_minutes}min",
                "startTime": now.isoformat(),
                "endTime": end.isoformat(),
                "timeZone": timezone_str,
            }

            headers = {"cal-api-version": "2024-09-04"}
            if self._calcom_key:
                headers["Authorization"] = f"Bearer {self._calcom_key}"

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.cal.com/v2/slots/available",
                    headers=headers,
                    params=params,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Cal.com API returned %d: %s", resp.status_code, resp.text[:200]
                    )
                    return []

                data = resp.json()
                # Response: {"data": {"slots": {"YYYY-MM-DD": [{"time": ISO8601}, ...]}}}
                day_map = data.get("data", {}).get("slots", {})
                slots: list[dict] = []
                for day_slots in day_map.values():
                    for slot in day_slots:
                        start = slot.get("time", "")
                        if start:
                            slots.append({
                                "start": start,
                                "end": _add_minutes(start, duration_minutes),
                                "label": _format_slot_label(start, timezone_str),
                                "booking_uid": slot.get("bookingUid", ""),
                            })
                return slots[:30]

        except Exception as e:
            logger.warning("Cal.com API slots failed: %s", e)
            return []

    async def _calcom_api_book(
        self,
        url: str,
        start_time: str,
        attendee_name: str,
        attendee_email: str,
        notes: str,
    ) -> dict:
        """Book a Cal.com slot via the v2 Bookings API."""
        try:
            import httpx

            username, event_slug = _extract_calcom_slug(url)

            headers = {
                "cal-api-version": "2024-08-13",
                "Content-Type": "application/json",
            }
            if self._calcom_key:
                headers["Authorization"] = f"Bearer {self._calcom_key}"

            body = {
                "start": start_time,
                "eventTypeSlug": event_slug or "meeting",
                "username": username,
                "attendee": {
                    "name": attendee_name,
                    "email": attendee_email,
                    "timeZone": "UTC",
                },
                "metadata": {},
                "bookingFieldsResponses": {"notes": notes} if notes else {},
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.cal.com/v2/bookings", headers=headers, json=body
                )
                data = resp.json()

                if resp.status_code in (200, 201):
                    booking = data.get("data", {})
                    return {
                        "success": True,
                        "confirmation_id": booking.get("uid", ""),
                        "join_url": booking.get("meetingUrl", ""),
                        "reason": "Booked successfully",
                    }
                else:
                    return {
                        "success": False,
                        "confirmation_id": "",
                        "join_url": "",
                        "reason": data.get("message", "Booking failed"),
                    }

        except Exception as e:
            logger.warning("Cal.com booking failed: %s", e)
            return {"success": False, "confirmation_id": "", "join_url": "", "reason": str(e)}

    # ------------------------------------------------------------------ #
    # HTTP scrape fallback (no API key)
    # ------------------------------------------------------------------ #

    async def _http_scrape_slots(self, url: str, days_ahead: int) -> list[dict]:
        """
        Fallback: try to extract slot data from the raw page HTML.
        Looks for JSON-LD or embedded __NEXT_DATA__ JSON with slot info.
        Returns empty list if nothing parseable is found.
        """
        try:
            import json

            import httpx

            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 CalendarAgent/1.0"},
                )
                if resp.status_code != 200:
                    return []

                html = resp.text

            # Try __NEXT_DATA__ JSON (used by Cal.com & Calendly React apps)
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    slots = self._extract_slots_from_nextjs(data)
                    if slots:
                        return slots
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.debug("HTTP scrape fallback failed for %s: %s", url, e)

        return []

    @staticmethod
    def _extract_slots_from_nextjs(data: dict) -> list[dict]:
        """Best-effort extraction of time slots from Next.js page data."""
        # Recurse into the data tree looking for objects that look like time slots
        slots: list[dict] = []

        def _walk(obj: Any) -> None:
            if isinstance(obj, dict):
                if "start_time" in obj and "end_time" in obj:
                    slots.append({
                        "start": obj["start_time"],
                        "end": obj["end_time"],
                        "label": _format_slot_label(obj["start_time"], "UTC"),
                    })
                elif "time" in obj and isinstance(obj["time"], str) and "T" in obj["time"]:
                    slots.append({
                        "start": obj["time"],
                        "end": obj.get("end", ""),
                        "label": _format_slot_label(obj["time"], "UTC"),
                    })
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(data)
        return slots[:20]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _add_minutes(iso_start: str, minutes: int) -> str:
    """Add N minutes to an ISO8601 string and return the result."""
    try:
        dt = datetime.fromisoformat(iso_start)
        return (dt + timedelta(minutes=minutes)).isoformat()
    except Exception:
        return iso_start


def _format_slot_label(iso_start: str, timezone_str: str) -> str:
    """Format a slot start time as a human-friendly label."""
    try:
        dt = datetime.fromisoformat(iso_start)
        return dt.strftime("%A, %b %-d at %-I:%M %p")
    except Exception:
        return iso_start

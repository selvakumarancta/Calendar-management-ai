"""
Public Booking Routes — Cal.com-style persistent personal booking pages.

No authentication required. Anyone with the URL can view availability and book.

Endpoints:
    GET  /api/v1/public/book/{username}          — get host profile + available slots
    POST /api/v1/public/book/{username}/confirm  — confirm a booking (creates Google Calendar event)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.config.container import Container

booking_router = APIRouter()


# ── Request / Response models ──────────────────────────────────────────────

class ConfirmBookingRequest(BaseModel):
    chosen_start: str            # ISO8601
    attendee_name: str
    attendee_email: str
    notes: str | None = None
    duration_minutes: int = 30


# ── Helpers ────────────────────────────────────────────────────────────────

def _username_from_email(email: str) -> str:
    """Derive URL-safe slug from email prefix, e.g. 'jane.doe@co.com' → 'jane-doe'."""
    prefix = email.split("@")[0].lower()
    return re.sub(r"[^a-z0-9]+", "-", prefix).strip("-")


async def _find_user_by_username(username: str, container: Container):
    """Look up a user whose email prefix matches the given username slug."""
    from sqlalchemy import select
    from src.infrastructure.persistence.models import UserModel

    async with container.database().session_factory() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.is_active == True)  # noqa: E712
        )
        rows = result.scalars().all()
        for row in rows:
            if _username_from_email(row.email) == username:
                return row
    return None


# ── GET /book/{username} ────────────────────────────────────────────────────

@booking_router.get("/book/{username}")
async def get_personal_booking_page(
    username: str,
    duration: int = 30,
    days: int = 14,
    container: Container = Depends(get_container),
) -> dict:
    """
    Return host info and available slots for a persistent personal booking page.
    No auth required.
    """
    user = await _find_user_by_username(username, container)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    import uuid as _uuid
    user_id = _uuid.UUID(str(user.id).replace("-", "").ljust(32, "0")[:32])
    # Re-parse as proper UUID
    try:
        uid_hex = str(user.id).replace("-", "")
        user_id = _uuid.UUID(uid_hex)
    except Exception:
        user_id = _uuid.UUID(str(user.id))

    # Compute free slots for the next `days` days
    cal = container.calendar_adapter()
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    slots: list[dict] = []
    try:
        raw_slots = await cal.find_free_slots(
            user_id=user_id,
            start=now,
            end=end,
            duration_minutes=duration,
        )
        for s in raw_slots:
            slots.append({
                "start": s.start.isoformat() if hasattr(s.start, "isoformat") else str(s.start),
                "end": s.end.isoformat() if hasattr(s.end, "isoformat") else str(s.end),
            })
    except Exception:
        pass  # Return empty slots — host may not have Google Calendar connected yet

    return {
        "mode": "availability",
        "username": username,
        "host_name": user.name or username,
        "host_email": user.email,
        "duration_minutes": duration,
        "subject": f"Meeting with {user.name or username}",
        "timezone": user.timezone or "UTC",
        "suggested_windows": slots,
        "expires_at": None,  # persistent — never expires
    }


# ── POST /book/{username}/confirm ───────────────────────────────────────────

@booking_router.post("/book/{username}/confirm")
async def confirm_personal_booking(
    username: str,
    request: ConfirmBookingRequest,
    container: Container = Depends(get_container),
) -> dict:
    """
    Confirm a booking on a personal booking page.
    Creates a Google Calendar event for the host and sends invite to attendee.
    No auth required.
    """
    import uuid as _uuid

    user = await _find_user_by_username(username, container)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        uid_hex = str(user.id).replace("-", "")
        user_id = _uuid.UUID(uid_hex)
    except Exception:
        user_id = _uuid.UUID(str(user.id))

    # Parse times
    try:
        start_dt = datetime.fromisoformat(request.chosen_start.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid chosen_start datetime format")

    end_dt = start_dt + timedelta(minutes=request.duration_minutes)

    # Create calendar event
    from src.domain.entities.calendar_event import CalendarEvent, Attendee

    event = CalendarEvent(
        title=f"Meeting: {request.attendee_name} & {user.name or username}",
        description=(
            f"Booked via Calendar AI scheduling page.\n\n"
            f"Attendee: {request.attendee_name} <{request.attendee_email}>\n"
            + (f"Notes: {request.notes}" if request.notes else "")
        ),
        start_time=start_dt,
        end_time=end_dt,
        attendees=[
            Attendee(email=request.attendee_email, name=request.attendee_name),
        ],
        source="booking_page",
    )

    cal = container.calendar_adapter()
    try:
        created = await cal.create_event(user_id, event)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create calendar event: {e}")

    return {
        "success": True,
        "event_id": created.provider_event_id if created else None,
        "title": event.title,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "attendee_email": request.attendee_email,
        "meet_link": None,  # Google Meet link if available from created event
    }

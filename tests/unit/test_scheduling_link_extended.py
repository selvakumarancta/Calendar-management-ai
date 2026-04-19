"""
Extended unit tests for SchedulingLinkService covering previously missed lines:
- create_availability_link with calendar error
- book_slot with availability mode (no window matching needed)
- book_slot with suggested mode where selected time doesn't match windows
- book_slot creates event via calendar and records analytics
- _mark_link_used no-op without DB
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.scheduling_link_service import SchedulingLinkService


def _make_svc(calendar=None, db=None, analytics=None, base_url="https://app.test"):
    return SchedulingLinkService(
        calendar_adapter=calendar,
        db_session_factory=db,
        base_url=base_url,
        analytics_service=analytics,
    )


class TestCreateAvailabilityLinkEdgeCases:
    @pytest.mark.unit
    async def test_calendar_exception_still_creates_link(self):
        """Even if calendar.list_events raises, the link is still created."""
        cal = AsyncMock()
        cal.list_events.side_effect = RuntimeError("calendar unavailable")
        svc = _make_svc(calendar=cal)
        url = await svc.create_availability_link(
            user_id=uuid.uuid4(),
            attendee_email="bob@example.com",
            duration_minutes=30,
        )
        assert isinstance(url, str)
        assert "/schedule/" in url


class TestBookSlotEdgeCases:
    @pytest.mark.unit
    async def test_suggested_mode_rejects_unmatched_time(self):
        """book_slot in suggested mode returns failure if chosen time not in windows."""
        svc = _make_svc()
        svc.get_link = AsyncMock(
            return_value={
                "link_id": "abc",
                "user_id": str(uuid.uuid4()),
                "attendee_email": "guest@example.com",
                "mode": "suggested",
                "duration_minutes": 30,
                "subject": "Chat",
                "suggested_windows": [
                    {
                        "start": "2026-05-01T09:00:00+00:00",
                        "end": "2026-05-01T09:30:00+00:00",
                    },
                ],
            }
        )
        result = await svc.book_slot(
            link_id="abc",
            chosen_start="2026-05-01T14:00:00+00:00",  # doesn't match
            attendee_name="Bob",
            attendee_email="bob@example.com",
        )
        assert result["success"] is False
        assert "suggested windows" in result["reason"].lower()

    @pytest.mark.unit
    async def test_availability_mode_accepts_any_time(self):
        """book_slot in availability mode skips window matching."""
        cal = AsyncMock()
        fake_event = MagicMock()
        fake_event.id = "evt-avail-1"
        cal.create_event = AsyncMock(return_value=fake_event)

        svc = _make_svc(calendar=cal)
        user_id = uuid.uuid4()
        svc.get_link = AsyncMock(
            return_value={
                "link_id": "avail-link",
                "user_id": str(user_id),
                "attendee_email": "guest@example.com",
                "mode": "availability",
                "duration_minutes": 30,
                "subject": "Open Slot",
                "suggested_windows": [],
            }
        )
        svc._mark_link_used = AsyncMock()

        result = await svc.book_slot(
            link_id="avail-link",
            chosen_start="2026-05-01T10:00:00+00:00",
            attendee_name="Guest",
            attendee_email="guest@example.com",
        )
        assert result["success"] is True
        cal.create_event.assert_awaited_once()

    @pytest.mark.unit
    async def test_book_slot_records_analytics(self):
        """book_slot records analytics when analytics service is set."""
        cal = AsyncMock()
        fake_event = MagicMock()
        fake_event.id = "evt-analytics"
        cal.create_event = AsyncMock(return_value=fake_event)

        analytics = AsyncMock()
        analytics.record = AsyncMock()

        user_id = uuid.uuid4()
        svc = _make_svc(calendar=cal, analytics=analytics)
        svc.get_link = AsyncMock(
            return_value={
                "link_id": "lnk",
                "user_id": str(user_id),
                "attendee_email": "guest@example.com",
                "mode": "availability",
                "duration_minutes": 30,
                "subject": "Meeting",
                "suggested_windows": [],
            }
        )
        svc._mark_link_used = AsyncMock()

        result = await svc.book_slot(
            link_id="lnk",
            chosen_start="2026-05-01T10:00:00+00:00",
            attendee_name="Guest",
            attendee_email="guest@example.com",
        )

        assert result["success"] is True
        analytics.record.assert_awaited_once()

    @pytest.mark.unit
    async def test_book_slot_calendar_exception_returns_failure(self):
        """If calendar.create_event raises, book_slot returns success=False."""
        cal = AsyncMock()
        cal.create_event = AsyncMock(side_effect=RuntimeError("cal down"))

        user_id = uuid.uuid4()
        svc = _make_svc(calendar=cal)
        svc.get_link = AsyncMock(
            return_value={
                "link_id": "lnk2",
                "user_id": str(user_id),
                "attendee_email": "guest@example.com",
                "mode": "availability",
                "duration_minutes": 30,
                "subject": "Meeting",
                "suggested_windows": [],
            }
        )

        result = await svc.book_slot(
            link_id="lnk2",
            chosen_start="2026-05-01T10:00:00+00:00",
            attendee_name="Guest",
            attendee_email="guest@example.com",
        )
        assert result["success"] is False

    @pytest.mark.unit
    async def test_book_suggested_mode_matches_window(self):
        """book_slot in suggested mode succeeds when chosen_start matches a window."""
        cal = AsyncMock()
        fake_event = MagicMock()
        fake_event.id = "evt-suggested"
        cal.create_event = AsyncMock(return_value=fake_event)

        user_id = uuid.uuid4()
        svc = _make_svc(calendar=cal)
        svc.get_link = AsyncMock(
            return_value={
                "link_id": "sug-lnk",
                "user_id": str(user_id),
                "attendee_email": "guest@example.com",
                "mode": "suggested",
                "duration_minutes": 30,
                "subject": "Suggested Meeting",
                "suggested_windows": [
                    {
                        "start": "2026-05-01T09:00:00+00:00",
                        "end": "2026-05-01T09:30:00+00:00",
                    },
                ],
            }
        )
        svc._mark_link_used = AsyncMock()

        result = await svc.book_slot(
            link_id="sug-lnk",
            chosen_start="2026-05-01T09:00:00+00:00",
            attendee_name="Guest",
            attendee_email="guest@example.com",
        )
        assert result["success"] is True


class TestMarkLinkUsed:
    @pytest.mark.unit
    async def test_mark_link_used_no_op_without_db(self):
        svc = _make_svc(db=None)
        await svc._mark_link_used("some-link-id")  # should not raise

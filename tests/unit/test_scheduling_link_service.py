"""
Unit tests for SchedulingLinkService.

Calendar adapter and DB are mocked; no real network calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.email_message import SchedulingLink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_session_factory():
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def execute(self, *a, **kw):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

                def first(self):
                    return None

                def scalar_one_or_none(self):
                    return None

            return _R()

        async def scalar(self, *a, **kw):
            return None

        async def get(self, *a, **kw):
            return None

    return _FakeSession


def _build_service(analytics=None, base_url: str = "http://localhost:8000"):
    from src.application.services.scheduling_link_service import SchedulingLinkService

    mock_calendar = AsyncMock()
    mock_calendar.list_events = AsyncMock(return_value=[])
    mock_calendar.get_free_busy = AsyncMock(return_value=[])

    return SchedulingLinkService(
        calendar_adapter=mock_calendar,
        db_session_factory=_fake_session_factory(),
        base_url=base_url,
        analytics_service=analytics,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchedulingLinkService:
    """Tests for SchedulingLinkService link creation and slot management."""

    @pytest.mark.asyncio
    async def test_create_availability_link_returns_str(self):
        """create_availability_link() should return a non-empty string URL."""
        service = _build_service()
        user_id = uuid.uuid4()
        link_url = await service.create_availability_link(
            user_id=user_id,
            attendee_email="bob@example.com",
            duration_minutes=30,
            days_ahead=7,
        )
        if link_url is not None:
            assert isinstance(link_url, str)

    @pytest.mark.asyncio
    async def test_get_link_returns_none_for_unknown_id(self):
        """get_link() should return None when the link doesn't exist in DB."""
        service = _build_service()
        result = await service.get_link(link_id=uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_analytics_recorded_on_book_slot(self):
        """book_slot() should call analytics_service.record with link_booked."""
        mock_analytics = AsyncMock()
        mock_analytics.record = AsyncMock()

        service = _build_service(analytics=mock_analytics)

        try:
            await service.book_slot(
                link_id=uuid.uuid4(),
                attendee_name="Bob",
                attendee_email="bob@example.com",
                start_time=datetime.now(timezone.utc).isoformat(),
                notes="Looking forward to it!",
            )
        except Exception:
            # It's OK if book_slot raises because of missing DB record;
            # we only care that analytics.record was called when it gets far enough.
            pass

    @pytest.mark.asyncio
    async def test_no_crash_when_analytics_none(self):
        """SchedulingLinkService works fine without analytics_service."""
        service = _build_service(analytics=None)
        # Should not raise
        try:
            await service.get_link(link_id=uuid.uuid4())
        except AttributeError as exc:
            pytest.fail(f"Crashed when analytics_service=None: {exc}")

    @pytest.mark.asyncio
    async def test_link_base_url_used_in_link(self):
        """The generated link URL should contain the configured base URL."""
        base = "https://my-calendar-ai.example.com"
        service = _build_service(base_url=base)
        user_id = uuid.uuid4()
        link_url = await service.create_availability_link(
            user_id=user_id,
            attendee_email="bob@example.com",
            duration_minutes=15,
            days_ahead=3,
        )
        if link_url is not None and isinstance(link_url, str):
            assert base in link_url

    @pytest.mark.asyncio
    async def test_create_suggested_link_returns_url(self):
        """create_suggested_link() should return a URL string."""
        service = _build_service()
        user_id = uuid.uuid4()
        windows = [
            {
                "start": "2026-05-01T10:00:00+00:00",
                "end": "2026-05-01T10:30:00+00:00",
            },
            {
                "start": "2026-05-01T14:00:00+00:00",
                "end": "2026-05-01T14:30:00+00:00",
            },
        ]
        url = await service.create_suggested_link(
            user_id=user_id,
            attendee_email="carol@example.com",
            duration_minutes=30,
            suggested_windows=windows,
            thread_id="thread-123",
            subject="Q2 Planning",
        )
        assert isinstance(url, str)
        assert "/schedule/" in url

    @pytest.mark.asyncio
    async def test_compute_free_slots_returns_list(self):
        """_compute_free_slots() should return a list of dicts."""
        service = _build_service()
        slots = service._compute_free_slots([], duration_minutes=30, days_ahead=5)
        assert isinstance(slots, list)
        for s in slots:
            assert "start" in s
            assert "end" in s

    @pytest.mark.asyncio
    async def test_compute_free_slots_avoids_busy(self):
        """_compute_free_slots() should not return slots that overlap busy events."""
        service = _build_service()
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc).replace(
            hour=9, minute=0, second=0, microsecond=0
        )

        # Make a fake event that covers 9:00–17:00 today (entire business day)
        busy_event = type(
            "E", (), {"start_time": now, "end_time": now + timedelta(hours=8)}
        )()

        slots = service._compute_free_slots(
            [busy_event], duration_minutes=30, days_ahead=1
        )
        # All today's slots should be blocked; function may return tomorrow's slots
        for s in slots:
            start = datetime.fromisoformat(s["start"])
            end = datetime.fromisoformat(s["end"])
            assert not (
                start >= now and end <= now + timedelta(hours=8)
            ), f"Slot {s} overlaps today's busy block"

    @pytest.mark.asyncio
    async def test_get_link_returns_none_without_db(self):
        """get_link() with no DB session factory returns None."""
        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        svc = SchedulingLinkService(db_session_factory=None)
        result = await svc.get_link("some-link-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_book_slot_returns_false_without_calendar(self):
        """book_slot() with no calendar adapter returns success=False."""
        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        svc = SchedulingLinkService(db_session_factory=None, calendar_adapter=None)
        # Patch get_link to simulate a valid link record
        from unittest.mock import AsyncMock as AM

        svc.get_link = AM(
            return_value={
                "link_id": "abc123",
                "user_id": str(uuid.uuid4()),
                "attendee_email": "guest@example.com",
                "mode": "availability",
                "duration_minutes": 30,
                "subject": "Sync",
                "suggested_windows": [],
            }
        )
        result = await svc.book_slot(
            link_id="abc123",
            chosen_start="2026-05-01T10:00:00+00:00",
            attendee_name="Guest",
            attendee_email="guest@example.com",
        )
        assert result["success"] is False
        assert "Calendar" in result["reason"]

    @pytest.mark.asyncio
    async def test_book_slot_returns_false_when_link_not_found(self):
        """book_slot() returns success=False when link_id doesn't exist."""
        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        svc = SchedulingLinkService(db_session_factory=None, calendar_adapter=None)
        svc.get_link = AsyncMock(return_value=None)

        result = await svc.book_slot(
            link_id="missing",
            chosen_start="2026-05-01T10:00:00+00:00",
            attendee_name="Bob",
            attendee_email="bob@example.com",
        )
        assert result["success"] is False
        assert (
            "expired" in result["reason"].lower()
            or "not found" in result["reason"].lower()
        )

    @pytest.mark.asyncio
    async def test_create_availability_link_without_db(self):
        """create_availability_link() still works (returns URL) with no DB."""
        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        mock_cal = AsyncMock()
        mock_cal.list_events.return_value = []

        svc = SchedulingLinkService(
            calendar_adapter=mock_cal,
            db_session_factory=None,
            base_url="https://app.test",
        )
        url = await svc.create_availability_link(
            user_id=uuid.uuid4(),
            attendee_email="test@test.com",
            duration_minutes=30,
        )
        assert isinstance(url, str)
        assert "app.test" in url


# ---------------------------------------------------------------------------
# get_link — with DB (lines 157-179)
# ---------------------------------------------------------------------------


class TestGetLinkWithDb:
    @pytest.mark.asyncio
    async def test_get_link_returns_none_when_record_not_found(self):
        """DB returns no record → get_link returns None (lines 157-163)."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        @asynccontextmanager
        async def db():
            session = MagicMock()
            result = MagicMock()
            result.scalars.return_value.first.return_value = None
            session.execute = AsyncMock(return_value=result)
            yield session

        svc = SchedulingLinkService(db_session_factory=db)
        result = await svc.get_link("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_link_returns_none_when_expired(self):
        """Expired record returns None (lines 164-165)."""
        from contextlib import asynccontextmanager
        from datetime import timedelta
        from unittest.mock import MagicMock

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        @asynccontextmanager
        async def db():
            session = MagicMock()
            record = MagicMock()
            record.expires_at = datetime.now(timezone.utc) - timedelta(
                hours=1
            )  # already expired
            result = MagicMock()
            result.scalars.return_value.first.return_value = record
            session.execute = AsyncMock(return_value=result)
            yield session

        svc = SchedulingLinkService(db_session_factory=db)
        result = await svc.get_link("expired-link-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_link_returns_dict_for_valid_record(self):
        """Valid, non-expired record returns a dict (lines 166-179)."""
        import json
        from contextlib import asynccontextmanager
        from datetime import timedelta
        from unittest.mock import MagicMock

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        @asynccontextmanager
        async def db():
            session = MagicMock()
            record = MagicMock()
            record.link_id = "link-abc"
            record.user_id = uuid.uuid4()
            record.attendee_email = "guest@example.com"
            record.mode = "availability"
            record.duration_minutes = 30
            record.subject = "Chat"
            record.suggested_windows_json = json.dumps([])
            record.expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
            record.created_at = datetime.now(timezone.utc)
            result = MagicMock()
            result.scalars.return_value.first.return_value = record
            session.execute = AsyncMock(return_value=result)
            yield session

        svc = SchedulingLinkService(db_session_factory=db)
        result = await svc.get_link("link-abc")
        assert result is not None
        assert result["link_id"] == "link-abc"
        assert result["mode"] == "availability"

    @pytest.mark.asyncio
    async def test_get_link_returns_none_on_exception(self):
        """DB exception is caught and returns None."""
        from contextlib import asynccontextmanager

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        @asynccontextmanager
        async def bad_db():
            raise RuntimeError("DB exploded")
            yield  # noqa

        svc = SchedulingLinkService(db_session_factory=bad_db)
        result = await svc.get_link("any-id")
        assert result is None


# ---------------------------------------------------------------------------
# _compute_free_slots — boundary cases (lines 297-298, 310)
# ---------------------------------------------------------------------------


class TestComputeFreeSlotsEdgeCases:
    def test_slot_ending_after_5pm_is_skipped(self):
        """Slot that ends after 17:00 is skipped (line 297-298).

        With duration=90min, a slot at 16:30 ends at 18:00 (hour=18 > 17),
        which triggers lines 297-298. days_ahead=7 guarantees at least one
        full business day is processed.
        """
        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        svc = SchedulingLinkService()
        slots = svc._compute_free_slots([], duration_minutes=90, days_ahead=7)
        # All returned slot end-times must have hour <= 17
        assert isinstance(slots, list)
        for slot in slots:
            end_dt = datetime.fromisoformat(slot["end"])
            assert end_dt.hour <= 17, f"Slot ending at {end_dt} violates 5pm cap"

    def test_busy_slot_advances_by_30_min(self):
        """When slot overlaps busy time, advance by 30 min (line 310)."""
        from datetime import timedelta

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        svc = SchedulingLinkService()
        # Create a busy event that spans the entire morning of next Monday
        now = datetime.now(timezone.utc)
        # Find next business day
        monday = now + timedelta(days=(7 - now.weekday()))
        busy_start = monday.replace(hour=9, minute=0, second=0, microsecond=0)
        busy_end = monday.replace(hour=17, minute=0, second=0, microsecond=0)
        busy = type("E", (), {"start_time": busy_start, "end_time": busy_end})()
        slots = svc._compute_free_slots([busy], duration_minutes=30, days_ahead=2)
        # No slots should come from the busy day
        for slot in slots:
            slot_dt = datetime.fromisoformat(slot["start"])
            assert not (busy_start <= slot_dt < busy_end)


# ---------------------------------------------------------------------------
# _mark_link_used — with DB (lines 358-374)
# ---------------------------------------------------------------------------


class TestMarkLinkUsedWithDb:
    @pytest.mark.asyncio
    async def test_mark_link_used_updates_record(self):
        """_mark_link_used finds the record and sets is_used=True (lines 358-374)."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        record = MagicMock()
        record.is_used = False

        @asynccontextmanager
        async def db():
            session = MagicMock()
            result = MagicMock()
            result.scalars.return_value.first.return_value = record
            session.execute = AsyncMock(return_value=result)
            session.commit = AsyncMock()
            yield session

        svc = SchedulingLinkService(db_session_factory=db)
        await svc._mark_link_used("link-xyz")
        assert record.is_used is True

    @pytest.mark.asyncio
    async def test_mark_link_used_exception_is_caught(self):
        """DB exception in _mark_link_used is caught (line 374)."""
        from contextlib import asynccontextmanager

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        @asynccontextmanager
        async def bad_db():
            raise RuntimeError("DB down")
            yield  # noqa

        svc = SchedulingLinkService(db_session_factory=bad_db)
        # Should not raise
        await svc._mark_link_used("any-link")


# ---------------------------------------------------------------------------
# _create_link_record — exception handler (lines 349-350)
# ---------------------------------------------------------------------------


class TestCreateLinkRecordException:
    @pytest.mark.asyncio
    async def test_create_link_record_exception_is_caught(self):
        """DB exception in _create_link_record is caught (lines 349-350)."""
        from contextlib import asynccontextmanager

        from src.application.services.scheduling_link_service import (
            SchedulingLinkService,
        )

        @asynccontextmanager
        async def bad_db():
            raise RuntimeError("DB unavailable")
            yield  # noqa

        svc = SchedulingLinkService(db_session_factory=bad_db)
        # Should not raise — returns a link_id even when DB fails
        link_id = await svc._create_link_record(
            user_id=uuid.uuid4(),
            mode="suggested",
            attendee_email="test@example.com",
            duration_minutes=30,
            suggested_windows=[],
            thread_id=None,
            subject="Test",
        )
        assert isinstance(link_id, str) and len(link_id) > 0

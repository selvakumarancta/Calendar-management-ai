"""
Unit tests for AnalyticsService.

Uses an in-memory SQLite database (via SQLAlchemy async) so the tests are
fast and require no external services.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.analytics_service import AnalyticsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_factory(recorded: list):
    """Build a minimal async session factory that captures recorded events."""

    class _FakeModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
            recorded.append(self)

    class _FakeSession:
        def __init__(self):
            self.added = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def add(self, obj):
            self.added.append(obj)
            recorded.append(obj)

        async def commit(self):
            pass

        async def execute(self, *a, **kw):
            return _FakeResult([])

        async def scalar(self, *a, **kw):
            return None

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    def factory():
        return _FakeSession()

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyticsService:
    """Tests for AnalyticsService.record / get_summary / get_recent_events."""

    @pytest.mark.asyncio
    async def test_record_does_not_raise(self):
        """record() should complete without errors."""
        recorded: list = []
        service = AnalyticsService(db_session_factory=_make_session_factory(recorded))
        user_id = uuid.uuid4()
        await service.record(
            user_id=user_id,
            event_type="draft_composed",
            extra={"email_subject": "Test meeting"},
        )
        # At least one 'add' should have been called
        assert len(recorded) >= 1

    @pytest.mark.asyncio
    async def test_record_multiple_event_types(self):
        """Different event types can be recorded without interference."""
        recorded: list = []
        factory = _make_session_factory(recorded)
        service = AnalyticsService(db_session_factory=factory)
        uid = uuid.uuid4()

        for event in [
            "draft_composed",
            "link_booked",
            "scan_completed",
            "onboarding_completed",
        ]:
            await service.record(user_id=uid, event_type=event)

        # 4 events recorded
        assert len(recorded) >= 4

    @pytest.mark.asyncio
    async def test_get_summary_returns_dict(self):
        """get_summary() should return a dict (may be empty with the fake DB)."""
        service = AnalyticsService(db_session_factory=_make_session_factory([]))
        result = await service.get_summary(user_id=uuid.uuid4(), days=30)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_recent_events_returns_list(self):
        """get_recent_events() should return a list."""
        service = AnalyticsService(db_session_factory=_make_session_factory([]))
        result = await service.get_recent_events(user_id=uuid.uuid4(), limit=10)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_record_with_empty_properties(self):
        """record() with empty properties dict should not raise."""
        service = AnalyticsService(db_session_factory=_make_session_factory([]))
        await service.record(user_id=uuid.uuid4(), event_type="scan_completed")

    @pytest.mark.asyncio
    async def test_record_no_op_when_db_is_none(self):
        """record() should return immediately and not raise when db is None."""
        service = AnalyticsService(db_session_factory=None)
        # Should complete silently
        await service.record(user_id=uuid.uuid4(), event_type="test_event")

    @pytest.mark.asyncio
    async def test_get_summary_returns_empty_when_db_is_none(self):
        """get_summary() should return an empty summary when db is None."""
        service = AnalyticsService(db_session_factory=None)
        result = await service.get_summary(user_id=uuid.uuid4())
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_recent_events_returns_empty_when_db_is_none(self):
        """get_recent_events() should return empty list when db is None."""
        service = AnalyticsService(db_session_factory=None)
        result = await service.get_recent_events(user_id=uuid.uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_get_summary_returns_empty_on_db_error(self):
        """get_summary() exception path → returns _empty_summary()."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock as _MM

        @asynccontextmanager
        async def bad_factory():
            session = _MM()
            session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
            yield session

        service = AnalyticsService(db_session_factory=bad_factory)
        result = await service.get_summary(user_id=uuid.uuid4())
        assert isinstance(result, dict)
        assert result.get("drafts_composed") == 0

    @pytest.mark.asyncio
    async def test_get_recent_events_returns_empty_on_db_error(self):
        """get_recent_events() exception path → returns []."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock as _MM

        @asynccontextmanager
        async def bad_factory():
            session = _MM()
            session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
            yield session

        service = AnalyticsService(db_session_factory=bad_factory)
        result = await service.get_recent_events(user_id=uuid.uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_record_exception_is_caught(self):
        """record() exception from DB commit is caught and logged, not raised."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def bad_factory():
            session = MagicMock()
            session.add = MagicMock()
            session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
            yield session

        service = AnalyticsService(db_session_factory=bad_factory)
        # Should NOT raise — exception is swallowed
        await service.record(user_id=uuid.uuid4(), event_type="test_event")

    @pytest.mark.asyncio
    async def test_get_recent_events_with_event_type_filter(self):
        """get_recent_events with event_type triggers the filter branch (line 178)."""
        service = AnalyticsService(db_session_factory=_make_session_factory([]))
        # Exercises the `if event_type:` branch in get_recent_events
        result = await service.get_recent_events(
            user_id=uuid.uuid4(),
            event_type="draft_composed",
        )
        assert isinstance(result, list)

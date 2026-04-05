"""
Unit tests for OnboardingService.

All external dependencies (LLM, calendar, email provider, DB) are mocked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.onboarding_service import (
    OnboardingService,
    OnboardingStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_ID = uuid.uuid4()
USER_EMAIL = "alice@example.com"


def _fake_email_obj(subject: str = "Team sync confirmed", body: str = "Confirmed for Monday 2pm"):
    obj = MagicMock()
    obj.subject = subject
    obj.body_text = body
    obj.sender_email = "bob@example.com"
    obj.received_at = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc)
    return obj


def _fake_calendar_event():
    ev = MagicMock()
    ev.title = "Weekly sync"
    ev.start_time = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    ev.end_time = datetime(2026, 3, 10, 11, 0, tzinfo=timezone.utc)
    return ev


def _fake_session_factory():
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, *_):
            result = MagicMock()
            result.scalars.return_value.first.return_value = None
            result.scalars.return_value.all.return_value = []
            return result

        def add(self, *_):
            pass

        async def commit(self):
            pass

    def factory():
        return _FakeSession()

    return factory


# ---------------------------------------------------------------------------
# Tests — minimal dependencies (no LLM, no calendar)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_onboarding_completes_without_adapters():
    """
    run_onboarding with no adapters returns status=completed with zero counts.
    No exceptions raised.
    """
    svc = OnboardingService(
        llm_adapter=None,
        calendar_adapter=None,
        db_session_factory=_fake_session_factory(),
    )
    result = await svc.run_onboarding(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        user_timezone="UTC",
        email_provider=None,
    )
    assert result["status"] == "completed"
    assert result["calendar_events_backfilled"] == 0
    assert result["emails_analyzed"] == 0
    assert result["scheduling_guide_generated"] is False
    assert result["style_guide_generated"] is False
    assert result["errors"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_onboarding_no_db_still_runs():
    """run_onboarding with no DB factory completes without crashing."""
    svc = OnboardingService(llm_adapter=None, calendar_adapter=None, db_session_factory=None)
    result = await svc.run_onboarding(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        user_timezone="UTC",
        email_provider=None,
    )
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Tests — history gathering
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_history_gathered_from_calendar_and_email():
    """
    Calendar + email provider both returning data → emails_analyzed > 0
    and scheduling_guide_generated is True when LLM is provided.
    """
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[_fake_calendar_event()])
    calendar.create_event = AsyncMock(return_value={"id": "evt-1"})

    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(return_value=[_fake_email_obj()])

    llm = AsyncMock()
    # For backfill step: return null (no confirmed event to add)
    # For guide generation: return useful text
    llm.chat_completion = AsyncMock(side_effect=[
        "null",  # backfill extract → no event
        "· You prefer morning meetings (10am)\n· 30-min default duration",  # scheduling prefs
        "· Opens with 'Hi [name],'\n· Signs off 'Best'",  # style guide
    ])

    svc = OnboardingService(
        llm_adapter=llm,
        calendar_adapter=calendar,
        db_session_factory=_fake_session_factory(),
    )
    result = await svc.run_onboarding(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        user_timezone="America/New_York",
        email_provider=email_provider,
    )
    assert result["status"] == "completed"
    assert result["emails_analyzed"] >= 1
    assert result["scheduling_guide_generated"] is True
    assert result["style_guide_generated"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_backfill_adds_event_when_llm_confirms():
    """
    When LLM returns a valid confirmed event JSON during backfill,
    calendar.create_event is called once.
    """
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[])  # no conflict
    calendar.create_event = AsyncMock(return_value={"id": "evt-new"})

    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(return_value=[_fake_email_obj()])

    backfill_json = '{"summary": "Team sync", "start_iso": "2026-03-15T14:00:00+00:00", "end_iso": "2026-03-15T15:00:00+00:00"}'
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=[
        backfill_json,  # backfill extract → confirmed event
        "",             # scheduling prefs guide (empty)
        "",             # style guide (empty)
    ])

    svc = OnboardingService(
        llm_adapter=llm,
        calendar_adapter=calendar,
        db_session_factory=_fake_session_factory(),
    )
    result = await svc.run_onboarding(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        user_timezone="UTC",
        email_provider=email_provider,
    )
    assert result["calendar_events_backfilled"] == 1
    calendar.create_event.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — error resilience
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_calendar_error_in_backfill_does_not_crash():
    """If calendar adapter raises during backfill, onboarding still completes."""
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(side_effect=RuntimeError("Calendar API down"))
    calendar.create_event = AsyncMock()

    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(return_value=[])

    svc = OnboardingService(
        llm_adapter=None,
        calendar_adapter=calendar,
        db_session_factory=_fake_session_factory(),
    )
    result = await svc.run_onboarding(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        user_timezone="UTC",
        email_provider=email_provider,
    )
    # Should still complete — errors are collected, not re-raised
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_email_provider_error_does_not_crash():
    """If email provider raises during history gathering, onboarding still completes."""
    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(side_effect=RuntimeError("Token expired"))

    svc = OnboardingService(
        llm_adapter=None,
        calendar_adapter=None,
        db_session_factory=_fake_session_factory(),
    )
    result = await svc.run_onboarding(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        user_timezone="UTC",
        email_provider=email_provider,
    )
    assert result["status"] == "completed"
    assert result["emails_analyzed"] == 0


# ---------------------------------------------------------------------------
# Tests — get_onboarding_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_returns_not_started_when_no_db():
    """Without DB factory, get_onboarding_status returns 'not_started'."""
    svc = OnboardingService(db_session_factory=None)
    status = await svc.get_onboarding_status(USER_ID)
    assert status == OnboardingStatus.NOT_STARTED.value


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_returns_not_started_when_no_record():
    """DB with no record → 'not_started'."""
    svc = OnboardingService(db_session_factory=_fake_session_factory())
    status = await svc.get_onboarding_status(USER_ID)
    assert status == OnboardingStatus.NOT_STARTED.value

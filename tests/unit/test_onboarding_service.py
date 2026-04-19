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


def _fake_email_obj(
    subject: str = "Team sync confirmed", body: str = "Confirmed for Monday 2pm"
):
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
    svc = OnboardingService(
        llm_adapter=None, calendar_adapter=None, db_session_factory=None
    )
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
    llm.chat_completion = AsyncMock(
        side_effect=[
            "null",  # backfill extract → no event
            "· You prefer morning meetings (10am)\n· 30-min default duration",  # scheduling prefs
            "· Opens with 'Hi [name],'\n· Signs off 'Best'",  # style guide
        ]
    )

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
    llm.chat_completion = AsyncMock(
        side_effect=[
            backfill_json,  # backfill extract → confirmed event
            "",  # scheduling prefs guide (empty)
            "",  # style guide (empty)
        ]
    )

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
    email_provider.list_recent_emails = AsyncMock(
        side_effect=RuntimeError("Token expired")
    )

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


# ---------------------------------------------------------------------------
# Missing branch coverage tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_backfill_exception_is_captured_in_gather():
    """When _backfill_calendar_events raises, asyncio.gather captures it (lines 105-106)."""
    with patch.object(
        OnboardingService,
        "_backfill_calendar",
        new=AsyncMock(side_effect=RuntimeError("backfill boom")),
    ):
        svc = OnboardingService(db_session_factory=_fake_session_factory())
        result = await svc.run_onboarding(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            user_timezone="UTC",
            email_provider=None,
        )
    assert any("Backfill" in e for e in result.get("errors", []))
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_history_exception_is_captured_in_gather():
    """When _gather_history raises, asyncio.gather captures it (lines 113-115)."""
    with patch.object(
        OnboardingService,
        "_gather_history",
        new=AsyncMock(side_effect=RuntimeError("history boom")),
    ):
        svc = OnboardingService(db_session_factory=_fake_session_factory())
        result = await svc.run_onboarding(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            user_timezone="UTC",
            email_provider=None,
        )
    assert any("History" in e for e in result.get("errors", []))
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outer_except_in_run_onboarding():
    """When UserGuidesService.generate_all_guides raises, the outer except fires (lines 141-145)."""
    with patch(
        "src.application.services.user_guides_service.UserGuidesService"
    ) as mock_cls:
        mock_cls.return_value.generate_all_guides = AsyncMock(
            side_effect=RuntimeError("guides failed")
        )
        svc = OnboardingService(db_session_factory=_fake_session_factory())
        result = await svc.run_onboarding(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            user_timezone="UTC",
            email_provider=None,
        )
    assert result["status"] == "failed"
    assert len(result["errors"]) > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_backfill_skips_event_already_on_calendar():
    """When calendar.list_events returns results, the duplicate event is skipped (line 259)."""
    calendar = AsyncMock()
    # First check returns a conflicting event → skip; no more emails
    calendar.list_events = AsyncMock(return_value=["existing"])
    calendar.create_event = AsyncMock()

    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(return_value=[_fake_email_obj()])

    backfill_json = '{"summary": "Team sync", "start_iso": "2026-03-15T14:00:00+00:00", "end_iso": "2026-03-15T15:00:00+00:00"}'
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=[backfill_json, "", ""])

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
    # Event was found as existing → create_event NOT called
    calendar.create_event.assert_not_called()
    assert result["calendar_events_backfilled"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_backfill_inner_exception_per_email():
    """When create_event raises inside the backfill loop, it's caught per-email (lines 279-280)."""
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[])
    calendar.create_event = AsyncMock(side_effect=RuntimeError("create failed"))

    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(return_value=[_fake_email_obj()])

    backfill_json = '{"summary": "Team sync", "start_iso": "2026-03-15T14:00:00+00:00", "end_iso": "2026-03-15T15:00:00+00:00"}'
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=[backfill_json, "", ""])

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
    # Error is swallowed per-email; onboarding still completes
    assert result["status"] == "completed"
    assert result["calendar_events_backfilled"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_backfill_outer_exception():
    """When list_recent_emails raises, the outer backfill exception is caught (lines 282-283)."""
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[])

    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(
        side_effect=RuntimeError("provider down")
    )

    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=["", ""])  # guides

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
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_confirmed_event_returns_none_without_llm():
    """_extract_confirmed_event returns None when no LLM adapter is set (line 293)."""
    # Call the private method directly — it short-circuits at line 293 when no LLM
    svc = OnboardingService(llm_adapter=None)
    result = await svc._extract_confirmed_event(_fake_email_obj(), "UTC")
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_confirmed_event_llm_json_error():
    """Exception in _extract_confirmed_event LLM call is caught (lines 323-324)."""
    email_provider = AsyncMock()
    email_provider.list_recent_emails = AsyncMock(return_value=[_fake_email_obj()])

    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[])

    llm = AsyncMock()
    # First call (in _extract_confirmed_event) raises → caught at lines 323-324
    llm.chat_completion = AsyncMock(side_effect=[RuntimeError("LLM error"), "", ""])

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
    assert result["calendar_events_backfilled"] == 0
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_status_db_exception_returns_not_started():
    """DB exception in get_onboarding_status returns NOT_STARTED (lines 345-346)."""

    def broken_factory():
        class Broken:
            async def __aenter__(self):
                raise RuntimeError("DB exploded")

            async def __aexit__(self, *_):
                pass

        return Broken()

    svc = OnboardingService(db_session_factory=broken_factory)
    status = await svc.get_onboarding_status(USER_ID)
    assert status == OnboardingStatus.NOT_STARTED.value


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_status_updates_existing_record():
    """_save_onboarding_status updates an existing record's status (lines 370-371)."""
    existing_record = MagicMock()
    existing_record.status = "not_started"
    existing_record.updated_at = None

    def factory():
        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def execute(self, *_):
                r = MagicMock()
                r.scalars.return_value.first.return_value = existing_record
                return r

            def add(self, *_):
                pass

            async def commit(self):
                pass

        return Session()

    svc = OnboardingService(db_session_factory=factory)
    await svc._save_onboarding_status(USER_ID, OnboardingStatus.COMPLETED)
    assert existing_record.status == OnboardingStatus.COMPLETED.value


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_status_db_exception_is_caught():
    """DB exception in _save_onboarding_status is swallowed (lines 382-383)."""

    def broken_factory():
        class Broken:
            async def __aenter__(self):
                raise RuntimeError("DB down")

            async def __aexit__(self, *_):
                pass

        return Broken()

    svc = OnboardingService(db_session_factory=broken_factory)
    # Must not raise
    await svc._save_onboarding_status(USER_ID, OnboardingStatus.COMPLETED)

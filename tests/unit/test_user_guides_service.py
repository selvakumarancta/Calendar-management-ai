"""
Unit tests for UserGuidesService.

LLM and DB are mocked — no network or real database access.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.user_guides_service import UserGuidesService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_ID = uuid.uuid4()
USER_EMAIL = "alice@example.com"

_CALENDAR_EVENTS = [
    {"title": "1:1 with Bob", "start": "10:00", "end": "10:30", "day": "Monday", "date": "2026-03-10"},
    {"title": "Team sync", "start": "09:00", "end": "10:00", "day": "Wednesday", "date": "2026-03-12"},
    {"title": "Design review", "start": "14:00", "end": "15:00", "day": "Friday", "date": "2026-03-14"},
]

_SENT_EMAILS = [
    {"subject": "Re: Weekly call", "body": "Hi Bob, I'm free Monday 10am. Does that work?", "date": "2026-03-09", "sender": "alice@example.com"},
    {"subject": "Schedule sync", "body": "Let's meet Wednesday morning. I'll send a calendar invite.", "date": "2026-03-11", "sender": "alice@example.com"},
    {"subject": "Re: intro call", "body": "Happy to chat. How about Thursday 2pm? Best, Alice", "date": "2026-03-13", "sender": "alice@example.com"},
]

_SCHEDULING_GUIDE = "· You prefer morning meetings (9–11am)\n· You default to 30-minute 1:1s\n· You avoid back-to-back meetings"
_STYLE_GUIDE = "· You open with 'Hi [name],'\n· You keep replies concise (2–3 sentences)\n· You sign off with 'Best, Alice'"


def _make_llm(scheduling_response: str = _SCHEDULING_GUIDE, style_response: str = _STYLE_GUIDE) -> AsyncMock:
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=[scheduling_response, style_response])
    return llm


def _fake_session_factory(existing_guides: list | None = None):
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, *_):
            result = MagicMock()
            result.scalars.return_value.first.return_value = None
            result.scalars.return_value.all.return_value = existing_guides or []
            return result

        def add(self, *_):
            pass

        async def commit(self):
            pass

    def factory():
        return _FakeSession()

    return factory


# ---------------------------------------------------------------------------
# Tests — generate_all_guides
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_all_guides_returns_both():
    """generate_all_guides returns non-empty scheduling and style guides."""
    svc = UserGuidesService(
        llm_adapter=_make_llm(),
        db_session_factory=_fake_session_factory(),
    )
    scheduling, style = await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=_CALENDAR_EVENTS,
        sent_emails=_SENT_EMAILS,
    )
    assert scheduling == _SCHEDULING_GUIDE
    assert style == _STYLE_GUIDE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_calls_llm_twice():
    """LLM is called exactly twice: once for scheduling prefs, once for style."""
    llm = _make_llm()
    svc = UserGuidesService(llm_adapter=llm, db_session_factory=_fake_session_factory())
    await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=_CALENDAR_EVENTS,
        sent_emails=_SENT_EMAILS,
    )
    assert llm.chat_completion.call_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_without_llm_returns_empty_strings():
    """Without LLM adapter, both guides are empty strings."""
    svc = UserGuidesService(llm_adapter=None, db_session_factory=_fake_session_factory())
    scheduling, style = await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=_CALENDAR_EVENTS,
        sent_emails=_SENT_EMAILS,
    )
    assert scheduling == ""
    assert style == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_with_empty_emails_still_calls_scheduling_llm():
    """
    Even with no sent emails, scheduling prefs guide is generated (uses calendar events).
    Style guide returns empty (no emails to analyse).
    """
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(return_value=_SCHEDULING_GUIDE)
    svc = UserGuidesService(llm_adapter=llm, db_session_factory=_fake_session_factory())
    scheduling, style = await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=_CALENDAR_EVENTS,
        sent_emails=[],  # no sent emails
    )
    assert scheduling == _SCHEDULING_GUIDE
    assert style == ""  # _generate_email_style returns "" when no emails


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_with_no_calendar_events():
    """Works when calendar_events is empty — falls back to 'No recent events.'"""
    svc = UserGuidesService(
        llm_adapter=_make_llm(),
        db_session_factory=_fake_session_factory(),
    )
    scheduling, style = await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=[],
        sent_emails=_SENT_EMAILS,
    )
    assert scheduling == _SCHEDULING_GUIDE
    assert style == _STYLE_GUIDE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_exception_returns_empty_guide():
    """If LLM raises, the guide for that type is empty (no crash)."""
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=RuntimeError("API error"))
    svc = UserGuidesService(llm_adapter=llm, db_session_factory=_fake_session_factory())
    scheduling, style = await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=_CALENDAR_EVENTS,
        sent_emails=_SENT_EMAILS,
    )
    assert scheduling == ""
    assert style == ""


# ---------------------------------------------------------------------------
# Tests — get_user_guides
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_guides_no_db_returns_empty():
    """Without DB factory, get_user_guides returns two empty strings."""
    svc = UserGuidesService(db_session_factory=None)
    scheduling, style = await svc.get_user_guides(USER_ID)
    assert scheduling == ""
    assert style == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_guides_no_records_returns_empty():
    """DB with no guide records → returns two empty strings."""
    svc = UserGuidesService(db_session_factory=_fake_session_factory())
    scheduling, style = await svc.get_user_guides(USER_ID)
    assert scheduling == ""
    assert style == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_guides_returns_stored_guides():
    """DB with existing guide records → returns their content."""
    sched_guide = MagicMock()
    sched_guide.guide_type = "scheduling_preferences"
    sched_guide.content = _SCHEDULING_GUIDE

    style_guide = MagicMock()
    style_guide.guide_type = "email_style"
    style_guide.content = _STYLE_GUIDE

    svc = UserGuidesService(
        db_session_factory=_fake_session_factory(existing_guides=[sched_guide, style_guide])
    )
    scheduling, style = await svc.get_user_guides(USER_ID)
    assert scheduling == _SCHEDULING_GUIDE
    assert style == _STYLE_GUIDE


# ---------------------------------------------------------------------------
# Tests — generate result is plausible content
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduling_guide_passed_to_llm_contains_event_day():
    """
    The user message sent to the LLM should include the day of the week
    from structured calendar events.
    """
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(return_value=_SCHEDULING_GUIDE)

    svc = UserGuidesService(llm_adapter=llm, db_session_factory=_fake_session_factory())
    await svc.generate_all_guides(
        user_id=USER_ID,
        user_email=USER_EMAIL,
        calendar_events=_CALENDAR_EVENTS,
        sent_emails=[],
    )

    first_call_args = llm.chat_completion.call_args_list[0]
    messages = first_call_args.kwargs.get("messages") or first_call_args.args[0]
    user_message_content = next(
        m["content"] for m in messages if m["role"] == "user"
    )
    # The user prompt should contain the Monday event day
    assert "Monday" in user_message_content

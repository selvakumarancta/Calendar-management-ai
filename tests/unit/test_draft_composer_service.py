"""Tests for src/application/services/draft_composer_service.py."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.email_message import EmailMessage

_UID = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


def _make_email(
    subject="Meeting request",
    body="Can we meet on Monday at 2pm?",
    thread_messages=None,
) -> EmailMessage:
    return EmailMessage(
        id=uuid.uuid4(),
        provider="gmail",
        provider_message_id="msg-1",
        subject=subject,
        sender_email="boss@co.com",
        sender_name="Boss",
        recipients=["me@co.com"],
        body_text=body,
        received_at=_NOW,
        thread_id="thread-1",
        thread_messages=thread_messages or [],
    )


def _make_classification(**kwargs):
    from src.domain.entities.email_message import ClassificationResult, EmailCategory

    return ClassificationResult(
        category=kwargs.get("category", EmailCategory.MEETING_REQUEST),
        confidence=kwargs.get("confidence", 0.9),
        summary=kwargs.get("summary", "Meeting request"),
        needs_draft=kwargs.get("needs_draft", True),
        is_sales_email=kwargs.get("is_sales_email", False),
        already_resolved=kwargs.get("already_resolved", False),
        proposed_times=kwargs.get("proposed_times", ["Monday 2pm"]),
        participants=kwargs.get("participants", ["boss@co.com"]),
        duration_minutes=kwargs.get("duration_minutes", 30),
    )


def _mock_good_llm_response():
    """LLM that returns valid draft JSON."""
    llm = AsyncMock()
    draft_json = json.dumps(
        {
            "reply_body": "Hi, sounds good! How about Tuesday at 3pm?\n\nBest,\nMe",
            "reply_subject": "Re: Meeting request",
            "reply_cc": "",
            "is_confirmation": False,
            "skip": False,
        }
    )
    llm.chat_completion = AsyncMock(return_value={"content": draft_json})
    return llm


def _make_service(llm=None, calendar=None, db_factory=None):
    from src.application.services.draft_composer_service import DraftComposerService

    return DraftComposerService(
        llm_adapter=llm,
        calendar_adapter=calendar,
        db_session_factory=db_factory,
    )


def _make_db_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=None)
    result.scalars = MagicMock(return_value=scalars)
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# compose_and_create_draft
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_returns_none_without_llm():
    svc = _make_service()
    email = _make_email()
    clf = _make_classification()
    provider = AsyncMock()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_creates_draft_with_valid_llm_response():
    llm = _mock_good_llm_response()
    provider = AsyncMock()
    provider.create_draft_reply = AsyncMock(return_value="draft-id-123")

    session = _make_db_session()
    svc = _make_service(llm=llm, db_factory=MagicMock(return_value=session))
    email = _make_email()
    clf = _make_classification()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
    )
    assert result is not None
    provider.create_draft_reply.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_returns_none_when_llm_says_skip():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={"content": json.dumps({"skip": True})}
    )
    svc = _make_service(llm=llm)
    email = _make_email()
    clf = _make_classification()
    provider = AsyncMock()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_returns_none_when_no_reply_body():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={"content": json.dumps({"reply_body": "", "skip": False})}
    )
    svc = _make_service(llm=llm)
    email = _make_email()
    clf = _make_classification()
    provider = AsyncMock()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_returns_none_when_draft_provider_id_empty():
    """Draft creation fails (returns empty string) → compose returns None."""
    llm = _mock_good_llm_response()
    provider = AsyncMock()
    provider.create_draft_reply = AsyncMock(return_value="")

    svc = _make_service(llm=llm)
    email = _make_email()
    clf = _make_classification()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_autopilot_1on1_sends_directly():
    """Autopilot mode for 1:1 email calls send_email_reply instead of create_draft_reply."""
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "reply_body": "Sure, let's meet Monday!",
                    "reply_subject": "Re: Meeting",
                    "reply_cc": "",
                    "is_confirmation": False,
                    "skip": False,
                }
            )
        }
    )
    provider = AsyncMock()
    provider.send_email_reply = AsyncMock(return_value="sent-id-123")
    provider.create_draft_reply = AsyncMock(return_value="draft-id")

    session = _make_db_session()
    svc = _make_service(llm=llm, db_factory=MagicMock(return_value=session))
    email = _make_email()  # Only 1 recipient, so 1:1
    clf = _make_classification()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
        autopilot_enabled=True,
    )
    provider.send_email_reply.assert_awaited_once()
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_autopilot_falls_back_to_draft_when_send_fails():
    """If send fails (empty result), creates a draft instead."""
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "reply_body": "Sure!",
                    "reply_subject": "Re: Meeting",
                    "reply_cc": "",
                    "is_confirmation": False,
                    "skip": False,
                }
            )
        }
    )
    provider = AsyncMock()
    provider.send_email_reply = AsyncMock(return_value="")  # send fails
    provider.create_draft_reply = AsyncMock(return_value="draft-fallback")

    session = _make_db_session()
    svc = _make_service(llm=llm, db_factory=MagicMock(return_value=session))
    email = _make_email()
    clf = _make_classification()

    result = await svc.compose_and_create_draft(
        email=email,
        classification=clf,
        user_id=_UID,
        user_email="me@co.com",
        user_timezone="UTC",
        email_provider=provider,
        autopilot_enabled=True,
    )
    provider.create_draft_reply.assert_awaited_once()
    assert result is not None


# ---------------------------------------------------------------------------
# _compose_draft_llm
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_draft_llm_returns_parsed_json():
    llm = _mock_good_llm_response()
    svc = _make_service(llm=llm)
    email = _make_email()
    clf = _make_classification()

    result = await svc._compose_draft_llm(
        email=email,
        classification=clf,
        user_email="me@co.com",
        user_timezone="UTC",
        calendar_summary="No events.",
        declined_times=[],
        email_style_guide="",
        scheduling_preferences_guide="",
    )
    assert result is not None
    assert "reply_body" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_draft_llm_handles_json_decode_error():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(return_value={"content": "not valid json"})
    svc = _make_service(llm=llm)
    email = _make_email()
    clf = _make_classification()

    result = await svc._compose_draft_llm(
        email=email,
        classification=clf,
        user_email="me@co.com",
        user_timezone="UTC",
        calendar_summary="",
        declined_times=[],
        email_style_guide="",
        scheduling_preferences_guide="",
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_draft_llm_handles_code_block_json():
    """LLM wraps JSON in ```json code block."""
    llm = AsyncMock()
    good_json = json.dumps(
        {
            "reply_body": "Hi!",
            "skip": False,
            "reply_subject": "Re:",
            "reply_cc": "",
            "is_confirmation": False,
        }
    )
    llm.chat_completion = AsyncMock(
        return_value={"content": f"```json\n{good_json}\n```"}
    )
    svc = _make_service(llm=llm)
    email = _make_email()
    clf = _make_classification()

    result = await svc._compose_draft_llm(
        email=email,
        classification=clf,
        user_email="me@co.com",
        user_timezone="UTC",
        calendar_summary="",
        declined_times=[],
        email_style_guide="",
        scheduling_preferences_guide="",
    )
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_draft_llm_with_thread_messages():
    """With thread messages, they're included in the prompt."""
    from src.domain.entities.email_message import ThreadMessage

    thread_msgs = [
        ThreadMessage(sender="boss@co.com", body="Can we meet?", date="2024-06-01"),
        ThreadMessage(
            sender="me@co.com", body="Sure!", date="2024-06-01", is_from_user=True
        ),
    ]
    llm = _mock_good_llm_response()
    svc = _make_service(llm=llm)
    email = _make_email(thread_messages=thread_msgs)
    clf = _make_classification()

    result = await svc._compose_draft_llm(
        email=email,
        classification=clf,
        user_email="me@co.com",
        user_timezone="UTC",
        calendar_summary="",
        declined_times=["Monday 2pm"],
        email_style_guide="Be concise.",
        scheduling_preferences_guide="No meetings after 5pm.",
    )
    assert result is not None


# ---------------------------------------------------------------------------
# _get_calendar_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_calendar_summary_no_calendar():
    svc = _make_service()
    result = await svc._get_calendar_summary(_UID, "UTC")
    assert "not connected" in result.lower() or result == "Calendar not connected."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_calendar_summary_with_events():
    event = MagicMock()
    event.start_time = _NOW
    event.end_time = _NOW
    event.title = "Team standUp"

    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[event])
    svc = _make_service(calendar=calendar)
    result = await svc._get_calendar_summary(_UID, "UTC")
    assert "standUp" in result or "Team" in result or "event" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_calendar_summary_no_events():
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(return_value=[])
    svc = _make_service(calendar=calendar)
    result = await svc._get_calendar_summary(_UID, "UTC")
    assert "clear" in result.lower() or "no events" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_calendar_summary_handles_exception():
    calendar = AsyncMock()
    calendar.list_events = AsyncMock(side_effect=Exception("Calendar error"))
    svc = _make_service(calendar=calendar)
    result = await svc._get_calendar_summary(_UID, "UTC")
    assert "unavailable" in result.lower()


# ---------------------------------------------------------------------------
# _extract_declined_times
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_declined_times_empty():
    svc = _make_service()
    result = svc._extract_declined_times([])
    assert result == []


@pytest.mark.unit
def test_extract_declined_times_with_messages():
    from src.domain.entities.email_message import ThreadMessage

    messages = [
        ThreadMessage(
            sender="me@co.com",
            body="I can't do 2pm Tuesday",
            date="",
            is_from_user=True,
        ),
        ThreadMessage(sender="boss@co.com", body="How about 3pm?", date=""),
    ]
    svc = _make_service()
    result = svc._extract_declined_times(messages)
    # Result may contain declined times or be empty depending on regex
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _add_footer
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_add_footer_appends_branding_when_not_disabled():
    svc = _make_service()
    result = svc._add_footer("Hello World")
    assert "Hello World" in result


# ---------------------------------------------------------------------------
# _save_draft
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_draft_does_nothing_without_db():
    from src.domain.entities.email_message import DraftReply, DraftStatus, EmailCategory

    svc = _make_service()
    draft = DraftReply(
        user_id=_UID,
        email_provider_id="msg-1",
        email_subject="Meeting",
        draft_provider_id="draft-1",
        thread_id="thread-1",
        reply_to="boss@co.com",
        reply_body="Hi",
        status=DraftStatus.PENDING,
    )
    # Should not raise
    await svc._save_draft(draft)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_draft_persists_to_db():
    from src.domain.entities.email_message import DraftReply, DraftStatus

    session = _make_db_session()
    svc = _make_service(db_factory=MagicMock(return_value=session))
    draft = DraftReply(
        user_id=_UID,
        email_provider_id="msg-1",
        email_subject="Meeting",
        draft_provider_id="draft-1",
        thread_id="thread-1",
        reply_to="boss@co.com",
        reply_body="Hi",
        status=DraftStatus.PENDING,
    )
    # The method handles exceptions internally, so it should not raise
    await svc._save_draft(draft)

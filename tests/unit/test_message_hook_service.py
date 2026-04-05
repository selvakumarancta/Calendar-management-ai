"""
Unit tests for MessageHookService.

LLM and calendar adapters are mocked — no network or DB access.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.message_hook_service import MessageHookService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_ID = uuid.uuid4()

_MEETING_EXTRACTION = {
    "has_commitment": True,
    "confidence": 0.92,
    "event_summary": "Coffee chat with Alice",
    "proposed_start": "2026-04-10T14:00:00+00:00",
    "proposed_end": "2026-04-10T14:30:00+00:00",
    "duration_estimate_minutes": 30,
    "attendees": ["alice@example.com"],
    "location": None,
    "notes": "Alice wants to meet Friday",
    "is_question": False,
    "needs_reply": False,
}

_NO_COMMITMENT_EXTRACTION = {
    "has_commitment": False,
    "confidence": 0.2,
    "event_summary": None,
    "proposed_start": None,
    "proposed_end": None,
    "duration_estimate_minutes": None,
    "attendees": [],
    "location": None,
    "notes": "",
    "is_question": True,
    "needs_reply": True,
}


def _make_llm(extraction: dict) -> AsyncMock:
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(return_value=json.dumps(extraction))
    return llm


def _make_calendar() -> AsyncMock:
    calendar = AsyncMock()
    calendar.create_event = AsyncMock(return_value={"id": "evt-123", "title": "Coffee chat with Alice"})
    return calendar


# ---------------------------------------------------------------------------
# Tests — no LLM (heuristic fallback)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_llm_returns_not_detected():
    """Without an LLM adapter, process_message returns detected=False."""
    svc = MessageHookService(llm_adapter=None)
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Let's meet Monday at 9am",
        sender="alice@example.com",
    )
    assert result["detected"] is False


# ---------------------------------------------------------------------------
# Tests — LLM path: meeting detected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_meeting_detected_llm():
    """LLM returns has_commitment=True → detected is True with correct fields."""
    svc = MessageHookService(llm_adapter=_make_llm(_MEETING_EXTRACTION))
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Hey, can we grab coffee Friday at 2pm?",
        sender="alice@example.com",
        source="slack",
    )
    assert result["detected"] is True
    assert result["confidence"] == pytest.approx(0.92)
    assert result["event_summary"] == "Coffee chat with Alice"
    assert result["proposed_start"] == "2026-04-10T14:00:00+00:00"
    assert result["attendees"] == ["alice@example.com"]
    assert result["source"] == "slack"
    assert result["action"] == "suggested"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_auto_create_when_flag_false():
    """auto_create=False means event is only suggested, never created."""
    calendar = _make_calendar()
    svc = MessageHookService(
        llm_adapter=_make_llm(_MEETING_EXTRACTION),
        calendar_adapter=calendar,
    )
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Let's meet Friday 2pm",
        sender="alice@example.com",
        auto_create=False,
    )
    assert result["action"] == "suggested"
    calendar.create_event.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_create_above_threshold():
    """auto_create=True with confidence ≥ threshold → calendar event created."""
    calendar = _make_calendar()
    svc = MessageHookService(
        llm_adapter=_make_llm(_MEETING_EXTRACTION),
        calendar_adapter=calendar,
        auto_create_threshold=0.85,
    )
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Let's meet Friday 2pm",
        sender="alice@example.com",
        auto_create=True,
    )
    # 0.92 ≥ 0.85 and proposed_start is set → should have attempted creation
    calendar.create_event.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_create_below_threshold():
    """auto_create=True but confidence below threshold → still only suggested."""
    low_conf = {**_MEETING_EXTRACTION, "confidence": 0.70}
    calendar = _make_calendar()
    svc = MessageHookService(
        llm_adapter=_make_llm(low_conf),
        calendar_adapter=calendar,
        auto_create_threshold=0.85,
    )
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Let's meet Friday 2pm",
        sender="alice@example.com",
        auto_create=True,
    )
    assert result["action"] == "suggested"
    calendar.create_event.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_create_without_proposed_start():
    """auto_create=True but proposed_start is None → no event, action=suggested."""
    no_start = {**_MEETING_EXTRACTION, "proposed_start": None, "confidence": 0.95}
    calendar = _make_calendar()
    svc = MessageHookService(
        llm_adapter=_make_llm(no_start),
        calendar_adapter=calendar,
        auto_create_threshold=0.85,
    )
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Let's meet sometime next week",
        sender="alice@example.com",
        auto_create=True,
    )
    assert result["action"] == "suggested"
    calendar.create_event.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — LLM path: no commitment
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_commitment_detected():
    """LLM returns has_commitment=False → detected is False."""
    svc = MessageHookService(llm_adapter=_make_llm(_NO_COMMITMENT_EXTRACTION))
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Are you free sometime this week?",
        sender="bob@example.com",
    )
    assert result["detected"] is False
    assert result["confidence"] == pytest.approx(0.2)
    assert "reason" in result


# ---------------------------------------------------------------------------
# Tests — LLM error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_exception_returns_not_detected():
    """If LLM raises an exception, process_message returns detected=False gracefully."""
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    svc = MessageHookService(llm_adapter=llm)
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Call me tomorrow at 3pm",
        sender="carol@example.com",
    )
    assert result["detected"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_invalid_json_returns_not_detected():
    """If LLM returns non-JSON, process_message returns detected=False gracefully."""
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(return_value="sorry, I cannot help")
    svc = MessageHookService(llm_adapter=llm)
    result = await svc.process_message(
        user_id=USER_ID,
        message_text="Let's meet Monday",
        sender="dan@example.com",
    )
    assert result["detected"] is False

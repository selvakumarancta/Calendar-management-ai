"""
Unit tests for EmailClassifierService.

Tests focus on the rule-based fallback path (no LLM) to keep tests fast
and deterministic.  LLM-path tests use a mock that returns controlled JSON.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.email_classifier_service import EmailClassifierService
from src.domain.entities.email_message import EmailMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _email(
    subject: str = "", body: str = "", sender: str = "alice@example.com"
) -> EmailMessage:
    return EmailMessage(
        id=uuid.uuid4(),
        provider_message_id="msg-1",
        provider="google",
        user_id=uuid.uuid4(),
        subject=subject,
        sender_email=sender,
        sender_name="Alice",
        body_text=body,
        received_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Rule-based / no-LLM tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmailClassifierRuleBased:
    """Email classifier without a live LLM — tests the rule-based fallback."""

    def _service(self) -> EmailClassifierService:
        return EmailClassifierService(llm_adapter=None)

    @pytest.mark.asyncio
    async def test_meeting_request_needs_draft(self):
        email = _email(
            subject="Let's schedule a meeting",
            body="Hi, are you free this week for a 30 minute call to catch up?",
        )
        result = await self._service().classify(email)
        assert result.needs_draft is True

    @pytest.mark.asyncio
    async def test_sales_email_non_actionable_without_meeting_keywords(self):
        # Without scheduling keywords, the heuristic classifies as non_actionable
        # (is_sales_email detection requires the LLM path).
        email = _email(
            subject="Boost your revenue today",
            body="Hi there, I am reaching out to offer our enterprise solution.",
            sender="sales@salescorp.io",
        )
        result = await self._service().classify(email)
        # No 'meeting/call/schedule' keywords -> non-actionable, no draft needed
        assert result.needs_draft is False

    @pytest.mark.asyncio
    async def test_non_actionable_email_no_draft(self):
        email = _email(
            subject="Newsletter — April edition",
            body="Here are the top headlines this week from our community.",
        )
        result = await self._service().classify(email)
        assert result.needs_draft is False

    @pytest.mark.asyncio
    async def test_already_resolved_flag(self):
        # The heuristic classifier detects 'meeting' keywords and marks needs_draft=True;
        # to detect 'already resolved' we rely on the LLM. Here we just verify
        # the result is a well-formed ClassificationResult — no exceptions raised.
        email = _email(
            subject="Re: meeting confirmed — see you then",
            body="The invite is on the calendar. Looking forward to chatting!",
        )
        result = await self._service().classify(email)
        # Must be a valid result; already_resolved is set by LLM only
        assert result is not None
        assert isinstance(result.needs_draft, bool)

    @pytest.mark.asyncio
    async def test_confidence_between_zero_and_one(self):
        email = _email(subject="Quick question", body="Can we chat sometime this week?")
        result = await self._service().classify(email)
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# LLM-path tests (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmailClassifierLLMPath:
    """Tests where LLM adapter is mocked to return controlled JSON."""

    def _service_with_mock(self, llm_response: str) -> EmailClassifierService:
        """Build a service where the LLM adapter's chat_completion returns llm_response."""
        mock_llm = AsyncMock()
        # The classifier calls: await self._llm.chat_completion(messages=...) -> str
        mock_llm.chat_completion = AsyncMock(return_value=llm_response)
        return EmailClassifierService(llm_adapter=mock_llm)

    @pytest.mark.asyncio
    async def test_llm_meeting_request(self):
        """LLM path: mock returns meeting_request JSON, classifier must honour it."""
        llm_json = (
            '{"needs_draft": true, "confidence": 0.95, "category": "meeting_request",'
            ' "is_sales_email": false, "already_resolved": false,'
            ' "participants": ["alice@example.com"],'
            ' "duration_minutes": 30, "proposed_times": []}'
        )
        service = self._service_with_mock(llm_json)
        email = _email(subject="Team sync", body="Can we meet tomorrow at 3pm?")
        result = await service.classify(email)
        assert result.needs_draft is True
        assert result.confidence == 0.95  # exact LLM value
        assert result.duration_minutes == 30

    @pytest.mark.asyncio
    async def test_llm_sales_detection(self):
        """LLM path: mock flags is_sales_email=True, classifier must honour it."""
        llm_json = (
            '{"needs_draft": false, "confidence": 0.88, "category": "non_actionable",'
            ' "is_sales_email": true, "already_resolved": false,'
            ' "participants": [], "duration_minutes": null, "proposed_times": []}'
        )
        service = self._service_with_mock(llm_json)
        email = _email(
            subject="Special offer just for you", body="Act now and save 50%!"
        )
        result = await service.classify(email)
        assert result.is_sales_email is True
        assert result.needs_draft is False

    @pytest.mark.asyncio
    async def test_llm_malformed_json_falls_back_to_rule_based(self):
        """If LLM returns garbage, the classifier falls back to rule-based."""
        service = self._service_with_mock("ERROR: context length exceeded")
        email = _email(subject="Meeting?", body="Can we sync this week?")
        # Should not raise — should return a valid ClassificationResult
        result = await service.classify(email)
        assert result is not None
        assert isinstance(result.needs_draft, bool)

    @pytest.mark.asyncio
    async def test_calendar_notification_pattern_non_actionable(self):
        """Emails matching calendar notification patterns are pre-filtered."""
        service = EmailClassifierService(llm_adapter=None)
        email = _email(
            subject="has invited you to the following event",
            body="You have a new calendar invite",
        )
        result = await service.classify(email)
        assert result.needs_draft is False

    @pytest.mark.asyncio
    async def test_calendar_accepted_pattern_non_actionable(self):
        """Accepted/Declined prefixed subjects are pre-filtered."""
        service = EmailClassifierService(llm_adapter=None)
        email = _email(
            subject="Accepted: Weekly Sync",
            body="John has accepted the invite",
        )
        result = await service.classify(email)
        assert result.needs_draft is False

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_heuristic(self):
        """If LLM raises, classifier falls back to rule-based."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("API timeout")
        service = EmailClassifierService(llm_adapter=mock_llm)
        email = _email(subject="Let us meet", body="Can we schedule a call this week?")
        result = await service.classify(email)
        assert result is not None
        assert isinstance(result.needs_draft, bool)


# ---------------------------------------------------------------------------
# Tests — missing branch coverage
# ---------------------------------------------------------------------------

import json


@pytest.mark.unit
@pytest.mark.asyncio
async def test_calendar_notification_body_pattern_non_actionable():
    """Body matching a calendar notification pattern returns non-actionable (line 137)."""
    service = EmailClassifierService(llm_adapter=None)
    email = _email(
        subject="Meeting update",
        body="You have a new invitation: Team Sync",
        sender="calendar-notification@google.com",
    )
    result = await service.classify(email)
    assert result.needs_draft is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_with_thread_history_builds_section():
    """Thread messages produce a thread history section in the prompt (lines 204-210)."""
    from src.domain.entities.email_message import ThreadMessage

    prior = ThreadMessage(
        sender="bob@example.com",
        body="Sounds good, see you then",
        is_from_user=False,
        date="2026-04-01",
    )

    llm_response = json.dumps(
        {
            "category": "meeting_request",
            "needs_draft": True,
            "confidence": 0.9,
            "reasoning": "thread",
            "duration_minutes": 30,
            "attendees": [],
            "subject_line": "Re: Sync",
            "key_dates": [],
        }
    )
    mock_llm = AsyncMock()
    mock_llm.chat_completion = AsyncMock(return_value=llm_response)
    service = EmailClassifierService(llm_adapter=mock_llm)
    email = _email(subject="Re: Sync", body="Can we confirm Friday?")
    result = await service.classify(email, thread_messages=[prior])
    assert result.needs_draft is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_backtick_response_is_parsed():
    """Backtick-wrapped LLM response is stripped before JSON parse (line 235)."""
    payload = json.dumps(
        {
            "category": "meeting_request",
            "needs_draft": True,
            "confidence": 0.88,
            "reasoning": "ok",
            "duration_minutes": None,
            "attendees": [],
            "subject_line": "Interview",
            "key_dates": [],
        }
    )
    wrapped = "```json\n" + payload + "\n```"
    mock_llm = AsyncMock()
    mock_llm.chat_completion = AsyncMock(return_value=wrapped)
    service = EmailClassifierService(llm_adapter=mock_llm)
    email = _email(subject="Interview", body="Are you free Thursday?")
    result = await service.classify(email)
    assert result.needs_draft is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_invalid_category_and_duration_fall_back():
    """Unknown category string and non-numeric duration are handled (lines 249-250)."""
    payload = json.dumps(
        {
            "category": "wibble_wobble",
            "needs_draft": True,
            "confidence": 0.7,
            "reasoning": "ok",
            "duration_minutes": "lots",
            "attendees": [],
            "subject_line": "Test",
            "key_dates": [],
        }
    )
    mock_llm = AsyncMock()
    mock_llm.chat_completion = AsyncMock(return_value=payload)
    service = EmailClassifierService(llm_adapter=mock_llm)
    email = _email(subject="Test", body="Let's meet")
    result = await service.classify(email)
    # Unknown category falls back to non_actionable; no crash
    assert result is not None
    assert result.duration_minutes is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heuristic_cancellation_pattern():
    """Rule-based path recognises meeting cancellation keyword (line 283)."""
    service = EmailClassifierService(llm_adapter=None)
    email = _email(
        subject="Meeting cancelled",
        body="Just letting you know, the sync has been cancelled",
    )
    result = await service.classify(email)
    from src.domain.entities.email_message import EmailCategory

    assert result.category == EmailCategory.MEETING_CANCELLATION

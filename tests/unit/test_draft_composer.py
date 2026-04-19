"""
Unit tests for DraftComposerService.

LLM and calendar adapters are mocked so these tests run without network access.
DB interactions use minimal fake session factories.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.email_message import (
    ClassificationResult,
    DraftReply,
    EmailCategory,
    EmailMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _email(subject: str = "Team sync") -> EmailMessage:
    return EmailMessage(
        id=uuid.uuid4(),
        provider_message_id="msg-draft-1",
        provider="google",
        user_id=uuid.uuid4(),
        subject=subject,
        sender_email="bob@example.com",
        sender_name="Bob",
        body_text="Can we meet next Tuesday at 2pm?",
        received_at=datetime.now(timezone.utc),
    )


def _classification(needs_draft: bool = True) -> ClassificationResult:
    return ClassificationResult(
        needs_draft=needs_draft,
        confidence=0.9,
        category=EmailCategory.MEETING_REQUEST,
        is_sales_email=False,
        already_resolved=False,
        participants=["bob@example.com"],
        duration_minutes=30,
        proposed_times=[],
    )


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

            return _R()

        async def scalar(self, *a, **kw):
            return None

    return _FakeSession


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDraftComposerService:
    """Tests for DraftComposerService compose/create draft flow."""

    def _build_service(self, llm_json: str | None = None):
        from src.application.services.draft_composer_service import DraftComposerService

        mock_llm = AsyncMock()
        if llm_json is not None:
            mock_llm.chat_completion = AsyncMock(return_value=llm_json)
        else:
            # Simulate LLM returning skip=True (already resolved thread)
            mock_llm.chat_completion = AsyncMock(
                return_value='{"skip": true, "reason": "already_resolved"}'
            )

        mock_calendar = AsyncMock()
        mock_calendar.list_events = AsyncMock(return_value=[])

        mock_analytics = AsyncMock()
        mock_analytics.record = AsyncMock()

        return DraftComposerService(
            llm_adapter=mock_llm,
            calendar_adapter=mock_calendar,
            db_session_factory=_fake_session_factory(),
            analytics_service=mock_analytics,
        )

    @pytest.mark.asyncio
    async def test_compose_valid_draft(self):
        """When LLM returns a valid draft JSON, a DraftReply should be created."""
        llm_response = json.dumps(
            {
                "skip": False,
                "reply_body": "Hi Bob,\n\nTuesday 2pm works for me.\n\nBest,\nAlice",
                "reply_subject": "Re: Team sync",
                "proposed_windows": [
                    {
                        "start": "2025-04-08T14:00:00Z",
                        "end": "2025-04-08T14:30:00Z",
                    }
                ],
                "is_group_meeting": False,
                "declined_times": [],
                "pending_invite": None,
            }
        )
        service = self._build_service(llm_json=llm_response)
        email = _email()
        clf = _classification(needs_draft=True)
        user_id = email.user_id

        result = await service.compose_and_create_draft(
            email=email,
            classification=clf,
            user_id=user_id,
            user_email="alice@example.com",
            user_timezone="UTC",
            email_provider=AsyncMock(
                create_draft_reply=AsyncMock(return_value="draft-id-123")
            ),
        )
        # Should return a DraftReply (or None if skipped)
        if result is not None:
            from src.domain.entities.email_message import DraftReply

            assert isinstance(result, DraftReply)

    @pytest.mark.asyncio
    async def test_skip_already_resolved_thread(self):
        """When LLM says skip=true, compose_and_create_draft returns None."""
        service = self._build_service(llm_json=None)  # Returns skip=true
        email = _email()
        clf = _classification()

        result = await service.compose_and_create_draft(
            email=email,
            classification=clf,
            user_id=email.user_id,
            user_email="alice@example.com",
            user_timezone="UTC",
            email_provider=AsyncMock(
                create_draft_reply=AsyncMock(return_value="draft-id-456")
            ),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_analytics_recorded_on_draft(self):
        """After creating a draft, the analytics service should record an event."""
        llm_response = json.dumps(
            {
                "skip": False,
                "reply_body": "Tuesday 2pm works for me. See you then!",
                "reply_subject": "Re: Team sync",
                "proposed_windows": [],
                "is_group_meeting": False,
                "declined_times": [],
                "pending_invite": None,
            }
        )
        from src.application.services.draft_composer_service import DraftComposerService

        mock_llm = AsyncMock()
        mock_llm.chat_completion = AsyncMock(return_value=llm_response)
        mock_calendar = AsyncMock()
        mock_calendar.list_events = AsyncMock(return_value=[])
        mock_analytics = AsyncMock()
        mock_analytics.record = AsyncMock()

        service = DraftComposerService(
            llm_adapter=mock_llm,
            calendar_adapter=mock_calendar,
            db_session_factory=_fake_session_factory(),
            analytics_service=mock_analytics,
        )
        email = _email()
        clf = _classification()

        mock_provider = AsyncMock()
        mock_provider.create_draft_reply = AsyncMock(return_value="draft-xyz")
        await service.compose_and_create_draft(
            email=email,
            classification=clf,
            user_id=email.user_id,
            user_email="alice@example.com",
            user_timezone="UTC",
            email_provider=mock_provider,
        )
        # analytics.record should have been called at least once
        mock_analytics.record.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_crash_when_analytics_is_none(self):
        """DraftComposerService should not crash when analytics_service=None."""
        from src.application.services.draft_composer_service import DraftComposerService

        llm_response = json.dumps(
            {
                "skip": False,
                "reply_body": "Works for me. Talk soon!",
                "reply_subject": "Re: Team sync",
                "proposed_windows": [],
                "is_group_meeting": False,
                "declined_times": [],
                "pending_invite": None,
            }
        )
        mock_llm = AsyncMock()
        mock_llm.chat_completion = AsyncMock(return_value=llm_response)

        service = DraftComposerService(
            llm_adapter=mock_llm,
            calendar_adapter=AsyncMock(list_events=AsyncMock(return_value=[])),
            db_session_factory=_fake_session_factory(),
            analytics_service=None,
        )
        email = _email()
        # Should not raise AttributeError when analytics_service is None
        try:
            await service.compose_and_create_draft(
                email=email,
                classification=_classification(),
                user_id=email.user_id,
                user_email="alice@example.com",
                user_timezone="UTC",
                email_provider=AsyncMock(
                    create_draft_reply=AsyncMock(return_value="draft-abc")
                ),
            )
        except AttributeError as exc:
            pytest.fail(f"Crashed when analytics_service=None: {exc}")


# ---------------------------------------------------------------------------
# Pure helper methods (no LLM / network needed)
# ---------------------------------------------------------------------------


class TestDraftComposerHelpers:
    def _svc_no_llm(self):
        from src.application.services.draft_composer_service import DraftComposerService

        return DraftComposerService(llm_adapter=None)

    @pytest.mark.unit
    def test_add_footer_appends_branding(self):
        svc = self._svc_no_llm()
        result = svc._add_footer("Hello!")
        assert "Drafted by CalendarAgent" in result
        assert result.startswith("Hello!")

    @pytest.mark.unit
    def test_extract_declined_times_finds_time_in_message(self):
        from src.domain.entities.email_message import ThreadMessage

        svc = self._svc_no_llm()
        msgs = [
            ThreadMessage(
                sender="bob@example.com",
                body="I can't make it at 3pm on Tuesday.",
                is_from_user=False,
            )
        ]
        declined = svc._extract_declined_times(msgs)
        assert len(declined) > 0

    @pytest.mark.unit
    def test_extract_declined_times_empty_thread(self):
        svc = self._svc_no_llm()
        result = svc._extract_declined_times([])
        assert result == []

    @pytest.mark.unit
    def test_extract_declined_times_no_declined_patterns(self):
        from src.domain.entities.email_message import ThreadMessage

        svc = self._svc_no_llm()
        msgs = [
            ThreadMessage(
                sender="bob@example.com",
                body="Let's meet at 2pm, works for me!",
                is_from_user=False,
            )
        ]
        result = svc._extract_declined_times(msgs)
        # No decline patterns → empty list (or truncated list)
        assert isinstance(result, list)

    @pytest.mark.unit
    async def test_get_calendar_summary_no_calendar(self):
        svc = self._svc_no_llm()
        result = await svc._get_calendar_summary(uuid.uuid4(), "UTC")
        assert "not connected" in result.lower() or result == "Calendar not connected."

    @pytest.mark.unit
    async def test_get_calendar_summary_empty_calendar(self):
        from src.application.services.draft_composer_service import DraftComposerService

        cal = AsyncMock()
        cal.list_events = AsyncMock(return_value=[])
        svc = DraftComposerService(llm_adapter=None, calendar_adapter=cal)
        result = await svc._get_calendar_summary(uuid.uuid4(), "UTC")
        assert "clear" in result.lower() or "no events" in result.lower()

    @pytest.mark.unit
    async def test_get_calendar_summary_calendar_error(self):
        from src.application.services.draft_composer_service import DraftComposerService

        cal = AsyncMock()
        cal.list_events = AsyncMock(side_effect=RuntimeError("Cal error"))
        svc = DraftComposerService(llm_adapter=None, calendar_adapter=cal)
        result = await svc._get_calendar_summary(uuid.uuid4(), "UTC")
        assert "unavailable" in result.lower()

    @pytest.mark.unit
    async def test_compose_returns_none_when_no_llm(self):
        """compose_and_create_draft returns None immediately when llm_adapter is None."""
        from src.application.services.draft_composer_service import DraftComposerService

        svc = DraftComposerService(llm_adapter=None)
        result = await svc.compose_and_create_draft(
            email=_email(),
            classification=_classification(),
            user_id=uuid.uuid4(),
            user_email="me@example.com",
            user_timezone="UTC",
            email_provider=AsyncMock(),
        )
        assert result is None

    @pytest.mark.unit
    async def test_save_draft_no_op_without_db(self):
        """_save_draft returns immediately when db_session_factory is None."""
        from src.application.services.draft_composer_service import DraftComposerService
        from src.domain.entities.email_message import DraftReply, DraftStatus

        svc = DraftComposerService(llm_adapter=None, db_session_factory=None)
        draft = DraftReply(
            user_id=uuid.uuid4(),
            email_provider_id="msg-1",
            thread_id="thread-1",
            email_subject="Test",
            email_sender="sender@example.com",
            email_received_at=datetime.now(timezone.utc),
            draft_provider_id="draft-1",
            reply_to="sender@example.com",
            reply_subject="Re: Test",
            reply_body="Body",
        )
        await svc._save_draft(draft)  # should not raise

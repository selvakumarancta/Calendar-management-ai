"""
Tests for Email Intelligence — deterministic analysis, datetime resolution, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.application.services.email_intelligence_service import (
    EmailIntelligenceService,
)
from src.domain.entities.email_message import (
    EmailCategory,
    EmailMessage,
    SuggestionPriority,
)


@pytest.mark.unit
class TestDeterministicAnalysis:
    """Test the regex-based deterministic email analysis (no LLM needed)."""

    def _make_email(self, subject: str = "", body: str = "") -> EmailMessage:
        return EmailMessage(
            id=uuid.uuid4(),
            provider_message_id="test-123",
            provider="google",
            user_id=uuid.uuid4(),
            subject=subject,
            sender_email="alice@example.com",
            sender_name="Alice",
            body_text=body,
            received_at=datetime.now(timezone.utc),
        )

    def _service(self) -> EmailIntelligenceService:
        return EmailIntelligenceService()

    def test_meeting_request_in_subject(self) -> None:
        email = self._make_email(subject="Let's schedule a meeting for project review")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.MEETING_REQUEST

    def test_zoom_call_detected(self) -> None:
        email = self._make_email(subject="Zoom meeting tomorrow at 3pm")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.MEETING_REQUEST

    def test_standup_detected(self) -> None:
        email = self._make_email(subject="Daily standup — please join")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.MEETING_REQUEST

    def test_cancellation_detected(self) -> None:
        email = self._make_email(subject="Meeting cancelled: Project sync")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.MEETING_CANCELLATION

    def test_deadline_detected(self) -> None:
        email = self._make_email(
            subject="Report deadline reminder", body="Please submit by end of day"
        )
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.DEADLINE_REMINDER
        assert result.urgency == SuggestionPriority.HIGH

    def test_non_actionable_email(self) -> None:
        email = self._make_email(
            subject="Newsletter: Weekly updates",
            body="Here are your weekly highlights...",
        )
        result = self._service()._deterministic_analysis(email)
        # Should return None for non-actionable
        assert result is None

    def test_teams_meeting_in_body(self) -> None:
        email = self._make_email(
            subject="Follow up on project",
            body="Can we set up a Teams meeting to discuss this?",
        )
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True

    def test_invitation_detected(self) -> None:
        email = self._make_email(subject="Invitation to the quarterly review")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.category == EmailCategory.MEETING_REQUEST

    def test_simple_meeting_subject(self) -> None:
        """Just 'Meeting' in subject should be detected."""
        email = self._make_email(subject="Meeting")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.MEETING_REQUEST

    def test_google_calendar_notification(self) -> None:
        """Google Calendar notification format should be detected."""
        email = self._make_email(
            subject="Notification: TestEvent @ Wed Apr 1, 2026 12:30am - 1:30am (IST)"
        )
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True
        assert result.category == EmailCategory.EVENT_INVITATION
        assert "TestEvent" in result.suggested_title

    def test_conference_detected(self) -> None:
        email = self._make_email(subject="Annual conference registration open")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True

    def test_demo_detected(self) -> None:
        email = self._make_email(subject="Portrait Demo and Two Interesting Points")
        result = self._service()._deterministic_analysis(email)
        assert result is not None
        assert result.is_actionable is True


@pytest.mark.unit
class TestDateTimeResolution:
    """Test datetime parsing from email text."""

    def test_tomorrow(self) -> None:
        ref = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        start, end = EmailIntelligenceService._resolve_datetime(
            "tomorrow", "10:00 AM", 30, ref
        )
        assert start is not None
        assert start.day == 2
        assert start.hour == 10
        assert end is not None
        assert (end - start).total_seconds() == 30 * 60

    def test_today(self) -> None:
        ref = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        start, end = EmailIntelligenceService._resolve_datetime(
            "today", "2:00 PM", 60, ref
        )
        assert start is not None
        assert start.day == 1
        assert start.hour == 14

    def test_weekday_name(self) -> None:
        ref = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)  # Wednesday
        start, _ = EmailIntelligenceService._resolve_datetime(
            "friday", "9:00 AM", 30, ref
        )
        assert start is not None
        assert start.weekday() == 4  # Friday

    def test_no_date_no_time(self) -> None:
        ref = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        start, end = EmailIntelligenceService._resolve_datetime("", "", 30, ref)
        assert start is None
        assert end is None

    def test_iso_date(self) -> None:
        ref = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        start, _ = EmailIntelligenceService._resolve_datetime(
            "2026-04-15", "3:00 PM", 45, ref
        )
        assert start is not None
        assert start.day == 15
        assert start.hour == 15


@pytest.mark.unit
class TestTimeExtraction:
    """Test time extraction from text."""

    def test_extract_time_am_pm(self) -> None:
        result = EmailIntelligenceService._extract_time(
            "Let's meet at 3:00 PM tomorrow"
        )
        assert "3:00 PM" in result or "3:00 pm" in result.lower()

    def test_extract_time_24h(self) -> None:
        result = EmailIntelligenceService._extract_time("Meeting at 14:30")
        assert "14:30" in result

    def test_extract_date_tomorrow(self) -> None:
        result = EmailIntelligenceService._extract_date("Let's meet tomorrow at noon")
        assert "tomorrow" in result.lower()

    def test_extract_date_weekday(self) -> None:
        result = EmailIntelligenceService._extract_date("Can we do Monday?")
        assert "monday" in result.lower()


@pytest.mark.unit
class TestEmailEntities:
    """Test email domain entities."""

    def test_email_body_preview_short(self) -> None:
        email = EmailMessage(body_text="Short body", body_snippet="")
        assert email.body_preview == "Short body"

    def test_email_body_preview_long(self) -> None:
        email = EmailMessage(body_text="A" * 300, body_snippet="")
        assert len(email.body_preview) == 203  # 200 + "..."
        assert email.body_preview.endswith("...")

    def test_email_body_preview_uses_snippet(self) -> None:
        email = EmailMessage(body_text="Full body", body_snippet="Snippet text")
        assert email.body_preview == "Snippet text"

    def test_schedule_suggestion_defaults(self) -> None:
        from src.domain.entities.email_message import (
            ScheduleSuggestion,
            SuggestionStatus,
        )

        suggestion = ScheduleSuggestion()
        assert suggestion.status == SuggestionStatus.PENDING
        assert suggestion.has_conflict is False
        assert suggestion.attendees == []

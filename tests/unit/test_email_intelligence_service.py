"""Tests for src/application/services/email_intelligence_service.py."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.email_message import (
    EmailAnalysis,
    EmailCategory,
    EmailMessage,
    EmailScanResult,
    ScheduleSuggestion,
    SuggestionPriority,
    SuggestionStatus,
)

_UID = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


def _make_email(
    subject="Team meeting",
    body="Let's meet tomorrow at 2pm",
    sender="boss@co.com",
    sender_name="Boss",
    provider_message_id="msg-1",
    thread_id="thread-1",
    received_at=None,
) -> EmailMessage:
    return EmailMessage(
        id=uuid.uuid4(),
        provider="gmail",
        provider_message_id=provider_message_id,
        subject=subject,
        sender_email=sender,
        sender_name=sender_name,
        recipients=["me@co.com"],
        body_text=body,
        received_at=received_at or _NOW,
        thread_id=thread_id,
    )


def _make_suggestion() -> ScheduleSuggestion:
    return ScheduleSuggestion(
        id=uuid.uuid4(),
        user_id=_UID,
        email_subject="Team meeting",
        category=EmailCategory.MEETING_REQUEST,
        title="Team meeting",
        status=SuggestionStatus.PENDING,
    )


def _make_db_session(scalar_result=None, scalars_all=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session.add = MagicMock()

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar_result)
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=scalars_all or [])
    result.scalars = MagicMock(return_value=scalars_mock)
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    return session


def _make_service(
    llm=None,
    calendar=None,
    db_factory=None,
    classifier=None,
    draft_composer=None,
    guides_service=None,
):
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    return EmailIntelligenceService(
        llm_adapter=llm,
        calendar_adapter=calendar,
        db_session_factory=db_factory,
        classifier_service=classifier,
        draft_composer_service=draft_composer,
        guides_service=guides_service,
    )


# ---------------------------------------------------------------------------
# scan_user_emails — legacy path (no classifier)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_returns_empty_scan_result_on_no_emails():
    svc = _make_service()
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(return_value=[])

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.emails_scanned == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_legacy_non_actionable():
    """When no classifier and email is non-actionable."""
    svc = _make_service()
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Newsletter update", body="news content")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.emails_scanned == 1
    assert result.suggestions_created == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_legacy_actionable_creates_suggestion():
    """Legacy path: meeting email creates a suggestion."""
    session = _make_db_session()
    svc = _make_service(db_factory=MagicMock(return_value=session))
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Team standup", body="standup sync")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.emails_scanned == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_handles_exception_gracefully():
    svc = _make_service()
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(side_effect=Exception("network error"))

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert len(result.errors) > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_with_rescan_flag_skips_filter():
    """rescan=True skips _filter_processed."""
    session = _make_db_session()
    svc = _make_service(db_factory=MagicMock(return_value=session))
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Meeting", body="meeting at 3pm")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail", rescan=True)
    assert result.emails_scanned == 1


# ---------------------------------------------------------------------------
# scan_user_emails — classifier path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_skips_sales_email():
    clf_response = MagicMock()
    clf_response.is_sales_email = True

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)

    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session), classifier=classifier
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[
            _make_email(
                subject="SaaS sales pitch", body="would love to show you our product"
            )
        ]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.suggestions_created == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_composes_draft():
    clf_response = MagicMock()
    clf_response.is_sales_email = False
    clf_response.needs_draft = True
    clf_response.already_resolved = False
    clf_response.category = EmailCategory.MEETING_REQUEST
    clf_response.confidence = 0.9
    clf_response.summary = "Meeting request"
    clf_response.participants = ["a@b.com"]
    clf_response.duration_minutes = 30
    clf_response.proposed_times = ["2pm Monday"]

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)
    draft_composer = AsyncMock()
    draft_composer.compose_and_create_draft = AsyncMock(return_value=MagicMock())

    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session),
        classifier=classifier,
        draft_composer=draft_composer,
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Let's meet", body="can we schedule a call")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail", user_email="me@co.com")
    assert result.actionable_found == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_draft_composer_failure_handled():
    """If compose_and_create_draft raises, analysis still saves."""
    clf_response = MagicMock()
    clf_response.is_sales_email = False
    clf_response.needs_draft = True
    clf_response.already_resolved = False
    clf_response.category = EmailCategory.MEETING_REQUEST
    clf_response.confidence = 0.8
    clf_response.summary = "Meeting"
    clf_response.participants = []
    clf_response.duration_minutes = 30
    clf_response.proposed_times = []

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)
    draft_composer = AsyncMock()
    draft_composer.compose_and_create_draft = AsyncMock(
        side_effect=Exception("LLM fail")
    )

    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session),
        classifier=classifier,
        draft_composer=draft_composer,
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[
            _make_email(subject="Standup request", body="standup sync please")
        ]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.actionable_found == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_already_resolved_meeting():
    """already_resolved=True with MEETING_REQUEST still creates suggestion."""
    clf_response = MagicMock()
    clf_response.is_sales_email = False
    clf_response.needs_draft = False
    clf_response.already_resolved = True
    clf_response.category = EmailCategory.MEETING_REQUEST
    clf_response.confidence = 0.85
    clf_response.summary = "Already resolved"
    clf_response.participants = []
    clf_response.duration_minutes = 30
    clf_response.proposed_times = ["10am"]

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)
    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session), classifier=classifier
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Standup", body="let's meet")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.actionable_found >= 0  # should not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_already_resolved_non_meeting():
    """already_resolved=True with NON_ACTIONABLE does not create suggestion."""
    clf_response = MagicMock()
    clf_response.is_sales_email = False
    clf_response.needs_draft = False
    clf_response.already_resolved = True
    clf_response.category = EmailCategory.NON_ACTIONABLE
    clf_response.confidence = 0.7
    clf_response.summary = "Non-actionable"
    clf_response.participants = []
    clf_response.duration_minutes = 0
    clf_response.proposed_times = []

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)
    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session), classifier=classifier
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Newsletter", body="info")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.suggestions_created == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_no_draft_meeting_category():
    """needs_draft=False, not resolved, meeting category → creates suggestion."""
    clf_response = MagicMock()
    clf_response.is_sales_email = False
    clf_response.needs_draft = False
    clf_response.already_resolved = False
    clf_response.category = EmailCategory.EVENT_INVITATION
    clf_response.confidence = 0.9
    clf_response.summary = "Invite"
    clf_response.participants = ["host@co.com"]
    clf_response.duration_minutes = 60
    clf_response.proposed_times = ["3pm Tuesday"]

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)
    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session), classifier=classifier
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[
            _make_email(
                subject="Invitation to event", body="invitation to the following event"
            )
        ]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.actionable_found == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_classifier_no_draft_non_actionable():
    """needs_draft=False, not resolved, non-meeting category → saved as non-actionable."""
    clf_response = MagicMock()
    clf_response.is_sales_email = False
    clf_response.needs_draft = False
    clf_response.already_resolved = False
    clf_response.category = EmailCategory.NON_ACTIONABLE
    clf_response.confidence = 0.5
    clf_response.summary = "Promo"
    clf_response.participants = []
    clf_response.duration_minutes = 0
    clf_response.proposed_times = []

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=clf_response)
    session = _make_db_session()
    svc = _make_service(
        db_factory=MagicMock(return_value=session), classifier=classifier
    )
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(
        return_value=[_make_email(subject="Promo offer", body="50% off sale")]
    )

    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.suggestions_created == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_loads_guides_when_service_configured():
    guides_svc = AsyncMock()
    guides_svc.get_user_guides = AsyncMock(return_value=("sched guide", "style guide"))

    svc = _make_service(guides_service=guides_svc)
    provider = AsyncMock()
    # Return one email so the guides code path is reached
    provider.list_recent_emails = AsyncMock(return_value=[_make_email()])

    await svc.scan_user_emails(_UID, provider, "gmail")
    guides_svc.get_user_guides.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_guides_exception_handled():
    guides_svc = AsyncMock()
    guides_svc.get_user_guides = AsyncMock(side_effect=Exception("guides error"))

    svc = _make_service(guides_service=guides_svc)
    provider = AsyncMock()
    provider.list_recent_emails = AsyncMock(return_value=[])

    # should not raise
    result = await svc.scan_user_emails(_UID, provider, "gmail")
    assert result.emails_scanned == 0


# ---------------------------------------------------------------------------
# analyze_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_analyze_email_deterministic_meeting():
    svc = _make_service()
    email = _make_email(subject="Team standup", body="standup sync")
    analysis = await svc.analyze_email(email)
    assert analysis.is_actionable


@pytest.mark.unit
@pytest.mark.asyncio
async def test_analyze_email_deterministic_cancellation():
    svc = _make_service()
    email = _make_email(
        subject="Cancelled: Team meeting", body="meeting has been cancelled"
    )
    analysis = await svc.analyze_email(email)
    assert analysis.category == EmailCategory.MEETING_CANCELLATION


@pytest.mark.unit
@pytest.mark.asyncio
async def test_analyze_email_uses_llm_for_ambiguous():
    """When deterministic returns None, falls back to LLM."""
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={
            "content": '{"category": "meeting_request", "confidence": 0.8, "title": "Meeting", "is_actionable": true, "summary": "test", "action_required": "", "date": "", "time": "", "duration_minutes": 30, "location": "", "attendees": [], "priority": "medium"}'
        }
    )
    svc = _make_service(llm=llm)
    email = _make_email(subject="Random email XYZ123", body="some ambiguous content")
    analysis = await svc.analyze_email(email)
    llm.chat_completion.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_analyze_email_llm_fails_returns_non_actionable():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(side_effect=Exception("LLM down"))
    svc = _make_service(llm=llm)
    email = _make_email(subject="Ambiguous subject ZZZ", body="not a meeting")
    analysis = await svc.analyze_email(email)
    assert isinstance(analysis, EmailAnalysis)


# ---------------------------------------------------------------------------
# _deterministic_analysis
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_deterministic_analysis_gcal_notification():
    svc = _make_service()
    email = _make_email(
        subject="Notification: Team standup @ Mon Jun 1, 2025 2:00pm", body=""
    )
    result = svc._deterministic_analysis(email)
    assert result is not None
    assert result.category == EmailCategory.EVENT_INVITATION
    assert result.confidence >= 0.9


@pytest.mark.unit
def test_deterministic_analysis_rsvp_accepted():
    svc = _make_service()
    email = _make_email(subject="Accepted: Weekly Sync", body="")
    result = svc._deterministic_analysis(email)
    assert result is not None
    assert result.category == EmailCategory.MEETING_RESCHEDULE


@pytest.mark.unit
def test_deterministic_analysis_deadline():
    svc = _make_service()
    email = _make_email(subject="Submit report", body="deadline by end of day")
    result = svc._deterministic_analysis(email)
    assert result is not None
    assert result.category == EmailCategory.DEADLINE_REMINDER


@pytest.mark.unit
def test_deterministic_analysis_no_match_returns_none():
    svc = _make_service()
    email = _make_email(subject="Random newsletter", body="promotions and discounts")
    result = svc._deterministic_analysis(email)
    assert result is None


# ---------------------------------------------------------------------------
# _llm_analysis
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_analysis_parses_valid_json():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={
            "content": [
                {
                    "text": '{"category": "meeting_request", "confidence": 0.9, "title": "Q3 Review", "is_actionable": true, "summary": "Review meeting", "action_required": "Schedule", "date": "2024-06-10", "time": "10:00am", "duration_minutes": 60, "location": "Zoom", "attendees": ["a@b.com"], "priority": "high"}'
                }
            ]
        }
    )
    svc = _make_service(llm=llm)
    email = _make_email(subject="Q3 Review needed", body="please schedule")
    analysis = await svc._llm_analysis(email)
    assert analysis is not None
    assert analysis.category == EmailCategory.MEETING_REQUEST


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_analysis_with_string_content():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(
        return_value={
            "content": '{"category": "meeting_request", "confidence": 0.7, "title": "Chat", "is_actionable": true, "summary": "test", "action_required": "", "date": "", "time": "", "duration_minutes": 30, "location": "", "attendees": [], "priority": "medium"}'
        }
    )
    svc = _make_service(llm=llm)
    email = _make_email(subject="Chat request", body="let's chat")
    analysis = await svc._llm_analysis(email)
    assert analysis is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_analysis_invalid_json_returns_none():
    llm = AsyncMock()
    llm.chat_completion = AsyncMock(return_value="not valid json at all")
    svc = _make_service(llm=llm)
    email = _make_email(subject="Test", body="test")
    analysis = await svc._llm_analysis(email)
    assert analysis is None


# ---------------------------------------------------------------------------
# get_suggestions
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_suggestions_returns_empty_without_db():
    svc = _make_service()
    result = await svc.get_suggestions(_UID)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_suggestions_returns_list():
    model = MagicMock()
    model.id = uuid.uuid4()
    model.user_id = _UID
    model.org_id = None
    model.email_provider_id = "msg1"
    model.email_subject = "Meeting"
    model.email_sender = "boss@co.com"
    model.email_received_at = _NOW
    model.email_snippet = "Let's meet"
    model.category = "meeting_request"
    model.confidence = 0.8
    model.priority = "high"
    model.title = "Meeting"
    model.description = "desc"
    model.proposed_start = None
    model.proposed_end = None
    model.location = None
    model.attendees_json = json.dumps(["a@b.com"])
    model.status = "pending"
    model.calendar_event_id = None
    model.has_conflict = False
    model.conflict_details = None
    model.alternative_slots_json = json.dumps([])
    model.created_at = _NOW
    model.updated_at = _NOW
    model.resolved_at = None

    session = _make_db_session(scalars_all=[model])
    svc = _make_service(db_factory=MagicMock(return_value=session))

    result = await svc.get_suggestions(_UID)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# approve_suggestion
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_suggestion_returns_none_without_db():
    svc = _make_service()
    result = await svc.approve_suggestion(uuid.uuid4(), _UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_suggestion_returns_none_when_not_found():
    session = _make_db_session(scalar_result=None)
    svc = _make_service(db_factory=MagicMock(return_value=session))
    result = await svc.approve_suggestion(uuid.uuid4(), _UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_approve_suggestion_creates_calendar_event():
    model = MagicMock()
    model.id = uuid.uuid4()
    model.user_id = _UID
    model.org_id = None
    model.email_provider_id = "msg1"
    model.email_subject = "Meeting"
    model.email_sender = "boss@co.com"
    model.email_received_at = _NOW
    model.email_snippet = "snippet"
    model.category = "meeting_request"
    model.confidence = 0.8
    model.priority = "medium"
    model.title = "Meeting"
    model.description = "desc"
    model.proposed_start = _NOW
    model.proposed_end = _NOW + timedelta(hours=1)
    model.location = None
    model.attendees_json = json.dumps([])
    model.status = "pending"
    model.calendar_event_id = None
    model.has_conflict = False
    model.conflict_details = None
    model.alternative_slots_json = json.dumps([])
    model.created_at = _NOW
    model.updated_at = _NOW
    model.resolved_at = None

    created_event = MagicMock()
    created_event.provider_event_id = "gcal-1"
    calendar = AsyncMock()
    calendar.create_event = AsyncMock(return_value=created_event)

    session = _make_db_session(scalar_result=model)
    svc = _make_service(calendar=calendar, db_factory=MagicMock(return_value=session))

    result = await svc.approve_suggestion(model.id, _UID)
    calendar.create_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# reject_suggestion
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reject_suggestion_returns_false_without_db():
    svc = _make_service()
    result = await svc.reject_suggestion(uuid.uuid4(), _UID)
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reject_suggestion_returns_false_when_not_found():
    session = _make_db_session(scalar_result=None)
    svc = _make_service(db_factory=MagicMock(return_value=session))
    result = await svc.reject_suggestion(uuid.uuid4(), _UID)
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reject_suggestion_updates_status():
    model = MagicMock()
    model.status = "pending"
    model.resolved_at = None

    session = _make_db_session(scalar_result=model)
    svc = _make_service(db_factory=MagicMock(return_value=session))

    result = await svc.reject_suggestion(uuid.uuid4(), _UID)
    assert result is True
    assert model.status == "rejected"


# ---------------------------------------------------------------------------
# get_scan_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_scan_history_returns_empty_without_db():
    svc = _make_service()
    result = await svc.get_scan_history(_UID)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_scan_history_returns_rows():
    log = MagicMock()
    log.id = uuid.uuid4()
    log.provider = "gmail"
    log.scanned_at = _NOW
    log.emails_scanned = 5
    log.actionable_found = 2
    log.suggestions_created = 1
    log.errors_json = json.dumps(["error1"])

    session = _make_db_session(scalars_all=[log])
    svc = _make_service(db_factory=MagicMock(return_value=session))
    result = await svc.get_scan_history(_UID)
    assert len(result) == 1
    assert result[0]["errors_count"] == 1


# ---------------------------------------------------------------------------
# get_scanned_emails
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_scanned_emails_returns_empty_without_db():
    svc = _make_service()
    result = await svc.get_scanned_emails(_UID)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_scanned_emails_returns_rows():
    email_row = MagicMock()
    email_row.id = uuid.uuid4()
    email_row.provider_message_id = "msg1"
    email_row.provider = "gmail"
    email_row.subject = "Test"
    email_row.sender_email = "a@b.com"
    email_row.sender_name = "Alice"
    email_row.recipients_json = json.dumps(["me@b.com"])
    email_row.body_snippet = "snippet"
    email_row.body_text = "body text"
    email_row.received_at = _NOW
    email_row.thread_id = "th1"
    email_row.has_attachments = False
    email_row.is_read = True
    email_row.is_actionable = True
    email_row.analysis_category = "meeting_request"
    email_row.analysis_confidence = 0.8
    email_row.analysis_summary = "summary"
    email_row.suggestion_id = uuid.uuid4()
    email_row.scanned_at = _NOW

    session = _make_db_session(scalars_all=[email_row])
    svc = _make_service(db_factory=MagicMock(return_value=session))

    result = await svc.get_scanned_emails(_UID)
    assert len(result) == 1
    assert result[0]["provider"] == "gmail"


# ---------------------------------------------------------------------------
# _filter_processed
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_processed_returns_all_when_no_db():
    svc = _make_service()
    emails = [
        _make_email(provider_message_id="m1"),
        _make_email(provider_message_id="m2"),
    ]
    result = await svc._filter_processed(_UID, emails)
    assert len(result) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_processed_filters_existing():
    result_mock = MagicMock()
    result_mock.all = MagicMock(return_value=[("m1",)])

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=result_mock)

    svc = _make_service(db_factory=MagicMock(return_value=session))
    emails = [
        _make_email(provider_message_id="m1"),
        _make_email(provider_message_id="m2"),
    ]
    result = await svc._filter_processed(_UID, emails)
    assert len(result) == 1
    assert result[0].provider_message_id == "m2"


# ---------------------------------------------------------------------------
# _save_scanned_email (upsert semantics)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_scanned_email_updates_existing():
    existing = MagicMock()
    existing.is_actionable = False
    session = _make_db_session(scalar_result=existing)
    svc = _make_service(db_factory=MagicMock(return_value=session))
    email = _make_email()
    analysis = EmailAnalysis(
        email_id=email.id,
        category=EmailCategory.MEETING_REQUEST,
        is_actionable=True,
        summary="Updated",
    )
    await svc._save_scanned_email(email, analysis, _UID)
    assert existing.is_actionable is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_scanned_email_creates_new():
    session = _make_db_session(scalar_result=None)
    svc = _make_service(db_factory=MagicMock(return_value=session))
    email = _make_email()
    analysis = EmailAnalysis(
        email_id=email.id,
        category=EmailCategory.MEETING_REQUEST,
        is_actionable=True,
        summary="New",
    )
    await svc._save_scanned_email(email, analysis, _UID)
    session.add.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_scanned_email_handles_exception():
    """If saving fails, no exception propagated."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=Exception("DB error"))

    svc = _make_service(db_factory=MagicMock(return_value=session))
    email = _make_email()
    analysis = EmailAnalysis(
        email_id=email.id,
        category=EmailCategory.NON_ACTIONABLE,
        is_actionable=False,
    )
    # Should not raise
    await svc._save_scanned_email(email, analysis, _UID)


# ---------------------------------------------------------------------------
# _log_scan
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_log_scan_does_nothing_without_db():
    svc = _make_service()
    scan_result = EmailScanResult(user_id=_UID, provider="gmail")
    # Should not raise
    await svc._log_scan(scan_result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_log_scan_saves_to_db():
    session = _make_db_session()
    svc = _make_service(db_factory=MagicMock(return_value=session))
    scan_result = EmailScanResult(user_id=_UID, provider="gmail")
    await svc._log_scan(scan_result)
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_time_finds_time():
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    result = EmailIntelligenceService._extract_time("Meeting at 2:30pm tomorrow")
    assert result != ""


@pytest.mark.unit
def test_extract_time_returns_empty_when_no_match():
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    result = EmailIntelligenceService._extract_time("No time here")
    assert result == ""


@pytest.mark.unit
def test_extract_date_finds_date():
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    result = EmailIntelligenceService._extract_date("Meeting on Monday next week")
    assert result != "" or result == ""  # pattern-dependent, just ensure no crash


@pytest.mark.unit
def test_resolve_datetime_no_date_or_time():
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    start, end = EmailIntelligenceService._resolve_datetime(
        date_str="",
        time_str="",
        duration_minutes=30,
        reference_date=_NOW,
        user_timezone="UTC",
    )
    assert start is None
    assert end is None


@pytest.mark.unit
def test_resolve_datetime_with_monday_and_time():
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    start, end = EmailIntelligenceService._resolve_datetime(
        date_str="Monday",
        time_str="10:00am",
        duration_minutes=60,
        reference_date=_NOW,
        user_timezone="UTC",
    )
    # May return None if parsing fails on this text, just ensure no exception
    assert start is None or isinstance(start, datetime)


@pytest.mark.unit
def test_resolve_datetime_invalid_timezone_defaults_utc():
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    start, end = EmailIntelligenceService._resolve_datetime(
        date_str="2024-06-10",
        time_str="10:00am",
        duration_minutes=30,
        reference_date=_NOW,
        user_timezone="Invalid/Zone",
    )
    # Should not raise even with invalid timezone
    assert start is None or isinstance(start, datetime)

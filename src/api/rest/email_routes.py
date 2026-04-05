"""
Email Intelligence API Routes — scan emails, view suggestions, approve/reject.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_container, get_current_user, get_db_session
from src.config.container import Container
from src.domain.entities.user import User

email_router = APIRouter()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    provider: str = Field(default="google", description="google or microsoft")
    since_hours: int = Field(
        default=72, ge=1, le=168, description="How many hours back to scan"
    )
    max_emails: int = Field(default=30, ge=1, le=100)


class SuggestionResponse(BaseModel):
    id: str
    email_subject: str
    email_sender: str
    email_received_at: str | None
    email_snippet: str
    category: str
    confidence: float
    priority: str
    title: str
    description: str
    proposed_start: str | None
    proposed_end: str | None
    location: str
    attendees: list[str]
    status: str
    has_conflict: bool
    conflict_details: str
    alternative_slots: list[dict]
    created_at: str


class ScanResultResponse(BaseModel):
    provider: str
    emails_scanned: int
    actionable_found: int
    suggestions_created: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@email_router.post("/scan", response_model=ScanResultResponse)
async def scan_emails(
    request: ScanRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Trigger an email scan for the current user.

    Scans Gmail or Outlook inbox for scheduling-related emails,
    analyzes them with AI, and creates calendar suggestions.
    """
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()

    # Create appropriate email adapter
    if request.provider == "google":
        from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

        settings = container.settings
        email_adapter = GmailEmailAdapter(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        email_adapter.set_db_session_factory(db.session_factory)
    elif request.provider == "microsoft":
        from src.infrastructure.email_providers.outlook_email import OutlookEmailAdapter

        email_adapter = OutlookEmailAdapter()
        email_adapter.set_db_session_factory(db.session_factory)
    else:
        raise HTTPException(
            status_code=400, detail="Unsupported provider. Use 'google' or 'microsoft'."
        )

    # Create intelligence service
    service = EmailIntelligenceService(
        llm_adapter=container.llm_adapter(),
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=db.session_factory,
        classifier_service=container.email_classifier(),
        draft_composer_service=container.draft_composer(),
        guides_service=container.user_guides_service(),
    )

    result = await service.scan_user_emails(
        user_id=current_user.id,
        email_provider=email_adapter,
        provider_name=request.provider,
        since_hours=request.since_hours,
        max_emails=request.max_emails,
        user_email=getattr(current_user, "email", ""),
        user_timezone=getattr(current_user, "timezone", "UTC") or "UTC",
    )

    return {
        "provider": result.provider,
        "emails_scanned": result.emails_scanned,
        "actionable_found": result.actionable_found,
        "suggestions_created": result.suggestions_created,
        "errors": result.errors,
    }


@email_router.get("/suggestions", response_model=list[SuggestionResponse])
async def get_suggestions(
    status: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    """Get schedule suggestions for the current user."""
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()
    service = EmailIntelligenceService(db_session_factory=db.session_factory)

    suggestions = await service.get_suggestions(
        user_id=current_user.id,
        status=status,
        limit=limit,
    )

    return [
        {
            "id": str(s.id),
            "email_subject": s.email_subject,
            "email_sender": s.email_sender,
            "email_received_at": (
                s.email_received_at.isoformat() if s.email_received_at else None
            ),
            "email_snippet": s.email_snippet[:200],
            "category": s.category.value,
            "confidence": s.confidence,
            "priority": s.priority.value,
            "title": s.title,
            "description": s.description[:500],
            "proposed_start": (
                s.proposed_start.isoformat() if s.proposed_start else None
            ),
            "proposed_end": s.proposed_end.isoformat() if s.proposed_end else None,
            "location": s.location,
            "attendees": s.attendees,
            "status": s.status.value,
            "has_conflict": s.has_conflict,
            "conflict_details": s.conflict_details,
            "alternative_slots": s.alternative_slots,
            "created_at": s.created_at.isoformat() if s.created_at else "",
        }
        for s in suggestions
    ]


@email_router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Approve a suggestion and create a calendar event."""
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()
    service = EmailIntelligenceService(
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=db.session_factory,
    )

    result = await service.approve_suggestion(suggestion_id, current_user.id)
    if not result:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    return {
        "status": "approved",
        "title": result.title,
        "calendar_event_id": result.calendar_event_id,
    }


@email_router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Reject a schedule suggestion."""
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()
    service = EmailIntelligenceService(db_session_factory=db.session_factory)

    success = await service.reject_suggestion(suggestion_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    return {"status": "rejected"}


@email_router.get("/scan-history")
async def get_scan_history(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    """Get email scan history for the current user."""
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()
    service = EmailIntelligenceService(db_session_factory=db.session_factory)

    return await service.get_scan_history(current_user.id, limit=limit)


@email_router.get("/providers")
async def get_email_providers(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    """Get available email providers for the current user."""
    from sqlalchemy import select

    from src.infrastructure.persistence.org_models import ProviderConnectionModel

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id == current_user.id,
                ProviderConnectionModel.status == "active",
            )
        )
        connections = result.scalars().all()

    return [
        {
            "provider": conn.provider,
            "email": conn.provider_email,
            "email_sync_enabled": conn.email_sync_enabled,
            "last_sync_at": (
                conn.last_sync_at.isoformat() if conn.last_sync_at else None
            ),
            "scopes": conn.scopes,
        }
        for conn in connections
    ]


@email_router.get("/scanned-emails")
async def get_scanned_emails(
    actionable_only: bool = False,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    """Get scanned emails with their analysis results for browsing."""
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()
    service = EmailIntelligenceService(db_session_factory=db.session_factory)

    return await service.get_scanned_emails(
        user_id=current_user.id,
        actionable_only=actionable_only,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Draft Reply Routes
# ---------------------------------------------------------------------------


class SendDraftRequest(BaseModel):
    verify_before_invite: bool = Field(
        default=True,
        description="Run invite verification before creating calendar event",
    )


@email_router.get("/drafts")
async def list_drafts(
    status: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    """List AI-composed email drafts for the current user."""
    from sqlalchemy import select

    from src.infrastructure.persistence.email_models import DraftReplyModel

    db = container.database()
    async with db.session_factory() as session:
        q = select(DraftReplyModel).where(
            DraftReplyModel.user_id == current_user.id
        )
        if status:
            q = q.where(DraftReplyModel.status == status)
        q = q.order_by(DraftReplyModel.created_at.desc()).limit(limit)
        result = await session.execute(q)
        drafts = result.scalars().all()

    return [
        {
            "id": str(d.id),
            "thread_id": d.thread_id,
            "to_email": d.to_email,
            "subject": d.subject,
            "body": d.body,
            "proposed_windows": json.loads(d.proposed_windows_json or "[]"),
            "status": d.status,
            "was_sent": d.was_sent,
            "sent_by_autopilot": d.sent_by_autopilot,
            "created_at": d.created_at.isoformat(),
            "sent_at": d.sent_at.isoformat() if d.sent_at else None,
        }
        for d in drafts
    ]


@email_router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Get a specific draft by ID."""
    from sqlalchemy import select

    from src.infrastructure.persistence.email_models import DraftReplyModel

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(DraftReplyModel).where(
                DraftReplyModel.id == draft_id,
                DraftReplyModel.user_id == current_user.id,
            )
        )
        draft = result.scalars().first()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    return {
        "id": str(draft.id),
        "thread_id": draft.thread_id,
        "to_email": draft.to_email,
        "cc_emails": json.loads(draft.cc_emails_json or "[]"),
        "subject": draft.subject,
        "body": draft.body,
        "content_type": draft.content_type,
        "proposed_windows": json.loads(draft.proposed_windows_json or "[]"),
        "scheduling_link_id": draft.scheduling_link_id,
        "status": draft.status,
        "was_sent": draft.was_sent,
        "sent_by_autopilot": draft.sent_by_autopilot,
        "created_at": draft.created_at.isoformat(),
        "sent_at": draft.sent_at.isoformat() if draft.sent_at else None,
    }


@email_router.post("/drafts/{draft_id}/send")
async def send_draft(
    draft_id: UUID,
    request: SendDraftRequest = SendDraftRequest(),
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Manually send a draft and optionally create a calendar invite."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from src.infrastructure.persistence.email_models import DraftReplyModel

    db = container.database()

    async with db.session_factory() as session:
        result = await session.execute(
            select(DraftReplyModel).where(
                DraftReplyModel.id == draft_id,
                DraftReplyModel.user_id == current_user.id,
            )
        )
        draft = result.scalars().first()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        if draft.status != "ready":
            raise HTTPException(
                status_code=409, detail=f"Draft is already {draft.status}"
            )

        # Send via email provider
        settings = container.settings
        from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

        email_adapter = GmailEmailAdapter(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        email_adapter.set_db_session_factory(db.session_factory)

        success = await email_adapter.send_draft(current_user.id, draft.provider_draft_id)
        if not success:
            raise HTTPException(status_code=502, detail="Failed to send draft via email provider")

        draft.was_sent = True
        draft.status = "sent"
        draft.sent_at = datetime.now(timezone.utc)
        await session.commit()

    return {"status": "sent", "draft_id": str(draft_id)}


@email_router.delete("/drafts/{draft_id}")
async def discard_draft(
    draft_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Discard (delete) a draft reply."""
    from sqlalchemy import select

    from src.infrastructure.persistence.email_models import DraftReplyModel

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(DraftReplyModel).where(
                DraftReplyModel.id == draft_id,
                DraftReplyModel.user_id == current_user.id,
            )
        )
        draft = result.scalars().first()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        draft.status = "discarded"
        await session.commit()

    return {"status": "discarded", "draft_id": str(draft_id)}


# ---------------------------------------------------------------------------
# Onboarding Routes
# ---------------------------------------------------------------------------


@email_router.post("/onboarding/start")
async def start_onboarding(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """
    Trigger onboarding for the current user.

    Reads the last 60 days of Gmail history to:
    - Generate the scheduling preferences guide
    - Generate the email style guide
    - Backfill committed meetings to the CalendarAgent calendar
    """
    import asyncio

    from src.application.services.onboarding_service import OnboardingService

    settings = container.settings
    db = container.database()

    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    email_adapter = GmailEmailAdapter(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )
    email_adapter.set_db_session_factory(db.session_factory)

    service = OnboardingService(
        llm_adapter=container.llm_adapter(),
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=db.session_factory,
    )

    # Run in background — return immediately with status
    user_tz = getattr(current_user, "timezone", "UTC") or "UTC"
    asyncio.create_task(
        service.run_onboarding(
            user_id=current_user.id,
            user_email=getattr(current_user, "email", ""),
            user_timezone=user_tz,
            email_provider=email_adapter,
        )
    )

    return {"status": "started", "message": "Onboarding started in background. Check /email/onboarding/status for progress."}


@email_router.get("/onboarding/status")
async def get_onboarding_status(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Get the onboarding status for the current user."""
    from src.application.services.onboarding_service import OnboardingService

    db = container.database()
    service = OnboardingService(db_session_factory=db.session_factory)
    status = await service.get_onboarding_status(current_user.id)
    return {"user_id": str(current_user.id), "status": status}


# ---------------------------------------------------------------------------
# User Guides Routes
# ---------------------------------------------------------------------------


class GuideUpdateRequest(BaseModel):
    content: str = Field(..., description="New guide content (plain text, bullet points)")


@email_router.get("/guides")
async def get_guides(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Get AI-generated user guides (scheduling preferences and email style)."""
    from src.application.services.user_guides_service import UserGuidesService

    db = container.database()
    service = UserGuidesService(db_session_factory=db.session_factory)
    scheduling, style = await service.get_user_guides(current_user.id)

    return {
        "user_id": str(current_user.id),
        "scheduling_preferences": scheduling,
        "email_style": style,
    }


@email_router.put("/guides/preferences")
async def update_scheduling_preferences(
    request: GuideUpdateRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Manually update the scheduling preferences guide."""
    from src.application.services.user_guides_service import UserGuidesService

    db = container.database()
    service = UserGuidesService(db_session_factory=db.session_factory)
    await service._save_guides(
        user_id=current_user.id,
        scheduling_guide=request.content,
        style_guide="",
        emails_analyzed=0,
    )
    return {"status": "updated", "guide_type": "scheduling_preferences"}


@email_router.put("/guides/style")
async def update_email_style(
    request: GuideUpdateRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Manually update the email style guide."""
    from src.application.services.user_guides_service import UserGuidesService

    db = container.database()
    service = UserGuidesService(db_session_factory=db.session_factory)
    await service._save_guides(
        user_id=current_user.id,
        scheduling_guide="",
        style_guide=request.content,
        emails_analyzed=0,
    )
    return {"status": "updated", "guide_type": "email_style"}


# ---------------------------------------------------------------------------
# Gmail Pub/Sub Webhook
# ---------------------------------------------------------------------------


@email_router.post("/webhook/gmail")
async def gmail_webhook(
    request_data: dict,
    container: Container = Depends(get_container),
) -> dict:
    """
    Receives Gmail Pub/Sub push notifications.

    When Gmail detects a new email, it sends a POST here immediately,
    replacing the 15-minute polling cycle with near-realtime processing.

    Payload format:
      {"message": {"data": "<base64>", "messageId": "..."}, "subscription": "..."}
    """
    import base64
    import json as json_mod

    from fastapi import Request

    try:
        message = request_data.get("message", {})
        raw_data = message.get("data", "")
        decoded = json_mod.loads(base64.b64decode(raw_data).decode("utf-8"))
    except Exception as e:
        # Malformed payload — return 200 so Pub/Sub doesn't retry indefinitely
        return {"status": "ignored", "reason": str(e)}

    email_address = decoded.get("emailAddress", "")
    history_id = decoded.get("historyId", "")

    if not email_address:
        return {"status": "ignored", "reason": "no emailAddress in payload"}

    # Look up the user by email address
    try:
        from sqlalchemy import select

        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        db = container.database()
        async with db.session_factory() as session:
            result = await session.execute(
                select(ProviderConnectionModel).where(
                    ProviderConnectionModel.provider_email == email_address,
                    ProviderConnectionModel.provider == "google",
                    ProviderConnectionModel.status == "active",
                )
            )
            conn = result.scalars().first()

        if not conn:
            return {"status": "ignored", "reason": "no active user for this email"}

        user_id = conn.user_id

        # Trigger a scan just for this user (scan last 24h to catch what changed)
        import asyncio

        asyncio.create_task(_trigger_scan_for_user(user_id, container))

    except Exception as e:
        return {"status": "error", "reason": str(e)}

    return {"status": "accepted", "historyId": history_id}


async def _trigger_scan_for_user(user_id: "UUID", container: "Container") -> None:
    """Helper: kick off a quick email scan for a user after a Pub/Sub notification."""
    import logging

    trigger_log = logging.getLogger("calendar_agent.webhook")
    try:
        settings = container.settings
        db = container.database()

        from src.application.services.email_intelligence_service import (
            EmailIntelligenceService,
        )
        from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

        email_adapter = GmailEmailAdapter(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        email_adapter.set_db_session_factory(db.session_factory)

        service = EmailIntelligenceService(
            llm_adapter=container.llm_adapter(),
            calendar_adapter=container.calendar_adapter(),
            db_session_factory=db.session_factory,
        )
        await service.scan_user_emails(
            user_id=user_id,
            email_provider=email_adapter,
            provider_name="google",
            since_hours=2,
            max_emails=10,
        )
        trigger_log.info("Webhook-triggered scan complete for user %s", user_id)
    except Exception as e:
        trigger_log.warning("Webhook-triggered scan failed for user %s: %s", user_id, e)


# ---------------------------------------------------------------------------
# Scheduling Links Routes
# ---------------------------------------------------------------------------


class CreateSuggestedLinkRequest(BaseModel):
    attendee_email: str = Field(..., description="Who the link is for")
    duration_minutes: int = Field(default=30, ge=15, le=480)
    suggested_windows: list[dict] = Field(
        ..., description='List of {"start": ISO8601, "end": ISO8601} objects'
    )
    subject: str | None = None
    thread_id: str | None = None


class CreateAvailabilityLinkRequest(BaseModel):
    attendee_email: str
    duration_minutes: int = Field(default=30, ge=15, le=480)
    days_ahead: int = Field(default=14, ge=1, le=60)
    subject: str | None = None
    thread_id: str | None = None


class BookSlotRequest(BaseModel):
    chosen_start: str = Field(..., description="ISO8601 datetime the attendee selected")
    attendee_name: str
    attendee_email: str


@email_router.post("/scheduling-links/suggested")
async def create_suggested_link(
    request: CreateSuggestedLinkRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Create a scheduling link with pre-selected suggested time windows."""
    from src.application.services.scheduling_link_service import SchedulingLinkService

    settings = container.settings
    db = container.database()

    service = SchedulingLinkService(
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=db.session_factory,
        base_url=getattr(settings, "app_base_url", "https://app.example.com"),
    )
    url = await service.create_suggested_link(
        user_id=current_user.id,
        attendee_email=request.attendee_email,
        duration_minutes=request.duration_minutes,
        suggested_windows=request.suggested_windows,
        thread_id=request.thread_id,
        subject=request.subject,
    )
    return {"url": url, "mode": "suggested"}


@email_router.post("/scheduling-links/availability")
async def create_availability_link(
    request: CreateAvailabilityLinkRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Create a scheduling link showing full free/busy availability."""
    from src.application.services.scheduling_link_service import SchedulingLinkService

    settings = container.settings
    db = container.database()

    service = SchedulingLinkService(
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=db.session_factory,
        base_url=getattr(settings, "app_base_url", "https://app.example.com"),
    )
    url = await service.create_availability_link(
        user_id=current_user.id,
        attendee_email=request.attendee_email,
        duration_minutes=request.duration_minutes,
        days_ahead=request.days_ahead,
        thread_id=request.thread_id,
        subject=request.subject,
    )
    return {"url": url, "mode": "availability"}


@email_router.get("/scheduling-links/{link_id}")
async def get_scheduling_link(
    link_id: str,
    container: Container = Depends(get_container),
) -> dict:
    """Public endpoint: get a scheduling link's details (no auth required)."""
    from src.application.services.scheduling_link_service import SchedulingLinkService

    service = SchedulingLinkService(
        db_session_factory=container.database().session_factory,
    )
    link = await service.get_link(link_id)
    if not link:
        raise HTTPException(status_code=410, detail="Scheduling link not found or expired")
    return link


@email_router.post("/scheduling-links/{link_id}/book")
async def book_scheduling_slot(
    link_id: str,
    request: BookSlotRequest,
    container: Container = Depends(get_container),
) -> dict:
    """Public endpoint: book a slot from a scheduling link (no auth required)."""
    from src.application.services.scheduling_link_service import SchedulingLinkService

    service = SchedulingLinkService(
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=container.database().session_factory,
    )
    result = await service.book_slot(
        link_id=link_id,
        chosen_start=request.chosen_start,
        attendee_name=request.attendee_name,
        attendee_email=request.attendee_email,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reason", "Booking failed"))
    return result


# ---------------------------------------------------------------------------
# Message Hook Route
# ---------------------------------------------------------------------------


class MessageHookRequest(BaseModel):
    message: str = Field(..., description="Raw message text")
    sender: str = Field(..., description="Sender name or email")
    source: str = Field(
        default="unknown",
        description="Message source: slack, sms, email, text, etc.",
    )
    auto_create: bool = Field(
        default=False,
        description="Auto-create calendar event if confidence is high (>0.85)",
    )


@email_router.post("/hook/message")
async def message_hook(
    request: MessageHookRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """
    Process an arbitrary message for scheduling commitments.

    Accepts text from Slack, SMS, WhatsApp etc. and extracts meeting details.
    If auto_create=True and confidence is high, creates the calendar event immediately.
    """
    from src.application.services.message_hook_service import MessageHookService

    user_tz = getattr(current_user, "timezone", "UTC") or "UTC"

    service = MessageHookService(
        llm_adapter=container.llm_adapter(),
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=container.database().session_factory,
    )
    result = await service.process_message(
        user_id=current_user.id,
        message_text=request.message,
        sender=request.sender,
        source=request.source,
        user_timezone=user_tz,
        auto_create=request.auto_create,
    )
    return result

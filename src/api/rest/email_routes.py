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
        default=24, ge=1, le=168, description="How many hours back to scan"
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
    )

    result = await service.scan_user_emails(
        user_id=current_user.id,
        email_provider=email_adapter,
        provider_name=request.provider,
        since_hours=request.since_hours,
        max_emails=request.max_emails,
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

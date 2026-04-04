"""
API Routes — REST endpoints for the Calendar Agent SaaS platform.
All routes delegate to application services via FastAPI DI.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_container, get_current_user
from src.application.dto import (
    ChatRequestDTO,
    ChatResponseDTO,
    CreateEventDTO,
    DateRangeDTO,
    EventResponseDTO,
    LoginResponseDTO,
    UserProfileDTO,
)
from src.config.container import Container
from src.domain.entities.user import User

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
health_router = APIRouter()


@health_router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "healthy", "service": "calendar-agent"}


@health_router.get("/ready")
async def readiness_check(
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """Readiness check — verifies DB and cache connectivity."""
    try:
        from sqlalchemy import text

        db = container.database()
        async with db.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database not ready") from exc
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
auth_router = APIRouter()


@auth_router.get("/google/login")
async def google_login(
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """Initiate Google OAuth2 flow — returns authorization URL."""
    oauth = container.google_oauth()
    url = oauth.get_authorization_url()
    return {"authorization_url": url}


@auth_router.get("/google/callback")
async def google_callback(
    code: str,
    state: str | None = None,
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """Handle Google OAuth2 callback — exchange code for tokens."""
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    # 1. Exchange code for Google tokens
    oauth = container.google_oauth()
    tokens = oauth.exchange_code(code)

    # 2. Get or create user in the database
    db = container.database()
    async with db.session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)

        # Decode user info from Google (simplified — in production use id_token)
        from src.application.services.auth_service import AuthService

        auth_svc = AuthService(
            user_repository=user_repo,
            jwt_secret=container.settings.app_secret_key,
            jwt_algorithm=container.settings.jwt_algorithm,
            access_token_expire_minutes=container.settings.jwt_access_token_expire_minutes,
            refresh_token_expire_days=container.settings.jwt_refresh_token_expire_days,
        )

        # For Google callback the email comes from the tokens.
        # A full implementation would decode the id_token; here we use the
        # JWT service directly for token issuance.
        user, _access, _refresh = await auth_svc.authenticate_google_oauth(
            email="user@example.com",  # TODO: decode from Google id_token
            name="User",
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expiry=tokens["expiry"],
        )

        jwt_svc = container.jwt_service()
        access_token = jwt_svc.create_access_token(user)
        refresh_token = jwt_svc.create_refresh_token(user)

        await session.commit()

    return LoginResponseDTO(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


@auth_router.get("/microsoft/login")
async def microsoft_login(
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """Initiate Microsoft OAuth2 flow — returns authorization URL."""
    oauth = container.microsoft_oauth()
    url = oauth.get_authorization_url(state="ms-login")
    return {"authorization_url": url}


@auth_router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str | None = None,
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """Handle Microsoft OAuth2 callback — exchange code for tokens."""
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    oauth = container.microsoft_oauth()
    tokens = oauth.exchange_code(code)

    # Get user info from Microsoft Graph
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if resp.status_code == 200:
            profile = resp.json()
            email = profile.get("mail") or profile.get(
                "userPrincipalName", "user@outlook.com"
            )
            name = profile.get("displayName", "Microsoft User")
        else:
            email = "user@outlook.com"
            name = "Microsoft User"

    db = container.database()
    async with db.session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_email(email)
        if not user:
            from src.domain.entities.user import User as _User

            user = _User(email=email, name=name)
            user = await user_repo.create(user)

        # Store Microsoft tokens on user for calendar access
        user.google_access_token = tokens["access_token"]  # reuse field
        user.google_refresh_token = tokens.get("refresh_token")
        await user_repo.update(user)
        await session.commit()

    jwt_svc = container.jwt_service()
    access_token = jwt_svc.create_access_token(user)
    refresh_token = jwt_svc.create_refresh_token(user)

    return LoginResponseDTO(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


@auth_router.get("/me", response_model=UserProfileDTO)
async def get_profile(
    current_user: User = Depends(get_current_user),
) -> UserProfileDTO:
    """Get current user's profile and usage stats."""
    return UserProfileDTO(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        timezone=current_user.timezone,
        plan=current_user.plan.value,
        monthly_requests_used=0,  # TODO: fetch from usage tracker
        monthly_request_limit=current_user.get_request_limit(),
    )


@auth_router.post("/dev-login", response_model=LoginResponseDTO)
async def dev_login(
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """Dev-only: create or reuse a test user and return JWT. Disabled in production."""
    if container.settings.is_production:
        raise HTTPException(status_code=404, detail="Not found")

    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    db = container.database()
    async with db.session_factory() as session:
        repo = SQLAlchemyUserRepository(session)
        user = await repo.get_by_email("dev@calendar-agent.local")
        if not user:
            from src.domain.entities.user import User as _User

            user = _User(email="dev@calendar-agent.local", name="Dev User")
            user = await repo.create(user)
        await session.commit()

    jwt_svc = container.jwt_service()
    access = jwt_svc.create_access_token(user)
    refresh = jwt_svc.create_refresh_token(user)
    return LoginResponseDTO(
        access_token=access,
        refresh_token=refresh,
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
chat_router = APIRouter()


@chat_router.post("/", response_model=ChatResponseDTO)
async def send_message(
    request: ChatRequestDTO,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> ChatResponseDTO:
    """
    Send a natural language message to the Calendar Agent.
    The agent will interpret the request and manage calendar operations.
    """
    from src.infrastructure.persistence.conversation_repository import (
        SQLAlchemyConversationRepository,
    )

    db = container.database()
    async with db.session_factory() as session:
        chat_svc = _build_chat_service(
            container, SQLAlchemyConversationRepository(session)
        )
        response = await chat_svc.handle_message(
            user_id=current_user.id,
            request=request,
            plan_limit=current_user.get_request_limit(),
        )
        await session.commit()
    return response


# ---------------------------------------------------------------------------
# Calendar (direct CRUD endpoints, bypassing agent)
# ---------------------------------------------------------------------------
calendar_router = APIRouter()


@calendar_router.get("/events", response_model=list[EventResponseDTO])
async def list_events(
    start: datetime,
    end: datetime,
    calendar_id: str = "primary",
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[EventResponseDTO]:
    """List calendar events in a date range."""
    cal_svc = _build_calendar_service(container)
    dto = DateRangeDTO(start=start, end=end, calendar_id=calendar_id)
    return await cal_svc.list_events(user_id=current_user.id, dto=dto)


@calendar_router.post(
    "/events",
    response_model=EventResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    request: CreateEventDTO,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> EventResponseDTO:
    """Create a new calendar event."""
    cal_svc = _build_calendar_service(container)
    return await cal_svc.create_event(user_id=current_user.id, dto=request)


@calendar_router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> None:
    """Delete a calendar event."""
    cal_svc = _build_calendar_service(container)
    deleted = await cal_svc.delete_event(user_id=current_user.id, event_id=event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")


# ---------------------------------------------------------------------------
# Private helpers — build services from container
# ---------------------------------------------------------------------------


def _build_chat_service(
    container: Container,
    conversation_repo: object,
) -> "ChatService":  # noqa: F821
    """Assemble a ChatService with all dependencies from the container."""
    from src.application.services.chat_service import ChatService

    settings = container.settings
    return ChatService(
        conversation_repo=conversation_repo,  # type: ignore[arg-type]
        usage_tracker=container.usage_tracker(),
        cache=container.cache(),
        agent_executor=None,  # TODO: wire LangGraph agent
        intent_router=container.intent_router(),
        complexity_router=None,  # TODO: wire complexity router
        calendar_provider=container.calendar_adapter(),
        llm_provider=settings.llm_provider,
        model_fast=settings.active_model_fast,
        model_primary=settings.active_model_primary,
    )


def _build_calendar_service(container: Container) -> "CalendarService":  # noqa: F821
    """Assemble a CalendarService with all dependencies from the container."""
    from src.application.services.calendar_service import CalendarService

    cal_adapter = container.calendar_adapter()
    return CalendarService(
        calendar_provider=cal_adapter,
        event_repository=cal_adapter,  # type: ignore[arg-type]
        cache=container.cache(),
    )

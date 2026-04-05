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
) -> object:
    """Handle Google OAuth2 callback — exchange code for tokens, then redirect to app."""
    import uuid as _uuid

    import httpx
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select

    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository
    from src.infrastructure.security.token_encryption import encrypt_token

    # 1. Exchange code for Google tokens
    oauth = container.google_oauth()
    tokens = oauth.exchange_code(code)

    raw_access = tokens["access_token"]
    raw_refresh = tokens.get("refresh_token") or ""

    # 2. Get user info from Google userinfo endpoint
    google_email = "user@example.com"
    google_name = "Google User"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {raw_access}"},
        )
        if resp.status_code == 200:
            profile = resp.json()
            google_email = profile.get("email", google_email)
            google_name = profile.get("name", google_name)

    # 3. Get or create user; store encrypted tokens in users table
    db = container.database()
    enc_access = encrypt_token(raw_access)
    enc_refresh = encrypt_token(raw_refresh)

    async with db.session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)

        from src.application.services.auth_service import AuthService

        auth_svc = AuthService(
            user_repository=user_repo,
            jwt_secret=container.settings.app_secret_key,
            jwt_algorithm=container.settings.jwt_algorithm,
            access_token_expire_minutes=container.settings.jwt_access_token_expire_minutes,
            refresh_token_expire_days=container.settings.jwt_refresh_token_expire_days,
        )

        # Store encrypted tokens so GmailEmailAdapter can decrypt them later
        user, _access, _refresh = await auth_svc.authenticate_google_oauth(
            email=google_email,
            name=google_name,
            access_token=enc_access,
            refresh_token=enc_refresh or None,
            token_expiry=tokens["expiry"],
        )

        # 4. Also upsert a stand-alone ProviderConnection (org_id = user's own UUID
        #    used as a personal-account sentinel so GmailEmailAdapter finds tokens).
        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        existing = await session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id == user.id,
                ProviderConnectionModel.provider == "google",
                ProviderConnectionModel.org_id == user.id,  # personal sentinel
            )
        )
        conn_model = existing.scalar_one_or_none()
        if conn_model:
            conn_model.access_token = enc_access
            conn_model.refresh_token = enc_refresh
            conn_model.token_expiry = tokens["expiry"]
            conn_model.provider_email = google_email
            conn_model.status = "active"
        else:
            conn_model = ProviderConnectionModel(
                id=_uuid.uuid4(),
                org_id=user.id,          # personal account sentinel
                user_id=user.id,
                provider="google",
                provider_email=google_email,
                status="active",
                access_token=enc_access,
                refresh_token=enc_refresh,
                token_expiry=tokens["expiry"],
                scopes=" ".join([
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/gmail.readonly",
                ]),
            )
            session.add(conn_model)

        jwt_svc = container.jwt_service()
        jwt_access = jwt_svc.create_access_token(user)
        jwt_refresh = jwt_svc.create_refresh_token(user)
        await session.commit()

    # 5. Return an HTML page that stores the JWT in localStorage and redirects home
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head><title>Signing in...</title></head>
<body style="background:#0f0f11;color:#e4e4eb;font-family:sans-serif;
             display:flex;align-items:center;justify-content:center;height:100vh">
  <div style="text-align:center">
    <h2>&#x2705; Signed in as {google_name}</h2>
    <p>Redirecting to Calendar Agent&hellip;</p>
    <script>
      localStorage.setItem('token', '{jwt_access}');
      window.location.href = '/';
    </script>
    <p><a href="/" style="color:#8b5cf6">Click here if not redirected</a></p>
  </div>
</body>
</html>"""
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
) -> object:
    """Handle Microsoft OAuth2 callback — exchange code for tokens, then redirect to app."""
    import uuid as _uuid

    import httpx
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select

    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository
    from src.infrastructure.security.token_encryption import encrypt_token

    oauth = container.microsoft_oauth()
    tokens = oauth.exchange_code(code)

    raw_access = tokens["access_token"]
    raw_refresh = tokens.get("refresh_token") or ""

    # Get user info from Microsoft Graph
    ms_email = "user@outlook.com"
    ms_name = "Microsoft User"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {raw_access}"},
        )
        if resp.status_code == 200:
            profile = resp.json()
            ms_email = profile.get("mail") or profile.get("userPrincipalName", ms_email)
            ms_name = profile.get("displayName", ms_name)

    enc_access = encrypt_token(raw_access)
    enc_refresh = encrypt_token(raw_refresh)

    db = container.database()
    async with db.session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_email(ms_email)
        if not user:
            from src.domain.entities.user import User as _User

            user = _User(email=ms_email, name=ms_name)
            user = await user_repo.create(user)

        # Store encrypted Microsoft tokens reusing the google_access_token fields
        user.google_access_token = enc_access
        user.google_refresh_token = enc_refresh or None
        await user_repo.update(user)

        # Upsert a ProviderConnection so OutlookEmailAdapter can find tokens
        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        existing = await session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id == user.id,
                ProviderConnectionModel.provider == "microsoft",
                ProviderConnectionModel.org_id == user.id,
            )
        )
        conn_model = existing.scalar_one_or_none()
        if conn_model:
            conn_model.access_token = enc_access
            conn_model.refresh_token = enc_refresh
            conn_model.provider_email = ms_email
            conn_model.status = "active"
        else:
            conn_model = ProviderConnectionModel(
                id=_uuid.uuid4(),
                org_id=user.id,
                user_id=user.id,
                provider="microsoft",
                provider_email=ms_email,
                status="active",
                access_token=enc_access,
                refresh_token=enc_refresh,
                scopes="Mail.Read Calendars.ReadWrite",
            )
            session.add(conn_model)

        jwt_svc = container.jwt_service()
        jwt_access = jwt_svc.create_access_token(user)
        jwt_refresh = jwt_svc.create_refresh_token(user)
        await session.commit()

    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head><title>Signing in...</title></head>
<body style="background:#0f0f11;color:#e4e4eb;font-family:sans-serif;
             display:flex;align-items:center;justify-content:center;height:100vh">
  <div style="text-align:center">
    <h2>&#x2705; Signed in as {ms_name}</h2>
    <p>Redirecting to Calendar Agent&hellip;</p>
    <script>
      localStorage.setItem('token', '{jwt_access}');
      window.location.href = '/';
    </script>
    <p><a href="/" style="color:#8b5cf6">Click here if not redirected</a></p>
  </div>
</body>
</html>"""
    )


@auth_router.get("/me", response_model=UserProfileDTO)
async def get_profile(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> UserProfileDTO:
    """Get current user's profile and usage stats."""
    monthly_used = await container.usage_tracker().get_monthly_request_count(current_user.id)
    return UserProfileDTO(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        timezone=current_user.timezone,
        plan=current_user.plan.value,
        monthly_requests_used=monthly_used,
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
        agent_executor=container.calendar_agent(),
        intent_router=container.intent_router(),
        complexity_router=None,
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

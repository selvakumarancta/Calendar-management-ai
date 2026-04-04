"""
Organization API Routes — multi-tenant management endpoints.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_container, get_current_user, get_db_session
from src.config.container import Container
from src.domain.entities.organization import OrgRole, ProviderType
from src.domain.entities.user import User
from src.domain.exceptions import DomainError, InsufficientPermissionsError

org_router = APIRouter()
google_callback_router = APIRouter()


# ---------------------------------------------------------------------------
# Request/Response DTOs
# ---------------------------------------------------------------------------


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    domain: str | None = None


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    domain: str | None = None
    timezone: str = "UTC"
    is_active: bool = True
    max_members: int = 5
    member_count: int = 0


class InviteMemberRequest(BaseModel):
    email: str = Field(..., min_length=5)
    role: str = "member"


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    name: str
    role: str
    joined_at: str | None = None
    is_active: bool = True


class ConnectProviderRequest(BaseModel):
    provider: str = Field(..., description="google or microsoft")
    provider_email: str
    access_token: str = ""
    refresh_token: str | None = None
    scopes: str = ""


class ProviderConnectionResponse(BaseModel):
    id: str
    provider: str
    provider_email: str
    status: str
    calendar_sync_enabled: bool = True
    email_sync_enabled: bool = True
    last_sync_at: str | None = None


class UpdateOrgRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    timezone: str | None = None


# ---------------------------------------------------------------------------
# Helper: build org service from container + session
# ---------------------------------------------------------------------------


def _build_org_service(container: Container, session: AsyncSession):  # type: ignore[no-untyped-def]
    from src.application.services.organization_service import OrganizationService
    from src.infrastructure.persistence.org_repository import (
        SQLAlchemyMembershipRepository,
        SQLAlchemyOrganizationRepository,
        SQLAlchemyProviderConnectionRepository,
    )
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    return OrganizationService(
        org_repo=SQLAlchemyOrganizationRepository(session),
        membership_repo=SQLAlchemyMembershipRepository(session),
        provider_repo=SQLAlchemyProviderConnectionRepository(session),
        user_repo=SQLAlchemyUserRepository(session),
    )


# ---------------------------------------------------------------------------
# Organization CRUD
# ---------------------------------------------------------------------------


@org_router.get("/", response_model=list[OrgResponse])
async def list_organizations(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrgResponse]:
    """List all organizations the current user belongs to."""
    svc = _build_org_service(container, session)
    orgs = await svc.list_user_organizations(current_user.id)
    result = []
    for org in orgs:
        members = await svc._membership_repo.count_members(org.id)
        result.append(
            OrgResponse(
                id=str(org.id),
                name=org.name,
                slug=org.slug,
                domain=org.domain,
                timezone=org.timezone,
                is_active=org.is_active,
                max_members=org.max_members,
                member_count=members,
            )
        )
    return result


@org_router.post("/", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    request: CreateOrgRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> OrgResponse:
    """Create a new organization — caller becomes owner."""
    svc = _build_org_service(container, session)
    try:
        org = await svc.create_organization(
            name=request.name, owner_id=current_user.id, domain=request.domain
        )
        await session.commit()
        return OrgResponse(
            id=str(org.id),
            name=org.name,
            slug=org.slug,
            domain=org.domain,
            timezone=org.timezone,
            is_active=org.is_active,
            max_members=org.max_members,
            member_count=1,
        )
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


@org_router.get("/{org_id}", response_model=OrgResponse)
async def get_organization(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> OrgResponse:
    """Get organization details."""
    svc = _build_org_service(container, session)
    org = await svc.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    members = await svc._membership_repo.count_members(org_id)
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        domain=org.domain,
        timezone=org.timezone,
        is_active=org.is_active,
        max_members=org.max_members,
        member_count=members,
    )


@org_router.patch("/{org_id}", response_model=OrgResponse)
async def update_organization(
    org_id: UUID,
    request: UpdateOrgRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> OrgResponse:
    """Update org settings (admin/owner only)."""
    svc = _build_org_service(container, session)
    try:
        updates = {k: v for k, v in request.model_dump().items() if v is not None}
        org = await svc.update_organization(org_id, current_user.id, **updates)
        await session.commit()
        members = await svc._membership_repo.count_members(org_id)
        return OrgResponse(
            id=str(org.id),
            name=org.name,
            slug=org.slug,
            domain=org.domain,
            timezone=org.timezone,
            is_active=org.is_active,
            max_members=org.max_members,
            member_count=members,
        )
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@org_router.get("/{org_id}/members", response_model=list[MemberResponse])
async def list_members(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> list[MemberResponse]:
    """List organization members."""
    svc = _build_org_service(container, session)
    try:
        members = await svc.get_members(org_id, current_user.id)
        return [MemberResponse(**m) for m in members]
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)


@org_router.post(
    "/{org_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    org_id: UUID,
    request: InviteMemberRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> MemberResponse:
    """Invite a user to the organization."""
    svc = _build_org_service(container, session)
    try:
        role = (
            OrgRole(request.role)
            if request.role in [r.value for r in OrgRole]
            else OrgRole.MEMBER
        )
        membership = await svc.invite_member(
            org_id, request.email, role, current_user.id
        )
        await session.commit()
        user = await svc._user_repo.get_by_id(membership.user_id)
        return MemberResponse(
            id=str(membership.id),
            user_id=str(membership.user_id),
            email=user.email if user else request.email,
            name=user.name if user else "",
            role=(
                membership.role.value
                if isinstance(membership.role, OrgRole)
                else membership.role
            ),
            joined_at=(
                membership.joined_at.isoformat() if membership.joined_at else None
            ),
            is_active=membership.is_active,
        )
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


@org_router.delete(
    "/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    org_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a member from the organization."""
    svc = _build_org_service(container, session)
    try:
        await svc.remove_member(org_id, user_id, current_user.id)
        await session.commit()
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


# ---------------------------------------------------------------------------
# Provider Connections
# ---------------------------------------------------------------------------


@org_router.get("/{org_id}/providers", response_model=list[ProviderConnectionResponse])
async def list_providers(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> list[ProviderConnectionResponse]:
    """List all provider connections in an org."""
    svc = _build_org_service(container, session)
    try:
        connections = await svc.list_provider_connections(org_id, current_user.id)
        return [
            ProviderConnectionResponse(
                id=str(c.id),
                provider=(
                    c.provider.value
                    if isinstance(c.provider, ProviderType)
                    else c.provider
                ),
                provider_email=c.provider_email,
                status=c.status.value if hasattr(c.status, "value") else c.status,
                calendar_sync_enabled=c.calendar_sync_enabled,
                email_sync_enabled=c.email_sync_enabled,
                last_sync_at=c.last_sync_at.isoformat() if c.last_sync_at else None,
            )
            for c in connections
        ]
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)


@org_router.post(
    "/{org_id}/providers",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def connect_provider(
    org_id: UUID,
    request: ConnectProviderRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> ProviderConnectionResponse:
    """Connect a mail/calendar provider (Google, Microsoft)."""
    svc = _build_org_service(container, session)
    try:
        provider = ProviderType(request.provider)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Unsupported provider: {request.provider}"
        )

    try:
        conn = await svc.connect_provider(
            org_id=org_id,
            user_id=current_user.id,
            provider=provider,
            provider_email=request.provider_email,
            access_token=request.access_token or "dev-token",
            refresh_token=request.refresh_token,
            token_expiry=None,
            scopes=request.scopes,
        )
        await session.commit()
        return ProviderConnectionResponse(
            id=str(conn.id),
            provider=(
                conn.provider.value
                if isinstance(conn.provider, ProviderType)
                else conn.provider
            ),
            provider_email=conn.provider_email,
            status=conn.status.value if hasattr(conn.status, "value") else conn.status,
            calendar_sync_enabled=conn.calendar_sync_enabled,
            email_sync_enabled=conn.email_sync_enabled,
            last_sync_at=None,
        )
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


@org_router.delete(
    "/{org_id}/providers/{conn_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def disconnect_provider(
    org_id: UUID,
    conn_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Disconnect a provider."""
    svc = _build_org_service(container, session)
    try:
        await svc.disconnect_provider(conn_id, current_user.id, org_id)
        await session.commit()
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)


# ---------------------------------------------------------------------------
# Google OAuth Provider Connect Flow
# ---------------------------------------------------------------------------

GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


@org_router.get("/{org_id}/providers/google/auth")
async def google_provider_auth(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start Google OAuth flow to connect a Google Calendar provider.
    Returns an authorization URL the frontend should redirect to."""
    from google_auth_oauthlib.flow import Flow

    settings = container.settings
    if not settings.google_client_id or settings.google_client_id.startswith("your-"):
        raise HTTPException(
            status_code=400,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Settings.",
        )

    # Build the OAuth flow with Calendar scopes
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{_get_base_url(settings)}/api/v1/orgs/google-callback"],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=f"{_get_base_url(settings)}/api/v1/orgs/google-callback",
    )

    # Generate PKCE code verifier
    import base64
    import hashlib
    import json
    import secrets

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    # Encode org_id, user_id, and code_verifier in state parameter
    state_data = json.dumps(
        {
            "org_id": str(org_id),
            "user_id": str(current_user.id),
            "cv": code_verifier,
        }
    )
    state = base64.urlsafe_b64encode(state_data.encode()).decode()

    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return {"authorization_url": url}


@google_callback_router.get("/google-callback")
async def google_provider_callback(
    code: str,
    state: str = "",
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Handle Google OAuth callback — store real tokens in provider_connections.
    Returns an HTML page that redirects back to the app."""
    import base64
    import json

    # Allow Google to return fewer scopes than requested without raising an error
    import os

    import httpx
    from google_auth_oauthlib.flow import Flow

    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

    settings = container.settings
    redirect_uri = f"{_get_base_url(settings)}/api/v1/orgs/google-callback"

    # Decode state to get org_id, user_id, and PKCE code_verifier
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state))
        org_id = UUID(state_data["org_id"])
        user_id = UUID(state_data["user_id"])
        code_verifier = state_data.get("cv", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Exchange authorization code for tokens (with PKCE code_verifier)
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code, code_verifier=code_verifier)
    credentials = flow.credentials

    raw_access_token = credentials.token
    raw_refresh_token = credentials.refresh_token
    token_expiry = credentials.expiry

    # Encrypt tokens before storing
    from src.infrastructure.security.token_encryption import encrypt_token

    access_token = encrypt_token(raw_access_token)
    refresh_token = encrypt_token(raw_refresh_token or "")

    # Get the user's email from Google userinfo (use raw token for API call)
    provider_email = "unknown@gmail.com"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {raw_access_token}"},
            )
            if resp.status_code == 200:
                provider_email = resp.json().get("email", provider_email)
    except Exception:
        pass

    # Store or update the provider connection
    from sqlalchemy import select

    from src.infrastructure.persistence.org_models import ProviderConnectionModel

    # Check if a connection already exists for this org/user/google
    existing = await session.execute(
        select(ProviderConnectionModel).where(
            ProviderConnectionModel.org_id == org_id,
            ProviderConnectionModel.user_id == user_id,
            ProviderConnectionModel.provider == "google",
        )
    )
    model = existing.scalar_one_or_none()

    if model:
        # Update existing connection with real tokens
        model.access_token = access_token
        model.refresh_token = refresh_token or model.refresh_token
        model.token_expiry = token_expiry
        model.provider_email = provider_email
        model.status = "active"
        model.scopes = " ".join(GOOGLE_CALENDAR_SCOPES)
    else:
        # Create new provider connection
        import uuid as _uuid

        model = ProviderConnectionModel(
            id=_uuid.uuid4(),
            org_id=org_id,
            user_id=user_id,
            provider="google",
            provider_email=provider_email,
            status="active",
            access_token=access_token,
            refresh_token=refresh_token or "",
            token_expiry=token_expiry,
            scopes=" ".join(GOOGLE_CALENDAR_SCOPES),
        )
        session.add(model)

    await session.flush()

    # Return an HTML page that auto-redirects to the org-settings view
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        f"""
    <!DOCTYPE html>
    <html>
    <head><title>Connected!</title></head>
    <body style="background:#0f0f11;color:#e4e4eb;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
      <div style="text-align:center">
        <h2>✅ Google Calendar Connected!</h2>
        <p>Account: <strong>{provider_email}</strong></p>
        <p>Redirecting back to Calendar Agent...</p>
        <script>setTimeout(function(){{ window.location.href = '/'; }}, 2000);</script>
      </div>
    </body>
    </html>
    """
    )


def _get_base_url(settings: object) -> str:
    """Get the base URL for OAuth callbacks."""
    port = getattr(settings, "app_port", 8000)
    return f"http://localhost:{port}"

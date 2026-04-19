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
    name: str = Field(default="", description="Display name for the new member")
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


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(
        ...,
        description="New role: admin | member | viewer (owner cannot be assigned here)",
    )


class TransferOwnershipRequest(BaseModel):
    new_owner_email: str = Field(
        ...,
        description="Email of an existing org member who will become the new owner (super admin)",
    )


class LicenseConfigResponse(BaseModel):
    org_id: str
    seat_cost_cents: int
    seat_cost_dollars: float
    max_seats: int
    currency: str
    billing_cycle: str
    notes: str
    # Computed
    active_seats: int
    total_monthly_cost_cents: int


class UpdateLicenseConfigRequest(BaseModel):
    seat_cost_cents: int = Field(
        default=0, ge=0, description="Cost per seat in cents (0 = free / custom)"
    )
    max_seats: int = Field(default=5, ge=1, description="Maximum licensed seats")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_cycle: str = Field(default="monthly", description="monthly | annual")
    notes: str = ""


class CreateOrgEventRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    start_time: str = Field(
        ..., description="ISO 8601 datetime, e.g. 2026-05-01T10:00:00"
    )
    end_time: str = Field(
        ..., description="ISO 8601 datetime, e.g. 2026-05-01T11:00:00"
    )
    description: str = ""
    location: str = ""
    invite_all_members: bool = Field(
        default=True,
        description="If true, invite ALL active org members as attendees",
    )
    extra_attendee_emails: list[str] = Field(
        default_factory=list,
        description="Additional attendee emails beyond org members",
    )


class MemberDetailResponse(BaseModel):
    id: str
    user_id: str
    email: str
    name: str
    role: str
    joined_at: str | None = None
    is_active: bool = True
    plan: str = "free"


# ---------------------------------------------------------------------------
# Helper: build org service from container + session
# ---------------------------------------------------------------------------


def _build_org_service(container: Container, session: AsyncSession):  # type: ignore[no-untyped-def]
    from src.application.services.organization_service import OrganizationService
    from src.infrastructure.persistence.org_repository import (
        SQLAlchemyLicenseConfigRepository,
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
        license_repo=SQLAlchemyLicenseConfigRepository(session),
        db_session_factory=container.database().session_factory,
        settings=container.settings,
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


@org_router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an organization (OWNER only, irreversible)",
)
async def delete_organization(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """
    Permanently delete an organization and all its data:
    memberships, provider connections, pending invites, license config.

    Restricted to the organization OWNER.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import select

    from src.infrastructure.persistence.models import OrgPendingInviteModel
    from src.infrastructure.persistence.org_models import (
        OrganizationModel,
        OrgLicenseConfigModel,
        OrgMembershipModel,
        ProviderConnectionModel,
    )

    # Verify OWNER
    owner_result = await session.execute(
        select(OrgMembershipModel).where(
            OrgMembershipModel.org_id == org_id,
            OrgMembershipModel.user_id == current_user.id,
            OrgMembershipModel.role == "owner",
        )
    )
    if not owner_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organization OWNER can delete it.",
        )

    # Check org exists
    org_result = await session.execute(
        select(OrganizationModel).where(OrganizationModel.id == org_id)
    )
    if not org_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Organization not found")

    # Cascade delete in dependency order
    await session.execute(
        sql_delete(OrgPendingInviteModel).where(OrgPendingInviteModel.org_id == org_id)
    )
    await session.execute(
        sql_delete(OrgLicenseConfigModel).where(OrgLicenseConfigModel.org_id == org_id)
    )
    await session.execute(
        sql_delete(ProviderConnectionModel).where(
            ProviderConnectionModel.org_id == org_id
        )
    )
    await session.execute(
        sql_delete(OrgMembershipModel).where(OrgMembershipModel.org_id == org_id)
    )
    await session.execute(
        sql_delete(OrganizationModel).where(OrganizationModel.id == org_id)
    )
    await session.commit()

    # Audit trail
    try:
        from src.api.middleware.correlation_id import request_id_var

        await container.audit_log_service().record(
            action="org.deleted",
            actor_id=str(current_user.id),
            target_id=str(org_id),
            detail=f"Organization {org_id} permanently deleted by owner",
            request_id=request_id_var.get(""),
        )
    except Exception:
        pass


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
    page: int = 1,
    page_size: int = 50,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> list[MemberResponse]:
    """List organization members (paginated). page is 1-based, max page_size=200.

    Response headers:
    - ``X-Total-Count``: total number of members in the org
    - ``X-Page``: current page (1-based)
    - ``X-Page-Size``: page size used
    """
    from fastapi.responses import JSONResponse

    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = min(max(page_size, 1), 200)
    svc = _build_org_service(container, session)
    try:
        members = await svc.get_members(org_id, current_user.id)
        total = len(members)
        start = (page - 1) * page_size
        page_members = [MemberResponse(**m) for m in members[start : start + page_size]]
        return JSONResponse(
            content=[m.model_dump() for m in page_members],
            headers={
                "X-Total-Count": str(total),
                "X-Page": str(page),
                "X-Page-Size": str(page_size),
            },
        )
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
            org_id,
            request.email,
            role,
            current_user.id,
            member_name=request.name,
        )
        await session.commit()
        user = await svc._user_repo.get_by_id(membership.user_id)
        # Audit trail
        try:
            from src.api.middleware.correlation_id import request_id_var

            await container.audit_log_service().record(
                action="org.member_invited",
                actor_id=str(current_user.id),
                target_id=str(membership.user_id),
                detail=f"Invited {request.email} as {role.value} to org {org_id}",
                request_id=request_id_var.get(""),
            )
        except Exception:
            pass
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
        # Audit trail
        try:
            from src.api.middleware.correlation_id import request_id_var

            await container.audit_log_service().record(
                action="org.member_removed",
                actor_id=str(current_user.id),
                target_id=str(user_id),
                detail=f"Removed member {user_id} from org {org_id}",
                request_id=request_id_var.get(""),
            )
        except Exception:
            pass
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


# ---------------------------------------------------------------------------
# Pending invites (list + revoke)
# ---------------------------------------------------------------------------


class PendingInviteResponse(BaseModel):
    id: str
    org_id: str
    email: str
    role: str
    invited_by: str
    expires_at: str
    accepted: bool
    created_at: str


@org_router.get("/{org_id}/invites", response_model=list[PendingInviteResponse])
async def list_pending_invites(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> list[PendingInviteResponse]:
    """List non-expired, non-accepted invites for an org. OWNER/ADMIN only."""
    from datetime import timezone as _tz

    from sqlalchemy import select

    from src.infrastructure.persistence.models import OrgPendingInviteModel

    svc = _build_org_service(container, session)
    try:
        await svc._require_role(org_id, current_user.id, {OrgRole.OWNER, OrgRole.ADMIN})
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)

    now = __import__("datetime").datetime.now(_tz.utc)
    result = await session.execute(
        select(OrgPendingInviteModel).where(
            OrgPendingInviteModel.org_id == org_id,
            OrgPendingInviteModel.accepted == False,  # noqa: E712
            OrgPendingInviteModel.expires_at > now,
        )
    )
    rows = result.scalars().all()
    return [
        PendingInviteResponse(
            id=str(r.id),
            org_id=str(r.org_id),
            email=r.email,
            role=r.role,
            invited_by=str(r.invited_by),
            expires_at=r.expires_at.isoformat(),
            accepted=r.accepted,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@org_router.delete(
    "/{org_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_pending_invite(
    org_id: UUID,
    invite_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke (delete) a pending invite. OWNER/ADMIN only."""
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import select

    from src.infrastructure.persistence.models import OrgPendingInviteModel

    svc = _build_org_service(container, session)
    try:
        await svc._require_role(org_id, current_user.id, {OrgRole.OWNER, OrgRole.ADMIN})
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)

    result = await session.execute(
        select(OrgPendingInviteModel).where(
            OrgPendingInviteModel.id == invite_id,
            OrgPendingInviteModel.org_id == org_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Invite not found")
    await session.delete(row)
    await session.commit()


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
    import base64
    import json
    import os

    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=f"{_get_base_url(settings)}/api/v1/orgs/google-callback",
        autogenerate_code_verifier=False,
    )

    # Encode org_id and user_id in state parameter (no PKCE — confidential web client)
    state_data = json.dumps({"org_id": str(org_id), "user_id": str(current_user.id)})
    state = base64.urlsafe_b64encode(state_data.encode()).decode()

    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
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

    # Decode state to get org_id and user_id
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state))
        org_id = UUID(state_data["org_id"])
        user_id = UUID(state_data["user_id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Exchange authorization code for tokens (no PKCE — confidential web client)
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
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(code=code)
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
    """Get the base URL for OAuth callbacks — reads from app_base_url setting."""
    base = getattr(settings, "app_base_url", "")
    if base:
        return base.rstrip("/")
    port = getattr(settings, "app_port", 8000)
    return f"http://localhost:{port}"


# ---------------------------------------------------------------------------
# RBAC: Update member role (Feature 5)
# ---------------------------------------------------------------------------


@org_router.patch("/{org_id}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    org_id: UUID,
    user_id: UUID,
    request: UpdateMemberRoleRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> MemberResponse:
    """
    Update a member's role within the organization.
    Allowed transitions: admin | member | viewer.
    The OWNER role cannot be assigned here — use the transfer-ownership endpoint.
    Requires OWNER or ADMIN.
    """
    role_str = request.role.lower()
    if role_str == OrgRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign the OWNER role directly. Use POST /{org_id}/transfer-ownership.",
        )
    if role_str not in [r.value for r in OrgRole]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{request.role}'. Choose from: admin, member, viewer.",
        )

    svc = _build_org_service(container, session)
    try:
        membership = await svc.update_member_role(
            org_id, user_id, OrgRole(role_str), current_user.id
        )
        if not membership:
            raise HTTPException(status_code=404, detail="Member not found")
        await session.commit()
        user = await svc._user_repo.get_by_id(membership.user_id)
        return MemberResponse(
            id=str(membership.id),
            user_id=str(membership.user_id),
            email=user.email if user else "",
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


# ---------------------------------------------------------------------------
# Get single member detail (Feature 3)
# ---------------------------------------------------------------------------


@org_router.get("/{org_id}/members/{user_id}", response_model=MemberDetailResponse)
async def get_member(
    org_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> MemberDetailResponse:
    """Get detailed information about a specific org member."""
    svc = _build_org_service(container, session)
    try:
        detail = await svc.get_member_detail(org_id, user_id, current_user.id)
        return MemberDetailResponse(**detail)
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=404, detail=e.message)


# ---------------------------------------------------------------------------
# Transfer ownership / super-admin (Feature 2)
# ---------------------------------------------------------------------------


@org_router.post("/{org_id}/transfer-ownership", response_model=MemberResponse)
async def transfer_ownership(
    org_id: UUID,
    request: TransferOwnershipRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> MemberResponse:
    """
    Transfer the OWNER (super-admin) role to another org member.
    Only the current OWNER can call this. The old owner is demoted to ADMIN.
    """
    svc = _build_org_service(container, session)
    try:
        membership = await svc.transfer_ownership(
            org_id, current_user.id, request.new_owner_email
        )
        await session.commit()
        user = await svc._user_repo.get_by_id(membership.user_id)
        return MemberResponse(
            id=str(membership.id),
            user_id=str(membership.user_id),
            email=user.email if user else request.new_owner_email,
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


# ---------------------------------------------------------------------------
# Per-user license configuration (Feature 4)
# ---------------------------------------------------------------------------


@org_router.get("/{org_id}/license", response_model=LicenseConfigResponse)
async def get_license_config(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> LicenseConfigResponse:
    """
    Retrieve the per-user license configuration for the organization.
    Returns seat cost, max seats, billing cycle, and current cost summary.
    All org members can view; only OWNER/ADMIN can change.
    """
    svc = _build_org_service(container, session)
    try:
        cost_summary = await svc.calculate_license_cost(org_id, current_user.id)
        cfg = await svc.get_license_config(org_id, current_user.id)
        return LicenseConfigResponse(
            org_id=str(org_id),
            seat_cost_cents=cfg.seat_cost_cents if cfg else 0,
            seat_cost_dollars=(cfg.seat_cost_cents / 100) if cfg else 0.0,
            max_seats=cfg.max_seats if cfg else 5,
            currency=cfg.currency if cfg else "USD",
            billing_cycle=cfg.billing_cycle if cfg else "monthly",
            notes=cfg.notes if cfg else "",
            active_seats=cost_summary["active_seats"],
            total_monthly_cost_cents=cost_summary["total_cost_cents"],
        )
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


@org_router.put("/{org_id}/license", response_model=LicenseConfigResponse)
async def update_license_config(
    org_id: UUID,
    request: UpdateLicenseConfigRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> LicenseConfigResponse:
    """
    Create or update the per-user license configuration.
    OWNER and ADMIN only.

    seat_cost_cents: price per active user each billing cycle (in cents).
                     E.g. 1000 = $10/seat/month.
    max_seats: maximum concurrent licensed users (org member cap for billing).
    """
    svc = _build_org_service(container, session)
    try:
        cfg = await svc.set_license_config(
            org_id=org_id,
            actor_id=current_user.id,
            seat_cost_cents=request.seat_cost_cents,
            max_seats=request.max_seats,
            currency=request.currency,
            billing_cycle=request.billing_cycle,
            notes=request.notes,
        )
        await session.commit()
        active_seats = await svc._membership_repo.count_members(org_id)
        return LicenseConfigResponse(
            org_id=str(org_id),
            seat_cost_cents=cfg.seat_cost_cents,
            seat_cost_dollars=cfg.seat_cost_dollars,
            max_seats=cfg.max_seats,
            currency=cfg.currency,
            billing_cycle=cfg.billing_cycle,
            notes=cfg.notes,
            active_seats=active_seats,
            total_monthly_cost_cents=cfg.calculate_total_cost_cents(active_seats),
        )
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)
    except DomainError as e:
        raise HTTPException(status_code=400, detail=e.message)


# ---------------------------------------------------------------------------
# Org-scoped event creation with member invites (Feature 6)
# ---------------------------------------------------------------------------


@org_router.post(
    "/{org_id}/events",
    status_code=status.HTTP_201_CREATED,
)
async def create_org_event(
    org_id: UUID,
    request: CreateOrgEventRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Create a calendar event on behalf of the requesting user and automatically
    invite all active org members (or a specific subset).

    How it works:
      1. Collects email addresses of active org members from their provider_connections.
      2. Calls CalendarService.create_event() with those attendees.
      3. Google / Microsoft sends calendar invite emails to every attendee.
      4. The event appears on each invited member's calendar (if they accept).

    Requires MEMBER role or higher.
    """
    from datetime import datetime as _dt

    from src.application.dto import CreateEventDTO
    from src.infrastructure.persistence.org_models import ProviderConnectionModel

    svc = _build_org_service(container, session)

    # Any org member can create an org event
    try:
        memberships = await svc._membership_repo.get_members(org_id)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if not any(str(m.user_id) == str(current_user.id) for m in memberships):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    # Parse event datetimes
    try:
        start_dt = _dt.fromisoformat(request.start_time)
        end_dt = _dt.fromisoformat(request.end_time)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid datetime format. Use ISO 8601, e.g. 2026-05-01T10:00:00 "
                "or 2026-05-01T10:00:00+05:30"
            ),
        )

    attendee_emails: list[str] = list(request.extra_attendee_emails)

    if request.invite_all_members:
        # Collect primary emails from provider_connections for each member
        from sqlalchemy import select

        member_ids = [m.user_id for m in memberships]
        # Prefer provider connection emails (real calendar email)
        result = await session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id.in_(member_ids),
                ProviderConnectionModel.status == "active",
                ProviderConnectionModel.calendar_sync_enabled == True,  # noqa: E712
            )
        )
        connections = result.scalars().all()

        # One email per member (first found)
        seen_user_ids: set[str] = set()
        for conn in connections:
            uid = str(conn.user_id)
            if uid not in seen_user_ids and conn.provider_email:
                attendee_emails.append(conn.provider_email)
                seen_user_ids.add(uid)

        # Fall back to users.email for members without a provider connection
        for m in memberships:
            uid = str(m.user_id)
            if uid not in seen_user_ids:
                user_obj = await svc._user_repo.get_by_id(m.user_id)
                if user_obj and user_obj.email:
                    attendee_emails.append(user_obj.email)

    # Remove duplicates and the organiser themselves
    organiser_emails = {current_user.email}
    attendee_emails = [
        e for e in dict.fromkeys(attendee_emails) if e not in organiser_emails
    ]

    # Create the event via CalendarService
    from src.application.services.calendar_service import CalendarService

    cal_svc = CalendarService(
        calendar_provider=container.calendar_adapter(),
        event_repository=container.calendar_adapter(),  # adapter satisfies both ports
        cache=container.cache(),
    )

    dto = CreateEventDTO(
        title=request.title,
        description=request.description,
        location=request.location,
        start_time=start_dt,
        end_time=end_dt,
        attendee_emails=attendee_emails,
    )

    try:
        result_dto = await cal_svc.create_event(user_id=current_user.id, dto=dto)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create calendar event: {exc}",
        )

    event_id = result_dto.id if hasattr(result_dto, "id") else ""

    # Persist org-event record so GET /{org_id}/events can return history
    try:
        import json as _json

        from src.infrastructure.persistence.calendar_event_model import (
            CalendarEventModel,
        )

        record = CalendarEventModel(
            user_id=current_user.id,
            org_id=org_id,
            provider_event_id=event_id or None,
            title=request.title,
            description=request.description,
            location=request.location,
            start_time=start_dt,
            end_time=end_dt,
            attendees_json=_json.dumps(attendee_emails),
        )
        session.add(record)
        await session.commit()
    except Exception:
        pass  # Storage failure is non-fatal — calendar event was already created

    return {
        "event_id": event_id,
        "title": request.title,
        "start_time": request.start_time,
        "end_time": request.end_time,
        "attendees_invited": attendee_emails,
        "attendee_count": len(attendee_emails),
        "org_id": str(org_id),
    }


@org_router.get("/{org_id}/events")
async def list_org_events(
    org_id: UUID,
    page: int = 1,
    page_size: int = 50,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    List calendar events created for this org (newest first, paginated).

    Response headers: ``X-Total-Count``, ``X-Page``, ``X-Page-Size``.
    """
    import json as _json

    from fastapi.responses import JSONResponse
    from sqlalchemy import func, select

    from src.infrastructure.persistence.calendar_event_model import CalendarEventModel

    svc = _build_org_service(container, session)

    # Only org members can read org events
    try:
        memberships = await svc._membership_repo.get_members(org_id)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    if not any(str(m.user_id) == str(current_user.id) for m in memberships):
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    page = max(1, page)
    page_size = min(max(page_size, 1), 200)

    base_q = select(CalendarEventModel).where(CalendarEventModel.org_id == org_id)
    count_result = await session.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total = count_result.scalar_one()

    result = await session.execute(
        base_q.order_by(CalendarEventModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.scalars().all()

    items = [
        {
            "id": str(r.id),
            "title": r.title,
            "description": r.description,
            "location": r.location,
            "start_time": r.start_time.isoformat(),
            "end_time": r.end_time.isoformat(),
            "attendees": _json.loads(r.attendees_json or "[]"),
            "created_by_user_id": str(r.user_id),
            "provider_event_id": r.provider_event_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return JSONResponse(
        content=items,
        headers={
            "X-Total-Count": str(total),
            "X-Page": str(page),
            "X-Page-Size": str(page_size),
        },
    )


# ---------------------------------------------------------------------------
# WhatsApp integration config — OWNER/ADMIN only
# ---------------------------------------------------------------------------


class WhatsAppConfigRequest(BaseModel):
    phone_number_id: str = Field(..., min_length=1, max_length=60)
    display_phone: str = Field("", max_length=30)
    access_token: str = Field(..., min_length=1)
    verify_token: str = Field("calendar-agent-whatsapp", min_length=4, max_length=255)
    webhook_secret: str = Field("", max_length=255)
    auto_reply: bool = True
    enabled: bool = True


class WhatsAppConfigResponse(BaseModel):
    org_id: str
    phone_number_id: str
    display_phone: str
    verify_token: str
    auto_reply: bool
    enabled: bool
    has_access_token: bool  # never expose the raw token in responses
    updated_at: str


@org_router.get(
    "/{org_id}/whatsapp",
    response_model=WhatsAppConfigResponse,
    summary="Get WhatsApp config for org",
    tags=["WhatsApp Admin"],
)
async def get_whatsapp_config(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    container: Container = Depends(get_container),
) -> WhatsAppConfigResponse:
    """Return the WhatsApp configuration for an org. OWNER/ADMIN only."""
    from sqlalchemy import select

    from src.infrastructure.persistence.org_models import OrgWhatsAppConfigModel

    svc = _build_org_service(container, session)
    try:
        await svc._require_role(org_id, current_user.id, {OrgRole.OWNER, OrgRole.ADMIN})
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)

    result = await session.execute(
        select(OrgWhatsAppConfigModel).where(
            OrgWhatsAppConfigModel.org_id == org_id
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=404, detail="WhatsApp not configured for this org")

    return WhatsAppConfigResponse(
        org_id=str(cfg.org_id),
        phone_number_id=cfg.phone_number_id,
        display_phone=cfg.display_phone,
        verify_token=cfg.verify_token,
        auto_reply=cfg.auto_reply,
        enabled=cfg.enabled,
        has_access_token=bool(cfg.access_token),
        updated_at=cfg.updated_at.isoformat(),
    )


@org_router.put(
    "/{org_id}/whatsapp",
    response_model=WhatsAppConfigResponse,
    summary="Save / update WhatsApp config for org",
    tags=["WhatsApp Admin"],
)
async def upsert_whatsapp_config(
    org_id: UUID,
    body: WhatsAppConfigRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    container: Container = Depends(get_container),
) -> WhatsAppConfigResponse:
    """
    Create or update the WhatsApp integration for an org.

    Only OWNER or ADMIN of the org (or platform Super Admin) may call this.
    The raw access_token is stored in the DB; apply column-level encryption
    in production (Fernet, AWS KMS, or Vault).
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    from sqlalchemy import select

    from src.infrastructure.persistence.org_models import OrgWhatsAppConfigModel

    svc = _build_org_service(container, session)
    try:
        await svc._require_role(org_id, current_user.id, {OrgRole.OWNER, OrgRole.ADMIN})
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)

    result = await session.execute(
        select(OrgWhatsAppConfigModel).where(OrgWhatsAppConfigModel.org_id == org_id)
    )
    cfg = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if cfg is None:
        cfg = OrgWhatsAppConfigModel(
            id=_uuid.uuid4(),
            org_id=org_id,
            created_by=current_user.id,
            created_at=now,
        )
        session.add(cfg)

    cfg.phone_number_id = body.phone_number_id
    cfg.display_phone = body.display_phone
    cfg.access_token = body.access_token
    cfg.verify_token = body.verify_token
    cfg.webhook_secret = body.webhook_secret
    cfg.auto_reply = body.auto_reply
    cfg.enabled = body.enabled
    cfg.updated_at = now

    await session.commit()
    await session.refresh(cfg)

    return WhatsAppConfigResponse(
        org_id=str(cfg.org_id),
        phone_number_id=cfg.phone_number_id,
        display_phone=cfg.display_phone,
        verify_token=cfg.verify_token,
        auto_reply=cfg.auto_reply,
        enabled=cfg.enabled,
        has_access_token=bool(cfg.access_token),
        updated_at=cfg.updated_at.isoformat(),
    )


@org_router.delete(
    "/{org_id}/whatsapp",
    status_code=204,
    summary="Remove WhatsApp config for org",
    tags=["WhatsApp Admin"],
)
async def delete_whatsapp_config(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    container: Container = Depends(get_container),
) -> None:
    """Delete the WhatsApp integration for an org. OWNER only."""
    from sqlalchemy import select

    from src.infrastructure.persistence.org_models import OrgWhatsAppConfigModel

    svc = _build_org_service(container, session)
    try:
        await svc._require_role(org_id, current_user.id, {OrgRole.OWNER})
    except InsufficientPermissionsError as e:
        raise HTTPException(status_code=403, detail=e.message)

    result = await session.execute(
        select(OrgWhatsAppConfigModel).where(OrgWhatsAppConfigModel.org_id == org_id)
    )
    cfg = result.scalar_one_or_none()
    if cfg:
        await session.delete(cfg)
        await session.commit()

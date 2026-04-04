"""
Organization Service — multi-tenant orchestration.
Handles org CRUD, membership management, provider connections.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.domain.entities.organization import (
    ConnectionStatus,
    Organization,
    OrgMembership,
    OrgRole,
    ProviderConnection,
    ProviderType,
)
from src.domain.exceptions import (
    DomainError,
    InsufficientPermissionsError,
)
from src.domain.interfaces.organization_repository import (
    OrganizationRepositoryPort,
    OrgMembershipRepositoryPort,
    ProviderConnectionRepositoryPort,
)
from src.domain.interfaces.user_repository import UserRepositoryPort


class OrganizationService:
    """Orchestrates organization lifecycle and membership."""

    def __init__(
        self,
        org_repo: OrganizationRepositoryPort,
        membership_repo: OrgMembershipRepositoryPort,
        provider_repo: ProviderConnectionRepositoryPort,
        user_repo: UserRepositoryPort,
    ) -> None:
        self._org_repo = org_repo
        self._membership_repo = membership_repo
        self._provider_repo = provider_repo
        self._user_repo = user_repo

    # ---- Organization CRUD -------------------------------------------

    async def create_organization(
        self, name: str, owner_id: UUID, domain: str | None = None
    ) -> Organization:
        """Create a new organization and set the creator as owner."""
        slug = self._slugify(name)

        # Check slug uniqueness
        existing = await self._org_repo.get_by_slug(slug)
        if existing:
            slug = f"{slug}-{uuid4().hex[:6]}"

        org = Organization(
            name=name,
            slug=slug,
            owner_id=owner_id,
            domain=domain,
        )
        org = await self._org_repo.create(org)

        # Auto-add owner as first member
        membership = OrgMembership(
            org_id=org.id,
            user_id=owner_id,
            role=OrgRole.OWNER,
            joined_at=datetime.now(timezone.utc),
        )
        await self._membership_repo.add_member(membership)

        return org

    async def get_organization(self, org_id: UUID) -> Organization | None:
        return await self._org_repo.get_by_id(org_id)

    async def list_user_organizations(self, user_id: UUID) -> list[Organization]:
        return await self._org_repo.list_by_user(user_id)

    async def update_organization(
        self, org_id: UUID, actor_id: UUID, **kwargs: str | None
    ) -> Organization:
        """Update org settings — requires admin/owner role."""
        await self._require_role(org_id, actor_id, {OrgRole.OWNER, OrgRole.ADMIN})
        org = await self._org_repo.get_by_id(org_id)
        if not org:
            raise DomainError("Organization not found")

        for key, value in kwargs.items():
            if hasattr(org, key) and value is not None:
                setattr(org, key, value)
        org.updated_at = datetime.now(timezone.utc)
        return await self._org_repo.update(org)

    # ---- Membership ---------------------------------------------------

    async def invite_member(
        self, org_id: UUID, email: str, role: OrgRole, invited_by: UUID
    ) -> OrgMembership:
        """Invite a user to the organization."""
        await self._require_role(org_id, invited_by, {OrgRole.OWNER, OrgRole.ADMIN})

        org = await self._org_repo.get_by_id(org_id)
        if not org:
            raise DomainError("Organization not found")

        # Check member limit
        count = await self._membership_repo.count_members(org_id)
        if count >= org.max_members:
            raise DomainError(f"Organization member limit reached ({org.max_members})")

        # Find or create user
        user = await self._user_repo.get_by_email(email)
        if not user:
            from src.domain.entities.user import User

            user = User(email=email, name=email.split("@")[0])
            user = await self._user_repo.create(user)

        # Check not already member
        existing = await self._membership_repo.get_membership(org_id, user.id)
        if existing:
            raise DomainError("User is already a member of this organization")

        membership = OrgMembership(
            org_id=org_id,
            user_id=user.id,
            role=role,
            invited_by=invited_by,
            joined_at=datetime.now(timezone.utc),
        )
        return await self._membership_repo.add_member(membership)

    async def get_members(self, org_id: UUID, actor_id: UUID) -> list[dict]:
        """Get all members of an org with user details."""
        await self._require_role(
            org_id,
            actor_id,
            {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER, OrgRole.VIEWER},
        )
        memberships = await self._membership_repo.get_members(org_id)
        result = []
        for m in memberships:
            user = await self._user_repo.get_by_id(m.user_id)
            result.append(
                {
                    "id": str(m.id),
                    "user_id": str(m.user_id),
                    "email": user.email if user else "unknown",
                    "name": user.name if user else "Unknown",
                    "role": m.role.value if isinstance(m.role, OrgRole) else m.role,
                    "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                    "is_active": m.is_active,
                }
            )
        return result

    async def remove_member(self, org_id: UUID, user_id: UUID, actor_id: UUID) -> bool:
        """Remove a member — requires admin/owner."""
        await self._require_role(org_id, actor_id, {OrgRole.OWNER, OrgRole.ADMIN})
        if user_id == actor_id:
            raise DomainError("Cannot remove yourself from the organization")
        return await self._membership_repo.remove_member(org_id, user_id)

    async def update_member_role(
        self, org_id: UUID, user_id: UUID, new_role: OrgRole, actor_id: UUID
    ) -> OrgMembership | None:
        await self._require_role(org_id, actor_id, {OrgRole.OWNER, OrgRole.ADMIN})
        return await self._membership_repo.update_role(org_id, user_id, new_role.value)

    # ---- Provider Connections -----------------------------------------

    async def connect_provider(
        self,
        org_id: UUID,
        user_id: UUID,
        provider: ProviderType,
        provider_email: str,
        access_token: str,
        refresh_token: str | None,
        token_expiry: datetime | None,
        scopes: str = "",
    ) -> ProviderConnection:
        """Connect a mail/calendar provider for a user in an org."""
        await self._require_role(
            org_id, user_id, {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER}
        )

        # Check if connection already exists
        existing = await self._provider_repo.get_active_connection(
            user_id, provider.value, org_id
        )
        if existing:
            existing.refresh_tokens(
                access_token, refresh_token, token_expiry or datetime.now(timezone.utc)
            )
            existing.scopes = scopes
            existing.provider_email = provider_email
            return await self._provider_repo.update(existing)

        conn = ProviderConnection(
            org_id=org_id,
            user_id=user_id,
            provider=provider,
            provider_email=provider_email,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=token_expiry,
            scopes=scopes,
            status=ConnectionStatus.ACTIVE,
        )
        return await self._provider_repo.create(conn)

    async def list_provider_connections(
        self, org_id: UUID, actor_id: UUID
    ) -> list[ProviderConnection]:
        """List all provider connections in an org."""
        await self._require_role(
            org_id,
            actor_id,
            {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER, OrgRole.VIEWER},
        )
        return await self._provider_repo.list_by_org(org_id)

    async def disconnect_provider(
        self, conn_id: UUID, actor_id: UUID, org_id: UUID
    ) -> bool:
        """Remove a provider connection."""
        await self._require_role(org_id, actor_id, {OrgRole.OWNER, OrgRole.ADMIN})
        return await self._provider_repo.delete(conn_id)

    # ---- Helpers ------------------------------------------------------

    async def _require_role(
        self, org_id: UUID, user_id: UUID, allowed_roles: set[OrgRole]
    ) -> OrgMembership:
        """Verify user has one of the required roles in the org."""
        membership = await self._membership_repo.get_membership(org_id, user_id)
        if not membership:
            raise InsufficientPermissionsError("Not a member of this organization")
        role = (
            membership.role
            if isinstance(membership.role, OrgRole)
            else OrgRole(membership.role)
        )
        if role not in allowed_roles:
            raise InsufficientPermissionsError(
                f"Requires {' or '.join(r.value for r in allowed_roles)} role"
            )
        return membership

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to URL-safe slug."""
        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug[:50]

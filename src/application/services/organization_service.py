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
    OrgLicenseConfig,
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
    OrgLicenseConfigRepositoryPort,
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
        license_repo: OrgLicenseConfigRepositoryPort | None = None,
        db_session_factory: object | None = None,
        settings: object | None = None,
    ) -> None:
        self._org_repo = org_repo
        self._membership_repo = membership_repo
        self._provider_repo = provider_repo
        self._user_repo = user_repo
        self._license_repo = license_repo
        self._db_session_factory = db_session_factory
        self._settings = settings

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
        self,
        org_id: UUID,
        email: str,
        role: OrgRole,
        invited_by: UUID,
        member_name: str = "",
    ) -> OrgMembership:
        """Invite a user to the organization."""
        await self._require_role(org_id, invited_by, {OrgRole.OWNER, OrgRole.ADMIN})

        # OWNER is the super-admin: at most one per org.
        # New members can only be ADMIN, MEMBER, or VIEWER.
        if role == OrgRole.OWNER:
            raise DomainError(
                "Cannot assign the OWNER (super-admin) role via invitation. "
                "Use the transfer-ownership endpoint instead."
            )

        org = await self._org_repo.get_by_id(org_id)
        if not org:
            raise DomainError("Organization not found")

        # Seat cap: prefer license max_seats if configured, fall back to org.max_members
        count = await self._membership_repo.count_members(org_id)
        effective_cap = org.max_members
        if self._license_repo is not None:
            lic = await self._license_repo.get_by_org(org_id)
            if lic is not None:
                effective_cap = lic.max_seats
        if count >= effective_cap:
            raise DomainError(
                f"Organization seat limit reached ({effective_cap} seats). "
                "Increase max_seats via PUT /api/v1/orgs/{org_id}/license."
            )

        # Find or create user — use provided name if creating new account
        user = await self._user_repo.get_by_email(email)
        if not user:
            from src.domain.entities.user import User

            display_name = member_name.strip() if member_name else email.split("@")[0]
            user = User(email=email, name=display_name)
            user = await self._user_repo.create(user)
        elif member_name and not user.name:
            user.name = member_name.strip()
            await self._user_repo.update(user)

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
        membership = await self._membership_repo.add_member(membership)

        # Create pending-invite token for the magic-link accept flow
        await self._create_pending_invite(
            org_id=org_id,
            user_id=user.id,
            email=email,
            role=role,
            invited_by=invited_by,
        )

        return membership

    async def _create_pending_invite(
        self,
        org_id: UUID,
        user_id: UUID,
        email: str,
        role: "OrgRole",
        invited_by: UUID,
    ) -> str:
        """Create a pending-invite row, return the token, and send invite email."""
        import secrets
        from datetime import timedelta

        from src.infrastructure.persistence.models import OrgPendingInviteModel

        token = secrets.token_hex(48)
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        if self._db_session_factory is not None:
            async with self._db_session_factory() as session:
                row = OrgPendingInviteModel(
                    id=uuid4(),
                    org_id=org_id,
                    user_id=user_id,
                    invited_by=invited_by,
                    role=role.value if isinstance(role, OrgRole) else role,
                    token=token,
                    email=email,
                    expires_at=expires_at,
                    accepted=False,
                )
                session.add(row)
                await session.commit()

        import logging

        _log = logging.getLogger("calendar_agent")
        _log.info(
            "org.member_invited_pending email=%s org_id=%s token_prefix=%s",
            email,
            org_id,
            token[:8],
        )

        # Send invite email (no-op if SMTP not configured)
        if self._settings is not None:
            try:
                from src.infrastructure.notifications.email_sender import (
                    send_invite_email,
                )

                org = await self._org_repo.get_by_id(org_id)
                org_name = org.name if org else str(org_id)

                inviter = await self._user_repo.get_by_id(invited_by)
                inviter_name = inviter.name if inviter else "A team member"

                base_url = getattr(
                    self._settings, "app_base_url", "http://localhost:8000"
                )
                accept_url = f"{base_url}/api/v1/auth/accept-invite?token={token}"

                role_str = role.value if isinstance(role, OrgRole) else str(role)
                await send_invite_email(
                    settings=self._settings,
                    to_email=email,
                    org_name=org_name,
                    inviter_name=inviter_name,
                    accept_url=accept_url,
                    role=role_str,
                )
            except Exception as exc:
                _log.warning("Failed to send invite email to %s: %s", email, exc)

        return token

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

    # ---- License Configuration ----------------------------------------

    async def get_license_config(
        self, org_id: UUID, actor_id: UUID
    ) -> OrgLicenseConfig | None:
        """Retrieve the license config for an org. Any member can view."""
        await self._require_role(
            org_id,
            actor_id,
            {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER, OrgRole.VIEWER},
        )
        if self._license_repo is None:
            return None
        return await self._license_repo.get_by_org(org_id)

    async def set_license_config(
        self,
        org_id: UUID,
        actor_id: UUID,
        seat_cost_cents: int = 0,
        max_seats: int = 5,
        currency: str = "USD",
        billing_cycle: str = "monthly",
        notes: str = "",
    ) -> OrgLicenseConfig:
        """Create or update the per-user license config. OWNER/ADMIN only."""
        await self._require_role(org_id, actor_id, {OrgRole.OWNER, OrgRole.ADMIN})

        if seat_cost_cents < 0:
            raise DomainError("seat_cost_cents must be >= 0")
        if max_seats < 1:
            raise DomainError("max_seats must be >= 1")
        if billing_cycle not in ("monthly", "annual"):
            raise DomainError("billing_cycle must be 'monthly' or 'annual'")
        if len(currency) != 3:
            raise DomainError("currency must be a 3-letter ISO 4217 code")

        if self._license_repo is None:
            raise DomainError("License repository not available")

        config = OrgLicenseConfig(
            org_id=org_id,
            seat_cost_cents=seat_cost_cents,
            max_seats=max_seats,
            currency=currency.upper(),
            billing_cycle=billing_cycle,
            notes=notes,
        )
        saved = await self._license_repo.upsert(config)

        # Keep org.max_members in sync so all legacy seat-cap checks stay consistent
        org = await self._org_repo.get_by_id(org_id)
        if org and org.max_members != max_seats:
            org.max_members = max_seats
            org.updated_at = datetime.now(timezone.utc)
            await self._org_repo.update(org)

        return saved

    async def calculate_license_cost(self, org_id: UUID, actor_id: UUID) -> dict:
        """
        Return a cost summary for the org:
          active_seats, seat_cost_cents, total_cost_cents, currency, billing_cycle
        """
        await self._require_role(org_id, actor_id, {OrgRole.OWNER, OrgRole.ADMIN})
        active_seats = await self._membership_repo.count_members(org_id)
        if self._license_repo is None:
            return {
                "active_seats": active_seats,
                "seat_cost_cents": 0,
                "total_cost_cents": 0,
                "currency": "USD",
                "billing_cycle": "monthly",
            }
        cfg = await self._license_repo.get_by_org(org_id)
        if cfg is None:
            return {
                "active_seats": active_seats,
                "seat_cost_cents": 0,
                "total_cost_cents": 0,
                "currency": "USD",
                "billing_cycle": "monthly",
            }
        return {
            "active_seats": active_seats,
            "seat_cost_cents": cfg.seat_cost_cents,
            "total_cost_cents": cfg.calculate_total_cost_cents(active_seats),
            "currency": cfg.currency,
            "billing_cycle": cfg.billing_cycle,
        }

    # ---- Ownership Transfer -------------------------------------------

    async def transfer_ownership(
        self, org_id: UUID, current_owner_id: UUID, new_owner_email: str
    ) -> OrgMembership:
        """
        Transfer the OWNER role from the current owner to another org member.
        Only the current OWNER can do this.
        """
        await self._require_role(org_id, current_owner_id, {OrgRole.OWNER})

        new_owner = await self._user_repo.get_by_email(new_owner_email)
        if not new_owner:
            raise DomainError(f"No user found with email '{new_owner_email}'")

        new_owner_membership = await self._membership_repo.get_membership(
            org_id, new_owner.id
        )
        if not new_owner_membership:
            raise DomainError(
                f"User '{new_owner_email}' is not a member of this organization"
            )

        if new_owner.id == current_owner_id:
            raise DomainError("You are already the owner")

        # Demote current owner → ADMIN
        await self._membership_repo.update_role(
            org_id, current_owner_id, OrgRole.ADMIN.value
        )
        # Promote new owner → OWNER
        promoted = await self._membership_repo.update_role(
            org_id, new_owner.id, OrgRole.OWNER.value
        )

        # Update org.owner_id
        org = await self._org_repo.get_by_id(org_id)
        if org:
            org.owner_id = new_owner.id
            org.updated_at = datetime.now(timezone.utc)
            await self._org_repo.update(org)

        return promoted  # type: ignore[return-value]

    # ---- Org-scoped Member Detail ------------------------------------

    async def get_member_detail(
        self, org_id: UUID, target_user_id: UUID, actor_id: UUID
    ) -> dict:
        """Get a single member's full profile. Any org member can call this."""
        await self._require_role(
            org_id,
            actor_id,
            {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MEMBER, OrgRole.VIEWER},
        )
        membership = await self._membership_repo.get_membership(org_id, target_user_id)
        if not membership:
            raise DomainError("Member not found in this organization")
        user = await self._user_repo.get_by_id(target_user_id)
        return {
            "id": str(membership.id),
            "user_id": str(membership.user_id),
            "email": user.email if user else "unknown",
            "name": user.name if user else "Unknown",
            "role": (
                membership.role.value
                if isinstance(membership.role, OrgRole)
                else membership.role
            ),
            "joined_at": (
                membership.joined_at.isoformat() if membership.joined_at else None
            ),
            "is_active": membership.is_active,
            "plan": user.plan.value if user else "free",
        }

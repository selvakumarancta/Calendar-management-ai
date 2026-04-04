"""
SQLAlchemy repository implementations for organization entities.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.organization import (
    ConnectionStatus,
    Organization,
    OrgMembership,
    OrgRole,
    ProviderConnection,
    ProviderType,
)
from src.domain.interfaces.organization_repository import (
    OrganizationRepositoryPort,
    OrgMembershipRepositoryPort,
    ProviderConnectionRepositoryPort,
)
from src.infrastructure.persistence.org_models import (
    OrganizationModel,
    OrgMembershipModel,
    ProviderConnectionModel,
)

# ---------------------------------------------------------------------------
# Organization Repository
# ---------------------------------------------------------------------------


class SQLAlchemyOrganizationRepository(OrganizationRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, org_id: UUID) -> Organization | None:
        result = await self._session.execute(
            select(OrganizationModel).where(OrganizationModel.id == org_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self._session.execute(
            select(OrganizationModel).where(OrganizationModel.slug == slug)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_user(self, user_id: UUID) -> list[Organization]:
        result = await self._session.execute(
            select(OrganizationModel)
            .join(
                OrgMembershipModel,
                OrgMembershipModel.org_id == OrganizationModel.id,
            )
            .where(
                OrgMembershipModel.user_id == user_id,
                OrgMembershipModel.is_active == True,  # noqa: E712
            )
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, org: Organization) -> Organization:
        model = self._to_model(org)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, org: Organization) -> Organization:
        result = await self._session.execute(
            select(OrganizationModel).where(OrganizationModel.id == org.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.name = org.name
            model.slug = org.slug
            model.domain = org.domain
            model.logo_url = org.logo_url
            model.timezone = org.timezone
            model.is_active = org.is_active
            model.max_members = org.max_members
            model.updated_at = org.updated_at
            await self._session.flush()
            return self._to_entity(model)
        return org

    async def delete(self, org_id: UUID) -> bool:
        result = await self._session.execute(
            delete(OrganizationModel).where(OrganizationModel.id == org_id)
        )
        return result.rowcount > 0

    @staticmethod
    def _to_entity(model: OrganizationModel) -> Organization:
        return Organization(
            id=model.id,
            name=model.name,
            slug=model.slug,
            owner_id=model.owner_id,
            domain=model.domain,
            logo_url=model.logo_url,
            timezone=model.timezone,
            is_active=model.is_active,
            max_members=model.max_members,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _to_model(org: Organization) -> OrganizationModel:
        return OrganizationModel(
            id=org.id,
            name=org.name,
            slug=org.slug,
            owner_id=org.owner_id,
            domain=org.domain,
            logo_url=org.logo_url,
            timezone=org.timezone,
            is_active=org.is_active,
            max_members=org.max_members,
            created_at=org.created_at,
            updated_at=org.updated_at,
        )


# ---------------------------------------------------------------------------
# Membership Repository
# ---------------------------------------------------------------------------


class SQLAlchemyMembershipRepository(OrgMembershipRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_members(self, org_id: UUID) -> list[OrgMembership]:
        result = await self._session.execute(
            select(OrgMembershipModel).where(
                OrgMembershipModel.org_id == org_id,
                OrgMembershipModel.is_active == True,
            )  # noqa: E712
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_membership(self, org_id: UUID, user_id: UUID) -> OrgMembership | None:
        result = await self._session.execute(
            select(OrgMembershipModel).where(
                OrgMembershipModel.org_id == org_id,
                OrgMembershipModel.user_id == user_id,
                OrgMembershipModel.is_active == True,  # noqa: E712
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def add_member(self, membership: OrgMembership) -> OrgMembership:
        model = OrgMembershipModel(
            id=membership.id,
            org_id=membership.org_id,
            user_id=membership.user_id,
            role=(
                membership.role.value
                if isinstance(membership.role, OrgRole)
                else membership.role
            ),
            invited_by=membership.invited_by,
            invited_at=membership.invited_at,
            joined_at=membership.joined_at,
            is_active=membership.is_active,
        )
        self._session.add(model)
        await self._session.flush()
        return membership

    async def update_role(
        self, org_id: UUID, user_id: UUID, role: str
    ) -> OrgMembership | None:
        result = await self._session.execute(
            select(OrgMembershipModel).where(
                OrgMembershipModel.org_id == org_id,
                OrgMembershipModel.user_id == user_id,
            )
        )
        model = result.scalar_one_or_none()
        if model:
            model.role = role
            await self._session.flush()
            return self._to_entity(model)
        return None

    async def remove_member(self, org_id: UUID, user_id: UUID) -> bool:
        result = await self._session.execute(
            select(OrgMembershipModel).where(
                OrgMembershipModel.org_id == org_id,
                OrgMembershipModel.user_id == user_id,
            )
        )
        model = result.scalar_one_or_none()
        if model:
            model.is_active = False
            await self._session.flush()
            return True
        return False

    async def count_members(self, org_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(OrgMembershipModel)
            .where(
                OrgMembershipModel.org_id == org_id,
                OrgMembershipModel.is_active == True,
            )  # noqa: E712
        )
        return result.scalar_one()

    @staticmethod
    def _to_entity(model: OrgMembershipModel) -> OrgMembership:
        return OrgMembership(
            id=model.id,
            org_id=model.org_id,
            user_id=model.user_id,
            role=OrgRole(model.role),
            invited_by=model.invited_by,
            invited_at=model.invited_at,
            joined_at=model.joined_at,
            is_active=model.is_active,
        )


# ---------------------------------------------------------------------------
# Provider Connection Repository
# ---------------------------------------------------------------------------


class SQLAlchemyProviderConnectionRepository(ProviderConnectionRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, conn_id: UUID) -> ProviderConnection | None:
        result = await self._session.execute(
            select(ProviderConnectionModel).where(ProviderConnectionModel.id == conn_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_org(self, org_id: UUID) -> list[ProviderConnection]:
        result = await self._session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.org_id == org_id
            )
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_by_user(
        self, user_id: UUID, org_id: UUID | None = None
    ) -> list[ProviderConnection]:
        stmt = select(ProviderConnectionModel).where(
            ProviderConnectionModel.user_id == user_id
        )
        if org_id:
            stmt = stmt.where(ProviderConnectionModel.org_id == org_id)
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_active_connection(
        self, user_id: UUID, provider: str, org_id: UUID
    ) -> ProviderConnection | None:
        result = await self._session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id == user_id,
                ProviderConnectionModel.provider == provider,
                ProviderConnectionModel.org_id == org_id,
                ProviderConnectionModel.status == "active",
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def create(self, conn: ProviderConnection) -> ProviderConnection:
        model = self._to_model(conn)
        self._session.add(model)
        await self._session.flush()
        return conn

    async def update(self, conn: ProviderConnection) -> ProviderConnection:
        result = await self._session.execute(
            select(ProviderConnectionModel).where(ProviderConnectionModel.id == conn.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.access_token = conn.access_token
            model.refresh_token = conn.refresh_token
            model.token_expiry = conn.token_expiry
            model.status = (
                conn.status.value
                if isinstance(conn.status, ConnectionStatus)
                else conn.status
            )
            model.scopes = conn.scopes
            model.provider_email = conn.provider_email
            model.calendar_sync_enabled = conn.calendar_sync_enabled
            model.email_sync_enabled = conn.email_sync_enabled
            model.last_sync_at = conn.last_sync_at
            model.updated_at = conn.updated_at
            await self._session.flush()
        return conn

    async def delete(self, conn_id: UUID) -> bool:
        result = await self._session.execute(
            delete(ProviderConnectionModel).where(ProviderConnectionModel.id == conn_id)
        )
        return result.rowcount > 0

    @staticmethod
    def _to_entity(model: ProviderConnectionModel) -> ProviderConnection:
        return ProviderConnection(
            id=model.id,
            org_id=model.org_id,
            user_id=model.user_id,
            provider=ProviderType(model.provider),
            provider_email=model.provider_email,
            status=ConnectionStatus(model.status),
            access_token=model.access_token,
            refresh_token=model.refresh_token,
            token_expiry=model.token_expiry,
            scopes=model.scopes,
            calendar_sync_enabled=model.calendar_sync_enabled,
            email_sync_enabled=model.email_sync_enabled,
            last_sync_at=model.last_sync_at,
            webhook_channel_id=model.webhook_channel_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _to_model(conn: ProviderConnection) -> ProviderConnectionModel:
        return ProviderConnectionModel(
            id=conn.id,
            org_id=conn.org_id,
            user_id=conn.user_id,
            provider=(
                conn.provider.value
                if isinstance(conn.provider, ProviderType)
                else conn.provider
            ),
            provider_email=conn.provider_email,
            status=(
                conn.status.value
                if isinstance(conn.status, ConnectionStatus)
                else conn.status
            ),
            access_token=conn.access_token,
            refresh_token=conn.refresh_token,
            token_expiry=conn.token_expiry,
            scopes=conn.scopes,
            calendar_sync_enabled=conn.calendar_sync_enabled,
            email_sync_enabled=conn.email_sync_enabled,
            last_sync_at=conn.last_sync_at,
            webhook_channel_id=conn.webhook_channel_id,
            created_at=conn.created_at,
            updated_at=conn.updated_at,
        )

"""
Domain port: Organization Repository — persistence interface for orgs.
"""

from __future__ import annotations

import abc
from uuid import UUID

from src.domain.entities.organization import (
    Organization,
    OrgMembership,
    ProviderConnection,
)


class OrganizationRepositoryPort(abc.ABC):
    """Abstract repository for organizations."""

    @abc.abstractmethod
    async def get_by_id(self, org_id: UUID) -> Organization | None: ...

    @abc.abstractmethod
    async def get_by_slug(self, slug: str) -> Organization | None: ...

    @abc.abstractmethod
    async def list_by_user(self, user_id: UUID) -> list[Organization]: ...

    @abc.abstractmethod
    async def create(self, org: Organization) -> Organization: ...

    @abc.abstractmethod
    async def update(self, org: Organization) -> Organization: ...

    @abc.abstractmethod
    async def delete(self, org_id: UUID) -> bool: ...


class OrgMembershipRepositoryPort(abc.ABC):
    """Abstract repository for organization memberships."""

    @abc.abstractmethod
    async def get_members(self, org_id: UUID) -> list[OrgMembership]: ...

    @abc.abstractmethod
    async def get_membership(
        self, org_id: UUID, user_id: UUID
    ) -> OrgMembership | None: ...

    @abc.abstractmethod
    async def add_member(self, membership: OrgMembership) -> OrgMembership: ...

    @abc.abstractmethod
    async def update_role(
        self, org_id: UUID, user_id: UUID, role: str
    ) -> OrgMembership | None: ...

    @abc.abstractmethod
    async def remove_member(self, org_id: UUID, user_id: UUID) -> bool: ...

    @abc.abstractmethod
    async def count_members(self, org_id: UUID) -> int: ...


class ProviderConnectionRepositoryPort(abc.ABC):
    """Abstract repository for provider connections."""

    @abc.abstractmethod
    async def get_by_id(self, conn_id: UUID) -> ProviderConnection | None: ...

    @abc.abstractmethod
    async def list_by_org(self, org_id: UUID) -> list[ProviderConnection]: ...

    @abc.abstractmethod
    async def list_by_user(
        self, user_id: UUID, org_id: UUID | None = None
    ) -> list[ProviderConnection]: ...

    @abc.abstractmethod
    async def get_active_connection(
        self, user_id: UUID, provider: str, org_id: UUID
    ) -> ProviderConnection | None: ...

    @abc.abstractmethod
    async def create(self, conn: ProviderConnection) -> ProviderConnection: ...

    @abc.abstractmethod
    async def update(self, conn: ProviderConnection) -> ProviderConnection: ...

    @abc.abstractmethod
    async def delete(self, conn_id: UUID) -> bool: ...

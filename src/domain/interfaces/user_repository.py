"""Port: User Repository — abstract persistence for User entities."""

from __future__ import annotations

import abc
from uuid import UUID

from src.domain.entities.user import User


class UserRepositoryPort(abc.ABC):
    """Abstract user persistence interface."""

    @abc.abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None: ...

    @abc.abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abc.abstractmethod
    async def create(self, user: User) -> User: ...

    @abc.abstractmethod
    async def update(self, user: User) -> User: ...

    @abc.abstractmethod
    async def delete(self, user_id: UUID) -> bool: ...

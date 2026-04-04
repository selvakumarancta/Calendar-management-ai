"""Port: Conversation Repository — abstract persistence for conversations."""

from __future__ import annotations

import abc
from uuid import UUID

from src.domain.entities.conversation import Conversation


class ConversationRepositoryPort(abc.ABC):
    """Abstract conversation persistence interface."""

    @abc.abstractmethod
    async def get_by_id(self, conversation_id: UUID) -> Conversation | None: ...

    @abc.abstractmethod
    async def get_active_by_user(self, user_id: UUID) -> Conversation | None:
        """Get the most recent active conversation for a user."""
        ...

    @abc.abstractmethod
    async def create(self, conversation: Conversation) -> Conversation: ...

    @abc.abstractmethod
    async def update(self, conversation: Conversation) -> Conversation: ...

    @abc.abstractmethod
    async def delete(self, conversation_id: UUID) -> bool: ...

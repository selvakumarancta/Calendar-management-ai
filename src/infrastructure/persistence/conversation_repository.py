"""
Conversation Repository — SQLAlchemy adapter implementing ConversationRepositoryPort.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.conversation import Conversation, Message, MessageRole
from src.domain.interfaces.conversation_repository import ConversationRepositoryPort
from src.infrastructure.persistence.models import ConversationModel, MessageModel


class SQLAlchemyConversationRepository(ConversationRepositoryPort):
    """Concrete conversation repository using SQLAlchemy async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        result = await self._session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        msg_result = await self._session.execute(
            select(MessageModel)
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at)
        )
        messages = list(msg_result.scalars().all())
        return self._to_entity(model, messages)

    async def get_active_by_user(self, user_id: UUID) -> Conversation | None:
        result = await self._session.execute(
            select(ConversationModel)
            .where(ConversationModel.user_id == user_id)
            .order_by(ConversationModel.updated_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        return await self.get_by_id(model.id)

    async def create(self, conversation: Conversation) -> Conversation:
        model = ConversationModel(
            id=conversation.id,
            user_id=conversation.user_id,
            summary=conversation.summary,
        )
        self._session.add(model)

        for msg in conversation.messages:
            self._session.add(
                MessageModel(
                    conversation_id=conversation.id,
                    role=msg.role.value,
                    content=msg.content,
                    tool_name=msg.tool_name,
                    tool_call_id=msg.tool_call_id,
                    token_count=msg.token_count,
                    created_at=msg.timestamp,
                )
            )

        await self._session.flush()
        return conversation

    async def update(self, conversation: Conversation) -> Conversation:
        result = await self._session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.summary = conversation.summary
            model.updated_at = conversation.updated_at

        # Count existing messages to only add new ones
        existing = await self._session.execute(
            select(MessageModel.id).where(
                MessageModel.conversation_id == conversation.id
            )
        )
        existing_count = len(list(existing.scalars()))

        for msg in conversation.messages[existing_count:]:
            self._session.add(
                MessageModel(
                    conversation_id=conversation.id,
                    role=msg.role.value,
                    content=msg.content,
                    tool_name=msg.tool_name,
                    tool_call_id=msg.tool_call_id,
                    token_count=msg.token_count,
                    created_at=msg.timestamp,
                )
            )

        await self._session.flush()
        return conversation

    async def delete(self, conversation_id: UUID) -> bool:
        await self._session.execute(
            sa_delete(MessageModel).where(
                MessageModel.conversation_id == conversation_id
            )
        )
        result = await self._session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self._session.delete(model)
            await self._session.flush()
            return True
        return False

    @staticmethod
    def _to_entity(
        model: ConversationModel, message_models: list[MessageModel]
    ) -> Conversation:
        messages = [
            Message(
                role=MessageRole(m.role),
                content=m.content,
                tool_name=m.tool_name,
                tool_call_id=m.tool_call_id,
                token_count=m.token_count,
                timestamp=m.created_at,
            )
            for m in message_models
        ]
        return Conversation(
            id=model.id,
            user_id=model.user_id,
            messages=messages,
            summary=model.summary,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

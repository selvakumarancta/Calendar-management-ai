"""
User Repository — SQLAlchemy adapter implementing UserRepositoryPort.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.user import SubscriptionPlan, SystemRole, User
from src.domain.interfaces.user_repository import UserRepositoryPort
from src.infrastructure.persistence.models import UserModel


class SQLAlchemyUserRepository(UserRepositoryPort):
    """Concrete user repository using SQLAlchemy async."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def create(self, user: User) -> User:
        model = self._to_model(user)
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, user: User) -> User:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == user.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.email = user.email
            model.name = user.name
            model.timezone = user.timezone
            model.plan = user.plan.value
            model.system_role = user.system_role.value
            model.is_active = user.is_active
            model.google_access_token = user.google_access_token
            model.google_refresh_token = user.google_refresh_token
            model.google_token_expiry = user.google_token_expiry
            model.microsoft_access_token = user.microsoft_access_token
            model.microsoft_refresh_token = user.microsoft_refresh_token
            model.microsoft_token_expiry = user.microsoft_token_expiry
            model.stripe_customer_id = user.stripe_customer_id
            model.stripe_subscription_id = user.stripe_subscription_id
            model.updated_at = user.updated_at
            await self._session.commit()
            await self._session.refresh(model)
            return self._to_entity(model)
        return user

    async def delete(self, user_id: UUID) -> bool:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self._session.delete(model)
            await self._session.commit()
            return True
        return False

    @staticmethod
    def _to_entity(model: UserModel) -> User:
        return User(
            id=model.id,
            email=model.email,
            name=model.name,
            timezone=model.timezone,
            plan=SubscriptionPlan(model.plan),
            system_role=SystemRole(getattr(model, "system_role", "user")),
            is_active=model.is_active,
            google_access_token=model.google_access_token,
            google_refresh_token=model.google_refresh_token,
            google_token_expiry=model.google_token_expiry,
            microsoft_access_token=getattr(model, "microsoft_access_token", None),
            microsoft_refresh_token=getattr(model, "microsoft_refresh_token", None),
            microsoft_token_expiry=getattr(model, "microsoft_token_expiry", None),
            stripe_customer_id=model.stripe_customer_id,
            stripe_subscription_id=model.stripe_subscription_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _to_model(user: User) -> UserModel:
        return UserModel(
            id=user.id,
            email=user.email,
            name=user.name,
            timezone=user.timezone,
            plan=user.plan.value,
            is_active=user.is_active,
            google_access_token=user.google_access_token,
            google_refresh_token=user.google_refresh_token,
            google_token_expiry=user.google_token_expiry,
            microsoft_access_token=user.microsoft_access_token,
            microsoft_refresh_token=user.microsoft_refresh_token,
            microsoft_token_expiry=user.microsoft_token_expiry,
            stripe_customer_id=user.stripe_customer_id,
            stripe_subscription_id=user.stripe_subscription_id,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

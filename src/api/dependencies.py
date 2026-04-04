"""
FastAPI Dependency Injection — request-scoped dependencies.
Provides: container, DB sessions, current user, assembled services.
"""

from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.container import Container
from src.domain.entities.user import User
from src.infrastructure.persistence.conversation_repository import (
    SQLAlchemyConversationRepository,
)
from src.infrastructure.persistence.org_repository import (
    SQLAlchemyMembershipRepository,
    SQLAlchemyOrganizationRepository,
    SQLAlchemyProviderConnectionRepository,
)
from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

# Optional bearer token — does not auto-raise 403
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def get_container(request: Request) -> Container:
    """Retrieve the DI container stored on app.state during lifespan startup."""
    return request.app.state.container  # type: ignore[no-any-return]


async def get_db_session(
    container: Container = Depends(get_container),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a per-request async DB session with auto commit/rollback."""
    async with container.database().session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Repositories (per-request, session-scoped)
# ---------------------------------------------------------------------------


def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyUserRepository:
    return SQLAlchemyUserRepository(session)


def get_conversation_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyConversationRepository:
    return SQLAlchemyConversationRepository(session)


def get_org_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyOrganizationRepository:
    return SQLAlchemyOrganizationRepository(session)


def get_membership_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyMembershipRepository:
    return SQLAlchemyMembershipRepository(session)


def get_provider_connection_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyProviderConnectionRepository:
    return SQLAlchemyProviderConnectionRepository(session)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Decode JWT bearer token and return the authenticated User entity."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt_service = container.jwt_service()
    try:
        payload = jwt_service.decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user_repo = SQLAlchemyUserRepository(session)
    user = await user_repo.get_by_id(UUID(user_id_str))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> User | None:
    """Return the authenticated user if a token is provided, else None."""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, container, session)
    except HTTPException:
        return None

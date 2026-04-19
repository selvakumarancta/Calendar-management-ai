"""
Unit tests for src/api/dependencies.py.

These tests exercise the repository factory functions and auth helpers
directly, without spinning up a FastAPI app.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.dependencies import (
    get_conversation_repository,
    get_membership_repository,
    get_org_repository,
    get_provider_connection_repository,
    get_user_repository,
)

# ---------------------------------------------------------------------------
# Repository factory functions (lines 62, 68, 74, 80, 86)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_user_repository_returns_instance():
    """get_user_repository returns SQLAlchemyUserRepository (line 62)."""
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    session = MagicMock()
    repo = get_user_repository(session=session)
    assert isinstance(repo, SQLAlchemyUserRepository)


@pytest.mark.unit
def test_get_conversation_repository_returns_instance():
    """get_conversation_repository returns SQLAlchemyConversationRepository (line 68)."""
    from src.infrastructure.persistence.conversation_repository import (
        SQLAlchemyConversationRepository,
    )

    session = MagicMock()
    repo = get_conversation_repository(session=session)
    assert isinstance(repo, SQLAlchemyConversationRepository)


@pytest.mark.unit
def test_get_org_repository_returns_instance():
    """get_org_repository returns SQLAlchemyOrganizationRepository (line 74)."""
    from src.infrastructure.persistence.org_repository import (
        SQLAlchemyOrganizationRepository,
    )

    session = MagicMock()
    repo = get_org_repository(session=session)
    assert isinstance(repo, SQLAlchemyOrganizationRepository)


@pytest.mark.unit
def test_get_membership_repository_returns_instance():
    """get_membership_repository returns SQLAlchemyMembershipRepository (line 80)."""
    from src.infrastructure.persistence.org_repository import (
        SQLAlchemyMembershipRepository,
    )

    session = MagicMock()
    repo = get_membership_repository(session=session)
    assert isinstance(repo, SQLAlchemyMembershipRepository)


@pytest.mark.unit
def test_get_provider_connection_repository_returns_instance():
    """get_provider_connection_repository returns SQLAlchemyProviderConnectionRepository (line 86)."""
    from src.infrastructure.persistence.org_repository import (
        SQLAlchemyProviderConnectionRepository,
    )

    session = MagicMock()
    repo = get_provider_connection_repository(session=session)
    assert isinstance(repo, SQLAlchemyProviderConnectionRepository)


# ---------------------------------------------------------------------------
# get_current_user — error paths (lines 132, 139-145)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_user_raises_when_sub_missing():
    """get_current_user raises 401 when 'sub' is missing from token payload (line 132)."""
    from fastapi.security import HTTPAuthorizationCredentials

    from src.api.dependencies import get_current_user

    creds = MagicMock(spec=HTTPAuthorizationCredentials)
    creds.credentials = "fake.jwt.token"

    # jwt_service.decode_token returns payload without 'sub'
    jwt_svc = MagicMock()
    jwt_svc.decode_token.return_value = {"email": "test@example.com"}  # no sub
    container = MagicMock()
    container.jwt_service.return_value = jwt_svc
    session = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(
            credentials=creds,
            container=container,
            session=session,
        )
    assert exc_info.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_user_raises_when_user_not_found():
    """get_current_user raises 401 when user not found in DB (lines 139-145)."""
    from fastapi.security import HTTPAuthorizationCredentials

    from src.api.dependencies import get_current_user

    user_id = uuid.uuid4()
    creds = MagicMock(spec=HTTPAuthorizationCredentials)
    creds.credentials = "token"

    jwt_svc = MagicMock()
    jwt_svc.decode_token.return_value = {"sub": str(user_id)}
    container = MagicMock()
    container.jwt_service.return_value = jwt_svc
    session = MagicMock()

    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = None  # user not found

    with patch(
        "src.api.dependencies.SQLAlchemyUserRepository",
        return_value=mock_repo,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                credentials=creds,
                container=container,
                session=session,
            )
    assert exc_info.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_optional_user_returns_none_when_no_creds():
    """get_optional_user returns None when no credentials provided (line 154-155)."""
    from src.api.dependencies import get_optional_user

    container = MagicMock()
    session = MagicMock()

    result = await get_optional_user(
        credentials=None,
        container=container,
        session=session,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_optional_user_returns_none_on_http_exception():
    """get_optional_user returns None when get_current_user raises HTTPException (lines 157-159)."""
    from fastapi.security import HTTPAuthorizationCredentials

    from src.api.dependencies import get_optional_user

    creds = MagicMock(spec=HTTPAuthorizationCredentials)
    creds.credentials = "bad-token"
    container = MagicMock()
    session = MagicMock()

    with patch(
        "src.api.dependencies.get_current_user",
        new=AsyncMock(
            side_effect=HTTPException(status_code=401, detail="Unauthorized")
        ),
    ):
        result = await get_optional_user(
            credentials=creds,
            container=container,
            session=session,
        )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_user_returns_active_user():
    """get_current_user returns the authenticated User when token is valid (line 145)."""
    import uuid as _uuid

    from fastapi.security import HTTPAuthorizationCredentials

    from src.api.dependencies import get_current_user

    user_id = _uuid.uuid4()

    fake_user = MagicMock()
    fake_user.id = user_id
    fake_user.is_active = True

    jwt_svc = MagicMock()
    jwt_svc.decode_token.return_value = {"sub": str(user_id)}
    container = MagicMock()
    container.jwt_service.return_value = jwt_svc

    creds = MagicMock(spec=HTTPAuthorizationCredentials)
    creds.credentials = "valid-token"
    session = MagicMock()

    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = fake_user

    with patch(
        "src.api.dependencies.SQLAlchemyUserRepository",
        return_value=mock_repo,
    ):
        result = await get_current_user(
            credentials=creds,
            container=container,
            session=session,
        )
    assert result is fake_user


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_user_returns_active_user():
    """get_current_user returns the authenticated User when token is valid (line 145)."""
    import uuid as _uuid

    from fastapi.security import HTTPAuthorizationCredentials

    from src.api.dependencies import get_current_user

    user_id = _uuid.uuid4()

    fake_user = MagicMock()
    fake_user.id = user_id
    fake_user.is_active = True

    jwt_svc = MagicMock()
    jwt_svc.decode_token.return_value = {"sub": str(user_id)}
    container = MagicMock()
    container.jwt_service.return_value = jwt_svc

    creds = MagicMock(spec=HTTPAuthorizationCredentials)
    creds.credentials = "valid-token"
    session = MagicMock()

    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = fake_user

    with patch(
        "src.api.dependencies.SQLAlchemyUserRepository",
        return_value=mock_repo,
    ):
        result = await get_current_user(
            credentials=creds,
            container=container,
            session=session,
        )
    assert result is fake_user

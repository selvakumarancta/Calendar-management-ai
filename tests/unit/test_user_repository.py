"""
Unit tests for SQLAlchemyUserRepository.

The SQLAlchemy session is replaced with an AsyncMock — no DB needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.user import SubscriptionPlan, User
from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UID = uuid.uuid4()


def _user(**kw) -> User:
    return User(
        id=kw.pop("id", _UID),
        email=kw.pop("email", "alice@example.com"),
        name=kw.pop("name", "Alice"),
        **kw,
    )


def _model_mock(user: User | None = None) -> MagicMock:
    """Build a mock UserModel with all needed attributes."""
    u = user or _user()
    m = MagicMock()
    m.id = u.id
    m.email = u.email
    m.name = u.name
    m.timezone = u.timezone
    m.plan = u.plan.value
    m.is_active = u.is_active
    m.google_access_token = u.google_access_token
    m.google_refresh_token = u.google_refresh_token
    m.google_token_expiry = u.google_token_expiry
    m.microsoft_access_token = u.microsoft_access_token
    m.microsoft_refresh_token = u.microsoft_refresh_token
    m.microsoft_token_expiry = u.microsoft_token_expiry
    m.stripe_customer_id = u.stripe_customer_id
    m.stripe_subscription_id = u.stripe_subscription_id
    m.created_at = u.created_at
    m.updated_at = u.updated_at
    return m


def _make_session(scalar_result=None) -> AsyncMock:
    """Return an AsyncMock session that returns scalar_result from execute."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_result
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_id_returns_user_when_found():
    """get_by_id returns User entity when model exists (lines 24-28)."""
    u = _user()
    session = _make_session(scalar_result=_model_mock(u))
    repo = SQLAlchemyUserRepository(session)
    result = await repo.get_by_id(u.id)
    assert result is not None
    assert result.id == u.id
    assert result.email == u.email


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_not_found():
    """get_by_id returns None when no model matches (line 28)."""
    session = _make_session(scalar_result=None)
    repo = SQLAlchemyUserRepository(session)
    result = await repo.get_by_id(uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# get_by_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_email_returns_user_when_found():
    """get_by_email returns User entity when model exists (lines 31-35)."""
    u = _user()
    session = _make_session(scalar_result=_model_mock(u))
    repo = SQLAlchemyUserRepository(session)
    result = await repo.get_by_email(u.email)
    assert result is not None
    assert result.email == u.email


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_email_returns_none_when_not_found():
    """get_by_email returns None when no model matches (line 35)."""
    session = _make_session(scalar_result=None)
    repo = SQLAlchemyUserRepository(session)
    result = await repo.get_by_email("notfound@example.com")
    assert result is None


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_returns_user_entity():
    """create inserts a model and returns entity (lines 38-42)."""
    u = _user()
    model = _model_mock(u)

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    # refresh updates the model in-place — simulate by returning the model via side_effect
    session.refresh = AsyncMock()

    # After refresh, _to_entity is called on the model passed to add
    # We need to intercept the model added to session
    added_models = []

    def capture_add(m):
        added_models.append(m)

    session.add.side_effect = capture_add

    # Patch _to_model to return our mock
    with (
        __import__("unittest.mock", fromlist=["patch"]).patch.object(
            SQLAlchemyUserRepository, "_to_model", return_value=model
        ),
        __import__("unittest.mock", fromlist=["patch"]).patch.object(
            SQLAlchemyUserRepository, "_to_entity", return_value=u
        ),
    ):
        repo = SQLAlchemyUserRepository(session)
        result = await repo.create(u)

    assert result is u
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_modifies_existing_model():
    """update finds the model and updates all fields (lines 45-66)."""
    u = _user()
    model = _model_mock(u)
    session = _make_session(scalar_result=model)
    # refresh side-effect: update model attributes and return entity
    session.refresh = AsyncMock()

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        SQLAlchemyUserRepository, "_to_entity", return_value=u
    ):
        repo = SQLAlchemyUserRepository(session)
        result = await repo.update(u)

    session.commit.assert_called_once()
    assert model.email == u.email
    assert result is u


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_returns_user_when_model_not_found():
    """update returns the input user unchanged when no model found (line 67)."""
    u = _user()
    session = _make_session(scalar_result=None)
    repo = SQLAlchemyUserRepository(session)
    result = await repo.update(u)
    assert result is u
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_returns_true_when_found():
    """delete removes the model and returns True (lines 70-77)."""
    u = _user()
    model = _model_mock(u)
    session = _make_session(scalar_result=model)
    repo = SQLAlchemyUserRepository(session)
    result = await repo.delete(u.id)
    assert result is True
    session.delete.assert_called_once_with(model)
    session.commit.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_returns_false_when_not_found():
    """delete returns False when no model found (line 78)."""
    session = _make_session(scalar_result=None)
    repo = SQLAlchemyUserRepository(session)
    result = await repo.delete(uuid.uuid4())
    assert result is False

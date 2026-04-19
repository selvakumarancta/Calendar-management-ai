"""
Unit tests for AuthService.
All external dependencies (user_repository, JWT) are mocked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.auth_service import AuthService
from src.domain.exceptions import AuthenticationError


def _make_svc(**kwargs):
    user_repo = kwargs.pop("user_repo", AsyncMock())
    return (
        AuthService(
            user_repository=user_repo,
            jwt_secret="test-secret",
            **kwargs,
        ),
        user_repo,
    )


def _fake_user(active: bool = True) -> MagicMock:
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "user@example.com"
    u.name = "Test User"
    u.is_active = active
    u.update_google_tokens = MagicMock()
    return u


# ---------------------------------------------------------------------------
# authenticate_google_oauth
# ---------------------------------------------------------------------------


class TestAuthenticateGoogleOAuth:
    @pytest.mark.unit
    async def test_creates_new_user_when_not_found(self):
        svc, repo = _make_svc()
        repo.get_by_email.return_value = None
        fake = _fake_user()
        repo.create.return_value = fake

        user, access_tok, refresh_tok = await svc.authenticate_google_oauth(
            email="new@example.com",
            name="New User",
            access_token="tok",
            refresh_token="rtok",
            token_expiry=datetime.now(timezone.utc),
        )

        repo.create.assert_awaited_once()
        assert user is fake
        assert access_tok == ""
        assert refresh_tok == ""

    @pytest.mark.unit
    async def test_updates_existing_user_tokens(self):
        svc, repo = _make_svc()
        existing = _fake_user()
        repo.get_by_email.return_value = existing
        repo.update.return_value = existing

        user, _, _ = await svc.authenticate_google_oauth(
            email="existing@example.com",
            name="Existing",
            access_token="new_tok",
            refresh_token="new_rtok",
            token_expiry=datetime.now(timezone.utc),
        )

        existing.update_google_tokens.assert_called_once()
        repo.update.assert_awaited_once()
        assert user is existing

    @pytest.mark.unit
    async def test_returns_empty_jwt_strings(self):
        svc, repo = _make_svc()
        repo.get_by_email.return_value = None
        repo.create.return_value = _fake_user()

        _, access_tok, refresh_tok = await svc.authenticate_google_oauth(
            email="x@x.com",
            name="X",
            access_token="t",
            refresh_token=None,
            token_expiry=datetime.now(timezone.utc),
        )
        assert access_tok == ""
        assert refresh_tok == ""


# ---------------------------------------------------------------------------
# get_user_from_token — exercises _decode_token (raises NotImplementedError)
# ---------------------------------------------------------------------------


class TestGetUserFromToken:
    @pytest.mark.unit
    async def test_raises_not_implemented_without_subclass(self):
        svc, _ = _make_svc()
        with pytest.raises(NotImplementedError):
            await svc.get_user_from_token("some.jwt.token")


# ---------------------------------------------------------------------------
# _create_access_token / _create_refresh_token raise NotImplementedError
# ---------------------------------------------------------------------------


class TestTokenCreationNotImplemented:
    @pytest.mark.unit
    def test_create_access_token_not_implemented(self):
        svc, _ = _make_svc()
        user = _fake_user()
        with pytest.raises(NotImplementedError):
            svc._create_access_token(user)

    @pytest.mark.unit
    def test_create_refresh_token_not_implemented(self):
        svc, _ = _make_svc()
        user = _fake_user()
        with pytest.raises(NotImplementedError):
            svc._create_refresh_token(user)

    @pytest.mark.unit
    def test_decode_token_not_implemented(self):
        svc, _ = _make_svc()
        with pytest.raises(NotImplementedError):
            svc._decode_token("token")


# ---------------------------------------------------------------------------
# get_user_from_token — patched _decode_token
# ---------------------------------------------------------------------------


class TestGetUserFromTokenPatched:
    @pytest.mark.unit
    async def test_returns_active_user_on_valid_token(self):
        """With a mocked _decode_token, get_user_from_token returns the user."""
        user = _fake_user(active=True)
        svc, repo = _make_svc()
        repo.get_by_id.return_value = user
        payload = {"sub": str(user.id)}
        with patch.object(svc, "_decode_token", return_value=payload):
            result = await svc.get_user_from_token("valid.jwt.token")
        assert result is user

    @pytest.mark.unit
    async def test_raises_when_sub_missing_from_payload(self):
        """Empty payload (no 'sub') raises AuthenticationError."""
        svc, _ = _make_svc()
        with patch.object(svc, "_decode_token", return_value={}):
            with pytest.raises(AuthenticationError, match="Invalid token payload"):
                await svc.get_user_from_token("bad.payload.token")

    @pytest.mark.unit
    async def test_raises_when_user_not_found(self):
        """User not in repo raises AuthenticationError."""
        svc, repo = _make_svc()
        repo.get_by_id.return_value = None
        with patch.object(
            svc, "_decode_token", return_value={"sub": str(uuid.uuid4())}
        ):
            with pytest.raises(AuthenticationError, match="not found"):
                await svc.get_user_from_token("valid.jwt.token")

    @pytest.mark.unit
    async def test_raises_when_user_inactive(self):
        """Inactive user raises AuthenticationError."""
        user = _fake_user(active=False)
        svc, repo = _make_svc()
        repo.get_by_id.return_value = user
        with patch.object(svc, "_decode_token", return_value={"sub": str(user.id)}):
            with pytest.raises(AuthenticationError, match="inactive"):
                await svc.get_user_from_token("valid.jwt.token")


# ---------------------------------------------------------------------------
# refresh_access_token — patched token helpers
# ---------------------------------------------------------------------------


class TestRefreshAccessToken:
    @pytest.mark.unit
    async def test_issues_new_tokens_from_refresh(self):
        """refresh_access_token calls get_user_from_token then creates tokens."""
        user = _fake_user(active=True)
        svc, _ = _make_svc()
        with (
            patch.object(svc, "get_user_from_token", new=AsyncMock(return_value=user)),
            patch.object(svc, "_create_access_token", return_value="new-access"),
            patch.object(svc, "_create_refresh_token", return_value="new-refresh"),
        ):
            access, refresh = await svc.refresh_access_token("old.refresh.token")
        assert access == "new-access"
        assert refresh == "new-refresh"

"""
Unit tests for GoogleOAuthService (infrastructure/auth/google_oauth.py).
Mocks google_auth_oauthlib to avoid real OAuth flows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.domain.exceptions import AuthenticationError
from src.infrastructure.auth.google_oauth import GoogleOAuthService


def _make_service() -> GoogleOAuthService:
    return GoogleOAuthService(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost/callback",
    )


# ---------------------------------------------------------------------------
# get_authorization_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_authorization_url_returns_url():
    """get_authorization_url returns a non-empty URL string."""
    svc = _make_service()
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/auth?foo=bar",
        "state123",
    )

    with patch("src.infrastructure.auth.google_oauth.Flow") as MockFlow:
        MockFlow.from_client_config.return_value = mock_flow
        url = svc.get_authorization_url(state="mystate")

    assert url == "https://accounts.google.com/auth?foo=bar"
    mock_flow.authorization_url.assert_called_once_with(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state="mystate",
    )


@pytest.mark.unit
def test_get_authorization_url_without_state():
    """get_authorization_url works with state=None."""
    svc = _make_service()
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/auth",
        None,
    )

    with patch("src.infrastructure.auth.google_oauth.Flow") as MockFlow:
        MockFlow.from_client_config.return_value = mock_flow
        url = svc.get_authorization_url()

    assert "google.com" in url


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exchange_code_returns_tokens():
    """exchange_code returns access_token, refresh_token, expiry dict."""
    svc = _make_service()

    mock_creds = MagicMock()
    mock_creds.token = "access_token_abc"
    mock_creds.refresh_token = "refresh_token_xyz"
    mock_creds.expiry = datetime(2026, 12, 31, tzinfo=timezone.utc)

    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("src.infrastructure.auth.google_oauth.Flow") as MockFlow:
        MockFlow.from_client_config.return_value = mock_flow
        result = svc.exchange_code("auth_code_123")

    assert result["access_token"] == "access_token_abc"
    assert result["refresh_token"] == "refresh_token_xyz"
    assert result["expiry"].tzinfo is not None


@pytest.mark.unit
def test_exchange_code_with_no_expiry_uses_now():
    """exchange_code falls back to now() when credentials.expiry is None."""
    svc = _make_service()

    mock_creds = MagicMock()
    mock_creds.token = "tok"
    mock_creds.refresh_token = "ref"
    mock_creds.expiry = None

    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    with patch("src.infrastructure.auth.google_oauth.Flow") as MockFlow:
        MockFlow.from_client_config.return_value = mock_flow
        result = svc.exchange_code("code")

    assert result["expiry"] is not None


@pytest.mark.unit
def test_exchange_code_raises_authentication_error_on_exception():
    """exchange_code wraps exceptions in AuthenticationError."""
    svc = _make_service()

    mock_flow = MagicMock()
    mock_flow.fetch_token.side_effect = Exception("network error")

    with patch("src.infrastructure.auth.google_oauth.Flow") as MockFlow:
        MockFlow.from_client_config.return_value = mock_flow
        with pytest.raises(AuthenticationError, match="OAuth code exchange failed"):
            svc.exchange_code("bad_code")

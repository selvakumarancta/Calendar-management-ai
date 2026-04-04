"""
Microsoft / Outlook OAuth Service — handles OAuth2 flow for Microsoft 365.
Supports both personal (outlook.com) and organizational accounts.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode


class MicrosoftOAuthService:
    """Microsoft OAuth2 service for Outlook/Microsoft 365 calendar access."""

    AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    # Scopes for calendar + mail read access
    DEFAULT_SCOPES = [
        "openid",
        "profile",
        "email",
        "offline_access",
        "Calendars.ReadWrite",
        "Mail.Read",
    ]

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        tenant_id: str = "common",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._tenant_id = tenant_id

    def get_authorization_url(self, state: str = "") -> str:
        """Generate the Microsoft OAuth2 authorization URL."""
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self.DEFAULT_SCOPES),
            "state": state,
        }
        base = self.AUTH_URL.format(tenant=self._tenant_id)
        return f"{base}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for tokens."""
        import httpx

        token_url = self.TOKEN_URL.format(tenant=self._tenant_id)
        response = httpx.post(
            token_url,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(self.DEFAULT_SCOPES),
            },
        )
        response.raise_for_status()
        data = response.json()

        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in", 3600),
            "scope": data.get("scope", ""),
        }

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an expired access token."""
        import httpx

        token_url = self.TOKEN_URL.format(tenant=self._tenant_id)
        response = httpx.post(
            token_url,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(self.DEFAULT_SCOPES),
            },
        )
        response.raise_for_status()
        data = response.json()

        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_in": data.get("expires_in", 3600),
        }

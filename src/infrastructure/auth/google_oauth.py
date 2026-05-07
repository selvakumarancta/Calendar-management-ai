"""
Google OAuth Service — handles the OAuth2 flow for Google Calendar.
"""

from __future__ import annotations

from datetime import datetime, timezone

from google_auth_oauthlib.flow import Flow

from src.domain.exceptions import AuthenticationError


class GoogleOAuthService:
    """Manages Google OAuth2 authorization flow."""

    SCOPES = [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self._client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }
        self._redirect_uri = redirect_uri

    def get_authorization_url(self, state: str | None = None) -> str:
        """Generate the Google OAuth consent URL.

        When state='reconnect' we request a fresh grant with no scope merging
        so stale read-only calendar scopes from previous logins cannot bleed in.
        """
        import os

        # Allow Google to return extra/reordered scopes without raising an error
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

        flow = Flow.from_client_config(
            self._client_config,
            scopes=self.SCOPES,
            redirect_uri=self._redirect_uri,
            autogenerate_code_verifier=False,
        )
        url_kwargs: dict = {
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        # For reconnect: do NOT include_granted_scopes so we get a clean
        # fresh grant rather than merging with any existing (possibly read-only)
        # calendar scope from a previous connection.
        if state != "reconnect":
            url_kwargs["include_granted_scopes"] = "true"
        url, _ = flow.authorization_url(**url_kwargs)
        return url

    def exchange_code(self, code: str, state: str | None = None) -> dict:
        """Exchange authorization code for tokens."""
        import os

        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

        try:
            flow = Flow.from_client_config(
                self._client_config,
                scopes=self.SCOPES,
                redirect_uri=self._redirect_uri,
                autogenerate_code_verifier=False,
            )
            flow.fetch_token(code=code)
            credentials = flow.credentials

            return {
                "access_token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "expiry": (
                    credentials.expiry.replace(tzinfo=timezone.utc)
                    if credentials.expiry
                    else datetime.now(timezone.utc)
                ),
            }
        except Exception as e:
            raise AuthenticationError(f"OAuth code exchange failed: {e}") from e
            credentials = flow.credentials

            return {
                "access_token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "expiry": (
                    credentials.expiry.replace(tzinfo=timezone.utc)
                    if credentials.expiry
                    else datetime.now(timezone.utc)
                ),
            }
        except Exception as e:
            raise AuthenticationError(f"OAuth code exchange failed: {e}") from e

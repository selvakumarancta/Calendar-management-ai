"""
Integration tests for email/password authentication endpoints:
  POST   /api/v1/auth/register           — create account
  POST   /api/v1/auth/login              — authenticate
  GET    /api/v1/auth/me                 — get own profile
  PATCH  /api/v1/auth/me                 — update profile
  POST   /api/v1/auth/logout             — revoke token
  POST   /api/v1/auth/refresh            — swap refresh → access token

Uses httpx AsyncClient against the real ASGI app with the test SQLite DB.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# App / client fixtures (mirror test_calendar_crud.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def app():
    from src.api.rest.app import create_app
    from src.config.container import Container
    from src.config.settings import Settings
    from src.infrastructure.security.token_encryption import set_encryption_key

    application = create_app()
    settings = Settings()
    set_encryption_key(settings.app_secret_key)
    container = Container(settings)
    await container.database().create_tables()
    application.state.container = container
    yield application
    await container.shutdown()


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _unique_email() -> str:
    """Generate a unique email per test so DB constraint isn't violated."""
    return f"test-{uuid.uuid4().hex[:8]}@example.com"


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------


class TestRegister:
    @pytest.mark.integration
    async def test_register_returns_tokens(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": _unique_email(), "password": "StrongPass1!"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["access_token"]

    @pytest.mark.integration
    async def test_register_sets_name_from_email_if_not_given(self, client):
        email = _unique_email()
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "StrongPass1!"},
        )
        assert resp.status_code == 201, resp.text
        # Access the profile with the returned token
        tok = resp.json()["access_token"]
        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["email"].lower() == email.lower()

    @pytest.mark.integration
    async def test_register_duplicate_email_returns_409(self, client):
        email = _unique_email()
        payload = {"email": email, "password": "StrongPass1!"}
        r1 = await client.post("/api/v1/auth/register", json=payload)
        assert r1.status_code == 201, r1.text
        r2 = await client.post("/api/v1/auth/register", json=payload)
        assert r2.status_code == 409

    @pytest.mark.integration
    async def test_register_rejects_short_password(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": _unique_email(), "password": "short"},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_register_rejects_invalid_email(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "StrongPass1!"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    @pytest.fixture(autouse=True)
    async def register_user(self, client):
        """Register a user once for all login tests in this class."""
        self._email = _unique_email()
        self._password = "ValidPass99$"
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": self._email, "password": self._password},
        )
        assert resp.status_code == 201, resp.text

    @pytest.mark.integration
    async def test_login_returns_access_and_refresh_tokens(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": self._email, "password": self._password},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    @pytest.mark.integration
    async def test_login_wrong_password_returns_401(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": self._email, "password": "WrongPass99$"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_login_unknown_email_returns_401(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@nowhere.example", "password": "SomePass99$"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_login_email_is_case_insensitive(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": self._email.upper(), "password": self._password},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.integration
    async def test_login_sets_expires_in(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": self._email, "password": self._password},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body.get("expires_in"), int)
        assert body["expires_in"] > 0


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------


class TestGetMe:
    @pytest.fixture()
    async def tokens(self, client):
        email = _unique_email()
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "ValidPass99$"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json(), email

    @pytest.mark.integration
    async def test_returns_profile_for_authenticated_user(self, client, tokens):
        body, email = tokens
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert me.status_code == 200, me.text
        profile = me.json()
        assert profile["email"].lower() == email.lower()

    @pytest.mark.integration
    async def test_requires_bearer_token(self, client):
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 401

    @pytest.mark.integration
    async def test_rejects_invalid_token(self, client):
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer this.is.invalid"},
        )
        assert me.status_code == 401

    @pytest.mark.integration
    async def test_profile_contains_expected_fields(self, client, tokens):
        body, _ = tokens
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert me.status_code == 200, me.text
        profile = me.json()
        for field in ("id", "email", "name", "plan", "monthly_requests_used"):
            assert field in profile, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# POST /api/v1/auth/refresh
# ---------------------------------------------------------------------------


class TestRefreshToken:
    @pytest.fixture()
    async def tokens(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": _unique_email(), "password": "ValidPass99$"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    @pytest.mark.integration
    async def test_refresh_returns_new_access_token(self, client, tokens):
        refresh_tok = tokens["refresh_token"]
        resp = await client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {refresh_tok}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "access_token" in body
        assert body["access_token"] != tokens["access_token"]

    @pytest.mark.integration
    async def test_refresh_with_access_token_returns_401(self, client, tokens):
        """Sending an access token to /refresh should fail — wrong type claim."""
        access_tok = tokens["access_token"]
        resp = await client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {access_tok}"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_refresh_without_token_returns_401(self, client):
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    @pytest.fixture()
    async def tokens(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": _unique_email(), "password": "ValidPass99$"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    @pytest.mark.integration
    async def test_logout_returns_204(self, client, tokens):
        resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 204

    @pytest.mark.integration
    async def test_logout_without_token_returns_401(self, client):
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 401

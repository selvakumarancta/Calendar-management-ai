"""
Integration tests for settings endpoints:
  GET   /api/v1/settings/user-preferences
  PUT   /api/v1/settings/user-preferences
  PATCH /api/v1/settings/user-preferences
  GET   /api/v1/settings/             (admin settings)
  GET   /api/v1/settings/schema       (settings schema)

All tests use the dev-login token — no real OAuth credentials needed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures (mirror test_auth_endpoints.py pattern)
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
async def auth_client(app):
    """AsyncClient pre-authenticated with a dev-login token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/dev-login")
        assert resp.status_code == 200, f"dev-login failed: {resp.text}"
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


# ---------------------------------------------------------------------------
# GET /api/v1/settings/user-preferences
# ---------------------------------------------------------------------------


class TestGetUserPreferences:
    @pytest.mark.integration
    async def test_returns_200_for_authenticated_user(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/user-preferences")
        assert resp.status_code == 200, resp.text

    @pytest.mark.integration
    async def test_response_has_expected_fields(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/user-preferences")
        body = resp.json()
        for field in (
            "autopilot_enabled",
            "email_draft_enabled",
            "onboarding_completed",
            "scheduling_guide_generated",
            "style_guide_generated",
        ):
            assert field in body, f"Missing field: {field}"

    @pytest.mark.integration
    async def test_requires_authentication(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/settings/user-preferences")
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_autopilot_enabled_is_bool(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/user-preferences")
        assert isinstance(resp.json()["autopilot_enabled"], bool)

    @pytest.mark.integration
    async def test_email_draft_enabled_is_bool(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/user-preferences")
        assert isinstance(resp.json()["email_draft_enabled"], bool)


# ---------------------------------------------------------------------------
# PUT /api/v1/settings/user-preferences
# ---------------------------------------------------------------------------


class TestUpdateUserPreferences:
    @pytest.mark.integration
    async def test_enable_autopilot(self, auth_client):
        resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["autopilot_enabled"] is True

    @pytest.mark.integration
    async def test_disable_autopilot(self, auth_client):
        # First enable, then disable
        await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": True},
        )
        resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["autopilot_enabled"] is False

    @pytest.mark.integration
    async def test_disable_email_drafts(self, auth_client):
        resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"email_draft_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["email_draft_enabled"] is False

    @pytest.mark.integration
    async def test_empty_body_is_accepted(self, auth_client):
        """PUT with no fields should succeed (no-op update)."""
        resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={},
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_is_persisted(self, auth_client):
        """Value set via PUT should be visible in subsequent GET."""
        await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": True},
        )
        resp = await auth_client.get("/api/v1/settings/user-preferences")
        assert resp.json()["autopilot_enabled"] is True

    @pytest.mark.integration
    async def test_requires_authentication(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/settings/user-preferences",
                json={"autopilot_enabled": True},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/v1/settings/user-preferences
# ---------------------------------------------------------------------------


class TestPatchUserPreferences:
    @pytest.mark.integration
    async def test_patch_single_field(self, auth_client):
        resp = await auth_client.patch(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["autopilot_enabled"] is True

    @pytest.mark.integration
    async def test_patch_returns_same_shape_as_put(self, auth_client):
        put_resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"email_draft_enabled": False},
        )
        patch_resp = await auth_client.patch(
            "/api/v1/settings/user-preferences",
            json={"email_draft_enabled": False},
        )
        assert put_resp.status_code == patch_resp.status_code == 200
        assert set(put_resp.json().keys()) == set(patch_resp.json().keys())


# ---------------------------------------------------------------------------
# GET /api/v1/settings/schema
# ---------------------------------------------------------------------------


class TestGetSettingsSchema:
    @pytest.mark.integration
    async def test_returns_200_for_authenticated_user(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/schema")
        assert resp.status_code == 200, resp.text

    @pytest.mark.integration
    async def test_returns_list(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/schema")
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    async def test_requires_authentication(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/settings/schema")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/settings/
# ---------------------------------------------------------------------------


class TestGetSettings:
    @pytest.mark.integration
    async def test_returns_200_for_authenticated_user(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/")
        assert resp.status_code == 200, resp.text

    @pytest.mark.integration
    async def test_response_has_schema_and_values(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/")
        body = resp.json()
        assert "schema_" in body or "schema" in body
        assert "values" in body

    @pytest.mark.integration
    async def test_values_dict_contains_string_values(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/")
        body = resp.json()
        values = body.get("values", {})
        # All returned values should be strings (secrets masked, rest plain)
        assert isinstance(values, dict)
        for k, v in values.items():
            assert isinstance(v, str), f"Non-string value for key {k!r}: {v!r}"

    @pytest.mark.integration
    async def test_requires_authentication(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/settings/")
        assert resp.status_code == 401

"""
Integration tests for all new work from session 4:
  - Email/password register + login
  - Pagination headers (X-Total-Count) on members + audit-log
  - Stripe processed-events cleanup cron job
  - Background task reference keeping (_fire helper)
  - Invite-accept audit trail + webhook notify setting
  - GET /orgs/{id}/events list + X-Total-Count
  - Invite token rate-limit (tight per-IP window)
  - DELETE /orgs/{id} cascade
  - OpenAPI response schemas (AuditLogEntrySchema, GDPRExportSchema)
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.rest.app import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def app():
    from src.config.container import Container
    from src.config.settings import Settings
    from src.infrastructure.security.token_encryption import set_encryption_key

    application = create_app()
    settings = Settings()
    set_encryption_key(settings.app_secret_key)
    container = Container(settings)
    db = container.database()
    await db.create_tables()
    application.state.container = container
    yield application
    await container.shutdown()


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def auth_headers(client):
    resp = await client.post("/api/v1/auth/dev-login")
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
async def org_id(client, auth_headers):
    slug = f"s4-test-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/orgs/",
        json={"name": "Session4 Org", "slug": slug},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# 1. Email/password register + login
# ---------------------------------------------------------------------------


class TestEmailPasswordAuth:
    @pytest.mark.integration
    async def test_register_creates_account(self, client):
        email = f"test_{uuid.uuid4().hex[:8]}@example.com"
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "securePass123", "name": "Test User"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    @pytest.mark.integration
    async def test_register_duplicate_email_409(self, client):
        email = f"dup_{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "securePass123"},
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "securePass123"},
        )
        assert resp.status_code == 409

    @pytest.mark.integration
    async def test_login_valid_credentials(self, client):
        email = f"login_{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "hunter2Secure!"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "hunter2Secure!"},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    @pytest.mark.integration
    async def test_login_wrong_password_401(self, client):
        email = f"bad_{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correctPass123"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "wrongPass123!"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_login_nonexistent_user_401(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "somePass123"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_password_too_short_422(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "x@example.com", "password": "short"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. Pagination headers on GET /orgs/{id}/members
# ---------------------------------------------------------------------------


class TestPaginationHeaders:
    @pytest.mark.integration
    async def test_members_list_returns_x_total_count(
        self, client, auth_headers, org_id
    ):
        resp = await client.get(
            f"/api/v1/orgs/{org_id}/members",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "x-total-count" in resp.headers
        assert int(resp.headers["x-total-count"]) >= 1
        assert "x-page" in resp.headers
        assert "x-page-size" in resp.headers

    @pytest.mark.integration
    async def test_members_pagination_page_1(self, client, auth_headers, org_id):
        resp = await client.get(
            f"/api/v1/orgs/{org_id}/members?page=1&page_size=1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert resp.headers["x-page"] == "1"

    @pytest.mark.integration
    async def test_audit_log_has_x_total_count(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/admin/audit-logs",
            headers=auth_headers,
        )
        # Accept 200 or 403 depending on whether user has an owner membership
        assert resp.status_code in (200, 403)
        if resp.status_code == 200:
            assert "x-total-count" in resp.headers


# ---------------------------------------------------------------------------
# 3. Stripe processed-events cleanup cron
# ---------------------------------------------------------------------------


class TestCleanupCronJob:
    @pytest.mark.integration
    async def test_cleanup_job_runs_without_error(self, app):
        """cleanup_stale_records_job should succeed even with empty tables."""
        from src.infrastructure.workers.arq_email_scanner import (
            cleanup_stale_records_job,
        )

        container = app.state.container
        ctx = {"container": container}
        result = await cleanup_stale_records_job(ctx)
        assert "stripe_events_deleted" in result
        assert "invites_deleted" in result

    @pytest.mark.integration
    async def test_cleanup_job_missing_container(self):
        """cleanup_stale_records_job returns skipped when context has no container."""
        from src.infrastructure.workers.arq_email_scanner import (
            cleanup_stale_records_job,
        )

        result = await cleanup_stale_records_job({})
        assert result.get("skipped") is True

    @pytest.mark.integration
    async def test_cleanup_deletes_old_stripe_events(self, app):
        """Stripe events older than 30 days are pruned."""
        from datetime import datetime, timedelta, timezone

        from src.infrastructure.persistence.models import StripeProcessedEventModel

        container = app.state.container
        old_event_id = f"evt_old_{uuid.uuid4().hex[:12]}"

        db = container.database()
        async with db.session_factory() as session:
            old_ts = datetime.now(timezone.utc) - timedelta(days=31)
            row = StripeProcessedEventModel(
                event_id=old_event_id,
                event_type="checkout.session.completed",
                processed_at=old_ts,
            )
            session.add(row)
            await session.commit()

        from src.infrastructure.workers.arq_email_scanner import (
            cleanup_stale_records_job,
        )

        result = await cleanup_stale_records_job({"container": container})
        assert result["stripe_events_deleted"] >= 1


# ---------------------------------------------------------------------------
# 4. Background task reference keeping
# ---------------------------------------------------------------------------


class TestFireHelper:
    @pytest.mark.integration
    async def test_fire_helper_exists(self):
        from src.api.rest.email_routes import _bg_tasks, _fire

        assert callable(_fire)
        assert isinstance(_bg_tasks, set)

    @pytest.mark.integration
    async def test_fire_schedules_and_completes_coro(self):
        import asyncio

        from src.api.rest.email_routes import _bg_tasks, _fire

        done = []

        async def _work():
            await asyncio.sleep(0)
            done.append(True)

        _fire(_work())
        # Give the event loop a tick to run it
        await asyncio.sleep(0.05)
        assert done


# ---------------------------------------------------------------------------
# 5. Invite-accept webhook setting
# ---------------------------------------------------------------------------


class TestInviteAcceptWebhookSetting:
    @pytest.mark.integration
    async def test_webhook_url_setting_default_empty(self):
        from src.config.settings import Settings

        s = Settings()
        assert s.invite_accept_webhook_url == ""

    @pytest.mark.integration
    async def test_webhook_url_setting_accepts_value(self, monkeypatch):
        import os

        monkeypatch.setenv(
            "INVITE_ACCEPT_WEBHOOK_URL", "https://hooks.example.com/invite"
        )
        from importlib import reload

        import src.config.settings as _mod

        reload(_mod)
        s = _mod.Settings()
        assert s.invite_accept_webhook_url == "https://hooks.example.com/invite"
        # Restore
        reload(_mod)


# ---------------------------------------------------------------------------
# 6. GET /orgs/{id}/events
# ---------------------------------------------------------------------------


class TestOrgEventsHistory:
    @pytest.mark.integration
    async def test_list_events_empty(self, client, auth_headers, org_id):
        resp = await client.get(
            f"/api/v1/orgs/{org_id}/events",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert "x-total-count" in resp.headers

    @pytest.mark.integration
    async def test_list_events_requires_auth(self, client, org_id):
        resp = await client.get(f"/api/v1/orgs/{org_id}/events")
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_list_events_pagination_headers(self, client, auth_headers, org_id):
        resp = await client.get(
            f"/api/v1/orgs/{org_id}/events?page=1&page_size=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-page") == "1"
        assert resp.headers.get("x-page-size") == "10"


# ---------------------------------------------------------------------------
# 7. Invite token rate-limit
# ---------------------------------------------------------------------------


class TestInviteTokenRateLimit:
    @pytest.mark.integration
    async def test_rate_limiter_has_tight_invite_limit(self):
        from src.api.middleware.rate_limiter import RateLimiterMiddleware

        assert RateLimiterMiddleware._INVITE_LIMIT == 5
        assert RateLimiterMiddleware._INVITE_WINDOW == 60

    @pytest.mark.integration
    async def test_invite_endpoint_accepts_valid_first_request(self, client):
        """A bogus (nonexistent) token gets 404, not 429, on first attempt."""
        resp = await client.get(
            "/api/v1/auth/accept-invite?token=nonexistent-token-abc"
        )
        # Rate limiter allows first request; auth rejects with 404
        assert resp.status_code == 404

    @pytest.mark.integration
    async def test_rate_limited_after_many_attempts(self, client):
        """After 5 rapid invite attempts from one IP the 6th gets 429."""
        import os

        if os.environ.get("TESTING") == "1":
            pytest.skip("Rate limiter disabled in TESTING mode")
        for _ in range(5):
            await client.get("/api/v1/auth/accept-invite?token=bad-token-x")
        resp = await client.get("/api/v1/auth/accept-invite?token=bad-token-x")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# 8. DELETE /orgs/{id}
# ---------------------------------------------------------------------------


class TestDeleteOrganization:
    @pytest.mark.integration
    async def test_delete_org_by_owner(self, client, auth_headers):
        slug = f"del-{uuid.uuid4().hex[:8]}"
        create_resp = await client.post(
            "/api/v1/orgs/",
            json={"name": "To Delete", "slug": slug},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        org_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/orgs/{org_id}",
            headers=auth_headers,
        )
        assert del_resp.status_code == 204

        # Confirm 404 on subsequent GET
        get_resp = await client.get(f"/api/v1/orgs/{org_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    @pytest.mark.integration
    async def test_delete_nonexistent_org_404(self, client, auth_headers):
        resp = await client.delete(
            f"/api/v1/orgs/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code in (403, 404)

    @pytest.mark.integration
    async def test_delete_requires_auth(self, client):
        resp = await client.delete(f"/api/v1/orgs/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 9. OpenAPI schemas (AuditLogEntrySchema, GDPRExportSchema registered)
# ---------------------------------------------------------------------------


class TestOpenAPISchemas:
    @pytest.mark.integration
    async def test_openapi_schema_has_audit_log_schema(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        components = schema.get("components", {}).get("schemas", {})
        # AuditLogEntrySchema should appear since it's used as response_model
        assert "AuditLogEntrySchema" in components

    @pytest.mark.integration
    async def test_openapi_schema_has_gdpr_export_schema(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        components = schema.get("components", {}).get("schemas", {})
        assert "GDPRExportSchema" in components

    @pytest.mark.integration
    async def test_openapi_schema_has_password_auth_endpoints(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/v1/auth/register" in paths
        assert "/api/v1/auth/login" in paths

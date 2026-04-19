"""
Integration tests for session-7 hardening additions:
  - POST /api/v1/auth/logout
  - POST /api/v1/auth/change-password
  - POST /api/v1/auth/forgot-password
  - POST /api/v1/auth/reset-password
  - PATCH /api/v1/auth/me
  - GET  /api/v1/billing/usage
  - Token blocklist (revoked token rejected on next request)
  - Invite-accept idempotency guard (race condition)
  - Audit log IP population (client_ip_var)
"""

from __future__ import annotations

import base64
import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures (identical pattern to test_new_endpoints.py)
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
    db = container.database()
    await db.create_tables()

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


@pytest.fixture()
async def email_password_client(app):
    """
    AsyncClient pre-authenticated via email/password registration + login.
    registers a fresh user so we have a hashed_password on the account.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = f"pwuser_{uuid.uuid4().hex[:8]}@example.com"
        password = "Sup3rSafe!"

        reg = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "name": "PW User"},
        )
        assert reg.status_code in (200, 201), f"register failed: {reg.text}"

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, f"login failed: {login.text}"
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        client.extra = {"email": email, "password": password}  # type: ignore[attr-defined]
        yield client


# ---------------------------------------------------------------------------
# Logout & Token Blocklist
# ---------------------------------------------------------------------------


class TestLogout:
    @pytest.mark.integration
    async def test_logout_returns_204(self, auth_client):
        resp = await auth_client.post("/api/v1/auth/logout")
        assert resp.status_code == 204

    @pytest.mark.integration
    async def test_logout_without_token_returns_401(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/auth/logout")
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_revoked_token_rejected(self, auth_client):
        """After logout the same token must return 401 on any protected endpoint."""
        # Logout first
        logout = await auth_client.post("/api/v1/auth/logout")
        assert logout.status_code == 204

        # Re-use the same client (same Authorization header) — should now be 401
        profile = await auth_client.get("/api/v1/auth/me")
        assert (
            profile.status_code == 401
        ), f"Expected 401 after logout, got {profile.status_code}"


# ---------------------------------------------------------------------------
# Change Password
# ---------------------------------------------------------------------------


class TestChangePassword:
    @pytest.mark.integration
    async def test_change_password_success(self, email_password_client, app):
        client = email_password_client
        old_pw = client.extra["password"]  # type: ignore[attr-defined]
        new_pw = "N3wPa$$word!"

        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": old_pw, "new_password": new_pw},
        )
        assert resp.status_code == 204, f"change-password failed: {resp.text}"

        # Can now login with new password
        login = await AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ).post(
            "/api/v1/auth/login",
            json={"email": client.extra["email"], "password": new_pw},  # type: ignore[attr-defined]
        )
        assert login.status_code == 200, "Login with new password failed"

    @pytest.mark.integration
    async def test_change_password_wrong_old_password(self, email_password_client):
        resp = await email_password_client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongPass!", "new_password": "ShouldFail123"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_change_password_short_new_password(self, email_password_client):
        old_pw = email_password_client.extra["password"]  # type: ignore[attr-defined]
        resp = await email_password_client.post(
            "/api/v1/auth/change-password",
            json={"old_password": old_pw, "new_password": "short"},
        )
        assert resp.status_code == 422  # pydantic validation error

    @pytest.mark.integration
    async def test_change_password_oauth_account_returns_400(self, auth_client):
        """dev-login account has no hashed_password → 400."""
        resp = await auth_client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "anything", "new_password": "Newpass123"},
        )
        assert resp.status_code == 400

    @pytest.mark.integration
    async def test_change_password_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/auth/change-password",
                json={"old_password": "x", "new_password": "Newpass123"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Forgot / Reset Password
# ---------------------------------------------------------------------------


class TestForgotResetPassword:
    @pytest.mark.integration
    async def test_forgot_password_unknown_email_returns_202(self, app):
        """Always returns 202 to prevent user enumeration."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/auth/forgot-password",
                json={"email": "nonexistent@example.com"},
            )
        assert resp.status_code == 202

    @pytest.mark.integration
    async def test_forgot_password_registered_email_returns_202(
        self, email_password_client
    ):
        resp = await email_password_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": email_password_client.extra["email"]},  # type: ignore[attr-defined]
        )
        assert resp.status_code == 202

    @pytest.mark.integration
    async def test_reset_password_invalid_token_returns_410(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/auth/reset-password",
                json={
                    "token": "completely_fake_token_xyz",
                    "new_password": "NewPass123!",
                },
            )
        assert resp.status_code == 410

    @pytest.mark.integration
    async def test_full_forgot_reset_flow(self, email_password_client, app):
        """End-to-end: request reset → grab token from DB → reset → login."""
        import secrets
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from src.infrastructure.persistence.models import (
            PasswordResetTokenModel,
            UserModel,
        )

        client = email_password_client
        email = client.extra["email"]  # type: ignore[attr-defined]

        # Trigger forgot-password (creates DB row)
        resp = await client.post("/api/v1/auth/forgot-password", json={"email": email})
        assert resp.status_code == 202

        # Read the token directly from the DB (simulating email link click in tests)
        container = app.state.container
        db = container.database()
        async with db.session_factory() as session:
            user_r = await session.execute(
                select(UserModel).where(UserModel.email == email)
            )
            user_row = user_r.scalar_one()
            tok_r = await session.execute(
                select(PasswordResetTokenModel).where(
                    PasswordResetTokenModel.user_id == user_row.id,
                    PasswordResetTokenModel.used == False,  # noqa: E712
                )
            )
            token_row = tok_r.scalar_one()
            raw_token = token_row.token

        # Reset password
        new_password = "Freshn3wPass!"
        reset_resp = await AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ).post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": new_password},
        )
        assert (
            reset_resp.status_code == 204
        ), f"reset-password failed: {reset_resp.text}"

        # Login with new password works
        login_resp = await AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ).post(
            "/api/v1/auth/login",
            json={"email": email, "password": new_password},
        )
        assert (
            login_resp.status_code == 200
        ), f"login with new password failed: {login_resp.text}"

        # Using the reset token a second time returns 410
        reuse_resp = await AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ).post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "AnotherPass!"},
        )
        assert reuse_resp.status_code == 410, "Expected 410 for already-used token"


# ---------------------------------------------------------------------------
# PATCH /auth/me
# ---------------------------------------------------------------------------


class TestUpdateProfile:
    @pytest.mark.integration
    async def test_update_name(self, auth_client):
        resp = await auth_client.patch("/api/v1/auth/me?name=Alice+Updated")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Alice Updated"

    @pytest.mark.integration
    async def test_update_timezone(self, auth_client):
        resp = await auth_client.patch("/api/v1/auth/me?timezone=America%2FNew_York")
        assert resp.status_code == 200
        assert resp.json()["timezone"] == "America/New_York"

    @pytest.mark.integration
    async def test_update_name_and_timezone(self, auth_client):
        resp = await auth_client.patch(
            "/api/v1/auth/me?name=Bob&timezone=Europe%2FLondon"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Bob"
        assert data["timezone"] == "Europe/London"

    @pytest.mark.integration
    async def test_update_profile_no_fields_returns_400(self, auth_client):
        resp = await auth_client.patch("/api/v1/auth/me")
        assert resp.status_code == 400

    @pytest.mark.integration
    async def test_update_profile_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.patch("/api/v1/auth/me?name=Hacker")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /billing/usage
# ---------------------------------------------------------------------------


class TestBillingUsage:
    @pytest.mark.integration
    async def test_usage_returns_200(self, auth_client):
        resp = await auth_client.get("/api/v1/billing/usage")
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_usage_response_schema(self, auth_client):
        resp = await auth_client.get("/api/v1/billing/usage")
        data = resp.json()
        assert "period" in data
        assert "monthly_request_count" in data
        assert "monthly_request_limit" in data
        assert "monthly_token_usage" in data
        assert "estimated_cost_usd" in data
        assert "plan" in data

    @pytest.mark.integration
    async def test_usage_request_limit_positive(self, auth_client):
        resp = await auth_client.get("/api/v1/billing/usage")
        assert resp.json()["monthly_request_limit"] > 0

    @pytest.mark.integration
    async def test_usage_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/billing/usage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Invite-accept race condition (idempotency)
# ---------------------------------------------------------------------------


class TestInviteAcceptRaceCondition:
    @pytest.mark.integration
    async def test_accept_invite_invalid_token_404(self, auth_client):
        resp = await auth_client.get(
            "/api/v1/auth/accept-invite?token=totally_fake_token"
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    async def test_accept_invite_expired_token_410(self, app):
        """Insert an expired invite row and verify 410 is returned."""
        import uuid
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import insert

        from src.infrastructure.persistence.models import (
            OrgPendingInviteModel,
        )
        from src.infrastructure.persistence.org_models import OrganizationModel

        container = app.state.container
        db = container.database()

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        async with db.session_factory() as session:
            await session.execute(
                insert(OrganizationModel).values(
                    id=org_id,
                    name="Race Test Org",
                    slug=f"race-test-{org_id.hex[:8]}",
                    owner_id=user_id,
                )
            )
            expired_token = f"expired_{uuid.uuid4().hex}"
            await session.execute(
                insert(OrgPendingInviteModel).values(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    email="racetest@example.com",
                    user_id=user_id,
                    invited_by=user_id,
                    token=expired_token,
                    role="member",
                    expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
                    accepted=False,
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/auth/accept-invite?token={expired_token}")
        assert resp.status_code == 410

    @pytest.mark.integration
    async def test_accept_invite_already_accepted_410(self, app):
        """A pre-accepted invite must return 410 immediately."""
        import uuid
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import insert

        from src.infrastructure.persistence.models import (
            OrgPendingInviteModel,
        )
        from src.infrastructure.persistence.org_models import OrganizationModel

        container = app.state.container
        db = container.database()

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        async with db.session_factory() as session:
            await session.execute(
                insert(OrganizationModel).values(
                    id=org_id,
                    name="Accepted Org",
                    slug=f"accepted-org-{org_id.hex[:8]}",
                    owner_id=user_id,
                )
            )
            token = f"accepted_{uuid.uuid4().hex}"
            await session.execute(
                insert(OrgPendingInviteModel).values(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    email="already@example.com",
                    user_id=user_id,
                    invited_by=user_id,
                    token=token,
                    role="member",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    accepted=True,  # already accepted
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"/api/v1/auth/accept-invite?token={token}")
        assert resp.status_code == 410


# ---------------------------------------------------------------------------
# Audit log IP — smoke test
# ---------------------------------------------------------------------------


class TestAuditLogIP:
    @pytest.mark.integration
    async def test_audit_entries_present_after_action(self, app):
        """Verify audit records are written (IP may be empty in testclient)."""
        from sqlalchemy import select

        from src.infrastructure.persistence.models import AuditLogModel

        container = app.state.container
        db = container.database()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            login = await c.post("/api/v1/auth/dev-login")
            token = login.json()["access_token"]
            c.headers["Authorization"] = f"Bearer {token}"
            logout = await c.post("/api/v1/auth/logout")
            assert logout.status_code == 204

        async with db.session_factory() as session:
            result = await session.execute(
                select(AuditLogModel).where(AuditLogModel.action == "auth.logout")
            )
            rows = result.scalars().all()
        assert len(rows) > 0, "Expected at least one auth.logout audit row"


# ---------------------------------------------------------------------------
# Token blocklist  — unit level helpers
# ---------------------------------------------------------------------------


class TestTokenBlocklist:
    @pytest.mark.unit
    async def test_revoke_and_is_revoked(self):
        from datetime import datetime, timedelta, timezone

        from src.infrastructure.security.token_blocklist import TokenBlocklist

        bl = TokenBlocklist()
        jti = str(uuid.uuid4())
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)

        assert not await bl.is_revoked(jti)
        await bl.revoke(jti, expires)
        assert await bl.is_revoked(jti)

    @pytest.mark.unit
    async def test_expired_blocklist_entry_not_revoked(self):
        """An entry whose expires_at is in the past should be treated as cleared."""
        from datetime import datetime, timedelta, timezone

        from src.infrastructure.security.token_blocklist import TokenBlocklist

        bl = TokenBlocklist()
        jti = str(uuid.uuid4())
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)

        await bl.revoke(jti, expired)
        # In-memory path: expired entries should not be considered revoked
        assert not await bl.is_revoked(jti)

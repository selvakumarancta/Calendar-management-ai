"""
Integration tests for new API endpoints added in the recent feature phase:
  - POST /api/v1/email/scan
  - GET  /api/v1/email/drafts
  - GET  /api/v1/email/analytics/summary
  - POST /api/v1/email/hook/message
  - POST /api/v1/email/onboarding/start
  - GET  /api/v1/email/onboarding/status
  - GET  /api/v1/email/guides
  - PUT  /api/v1/email/guides/preferences
  - PUT  /api/v1/email/guides/style
  - POST /api/v1/email/scheduling-links/availability
  - POST /api/v1/email/scheduling-links/suggested
  - POST /api/v1/email/booking-page/slots
  - GET  /api/v1/settings/user-preferences
  - PUT  /api/v1/settings/user-preferences

All tests use the dev-login token so no real OAuth credentials are needed.
Adapters that require real tokens gracefully return empty/zero results for
the dev user — we assert HTTP status rather than data values.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app():
    from src.api.rest.app import create_app
    from src.config.container import Container
    from src.config.settings import Settings

    application = create_app()
    settings = Settings()
    container = Container(settings)
    application.state.container = container
    return application


@pytest.fixture()
async def auth_client(app):
    """AsyncClient pre-authenticated with a dev token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/dev-login")
        assert resp.status_code == 200, f"dev-login failed: {resp.text}"
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


# ---------------------------------------------------------------------------
# Auth guard tests  (no token → 401)
# ---------------------------------------------------------------------------


class TestAuthGuards:
    @pytest.mark.integration
    @pytest.mark.parametrize("method,path,body", [
        ("POST", "/api/v1/email/scan", {"provider": "google", "since_hours": 1}),
        ("GET",  "/api/v1/email/drafts", None),
        ("GET",  "/api/v1/email/analytics/summary", None),
        ("POST", "/api/v1/email/hook/message", {"message": "hi", "sender": "x"}),
        ("POST", "/api/v1/email/onboarding/start", {}),
        ("GET",  "/api/v1/email/onboarding/status", None),
        ("GET",  "/api/v1/email/guides", None),
        ("GET",  "/api/v1/settings/user-preferences", None),
        ("PUT",  "/api/v1/settings/user-preferences", {"autopilot_enabled": False}),
    ])
    async def test_endpoint_requires_auth(self, app, method, path, body):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            fn = getattr(client, method.lower())
            resp = await fn(path, json=body) if body is not None else await fn(path)
            assert resp.status_code == 401, (
                f"{method} {path} should require auth, got {resp.status_code}"
            )


# ---------------------------------------------------------------------------
# Email scan
# ---------------------------------------------------------------------------


class TestEmailScan:
    @pytest.mark.integration
    async def test_scan_google_no_account_returns_200(self, auth_client):
        """Dev user has no Google account — scan returns 200 with 0 emails."""
        resp = await auth_client.post(
            "/api/v1/email/scan",
            json={"provider": "google", "since_hours": 24, "max_emails": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "emails_scanned" in data
        assert "suggestions_created" in data
        assert isinstance(data["errors"], list)

    @pytest.mark.integration
    async def test_scan_microsoft_no_account_returns_200(self, auth_client):
        """Dev user has no Microsoft account — scan returns 200 with 0 emails."""
        resp = await auth_client.post(
            "/api/v1/email/scan",
            json={"provider": "microsoft", "since_hours": 24, "max_emails": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "emails_scanned" in data

    @pytest.mark.integration
    async def test_scan_invalid_provider_returns_error(self, auth_client):
        """Unknown provider value is rejected (400 or 422)."""
        resp = await auth_client.post(
            "/api/v1/email/scan",
            json={"provider": "yahoo", "since_hours": 24},
        )
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


class TestDrafts:
    @pytest.mark.integration
    async def test_list_drafts_returns_list(self, auth_client):
        resp = await auth_client.get("/api/v1/email/drafts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    async def test_get_nonexistent_draft_returns_404(self, auth_client):
        resp = await auth_client.get("/api/v1/email/drafts/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    @pytest.mark.integration
    async def test_send_nonexistent_draft_returns_404(self, auth_client):
        resp = await auth_client.post("/api/v1/email/drafts/00000000-0000-0000-0000-000000000000/send")
        assert resp.status_code == 404

    @pytest.mark.integration
    async def test_delete_nonexistent_draft_returns_404(self, auth_client):
        resp = await auth_client.delete("/api/v1/email/drafts/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    @pytest.mark.integration
    async def test_analytics_summary_returns_200(self, auth_client):
        resp = await auth_client.get("/api/v1/email/analytics/summary")
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_analytics_summary_days_param(self, auth_client):
        resp = await auth_client.get("/api/v1/email/analytics/summary?days=7")
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_analytics_events_returns_200(self, auth_client):
        resp = await auth_client.get("/api/v1/email/analytics/events")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Message Hook
# ---------------------------------------------------------------------------


class TestMessageHook:
    @pytest.mark.integration
    async def test_hook_missing_message_returns_422(self, auth_client):
        resp = await auth_client.post(
            "/api/v1/email/hook/message",
            json={"sender": "alice"},  # missing required 'message' field
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_hook_valid_payload_returns_200(self, auth_client):
        """Well-formed request returns 200 — no LLM needed (graceful fallback)."""
        resp = await auth_client.post(
            "/api/v1/email/hook/message",
            json={
                "message": "Let's catch up Monday at 10am",
                "sender": "bob@example.com",
                "source": "slack",
                "auto_create": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response must include an extracted/detected field
        assert "extracted" in data or "detected" in data or "has_commitment" in data


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


class TestOnboarding:
    @pytest.mark.integration
    async def test_start_onboarding_returns_200(self, auth_client):
        resp = await auth_client.post("/api/v1/email/onboarding/start")
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data or "status" in data

    @pytest.mark.integration
    async def test_get_onboarding_status_returns_200(self, auth_client):
        resp = await auth_client.get("/api/v1/email/onboarding/status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# User Guides
# ---------------------------------------------------------------------------


class TestUserGuides:
    @pytest.mark.integration
    async def test_get_guides_returns_200(self, auth_client):
        resp = await auth_client.get("/api/v1/email/guides")
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_scheduling_prefs_returns_200(self, auth_client):
        """PUT /guides/preferences takes a 'content' field (plain text bullet points)."""
        resp = await auth_client.put(
            "/api/v1/email/guides/preferences",
            json={"content": "· Prefers mornings\n· 30-min default"},
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_style_guide_returns_200(self, auth_client):
        """PUT /guides/style takes a 'content' field (plain text bullet points)."""
        resp = await auth_client.put(
            "/api/v1/email/guides/style",
            json={"content": "· Opens with Hi [name],\n· Signs off Best"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scheduling Links
# ---------------------------------------------------------------------------


class TestSchedulingLinks:
    @pytest.mark.integration
    async def test_create_availability_link_returns_200(self, auth_client):
        resp = await auth_client.post(
            "/api/v1/email/scheduling-links/availability",
            json={
                "attendee_email": "bob@example.com",
                "duration_minutes": 30,
                "days_ahead": 7,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data

    @pytest.mark.integration
    async def test_create_suggested_link_returns_200(self, auth_client):
        resp = await auth_client.post(
            "/api/v1/email/scheduling-links/suggested",
            json={
                "attendee_email": "carol@example.com",
                "duration_minutes": 30,
                "suggested_windows": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data

    @pytest.mark.integration
    async def test_get_nonexistent_link_returns_410(self, auth_client):
        resp = await auth_client.get("/api/v1/email/scheduling-links/nonexistent-id")
        assert resp.status_code == 410


# ---------------------------------------------------------------------------
# Booking Page
# ---------------------------------------------------------------------------


class TestBookingPage:
    @pytest.mark.integration
    async def test_booking_slots_missing_url_returns_422(self, auth_client):
        resp = await auth_client.post(
            "/api/v1/email/booking-page/slots",
            json={"duration_minutes": 30},  # missing required 'url'
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_booking_slots_valid_request_returns_200(self, auth_client):
        """Valid URL returns 200 — adapter returns [] for unconfigured/mock URLs."""
        resp = await auth_client.post(
            "/api/v1/email/booking-page/slots",
            json={
                "url": "https://calendly.com/test/30min",
                "duration_minutes": 30,
                "days_ahead": 7,
            },
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# User Preferences
# ---------------------------------------------------------------------------


class TestUserPreferences:
    @pytest.mark.integration
    async def test_get_preferences_returns_200(self, auth_client):
        resp = await auth_client.get("/api/v1/settings/user-preferences")
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_autopilot_off_returns_200(self, auth_client):
        resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": False},
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_autopilot_on_returns_200(self, auth_client):
        resp = await auth_client.put(
            "/api/v1/settings/user-preferences",
            json={"autopilot_enabled": True},
        )
        assert resp.status_code == 200

"""
Integration tests for billing API endpoints:
  GET  /api/v1/billing/plan
  POST /api/v1/billing/checkout
  POST /api/v1/billing/portal
  POST /api/v1/billing/webhook

Tests validate HTTP status codes, response shapes, and auth guards.
No real Stripe credentials are used; Stripe calls that would hit the network
are expected to 502 when STRIPE_SECRET_KEY is unset / a test value.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_new_endpoints.py)
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
    """AsyncClient pre-authenticated via dev-login."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/dev-login")
        assert resp.status_code == 200, f"dev-login failed: {resp.text}"
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


@pytest.fixture()
async def anon_client(app):
    """AsyncClient without any auth token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Auth guard tests — all billing endpoints must return 401 without a token
# ---------------------------------------------------------------------------


class TestBillingAuthGuards:
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("GET", "/api/v1/billing/plan", None),
            (
                "POST",
                "/api/v1/billing/checkout",
                {
                    "plan": "pro",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            ),
            (
                "POST",
                "/api/v1/billing/portal",
                {"return_url": "https://example.com"},
            ),
        ],
    )
    async def test_unauthenticated_returns_401(self, anon_client, method, path, body):
        if method == "GET":
            resp = await anon_client.get(path)
        else:
            resp = await anon_client.post(path, json=body)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/billing/plan
# ---------------------------------------------------------------------------


class TestGetPlan:
    @pytest.mark.integration
    async def test_returns_200_with_plan_shape(self, auth_client):
        resp = await auth_client.get("/api/v1/billing/plan")
        assert resp.status_code == 200
        data = resp.json()
        assert "plan" in data
        assert "monthly_request_limit" in data
        assert "monthly_requests_used" in data
        # Stripe fields may be None for a fresh dev user
        assert "stripe_customer_id" in data
        assert "stripe_subscription_id" in data
        assert "subscription_status" in data

    @pytest.mark.integration
    async def test_default_plan_is_free(self, auth_client):
        resp = await auth_client.get("/api/v1/billing/plan")
        assert resp.status_code == 200
        assert resp.json()["plan"] == "free"


# ---------------------------------------------------------------------------
# POST /api/v1/billing/checkout
# ---------------------------------------------------------------------------


class TestCreateCheckout:
    @pytest.mark.integration
    async def test_free_plan_returns_400(self, auth_client):
        resp = await auth_client.post(
            "/api/v1/billing/checkout",
            json={
                "plan": "free",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
        assert resp.status_code == 400
        assert "Cannot create checkout for free plan" in resp.json()["detail"]

    @pytest.mark.integration
    async def test_invalid_plan_returns_400(self, auth_client):
        resp = await auth_client.post(
            "/api/v1/billing/checkout",
            json={
                "plan": "diamondultra",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
        assert resp.status_code == 400
        assert "Invalid plan" in resp.json()["detail"]

    @pytest.mark.integration
    async def test_pro_without_stripe_key_returns_502_or_400(self, auth_client):
        """Without a real STRIPE_SECRET_KEY configured the call should either
        fail fast (400/422) or surface a gateway error (502). 200 would mean
        we accidentally hit live Stripe in CI."""
        resp = await auth_client.post(
            "/api/v1/billing/checkout",
            json={
                "plan": "pro",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
        assert resp.status_code in (400, 422, 500, 502)


# ---------------------------------------------------------------------------
# POST /api/v1/billing/portal
# ---------------------------------------------------------------------------


class TestCreatePortal:
    @pytest.mark.integration
    async def test_no_stripe_customer_returns_400(self, auth_client, app):
        """Dev user has no stripe_customer_id → should return 400."""
        # Explicitly clear stripe_customer_id in case prior tests set it
        db = app.state.container.database()
        async with db.session_factory() as session:
            from src.infrastructure.persistence.user_repository import (
                SQLAlchemyUserRepository,
            )

            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_customer_id = None
                await repo.update(user)
                await session.commit()

        resp = await auth_client.post(
            "/api/v1/billing/portal",
            json={"return_url": "https://example.com/billing"},
        )
        assert resp.status_code == 400
        assert "No Stripe customer" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/v1/billing/webhook — auth-free Stripe webhook endpoint
# ---------------------------------------------------------------------------


class TestStripeWebhook:
    @pytest.mark.integration
    async def test_missing_signature_returns_400(self, anon_client):
        """Webhook without Stripe-Signature header must be rejected."""
        resp = await anon_client.post(
            "/api/v1/billing/webhook",
            content=b'{"type":"test.event"}',
            headers={"Content-Type": "application/json"},
        )
        # 400 because the signature header is absent / invalid
        assert resp.status_code in (400, 422)

    @pytest.mark.integration
    async def test_bad_signature_returns_400(self, anon_client):
        """Webhook with a forged signature must be rejected."""
        resp = await anon_client.post(
            "/api/v1/billing/webhook",
            content=b'{"type":"test.event"}',
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": "t=123,v1=bad_signature",
            },
        )
        assert resp.status_code == 400

    @pytest.mark.integration
    async def test_webhook_checkout_completed_event(self, anon_client, app):
        """Lines 248-305: webhook processes checkout.session.completed event."""
        from unittest.mock import AsyncMock, MagicMock, patch

        event_id = "evt_test_checkout_001"
        event = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test001",
                    "subscription": "sub_test001",
                }
            },
        }

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.verify_webhook = MagicMock(return_value=event)

        import json

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await anon_client.post(
                "/api/v1/billing/webhook",
                content=json.dumps(event).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Stripe-Signature": "t=1234,v1=fakesig",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["received"] is True

    @pytest.mark.integration
    async def test_webhook_subscription_updated_event(self, anon_client, app):
        """Lines 292-297: webhook processes subscription.updated event."""
        from unittest.mock import AsyncMock, MagicMock, patch

        event_id = "evt_test_sub_updated_001"
        event = {
            "id": event_id,
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "customer": "cus_test002",
                    "id": "sub_test002",
                    "status": "active",
                    "items": {"data": [{"price": {"id": "price_test"}}]},
                }
            },
        }

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.verify_webhook = MagicMock(return_value=event)

        import json

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await anon_client.post(
                "/api/v1/billing/webhook",
                content=json.dumps(event).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Stripe-Signature": "t=5678,v1=fakesig2",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["received"] is True

    @pytest.mark.integration
    async def test_webhook_subscription_deleted_event(self, anon_client, app):
        """Lines 298-305: webhook processes subscription.deleted event."""
        from unittest.mock import AsyncMock, MagicMock, patch

        event_id = "evt_test_sub_deleted_001"
        event = {
            "id": event_id,
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "customer": "cus_test003",
                    "id": "sub_deleted_001",
                    "status": "canceled",
                }
            },
        }

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.verify_webhook = MagicMock(return_value=event)

        import json

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await anon_client.post(
                "/api/v1/billing/webhook",
                content=json.dumps(event).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Stripe-Signature": "t=9012,v1=fakesig3",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["received"] is True

    @pytest.mark.integration
    async def test_webhook_duplicate_event_returns_duplicate_true(
        self, anon_client, app
    ):
        """Lines 282-285: duplicate event_id returns duplicate:True without reprocessing."""
        from unittest.mock import MagicMock, patch

        event_id = "evt_dup_test_001"
        event = {
            "id": event_id,
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_dup_001"}},
        }

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.verify_webhook = MagicMock(return_value=event)

        import json

        payload = json.dumps(event).encode()
        headers = {
            "Content-Type": "application/json",
            "Stripe-Signature": "t=1111,v1=fakesig_dup",
        }

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            # First call — processes event
            resp1 = await anon_client.post(
                "/api/v1/billing/webhook", content=payload, headers=headers
            )
            # Second call — duplicate should be detected
            resp2 = await anon_client.post(
                "/api/v1/billing/webhook", content=payload, headers=headers
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp2.json().get("duplicate") is True


# ---------------------------------------------------------------------------
# GET /api/v1/billing/plan — subscription_status path (lines 72-78)
# ---------------------------------------------------------------------------


class TestBillingPlanExtras:
    @pytest.mark.integration
    async def test_plan_status_with_stripe_subscription_id(self, auth_client, app):
        """Lines 72-78: user with stripe_subscription_id triggers stripe_svc.get_subscription_status."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.get_subscription_status = AsyncMock(return_value="active")

        # Set stripe_subscription_id on the dev user
        from src.infrastructure.persistence.user_repository import (
            SQLAlchemyUserRepository,
        )

        db = app.state.container.database()
        async with db.session_factory() as session:
            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_subscription_id = "sub_fake_001"
                await repo.update(user)
                await session.commit()

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await auth_client.get("/api/v1/billing/plan")

        assert resp.status_code == 200
        data = resp.json()
        assert data["subscription_status"] == "active"

        # Cleanup
        async with db.session_factory() as session:
            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_subscription_id = None
                await repo.update(user)
                await session.commit()

    @pytest.mark.integration
    async def test_plan_status_stripe_exception_gives_unknown(self, auth_client, app):
        """Lines 77-78: stripe_svc exception → subscription_status='unknown'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.get_subscription_status = AsyncMock(
            side_effect=Exception("stripe error")
        )

        # Ensure user has stripe_subscription_id
        db = app.state.container.database()
        async with db.session_factory() as session:
            from src.infrastructure.persistence.user_repository import (
                SQLAlchemyUserRepository,
            )

            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_subscription_id = "sub_fake_002"
                await repo.update(user)
                await session.commit()

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await auth_client.get("/api/v1/billing/plan")

        assert resp.status_code == 200
        assert resp.json()["subscription_status"] == "unknown"

        # Cleanup
        async with db.session_factory() as session:
            from src.infrastructure.persistence.user_repository import (
                SQLAlchemyUserRepository,
            )

            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_subscription_id = None
                await repo.update(user)
                await session.commit()


# ---------------------------------------------------------------------------
# POST /api/v1/billing/checkout — success path (lines 130-160)
# ---------------------------------------------------------------------------


class TestCheckoutSuccess:
    @pytest.mark.integration
    async def test_checkout_creates_customer_and_returns_url(self, auth_client, app):
        """Lines 130-160: checkout with new customer — creates customer + checkout URL."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.create_customer = AsyncMock(return_value="cus_new_001")
        mock_stripe_svc.create_checkout_session = AsyncMock(
            return_value="https://checkout.stripe.com/pay/test"
        )

        # Clear stripe_customer_id so it triggers customer creation
        db = app.state.container.database()
        async with db.session_factory() as session:
            from src.infrastructure.persistence.user_repository import (
                SQLAlchemyUserRepository,
            )

            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_customer_id = None
                await repo.update(user)
                await session.commit()

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await auth_client.post(
                "/api/v1/billing/checkout",
                json={
                    "plan": "pro",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "checkout_url" in data
        assert "checkout.stripe.com" in data["checkout_url"]

        # Cleanup: reset stripe fields to avoid polluting subsequent tests
        async with db.session_factory() as session:
            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_customer_id = None
                await repo.update(user)
                await session.commit()

    @pytest.mark.integration
    async def test_checkout_with_existing_customer_id(self, auth_client, app):
        """Lines 176-183: checkout with existing customer_id — skips customer creation."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_stripe_svc = MagicMock()
        mock_stripe_svc.create_checkout_session = AsyncMock(
            return_value="https://checkout.stripe.com/pay/existing"
        )

        # Set stripe_customer_id on dev user
        db = app.state.container.database()
        async with db.session_factory() as session:
            from src.infrastructure.persistence.user_repository import (
                SQLAlchemyUserRepository,
            )

            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_customer_id = "cus_existing_001"
                await repo.update(user)
                await session.commit()

        with patch.object(
            app.state.container, "stripe_service", return_value=mock_stripe_svc
        ):
            resp = await auth_client.post(
                "/api/v1/billing/checkout",
                json={
                    "plan": "pro",
                    "success_url": "https://example.com/success",
                    "cancel_url": "https://example.com/cancel",
                },
            )

        assert resp.status_code == 200
        (
            mock_stripe_svc.create_customer.assert_not_called()
            if hasattr(mock_stripe_svc, "create_customer")
            else None
        )

        # Cleanup: reset stripe fields
        async with db.session_factory() as session:
            from src.infrastructure.persistence.user_repository import (
                SQLAlchemyUserRepository,
            )

            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_email("dev@calendar-agent.local")
            if user:
                user.stripe_customer_id = None
                await repo.update(user)
                await session.commit()

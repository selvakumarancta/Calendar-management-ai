"""
Unit tests for StripeBillingService.

All stripe API calls are mocked — no network or real Stripe account needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.billing.plans import PlanTier
from src.billing.stripe_service import StripeBillingService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_svc(price_ids: dict | None = None) -> StripeBillingService:
    return StripeBillingService(
        secret_key="sk_test_fake",
        webhook_secret="whsec_fake",
        price_ids=price_ids
        or {PlanTier.PRO: "price_pro", PlanTier.ENTERPRISE: "price_ent"},
    )


# ---------------------------------------------------------------------------
# create_customer
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_customer_returns_id():
    """create_customer calls stripe.Customer.create and returns the ID (line 38)."""
    svc = _make_svc()
    fake_customer = MagicMock()
    fake_customer.id = "cus_test123"

    with patch(
        "src.billing.stripe_service.stripe.Customer.create", return_value=fake_customer
    ) as mock_create:
        result = await svc.create_customer("alice@example.com", "Alice")

    assert result == "cus_test123"
    mock_create.assert_called_once_with(email="alice@example.com", name="Alice")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_customer_passes_idempotency_key():
    """create_customer includes idempotency_key when provided (lines 35-37)."""
    svc = _make_svc()
    fake_customer = MagicMock()
    fake_customer.id = "cus_idem"

    with patch(
        "src.billing.stripe_service.stripe.Customer.create", return_value=fake_customer
    ) as mock_create:
        result = await svc.create_customer("x@y.com", "X", idempotency_key="ikey-1")

    assert result == "cus_idem"
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["idempotency_key"] == "ikey-1"


# ---------------------------------------------------------------------------
# create_checkout_session
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_checkout_session_returns_url():
    """create_checkout_session returns the session URL (lines 49-64)."""
    svc = _make_svc()
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/cs_test"

    with patch(
        "src.billing.stripe_service.stripe.checkout.Session.create",
        return_value=fake_session,
    ):
        result = await svc.create_checkout_session(
            customer_id="cus_123",
            plan=PlanTier.PRO,
            success_url="https://app.example.com/success",
            cancel_url="https://app.example.com/cancel",
        )

    assert result == "https://checkout.stripe.com/pay/cs_test"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_checkout_session_raises_for_unknown_plan():
    """create_checkout_session raises ValueError for unconfigured plan (lines 50-51)."""
    svc = _make_svc(price_ids={PlanTier.ENTERPRISE: "price_ent"})  # PRO not configured

    with patch(
        "src.billing.stripe_service.stripe.checkout.Session.create"
    ) as mock_create:
        with pytest.raises(ValueError, match="No Stripe price ID"):
            await svc.create_checkout_session(
                customer_id="cus_123",
                plan=PlanTier.PRO,
                success_url="https://app/success",
                cancel_url="https://app/cancel",
            )
    # stripe.checkout.Session.create must NOT have been called
    mock_create.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_checkout_session_passes_idempotency_key():
    """create_checkout_session includes idempotency_key when set (lines 61-62)."""
    svc = _make_svc()
    fake_session = MagicMock()
    fake_session.url = "https://stripe.com/session"

    with patch(
        "src.billing.stripe_service.stripe.checkout.Session.create",
        return_value=fake_session,
    ) as mock_create:
        await svc.create_checkout_session(
            customer_id="cus_123",
            plan=PlanTier.PRO,
            success_url="https://app/success",
            cancel_url="https://app/cancel",
            idempotency_key="ik-2",
        )

    assert mock_create.call_args[1].get("idempotency_key") == "ik-2"


# ---------------------------------------------------------------------------
# cancel_subscription
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_subscription_returns_true_on_success():
    """cancel_subscription modifies Stripe subscription and returns True (lines 68-74)."""
    svc = _make_svc()
    with patch("src.billing.stripe_service.stripe.Subscription.modify") as mock_modify:
        result = await svc.cancel_subscription("sub_abc")

    assert result is True
    mock_modify.assert_called_once_with("sub_abc", cancel_at_period_end=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_subscription_returns_false_on_stripe_error():
    """cancel_subscription catches StripeError and returns False (line 74-75)."""
    import stripe as _stripe

    svc = _make_svc()
    with patch(
        "src.billing.stripe_service.stripe.Subscription.modify",
        side_effect=_stripe.error.StripeError("card declined"),
    ):
        result = await svc.cancel_subscription("sub_bad")

    assert result is False


# ---------------------------------------------------------------------------
# verify_webhook
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_verify_webhook_returns_event_dict():
    """verify_webhook constructs the event and returns a dict (lines 79-80)."""
    svc = _make_svc()
    fake_event = {"type": "customer.subscription.created", "data": {}}

    with patch(
        "src.billing.stripe_service.stripe.Webhook.construct_event",
        return_value=fake_event,
    ) as mock_construct:
        result = svc.verify_webhook(b"payload", "sig_header")

    assert result["type"] == "customer.subscription.created"
    mock_construct.assert_called_once_with(b"payload", "sig_header", "whsec_fake")


# ---------------------------------------------------------------------------
# get_subscription_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_subscription_status_returns_status():
    """get_subscription_status retrieves subscription and returns status (lines 84-85)."""
    svc = _make_svc()
    fake_sub = MagicMock()
    fake_sub.status = "active"

    with patch(
        "src.billing.stripe_service.stripe.Subscription.retrieve", return_value=fake_sub
    ):
        result = await svc.get_subscription_status("sub_xyz")

    assert result == "active"

"""
Stripe Billing Service — handles subscription lifecycle.
"""

from __future__ import annotations

from typing import Any

import stripe

from src.billing.plans import PlanTier


class StripeBillingService:
    """Manages Stripe subscriptions for SaaS billing."""

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        price_ids: dict[PlanTier, str],
    ) -> None:
        stripe.api_key = secret_key
        self._webhook_secret = webhook_secret
        self._price_ids = price_ids

    async def create_customer(self, email: str, name: str) -> str:
        """Create a Stripe customer. Returns customer ID."""
        customer = stripe.Customer.create(email=email, name=name)
        return customer.id

    async def create_checkout_session(
        self,
        customer_id: str,
        plan: PlanTier,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """Create a Stripe Checkout Session. Returns session URL."""
        price_id = self._price_ids.get(plan)
        if not price_id:
            raise ValueError(f"No Stripe price ID configured for plan: {plan}")

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return session.url or ""

    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel a subscription at period end."""
        try:
            stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True,
            )
            return True
        except stripe.error.StripeError:
            return False

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify and parse a Stripe webhook event."""
        event = stripe.Webhook.construct_event(payload, signature, self._webhook_secret)
        return dict(event)

    async def get_subscription_status(self, subscription_id: str) -> str:
        """Get current subscription status."""
        sub = stripe.Subscription.retrieve(subscription_id)
        return sub.status

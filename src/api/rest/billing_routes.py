"""
Billing API Routes — Stripe subscription management.
Handles checkout sessions, billing portal, plan status, and Stripe webhooks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.api.dependencies import get_container, get_current_user
from src.billing.plans import PLANS, PlanTier
from src.config.container import Container
from src.domain.entities.user import User

logger = logging.getLogger("calendar_agent.billing")

billing_router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    plan: str  # "pro" | "business" | "enterprise"
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    checkout_url: str


class PlanStatusResponse(BaseModel):
    plan: str
    monthly_request_limit: int
    monthly_requests_used: int
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    subscription_status: str | None


class BillingPortalRequest(BaseModel):
    return_url: str


class BillingPortalResponse(BaseModel):
    portal_url: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@billing_router.get("/plan", response_model=PlanStatusResponse)
async def get_plan_status(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> PlanStatusResponse:
    """Return the current user's subscription plan and usage summary."""
    monthly_used = await container.usage_tracker().get_monthly_request_count(
        current_user.id
    )

    subscription_status: str | None = None
    if current_user.stripe_subscription_id:
        try:
            stripe_svc = container.stripe_service()
            subscription_status = await stripe_svc.get_subscription_status(
                current_user.stripe_subscription_id
            )
        except Exception:
            subscription_status = "unknown"

    return PlanStatusResponse(
        plan=current_user.plan.value,
        monthly_request_limit=current_user.get_request_limit(),
        monthly_requests_used=monthly_used,
        stripe_customer_id=current_user.stripe_customer_id,
        stripe_subscription_id=current_user.stripe_subscription_id,
        subscription_status=subscription_status,
    )


@billing_router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> CheckoutResponse:
    """Create a Stripe Checkout Session for upgrading to a paid plan."""
    try:
        plan_tier = PlanTier(body.plan)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan: {body.plan}. Choose from: pro, business, enterprise",
        )

    if plan_tier == PlanTier.FREE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create checkout for free plan",
        )

    stripe_svc = container.stripe_service()

    # Ensure the user has a Stripe customer record.
    # Idempotency key is derived from the user's ID so retries don't create duplicates.
    customer_id = current_user.stripe_customer_id
    if not customer_id:
        try:
            customer_id = await stripe_svc.create_customer(
                email=current_user.email,
                name=current_user.name or current_user.email,
                idempotency_key=f"create_customer_{current_user.id}",
            )
        except Exception as exc:
            logger.error("Stripe create_customer failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to create Stripe customer",
            ) from exc
        # Persist customer_id
        from src.infrastructure.persistence.user_repository import (
            SQLAlchemyUserRepository,
        )

        db = container.database()
        async with db.session_factory() as session:
            repo = SQLAlchemyUserRepository(session)
            current_user.stripe_customer_id = customer_id
            await repo.update(current_user)
            await session.commit()

    try:
        url = await stripe_svc.create_checkout_session(
            customer_id=customer_id,
            plan=plan_tier,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            idempotency_key=f"checkout_{current_user.id}_{plan_tier.value}",
        )
    except Exception as exc:
        logger.error("Stripe create_checkout_session failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe checkout session",
        ) from exc
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stripe checkout session",
        )
    return CheckoutResponse(checkout_url=url)


@billing_router.post("/portal", response_model=BillingPortalResponse)
async def create_billing_portal(
    body: BillingPortalRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> BillingPortalResponse:
    """Create a Stripe Customer Portal session so the user can manage their subscription."""
    if not current_user.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe customer found. Please subscribe to a paid plan first.",
        )

    import stripe

    stripe.api_key = container.settings.stripe_secret_key
    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=body.return_url,
    )
    return BillingPortalResponse(portal_url=session.url)


class UsageResponse(BaseModel):
    """Detailed monthly usage breakdown."""

    period: str  # "YYYY-MM"
    monthly_request_count: int
    monthly_request_limit: int
    monthly_token_usage: int
    estimated_cost_usd: float
    plan: str


@billing_router.get("/usage", response_model=UsageResponse)
async def get_usage(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> UsageResponse:
    """
    Return the authenticated user's current-month token / request usage
    and a cost estimate based on the active LLM pricing.
    """
    from datetime import datetime
    from datetime import timezone as _tz

    tracker = container.usage_tracker()
    user_id = current_user.id

    monthly_requests = await tracker.get_monthly_request_count(user_id)
    monthly_tokens = await tracker.get_monthly_token_usage(user_id)
    cost_estimate = await tracker.get_monthly_cost_estimate(user_id)
    period = datetime.now(_tz.utc).strftime("%Y-%m")

    return UsageResponse(
        period=period,
        monthly_request_count=monthly_requests,
        monthly_request_limit=current_user.get_request_limit(),
        monthly_token_usage=monthly_tokens,
        estimated_cost_usd=float(cost_estimate),
        plan=current_user.plan.value,
    )


@billing_router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
    container: Container = Depends(get_container),
) -> dict:
    """
    Stripe webhook handler.
    Verifies the Stripe-Signature header, then handles:
      - checkout.session.completed       → activate subscription, update plan
      - customer.subscription.updated    → sync plan changes
      - customer.subscription.deleted    → downgrade to free
      - invoice.payment_failed           → log / alert
    """
    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    payload = await request.body()
    stripe_svc = container.stripe_service()

    try:
        event = stripe_svc.verify_webhook(payload, stripe_signature)
    except Exception as exc:
        logger.warning("Stripe webhook verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        ) from exc

    event_type: str = event.get("type", "")
    event_id: str = event.get("id", "")
    event_data: dict = event.get("data", {}).get("object", {})

    logger.info("Stripe webhook received: %s id=%s", event_type, event_id)

    db = container.database()

    # Idempotency guard — Stripe retries on non-2xx; process each event exactly once
    if event_id:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.exc import IntegrityError

        from src.infrastructure.persistence.models import StripeProcessedEventModel

        try:
            async with db.session_factory() as session:  # type: ignore[union-attr]
                row = StripeProcessedEventModel(
                    event_id=event_id, event_type=event_type
                )
                session.add(row)
                await session.commit()
        except IntegrityError:
            # Already processed — return 200 so Stripe doesn't retry
            logger.info("Stripe event %s already processed — skipping", event_id)
            return {"received": True, "duplicate": True}

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(db, event_data)

    elif event_type in (
        "customer.subscription.updated",
        "customer.subscription.created",
    ):
        await _handle_subscription_updated(db, event_data)

    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, event_data)

    elif event_type == "invoice.payment_failed":
        logger.warning(
            "Stripe payment failed for customer=%s invoice=%s",
            event_data.get("customer"),
            event_data.get("id"),
        )

    return {"received": True}


# ---------------------------------------------------------------------------
# Internal webhook helpers
# ---------------------------------------------------------------------------


async def _plan_tier_from_stripe_price(
    price_id: str, container_settings: object
) -> PlanTier:
    """Map a Stripe price ID back to a PlanTier."""
    s = container_settings
    mapping = {
        getattr(s, "stripe_price_pro", ""): PlanTier.PRO,
        getattr(s, "stripe_price_business", ""): PlanTier.BUSINESS,
    }
    return mapping.get(price_id, PlanTier.FREE)


async def _handle_checkout_completed(db: object, session_obj: dict) -> None:
    """Activate subscription after successful checkout."""
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    customer_id: str = session_obj.get("customer", "")
    subscription_id: str = session_obj.get("subscription", "")
    if not customer_id or not subscription_id:
        return

    async with db.session_factory() as session:  # type: ignore[union-attr]
        result = await session.execute(
            select(UserModel).where(UserModel.stripe_customer_id == customer_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.stripe_subscription_id = subscription_id
            # Plan will be set by subscription.updated event
            await session.commit()
            logger.info("Checkout completed for customer=%s", customer_id)


async def _handle_subscription_updated(db: object, subscription: dict) -> None:
    """Sync plan tier when a subscription changes."""
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel

    customer_id: str = subscription.get("customer", "")
    sub_id: str = subscription.get("id", "")
    sub_status: str = subscription.get("status", "")

    # Extract the plan from the first subscription item's price
    items = subscription.get("items", {}).get("data", [])
    price_id = items[0]["price"]["id"] if items else ""

    # Map price → plan tier
    plan_map: dict[str, str] = {}
    try:
        import os

        plan_map = {
            os.environ.get("STRIPE_PRICE_PRO", ""): "pro",
            os.environ.get("STRIPE_PRICE_BUSINESS", ""): "business",
        }
    except Exception:
        pass

    new_plan = plan_map.get(price_id, "free")
    if sub_status not in ("active", "trialing"):
        new_plan = "free"

    async with db.session_factory() as session:  # type: ignore[union-attr]
        result = await session.execute(
            select(UserModel).where(UserModel.stripe_customer_id == customer_id)
        )
        model = result.scalar_one_or_none()
        if model:
            old_plan = model.plan or "free"
            model.stripe_subscription_id = sub_id
            model.plan = new_plan
            await session.commit()
            logger.info(
                "Subscription updated: customer=%s plan=%s status=%s",
                customer_id,
                new_plan,
                sub_status,
            )
            # Audit trail
            try:
                from src.config.container import Container as _Container

                _audit = getattr(__builtins__, "_audit_log_svc", None)
            except Exception:
                pass
            logger.info(
                "audit: user.plan_changed actor=%s %s→%s",
                str(model.id),
                old_plan,
                new_plan,
            )


async def _handle_subscription_deleted(db: object, subscription: dict) -> None:
    """Downgrade user to free plan when subscription is cancelled."""
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel

    customer_id: str = subscription.get("customer", "")
    if not customer_id:
        return

    async with db.session_factory() as session:  # type: ignore[union-attr]
        result = await session.execute(
            select(UserModel).where(UserModel.stripe_customer_id == customer_id)
        )
        model = result.scalar_one_or_none()
        if model:
            old_plan = model.plan or "free"
            model.plan = "free"
            model.stripe_subscription_id = None
            await session.commit()
            logger.info(
                "Subscription deleted — downgraded to free: customer=%s (was %s)",
                customer_id,
                old_plan,
            )

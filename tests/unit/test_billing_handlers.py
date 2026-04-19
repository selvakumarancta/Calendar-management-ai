"""
Unit tests for billing route handlers (_handle_checkout_completed,
_handle_subscription_updated, _handle_subscription_deleted) and
_plan_tier_from_stripe_price.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_db(user_model=None):
    """Create a mock DB with optional user model."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session.add = MagicMock()

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=user_model)
    session.execute = AsyncMock(return_value=result)

    db = MagicMock()
    db.session_factory = MagicMock(return_value=session)
    return db


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_checkout_completed_skips_when_no_customer():
    from src.api.rest.billing_routes import _handle_checkout_completed

    db = _make_db()
    await _handle_checkout_completed(db, {"customer": "", "subscription": ""})
    # Should not call session.execute
    db.session_factory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_checkout_completed_updates_subscription_id():
    from src.api.rest.billing_routes import _handle_checkout_completed

    user_model = MagicMock()
    user_model.stripe_subscription_id = None
    user_model.stripe_customer_id = "cus_123"

    db = _make_db(user_model=user_model)
    await _handle_checkout_completed(
        db,
        {
            "customer": "cus_123",
            "subscription": "sub_abc",
        },
    )
    assert user_model.stripe_subscription_id == "sub_abc"
    db.session_factory().commit.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_checkout_completed_when_user_not_found():
    from src.api.rest.billing_routes import _handle_checkout_completed

    db = _make_db(user_model=None)
    # Should not raise
    await _handle_checkout_completed(
        db,
        {
            "customer": "cus_notexist",
            "subscription": "sub_abc",
        },
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_subscription_updated_sets_plan():
    from src.api.rest.billing_routes import _handle_subscription_updated

    user_model = MagicMock()
    user_model.id = uuid.uuid4()
    user_model.plan = "free"
    user_model.stripe_customer_id = "cus_123"

    db = _make_db(user_model=user_model)
    await _handle_subscription_updated(
        db,
        {
            "customer": "cus_123",
            "id": "sub_abc",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_pro"}}]},
        },
    )
    assert user_model.stripe_subscription_id == "sub_abc"
    db.session_factory().commit.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_subscription_updated_downgrades_on_inactive():
    from src.api.rest.billing_routes import _handle_subscription_updated

    user_model = MagicMock()
    user_model.id = uuid.uuid4()
    user_model.plan = "pro"

    db = _make_db(user_model=user_model)
    await _handle_subscription_updated(
        db,
        {
            "customer": "cus_123",
            "id": "sub_abc",
            "status": "past_due",
            "items": {"data": []},
        },
    )
    # Status is not active → should downgrade to free
    assert user_model.plan == "free"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_subscription_updated_user_not_found():
    from src.api.rest.billing_routes import _handle_subscription_updated

    db = _make_db(user_model=None)
    # Should not raise
    await _handle_subscription_updated(
        db,
        {
            "customer": "cus_notexist",
            "id": "sub_abc",
            "status": "active",
            "items": {"data": []},
        },
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_subscription_deleted_downgrades_to_free():
    from src.api.rest.billing_routes import _handle_subscription_deleted

    user_model = MagicMock()
    user_model.id = uuid.uuid4()
    user_model.plan = "pro"
    user_model.stripe_subscription_id = "sub_abc"

    db = _make_db(user_model=user_model)
    await _handle_subscription_deleted(db, {"customer": "cus_123"})
    assert user_model.plan == "free"
    assert user_model.stripe_subscription_id is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_subscription_deleted_skips_when_no_customer():
    from src.api.rest.billing_routes import _handle_subscription_deleted

    db = _make_db()
    await _handle_subscription_deleted(db, {"customer": ""})
    db.session_factory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_subscription_deleted_user_not_found():
    from src.api.rest.billing_routes import _handle_subscription_deleted

    db = _make_db(user_model=None)
    # Should not raise
    await _handle_subscription_deleted(db, {"customer": "cus_nope"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plan_tier_from_stripe_price_maps_known_price():
    from src.api.rest.billing_routes import _plan_tier_from_stripe_price
    from src.billing.plans import PlanTier

    settings = MagicMock()
    settings.stripe_price_pro = "price_pro_test"
    settings.stripe_price_business = "price_biz_test"

    result = await _plan_tier_from_stripe_price("price_pro_test", settings)
    assert result == PlanTier.PRO


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plan_tier_from_stripe_price_returns_free_for_unknown():
    from src.api.rest.billing_routes import _plan_tier_from_stripe_price
    from src.billing.plans import PlanTier

    settings = MagicMock()
    settings.stripe_price_pro = "price_pro_test"
    settings.stripe_price_business = "price_biz_test"

    result = await _plan_tier_from_stripe_price("price_unknown", settings)
    assert result == PlanTier.FREE

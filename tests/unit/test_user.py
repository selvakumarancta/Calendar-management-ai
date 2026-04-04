"""
Tests for User entity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.entities.user import SubscriptionPlan, User


class TestUser:
    """Test User domain entity."""

    @pytest.mark.unit
    def test_free_plan_request_limit(self) -> None:
        user = User(plan=SubscriptionPlan.FREE)
        assert user.get_request_limit() == 50

    @pytest.mark.unit
    def test_pro_plan_request_limit(self) -> None:
        user = User(plan=SubscriptionPlan.PRO)
        assert user.get_request_limit() == 500

    @pytest.mark.unit
    def test_business_plan_request_limit(self) -> None:
        user = User(plan=SubscriptionPlan.BUSINESS)
        assert user.get_request_limit() == 2000

    @pytest.mark.unit
    def test_free_plan_cannot_use_primary_model(self) -> None:
        user = User(plan=SubscriptionPlan.FREE)
        assert user.can_use_primary_model() is False

    @pytest.mark.unit
    def test_pro_plan_can_use_primary_model(self) -> None:
        user = User(plan=SubscriptionPlan.PRO)
        assert user.can_use_primary_model() is True

    @pytest.mark.unit
    def test_has_valid_google_token_when_not_expired(self) -> None:
        user = User(
            google_access_token="token",
            google_token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert user.has_valid_google_token() is True

    @pytest.mark.unit
    def test_has_invalid_google_token_when_expired(self) -> None:
        user = User(
            google_access_token="token",
            google_token_expiry=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert user.has_valid_google_token() is False

    @pytest.mark.unit
    def test_has_invalid_google_token_when_missing(self) -> None:
        user = User()
        assert user.has_valid_google_token() is False

    @pytest.mark.unit
    def test_update_google_tokens(self) -> None:
        user = User()
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        user.update_google_tokens("new-token", "new-refresh", expiry)
        assert user.google_access_token == "new-token"
        assert user.google_refresh_token == "new-refresh"
        assert user.google_token_expiry == expiry

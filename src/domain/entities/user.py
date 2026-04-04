"""
User entity — represents a tenant/user in the SaaS platform.
Pure domain object with no infrastructure dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SubscriptionPlan(str, Enum):
    """Available SaaS subscription tiers."""

    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


@dataclass
class User:
    """Core user entity."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = ""
    name: str = ""
    timezone: str = "UTC"
    plan: SubscriptionPlan = SubscriptionPlan.FREE
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # OAuth tokens (encrypted at rest in infra layer)
    google_access_token: str | None = None
    google_refresh_token: str | None = None
    google_token_expiry: datetime | None = None

    # Stripe
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None

    def has_valid_google_token(self) -> bool:
        """Check if Google OAuth token is still valid."""
        if not self.google_access_token or not self.google_token_expiry:
            return False
        return self.google_token_expiry > datetime.now(timezone.utc)

    def can_use_primary_model(self) -> bool:
        """Check if user's plan allows the primary (expensive) LLM model."""
        return self.plan in (
            SubscriptionPlan.PRO,
            SubscriptionPlan.BUSINESS,
            SubscriptionPlan.ENTERPRISE,
        )

    def get_request_limit(self) -> int:
        """Return monthly request limit based on plan."""
        limits = {
            SubscriptionPlan.FREE: 50,
            SubscriptionPlan.PRO: 500,
            SubscriptionPlan.BUSINESS: 2000,
            SubscriptionPlan.ENTERPRISE: 100_000,
        }
        return limits[self.plan]

    def update_google_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        expiry: datetime,
    ) -> None:
        """Update OAuth tokens after refresh."""
        self.google_access_token = access_token
        if refresh_token:
            self.google_refresh_token = refresh_token
        self.google_token_expiry = expiry
        self.updated_at = datetime.now(timezone.utc)

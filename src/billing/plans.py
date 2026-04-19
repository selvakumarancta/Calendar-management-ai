"""
SaaS Plan Definitions — subscription tiers, limits, and features.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlanTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


# Capability tags assigned to plans — do NOT hard-code model names here.
# Model-name allow-listing is done by ChatService using active settings,
# so upgrading LLM versions requires no plan changes.
_FREE_FEATURES = "basic_crud list_events single_calendar"
_PRO_FEATURES = "basic_crud list_events smart_scheduling conflict_detection multi_calendar free_slot_finder"
_BUSINESS_FEATURES = (
    _PRO_FEATURES + " team_calendars api_access priority_routing webhook_notifications"
)
_ENTERPRISE_FEATURES = "all"


@dataclass(frozen=True)
class PlanDefinition:
    """Immutable plan configuration."""

    tier: PlanTier
    name: str
    monthly_price_usd: float
    monthly_request_limit: int
    max_calendars: int
    allows_primary_model: bool
    """True if this plan may use the expensive/primary LLM model."""
    features: list[str]


# Plan catalog — model names intentionally omitted.
# Gate model access via User.can_use_primary_model() which reads this flag.
PLANS: dict[PlanTier, PlanDefinition] = {
    PlanTier.FREE: PlanDefinition(
        tier=PlanTier.FREE,
        name="Free",
        monthly_price_usd=0.0,
        monthly_request_limit=50,
        max_calendars=1,
        allows_primary_model=False,
        features=_FREE_FEATURES.split(),
    ),
    PlanTier.PRO: PlanDefinition(
        tier=PlanTier.PRO,
        name="Pro",
        monthly_price_usd=9.99,
        monthly_request_limit=500,
        max_calendars=5,
        allows_primary_model=True,
        features=_PRO_FEATURES.split(),
    ),
    PlanTier.BUSINESS: PlanDefinition(
        tier=PlanTier.BUSINESS,
        name="Business",
        monthly_price_usd=29.99,
        monthly_request_limit=2000,
        max_calendars=20,
        allows_primary_model=True,
        features=_BUSINESS_FEATURES.split(),
    ),
    PlanTier.ENTERPRISE: PlanDefinition(
        tier=PlanTier.ENTERPRISE,
        name="Enterprise",
        monthly_price_usd=0.0,  # Custom pricing
        monthly_request_limit=100_000,
        max_calendars=999,
        allows_primary_model=True,
        features=_ENTERPRISE_FEATURES.split(),
    ),
}


def get_plan(tier: PlanTier) -> PlanDefinition:
    """Get plan definition by tier."""
    return PLANS[tier]

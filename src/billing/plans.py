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


@dataclass(frozen=True)
class PlanDefinition:
    """Immutable plan configuration."""

    tier: PlanTier
    name: str
    monthly_price_usd: float
    monthly_request_limit: int
    max_calendars: int
    model_access: list[str]  # Which LLM models the plan allows
    features: list[str]

    @property
    def allows_primary_model(self) -> bool:
        """Check if plan allows expensive primary models (any provider)."""
        primary_models = {"gpt-4o", "claude-sonnet-4-20250514"}
        return bool(primary_models & set(self.model_access))


# Plan catalog
PLANS: dict[PlanTier, PlanDefinition] = {
    PlanTier.FREE: PlanDefinition(
        tier=PlanTier.FREE,
        name="Free",
        monthly_price_usd=0.0,
        monthly_request_limit=50,
        max_calendars=1,
        model_access=["gpt-4o-mini", "claude-haiku-3-20250414"],
        features=["basic_crud", "list_events", "single_calendar"],
    ),
    PlanTier.PRO: PlanDefinition(
        tier=PlanTier.PRO,
        name="Pro",
        monthly_price_usd=9.99,
        monthly_request_limit=500,
        max_calendars=5,
        model_access=[
            "gpt-4o-mini",
            "gpt-4o",
            "claude-haiku-3-20250414",
            "claude-sonnet-4-20250514",
        ],
        features=[
            "basic_crud",
            "list_events",
            "smart_scheduling",
            "conflict_detection",
            "multi_calendar",
            "free_slot_finder",
        ],
    ),
    PlanTier.BUSINESS: PlanDefinition(
        tier=PlanTier.BUSINESS,
        name="Business",
        monthly_price_usd=29.99,
        monthly_request_limit=2000,
        max_calendars=20,
        model_access=[
            "gpt-4o-mini",
            "gpt-4o",
            "claude-haiku-3-20250414",
            "claude-sonnet-4-20250514",
        ],
        features=[
            "basic_crud",
            "list_events",
            "smart_scheduling",
            "conflict_detection",
            "multi_calendar",
            "free_slot_finder",
            "team_calendars",
            "api_access",
            "priority_routing",
            "webhook_notifications",
        ],
    ),
    PlanTier.ENTERPRISE: PlanDefinition(
        tier=PlanTier.ENTERPRISE,
        name="Enterprise",
        monthly_price_usd=0.0,  # Custom pricing
        monthly_request_limit=100_000,
        max_calendars=999,
        model_access=[
            "gpt-4o-mini",
            "gpt-4o",
            "claude-haiku-3-20250414",
            "claude-sonnet-4-20250514",
        ],
        features=["all"],
    ),
}


def get_plan(tier: PlanTier) -> PlanDefinition:
    """Get plan definition by tier."""
    return PLANS[tier]

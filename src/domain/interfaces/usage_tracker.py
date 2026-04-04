"""Port: Usage Tracker — abstract interface for tracking API/LLM usage per tenant."""

from __future__ import annotations

import abc
from uuid import UUID

from src.domain.value_objects import TokenUsage


class UsageTrackerPort(abc.ABC):
    """Abstract usage tracking interface for SaaS billing."""

    @abc.abstractmethod
    async def record_request(
        self, user_id: UUID, token_usage: TokenUsage | None = None
    ) -> None:
        """Record a single agent request and optional token usage."""
        ...

    @abc.abstractmethod
    async def get_monthly_request_count(self, user_id: UUID) -> int:
        """Get the number of requests made this month."""
        ...

    @abc.abstractmethod
    async def get_monthly_token_usage(self, user_id: UUID) -> int:
        """Get total tokens consumed this month."""
        ...

    @abc.abstractmethod
    async def is_within_quota(self, user_id: UUID, limit: int) -> bool:
        """Check if user is within their plan's request quota."""
        ...

    @abc.abstractmethod
    async def get_monthly_cost_estimate(self, user_id: UUID) -> float:
        """Estimated LLM cost for the current month."""
        ...
